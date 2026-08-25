import asyncio
import logging
import time
import math
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

from config import (
    HEALTH_CHECK_INTERVAL,
    SOURCE_REFRESH_INTERVAL,
    OFFLINE_RECHECK_INTERVAL,
)
from node_registry import (
    NodeHealth,
    NetworkContext,
    CandidateNode,
    registry,
)
from config_delivery_engine import delivery_engine
from tester import ping_single_config

logger = logging.getLogger("HealthMonitor")

@dataclass
class RequestMetric:
    timestamp: float
    carrier: str
    network_type: str
    region: str
    latency_ms: int
    ttfb_ms: int
    retry_count: int
    timeout_occurred: bool
    cache_hit: bool
    cache_level: str
    node_id: int

class MetricsCollector:
    """
    سیستم جمع‌آوری و تحلیل بلادرنگ تله‌متری و معیارهای عملکردی (Observability & Metrics)
    """
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._history: List[RequestMetric] = []
        self._lock = asyncio.Lock()

    async def record(self, metric: RequestMetric):
        async with self._lock:
            self._history.append(metric)
            if len(self._history) > self.max_history:
                self._history.pop(0)

    def get_summary(self) -> Dict[str, Any]:
        """محاسبه شاخص‌های آماری کلیدی (P50, P75, P95, P99, Success Rate, Cache Hit Rate)"""
        if not self._history:
            return {
                "total_requests": 0,
                "p50_latency": 0,
                "p75_latency": 0,
                "p95_latency": 0,
                "p99_latency": 0,
                "avg_ttfb": 0,
                "success_rate": 100.0,
                "timeout_rate": 0.0,
                "cache_hit_rate": 0.0,
                "carrier_stats": {},
                "regional_stats": {}
            }

        latencies = [m.latency_ms for m in self._history if m.latency_ms > 0]
        latencies.sort()
        
        ttfbs = [m.ttfb_ms for m in self._history if m.ttfb_ms > 0]
        avg_ttfb = int(sum(ttfbs) / len(ttfbs)) if ttfbs else 0

        def percentile(data: List[int], p: float) -> int:
            if not data:
                return 0
            k = (len(data) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return data[int(k)]
            d0 = data[int(f)] * (c - k)
            d1 = data[int(c)] * (k - f)
            return int(d0 + d1)

        total = len(self._history)
        timeouts = sum(1 for m in self._history if m.timeout_occurred)
        cache_hits = sum(1 for m in self._history if m.cache_hit)
        
        # تفکیک بر اساس اپراتور
        carrier_counts: Dict[str, Dict[str, int]] = {}
        for m in self._history:
            c = m.carrier or "all"
            if c not in carrier_counts:
                carrier_counts[c] = {"total": 0, "sum_lat": 0, "count_lat": 0}
            carrier_counts[c]["total"] += 1
            if m.latency_ms > 0:
                carrier_counts[c]["sum_lat"] += m.latency_ms
                carrier_counts[c]["count_lat"] += 1

        carrier_summary = {}
        for c, st in carrier_counts.items():
            avg_lat = int(st["sum_lat"] / max(1, st["count_lat"]))
            carrier_summary[c] = {"requests": st["total"], "avg_latency": avg_lat}

        return {
            "total_requests": total,
            "p50_latency": percentile(latencies, 0.50),
            "p75_latency": percentile(latencies, 0.75),
            "p95_latency": percentile(latencies, 0.95),
            "p99_latency": percentile(latencies, 0.99),
            "avg_ttfb": avg_ttfb,
            "success_rate": round(100.0 * (total - timeouts) / max(1, total), 1),
            "timeout_rate": round(100.0 * timeouts / max(1, total), 1),
            "cache_hit_rate": round(100.0 * cache_hits / max(1, total), 1),
            "carrier_stats": carrier_summary
        }

metrics_collector = MetricsCollector()

class HealthMonitorEngine:
    """
    موتور پایش مستقل و دوره‌ای سلامت، رتبه‌بندی و رفرش ۳۰ دقیقه‌ای نودها
    """
    def __init__(self):
        self._is_running = False
        self._last_offline_check = 0.0

    async def run_health_check_cycle(self):
        """
        یک چرخه ارزیابی سلامت با اولویت‌بندی هوشمند:
        1. نودهای در حال بازیابی (RECOVERING)
        2. نودهای دچار افت یا نوسان (DEGRADED / UNSTABLE)
        3. نودهای سالم (HEALTHY)
        4. نودهای قطع (OFFLINE - هر ۵ دقیقه یکبار)
        """
        try:
            pool = list(registry._l2_pool.values())
            if not pool:
                return

            now = time.time()
            check_offline = (now - self._last_offline_check) >= OFFLINE_RECHECK_INTERVAL
            if check_offline:
                self._last_offline_check = now

            # فیلتر و اولویت‌بندی نودها
            recovering_nodes = [n for n in pool if n.health_state == NodeHealth.RECOVERING]
            unstable_nodes = [n for n in pool if n.health_state in (NodeHealth.DEGRADED, NodeHealth.UNSTABLE, NodeHealth.TIMEOUTING)]
            healthy_nodes = [n for n in pool if n.health_state == NodeHealth.HEALTHY and (now - n.last_tested_at) > 60.0]
            offline_nodes = [n for n in pool if n.health_state == NodeHealth.OFFLINE and check_offline]

            # ترکیب نودها برای تست در این دور
            batch_to_test = (recovering_nodes[:5] + unstable_nodes[:10] + healthy_nodes[:15] + offline_nodes[:5])[:30]
            if not batch_to_test:
                return

            context = NetworkContext(carrier="all")
            await delivery_engine.probe_candidates_parallel(batch_to_test, context, concurrency=10)
            
            # ذخیره نود برتر در L1
            pool_healthy = registry.get_l2_pool(min_score=60.0)
            if pool_healthy:
                registry.put_l1(context, pool_healthy[0])

        except Exception as e:
            logger.error(f"Error in health check cycle: {e}")

    async def perform_30min_auto_refresh(self):
        """
        عملیات جامع و عمیق رفرش خودکار هر ۳۰ دقیقه:
        1. پایش سلامت تمام نودها
        2. شناسایی و حذف کانفیگ‌های نامعتبر
        3. رفرش سابسکریپشن‌ها و منابع آنلاین
        4. اعتبارسنجی اولیه نودهای تازه
        5. اجرای Shadow Test (مقایسه نود جدید با نود قبلی) و ارتقا به کش در صورت برتری
        """
        logger.info("🔄 شروع فرآیند جامع رفرش ۳۰ دقیقه‌ای مخازن و نودهای ابری...")
        try:
            from database import get_active_source_urls, delete_dead_configs
            from harvester import harvest_and_store_online_configs

            # ۱. دریافت تازه از تمام منابع فعال
            sources = await get_active_source_urls()
            report = await harvest_and_store_online_configs(sources=sources, instant_test_count=80)
            logger.info(f"رفرش ۳۰ دقیقه‌ای: {report['new_added']} سرور جدید ثبت و {report['instant_online']} نود آنلاین شد.")

            # ۲. بارگذاری مجدد کاندیداها به استخر L2
            l3_candidates = await delivery_engine._fetch_l3_candidates(limit=30)
            context = NetworkContext(carrier="all")
            probed_candidates = await delivery_engine.probe_candidates_parallel(l3_candidates, context, concurrency=12)

            # ۳. تست سایه (Shadow Test): مقایسه امتیاز نودهای جدید با کش فعلی
            current_l1, _ = registry.get_l1(context)
            current_score = current_l1.score if current_l1 else 0.0

            if probed_candidates:
                top_new = probed_candidates[0]
                if top_new.score >= current_score:
                    registry.put_l1(context, top_new)
                    logger.info(f"✅ نود برتر با امتیاز {top_new.score:.1f} (پینگ: {top_new.ping_ms}ms) به عنوان نود اصلی در کش L1 ارتقا یافت.")
                else:
                    logger.info(f"🛡️ نود فعلی با امتیاز {current_score:.1f} از کاندیدای جدید ({top_new.score:.1f}) برتر بود و حفظ شد.")

            # ۴. پاکسازی نودهای کاملاً مرده از دیتابیس
            await delete_dead_configs()

        except Exception as e:
            logger.error(f"Error in 30min auto refresh: {e}", exc_info=True)

    async def start_monitor_loop(self):
        """حلقه مداوم پایش و زمان‌بندی سلامت"""
        self._is_running = True
        logger.info("🚀 موتور پایش هوشمند سلامت و رفرش ۳۰ دقیقه‌ای نودها (HealthMonitor) فعال شد.")
        
        last_refresh = time.time()
        
        while self._is_running:
            try:
                # ۱. پایش سلامت سبک هر ۳۰ ثانیه
                await self.run_health_check_cycle()
                
                # ۲. بررسی فرارسیدن موعد رفرش ۳۰ دقیقه‌ای
                if (time.time() - last_refresh) >= SOURCE_REFRESH_INTERVAL:
                    await self.perform_30min_auto_refresh()
                    last_refresh = time.time()

                await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            except asyncio.CancelledError:
                logger.info("تسک پایش سلامت متوقف شد.")
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    def stop(self):
        self._is_running = False

# نمونه سراسری موتور پایش سلامت
health_monitor = HealthMonitorEngine()
