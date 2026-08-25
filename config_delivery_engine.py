import asyncio
import logging
import random
import time
from typing import Optional, List, Dict, Any, Tuple

from config import (
    DEFAULT_TAG,
    ENGINE_CONNECT_TIMEOUT,
    ENGINE_READ_TIMEOUT,
    ENGINE_MAX_RETRIES,
    MAX_PARALLEL_PROBES,
    CB_FAILURE_THRESHOLD,
    CB_COOLDOWN_SECONDS,
    SCORE_WEIGHT_LATENCY,
    SCORE_WEIGHT_STABILITY,
    SCORE_WEIGHT_SUCCESS_RATE,
    SCORE_WEIGHT_PACKET_LOSS,
    SCORE_WEIGHT_RESPONSE_TIME,
    SCORE_WEIGHT_HISTORICAL,
    SCORE_WEIGHT_REGIONAL,
)
from node_registry import (
    NodeHealth,
    NetworkContext,
    CandidateNode,
    registry,
)
from tester import ping_single_config, PingResult
from parser import transform_config

logger = logging.getLogger("ConfigDeliveryEngine")

class CircuitBreakerState:
    CLOSED = "CLOSED"         # مدار بسته: فعالیت عادی
    OPEN = "OPEN"             # مدار باز: نود قطع است و در حالت Cooldown
    HALF_OPEN = "HALF_OPEN"   # نیمه‌باز: تست آزمایشی برای بررسی بازگشت نود

class CircuitBreaker:
    """
    مدارشکن هوشمند برای جلوگیری از هدررفت منابع و درخواست‌های مکرر به نودهای خراب
    """
    def __init__(
        self, 
        failure_threshold: int = CB_FAILURE_THRESHOLD, 
        cooldown_seconds: int = CB_COOLDOWN_SECONDS
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._states: Dict[int, str] = {}           # node_id -> state
        self._failure_counts: Dict[int, int] = {}   # node_id -> count
        self._last_state_change: Dict[int, float] = {} # node_id -> timestamp

    def get_state(self, node_id: int) -> str:
        state = self._states.get(node_id, CircuitBreakerState.CLOSED)
        if state == CircuitBreakerState.OPEN:
            last_time = self._last_state_change.get(node_id, 0.0)
            if time.time() - last_time >= self.cooldown_seconds:
                self._states[node_id] = CircuitBreakerState.HALF_OPEN
                self._last_state_change[node_id] = time.time()
                return CircuitBreakerState.HALF_OPEN
        return state

    def is_allowed(self, node_id: int) -> bool:
        state = self.get_state(node_id)
        return state in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)

    def record_success(self, node_id: int):
        self._failure_counts[node_id] = 0
        self._states[node_id] = CircuitBreakerState.CLOSED
        self._last_state_change[node_id] = time.time()

    def record_failure(self, node_id: int):
        current_state = self.get_state(node_id)
        if current_state == CircuitBreakerState.HALF_OPEN:
            # شکست در حالت آزمایشی -> بازگشت فوری به OPEN
            self._states[node_id] = CircuitBreakerState.OPEN
            self._last_state_change[node_id] = time.time()
            return

        cnt = self._failure_counts.get(node_id, 0) + 1
        self._failure_counts[node_id] = cnt
        if cnt >= self.failure_threshold:
            self._states[node_id] = CircuitBreakerState.OPEN
            self._last_state_change[node_id] = time.time()
            logger.info(f"🚫 مدارشکن برای نود {node_id} باز شد (OPEN). نود وارد دوره خنک‌سازی شد.")

class ConfigDeliveryEngine:
    """
    موتور تطبیقی، خودترمیم‌شونده و ضدتایم‌اوت انتخاب و تحویل کانفیگ
    """
    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self.weights = {
            "latency": SCORE_WEIGHT_LATENCY,
            "stability": SCORE_WEIGHT_STABILITY,
            "success_rate": SCORE_WEIGHT_SUCCESS_RATE,
            "packet_loss": SCORE_WEIGHT_PACKET_LOSS,
            "response_time": SCORE_WEIGHT_RESPONSE_TIME,
            "historical": SCORE_WEIGHT_HISTORICAL,
            "regional": SCORE_WEIGHT_REGIONAL,
        }

    def calculate_score(self, node: CandidateNode, context: NetworkContext) -> float:
        """
        الگوریتم امتیازدهی هوشمند و تطبیقی (Adaptive Scoring):
        ترکیب پارامترهای پینگ، TTFB، پایداری، سابقه، نسبت موفقیت و تطابق اپراتور/منطقه
        """
        # ۱. امتیاز تاخیر پینگ (۰ تا ۱۰۰)
        ping_val = max(1, node.ping_ms)
        latency_score = max(0.0, 100.0 * (1.0 - (ping_val / 650.0)))

        # ۲. امتیاز پایداری (بر اساس وضعیت سلامت)
        state_scores = {
            NodeHealth.HEALTHY: 100.0,
            NodeHealth.RECOVERING: 80.0,
            NodeHealth.DEGRADED: 60.0,
            NodeHealth.UNSTABLE: 30.0,
            NodeHealth.TIMEOUTING: 10.0,
            NodeHealth.OFFLINE: 0.0
        }
        stability_score = state_scores.get(node.health_state, 50.0)

        # ۳. امتیاز نسبت موفقیت (۰ تا ۱۰۰)
        success_rate_score = max(0.0, min(100.0, node.success_rate * 100.0))

        # ۴. امتیاز TTFB (Time to First Byte)
        ttfb_val = max(1, node.ttfb_ms if node.ttfb_ms > 0 else node.ping_ms)
        response_time_score = max(0.0, 100.0 * (1.0 - (ttfb_val / 800.0)))

        # ۵. امتیاز عدم از دست رفتن بسته (بر اساس خطاهای متوالی اخیر)
        packet_loss_score = max(0.0, 100.0 - (node.consecutive_failures * 33.3))

        # ۶. امتیاز سابقه تاریخی
        historical_score = min(100.0, 50.0 + (node.total_successes * 2.0))

        # ۷. امتیاز تطابق منطقه‌ای و اپراتور
        regional_score = 50.0
        if context.carrier != "all" and context.carrier in node.carrier_scores:
            regional_score = node.carrier_scores[context.carrier]
        if context.region != "all" and context.region in node.region_scores:
            regional_score = (regional_score + node.region_scores[context.region]) / 2.0

        # محاسبه مجموع وزن‌دار
        total = (
            latency_score * self.weights["latency"] +
            stability_score * self.weights["stability"] +
            success_rate_score * self.weights["success_rate"] +
            packet_loss_score * self.weights["packet_loss"] +
            response_time_score * self.weights["response_time"] +
            historical_score * self.weights["historical"] +
            regional_score * self.weights["regional"]
        )

        # پاداش‌ها و جریمه‌های اختصاصی اپراتورهای ایران
        conf_low = node.raw_config.lower()
        
        # پاداش برای پروتکل فوق‌سریع و سبک Vision بدون سربار
        if "flow=xtls-rprx-vision" in conf_low:
            total += 15.0
            
        # جریمه برای هدرهای سنگین و غیربهینه HTTP که باعث کندی و پینگ کاذب می‌شوند
        if "headertype=http" in conf_low or "type=http" in conf_low:
            total -= 25.0
            
        if context.carrier == "mtn":
            # برای ایرانسل: Reality و Hysteria 2 و MahsaNet MTN بالاترین کیفیت را دارند
            if "mtn" in conf_low or "irancell" in conf_low or "ایرانسل" in conf_low:
                total += 30.0
            if "security=reality" in conf_low or "reality" in conf_low:
                total += 25.0
            if node.protocol in ("hysteria2", "hy2", "tuic"):
                total += 20.0
            if "security=none" in conf_low or (node.protocol == "vmess" and "tls" not in conf_low and "ws" not in conf_low):
                total -= 45.0  # جریمه سنگین برای پروتکل‌های مسدود در ایرانسل
                
        elif context.carrier == "mci":
            # برای همراه اول: Reality و MahsaNet MCI و WS TLS بهترین هستند
            if "mci" in conf_low or "hamrah" in conf_low or "همراه" in conf_low:
                total += 30.0
            if "security=reality" in conf_low or "reality" in conf_low:
                total += 25.0
            if "tls" in conf_low:
                total += 15.0
            if "security=none" in conf_low:
                total -= 45.0

        # جریمه‌های تاخیر و خطاهای متوالی
        penalties = node.consecutive_failures * 15.0
        if node.health_state in (NodeHealth.TIMEOUTING, NodeHealth.UNSTABLE):
            penalties += 20.0

        final_score = max(0.0, min(100.0, total - penalties))
        node.score = final_score
        return final_score

    async def probe_single_node(
        self, 
        node: CandidateNode, 
        context: NetworkContext, 
        timeout: float = ENGINE_CONNECT_TIMEOUT
    ) -> PingResult:
        """سنجش اتصال یک نود منفرد و بروزرسانی بلادرنگ متریک‌ها"""
        if not self.circuit_breaker.is_allowed(node.id):
            return PingResult(is_online=False, ping_ms=-1, error_reason="circuit_breaker_open")

        result = await ping_single_config(
            node.raw_config, 
            timeout=timeout + ENGINE_READ_TIMEOUT,
            connect_timeout=timeout,
            read_timeout=ENGINE_READ_TIMEOUT
        )

        if result.is_online:
            self.circuit_breaker.record_success(node.id)
            registry.update_node_metrics(
                node_id=node.id,
                is_online=True,
                ping_ms=result.ping_ms,
                ttfb_ms=result.ttfb_ms,
                dns_time=result.dns_time_ms,
                tcp_time=result.tcp_time_ms,
                tls_time=result.tls_time_ms,
                context=context
            )
        else:
            self.circuit_breaker.record_failure(node.id)
            registry.update_node_metrics(
                node_id=node.id,
                is_online=False,
                ping_ms=-1,
                context=context
            )

        self.calculate_score(node, context)
        return result

    async def probe_candidates_parallel(
        self,
        candidates: List[CandidateNode],
        context: NetworkContext,
        concurrency: int = MAX_PARALLEL_PROBES,
        fast_path_threshold: float = 65.0
    ) -> List[CandidateNode]:
        """
        کاوش موازی کاندیداها با قابلیت مسیر سریع (Fast Path):
        به محض یافتن اولین کاندیدای با کیفیت عالی، جهت پاسخ‌دهی آنی به کاربر برمی‌گردد
        و بقیه نودها در پس‌زمینه تکمیل می‌شوند.
        """
        if not candidates:
            return []

        semaphore = asyncio.Semaphore(concurrency)
        scored_candidates: List[CandidateNode] = []

        async def worker(node: CandidateNode):
            async with semaphore:
                await self.probe_single_node(node, context)
                scored_candidates.append(node)

        # اجرای اولیه ۵ کاندیدای اول به صورت موازی
        initial_batch = candidates[:concurrency]
        tasks = [asyncio.create_task(worker(n)) for n in initial_batch]

        # منتظر اتمام همه یا تا زمان یافتن اولین نود بسیار باکیفیت
        done, pending = await asyncio.wait(tasks, timeout=ENGINE_CONNECT_TIMEOUT + 0.5)

        # اگر نود عالی پیدا شد
        best_nodes = [n for n in scored_candidates if n.score >= fast_path_threshold and n.health_state == NodeHealth.HEALTHY]
        if best_nodes:
            best_nodes.sort(key=lambda x: x.score, reverse=True)
            # رها کردن بقیه در پس‌زمینه
            return best_nodes

        # اگر هیچ نود با کیفیتی در زمان اولیه به پایان نرسید، منتظر بقیه می‌مانیم
        if pending:
            await asyncio.wait(pending, timeout=ENGINE_READ_TIMEOUT)

        scored_candidates.sort(key=lambda x: x.score, reverse=True)
        return scored_candidates

    async def get_best_config(
        self, 
        context: Optional[NetworkContext] = None, 
        tag: str = DEFAULT_TAG
    ) -> Dict[str, Any]:
        """
        ورودی اصلی دریافت پرسرعت‌ترین و پایدارترین کانفیگ (Single Best Delivery):
        1. پاسخ زیر ۱ میلی‌ثانیه از کش L1
        2. اعمال الگوی Stale-While-Revalidate
        3. کاوش موازی هوشمند کاندیداها
        4. بازیابی سلسله‌مراتبی و در نهایت Last Known Good
        """
        ctx = context or NetworkContext()
        
        # ۱. بررسی کش L1 (حافظه رم)
        cached_node, is_fresh = registry.get_l1(ctx)
        if cached_node:
            if is_fresh:
                # تازه و معتبر: پاسخ فوری
                transformed, flag, proto = transform_config(cached_node.raw_config, tag=tag)
                return {
                    "direct": transformed,
                    "ping": cached_node.ping_ms,
                    "flag": flag,
                    "proto": proto,
                    "tag": tag,
                    "node_id": cached_node.id,
                    "score": cached_node.score,
                    "cache_level": "L1-Fresh",
                    "health_state": cached_node.health_state.value
                }
            else:
                # Stale: پاسخ سریع با داده قبلی + رفرش غیرمسدودکننده در پس‌زمینه
                asyncio.create_task(self._background_revalidate(ctx, tag))
                transformed, flag, proto = transform_config(cached_node.raw_config, tag=tag)
                return {
                    "direct": transformed,
                    "ping": cached_node.ping_ms,
                    "flag": flag,
                    "proto": proto,
                    "tag": tag,
                    "node_id": cached_node.id,
                    "score": cached_node.score,
                    "cache_level": "L1-Stale-Revalidating",
                    "health_state": cached_node.health_state.value
                }

        # ۲. بررسی استخر L2 (حافظه برنامه)
        l2_candidates = registry.get_l2_pool(min_score=35.0, carrier=ctx.carrier)
        if l2_candidates:
            best_probed = await self.probe_candidates_parallel(l2_candidates[:8], ctx)
            if best_probed and best_probed[0].score > 30.0:
                champion = best_probed[0]
                registry.put_l1(ctx, champion)
                transformed, flag, proto = transform_config(champion.raw_config, tag=tag)
                return {
                    "direct": transformed,
                    "ping": champion.ping_ms,
                    "flag": flag,
                    "proto": proto,
                    "tag": tag,
                    "node_id": champion.id,
                    "score": champion.score,
                    "cache_level": "L2-Pool",
                    "health_state": champion.health_state.value
                }

        # ۳. دریافت از مخزن دیتابیس (L3) با فیلتر هوشمند اپراتور
        l3_candidates = await self._fetch_l3_candidates(context=ctx)
        if l3_candidates:
            best_l3 = await self.probe_candidates_parallel(l3_candidates[:10], ctx)
            if best_l3 and best_l3[0].score > 25.0:
                champion = best_l3[0]
                registry.put_l1(ctx, champion)
                transformed, flag, proto = transform_config(champion.raw_config, tag=tag)
                return {
                    "direct": transformed,
                    "ping": champion.ping_ms,
                    "flag": flag,
                    "proto": proto,
                    "tag": tag,
                    "node_id": champion.id,
                    "score": champion.score,
                    "cache_level": "L3-Database",
                    "health_state": champion.health_state.value
                }

        # ۴. تلاش برای دریافت زنده از مخازن آنلاین معتبر متناسب با اپراتور
        live_candidates = await self._fetch_live_candidates(context=ctx)
        if live_candidates:
            best_live = await self.probe_candidates_parallel(live_candidates[:10], ctx)
            if best_live and best_live[0].score > 20.0:
                champion = best_live[0]
                registry.put_l1(ctx, champion)
                transformed, flag, proto = transform_config(champion.raw_config, tag=tag)
                return {
                    "direct": transformed,
                    "ping": champion.ping_ms,
                    "flag": flag,
                    "proto": proto,
                    "tag": tag,
                    "node_id": champion.id,
                    "score": champion.score,
                    "cache_level": "Live-Source",
                    "health_state": champion.health_state.value
                }

        # ۵. اورژانسی: تحویل L4 Last Known Good Config (تضمین ۱۰۰٪ عدم بازگشت Timeout)
        l4_node = registry.get_last_known_good()
        transformed, flag, proto = transform_config(l4_node.raw_config, tag=tag)
        logger.warning("استفاده از L4 Last Known Good Config جهت جلوگیری از تایم‌اوت درخواست کاربر.")
        return {
            "direct": transformed,
            "ping": l4_node.ping_ms,
            "flag": flag,
            "proto": proto,
            "tag": tag,
            "node_id": l4_node.id,
            "score": l4_node.score,
            "cache_level": "L4-LastKnownGood",
            "health_state": l4_node.health_state.value
        }

    async def get_best_configs(
        self, 
        count: int = 3, 
        context: Optional[NetworkContext] = None, 
        tag: str = DEFAULT_TAG
    ) -> List[Dict[str, Any]]:
        """
        دریافت چند کانفیگ برتر با تنوع پروتکل و تفکیک اپراتورها جهت ارسال گروهی به کانال
        """
        ctx = context or NetworkContext()
        # ۱. ابتدا استخر نودهای سالم را دریافت می‌کنیم
        pool = registry.get_l2_pool(min_score=30.0, carrier=ctx.carrier)
        if len(pool) < count:
            # دریافت از دیتابیس
            db_candidates = await self._fetch_l3_candidates(context=ctx, limit=count * 4)
            if db_candidates:
                probed = await self.probe_candidates_parallel(db_candidates, ctx)
                for p in probed:
                    if p.score > 25.0:
                        registry.put_l1(ctx, p)
            pool = registry.get_l2_pool(min_score=25.0, carrier=ctx.carrier)

        results = []
        seen_protos = set()
        
        # مرتب‌سازی بر اساس امتیاز
        pool.sort(key=lambda x: x.score, reverse=True)
        
        for node in pool:
            if len(results) >= count:
                break
            transformed, flag, proto = transform_config(node.raw_config, tag=tag)
            results.append({
                "id": node.id,
                "config": transformed,
                "raw_config": node.raw_config,
                "flag": flag,
                "proto": proto,
                "ping": node.ping_ms,
                "score": node.score
            })

        # در صورت کمبود کاندیدا، از کانفیگ‌های موجود یا L4 استفاده می‌کنیم
        if len(results) < count:
            single = await self.get_best_config(ctx, tag=tag)
            results.append({
                "id": single.get("node_id", 0),
                "config": single["direct"],
                "raw_config": single["direct"],
                "flag": single["flag"],
                "proto": single["proto"],
                "ping": single["ping"],
                "score": single.get("score", 70.0)
            })

        return results

    async def _background_revalidate(self, context: NetworkContext, tag: str):
        """تسک پس‌زمینه برای رفرش کش بر اساس الگوی Stale-While-Revalidate"""
        try:
            candidates = registry.get_l2_pool(min_score=20.0, carrier=context.carrier)
            if not candidates:
                candidates = await self._fetch_l3_candidates(context=context, limit=5)
            if candidates:
                best = await self.probe_candidates_parallel(candidates[:5], context)
                if best and best[0].score > 30.0:
                    registry.put_l1(context, best[0])
                    logger.debug(f"پس‌زمینه: کش L1 با موفقیت بروزرسانی شد (نود {best[0].id}, امتیاز: {best[0].score:.1f}).")
        except Exception as e:
            logger.debug(f"Background revalidation exception: {e}")

    async def _fetch_l3_candidates(self, context: Optional[NetworkContext] = None, limit: int = 20) -> List[CandidateNode]:
        """استخراج هوشمند نودها از پایگاه داده با اولویت‌بندی اپراتورها و فیلترهای ضد فیلترینگ"""
        try:
            from database import DB_PATH
            import aiosqlite
            ctx = context or NetworkContext()
            
            carrier_where = ""
            if ctx.carrier == "mtn":
                carrier_where = """
                AND (
                    raw_config LIKE '%security=reality%'
                    OR raw_config LIKE '%hysteria2://%'
                    OR raw_config LIKE '%hy2://%'
                    OR raw_config LIKE '%tuic://%'
                    OR raw_config LIKE '%mtn%'
                    OR raw_config LIKE '%irancell%'
                    OR raw_config LIKE '%ایرانسل%'
                    OR (protocol = 'vless' AND raw_config LIKE '%security=tls%')
                )
                """
            elif ctx.carrier == "mci":
                carrier_where = """
                AND (
                    raw_config LIKE '%security=reality%'
                    OR raw_config LIKE '%hysteria2://%'
                    OR raw_config LIKE '%hy2://%'
                    OR raw_config LIKE '%mci%'
                    OR raw_config LIKE '%hamrah%'
                    OR raw_config LIKE '%همراه%'
                    OR (protocol = 'vless' AND raw_config LIKE '%security=tls%')
                    OR (protocol = 'vmess' AND raw_config LIKE '%tls%')
                )
                """
                
            query = f"""
                SELECT id, raw_config, protocol, ping_ms, last_ping_status
                FROM configs
                WHERE is_active = 1 AND (last_ping_status != 0 OR last_ping_status IS NULL)
                {carrier_where}
                ORDER BY 
                    CASE 
                        WHEN '{ctx.carrier}' = 'mtn' AND (raw_config LIKE '%mtn%' OR raw_config LIKE '%irancell%' OR raw_config LIKE '%ایرانسل%') THEN 0
                        WHEN '{ctx.carrier}' = 'mci' AND (raw_config LIKE '%mci%' OR raw_config LIKE '%hamrah%' OR raw_config LIKE '%همراه%') THEN 0
                        WHEN raw_config LIKE '%security=reality%' THEN 1
                        WHEN raw_config LIKE '%hy2://%' OR raw_config LIKE '%hysteria2://%' THEN 2
                        WHEN last_ping_status = 1 THEN 3
                        ELSE 4
                    END,
                    ping_ms ASC
                LIMIT ?
            """
            
            async with aiosqlite.connect(DB_PATH, timeout=4.0) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(query, (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    candidates = []
                    for r in rows:
                        c = CandidateNode(
                            id=r["id"],
                            raw_config=r["raw_config"],
                            protocol=r["protocol"],
                            ping_ms=r["ping_ms"] if r["ping_ms"] > 0 else 180,
                            health_state=NodeHealth.HEALTHY if r["last_ping_status"] == 1 else NodeHealth.UNSTABLE
                        )
                        candidates.append(c)
                    return candidates
        except Exception as e:
            logger.debug(f"Error fetching L3 candidates: {e}")
            return []

    async def _fetch_live_candidates(self, context: Optional[NetworkContext] = None, limit: int = 20) -> List[CandidateNode]:
        """دریافت زنده از منابع سابسکریپشن اختصاصی متناسب با اپراتور"""
        try:
            from harvester import fetch_source_content, DEFAULT_SUBSCRIPTION_SOURCES
            from parser import extract_configs_from_text
            ctx = context or NetworkContext()
            
            # انتخاب سورس بر اساس اپراتور
            if ctx.carrier == "mtn":
                urls = [
                    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt",
                    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt",
                    "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt"
                ]
            elif ctx.carrier == "mci":
                urls = [
                    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mci/sub_1.txt",
                    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt",
                    "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt"
                ]
            else:
                urls = [
                    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt",
                    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mci/sub_1.txt",
                    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt"
                ]
            
            for u in urls:
                content = await fetch_source_content(u, timeout=ENGINE_CONNECT_TIMEOUT + ENGINE_READ_TIMEOUT)
                if content:
                    configs = extract_configs_from_text(content)
                    if configs:
                        candidates = []
                        for idx, conf in enumerate(configs[:limit]):
                            proto = conf.split("://", 1)[0] if "://" in conf else "custom"
                            node = CandidateNode(
                                id=100000 + idx + int(time.time() % 10000),
                                raw_config=conf,
                                protocol=proto,
                                ping_ms=120,
                                ttfb_ms=120,
                                health_state=NodeHealth.HEALTHY
                            )
                            registry._l2_pool[node.id] = node
                            candidates.append(node)
                        return candidates
        except Exception as e:
            logger.debug(f"Error fetching live candidates: {e}")
        return []

# نمونه سراسری و فعال موتور تحویل کانفیگ
delivery_engine = ConfigDeliveryEngine()
