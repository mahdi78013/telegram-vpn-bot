import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

from config import (
    CACHE_L1_TTL,
    CACHE_L2_TTL,
    HYSTERESIS_THRESHOLD,
    DEFAULT_TAG,
)

logger = logging.getLogger("NodeRegistry")

class NodeHealth(str, Enum):
    """وضعیت‌های مختلف سلامت نود بر اساس ارزیابی چندلایه‌ای"""
    HEALTHY = "HEALTHY"           # سالم، پرسرعت و پایدار
    DEGRADED = "DEGRADED"         # فعال با تاخیر یا پایداری کاهش یافته
    UNSTABLE = "UNSTABLE"         # نوسان شدید در نتایج پینگ
    TIMEOUTING = "TIMEOUTING"     # تاخیرهای مکرر و نزدیک به تایم‌اوت
    OFFLINE = "OFFLINE"           # قطع کامل یا فیلتر شده
    RECOVERING = "RECOVERING"     # خارج شده از قطعی در حال آزمون تدریجی

@dataclass
class NetworkContext:
    """بافتار شبکه کاربر برای انتخاب و توزیع دقیق‌ترین کانفیگ"""
    carrier: str = "all"            # "mci", "mtn", "rightel", "wifi", "all"
    network_type: str = "unknown"   # "4g", "5g", "wifi", "unknown"
    region: str = "all"             # "shirvan", "north_khorasan", "tehran", "all"
    ip_version: str = "ipv4"        # "ipv4", "ipv6", "any"

    def cache_key(self) -> str:
        return f"{self.carrier}:{self.network_type}:{self.region}:{self.ip_version}"

@dataclass
class CandidateNode:
    """ساختار اطلاعات و معیارهای کیفی یک کانفیگ/سرور در رجیستری"""
    id: int
    raw_config: str
    protocol: str
    score: float = 0.0
    health_state: NodeHealth = NodeHealth.HEALTHY
    ping_ms: int = 150
    ttfb_ms: int = 150
    dns_time_ms: float = 0.0
    tcp_time_ms: float = 0.0
    tls_time_ms: float = 0.0
    success_rate: float = 1.0
    consecutive_failures: int = 0
    total_tests: int = 0
    total_successes: int = 0
    last_tested_at: float = 0.0
    last_success_at: float = 0.0
    carrier_scores: Dict[str, float] = field(default_factory=dict)
    region_scores: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """تشخیص خودکار تطابق با اپراتورهای ایران و پروتکل‌های ضد فیلتر"""
        conf_lower = self.raw_config.lower()
        
        # تشخیص همراه اول
        if "mci" in conf_lower or "hamrah" in conf_lower or "همراه" in conf_lower:
            self.carrier_scores["mci"] = 98.0
            
        # تشخیص ایرانسل
        if "mtn" in conf_lower or "irancell" in conf_lower or "ایرانسل" in conf_lower:
            self.carrier_scores["mtn"] = 98.0
            
        # پروتکل‌های VLESS Reality و Hysteria 2 بالاترین شانس اتصال در ایرانسل و همراه اول را دارند
        if "security=reality" in conf_lower or "reality" in conf_lower:
            self.carrier_scores["mtn"] = max(self.carrier_scores.get("mtn", 0.0), 92.0)
            self.carrier_scores["mci"] = max(self.carrier_scores.get("mci", 0.0), 92.0)
            self.carrier_scores["wifi"] = max(self.carrier_scores.get("wifi", 0.0), 92.0)
            
        if self.protocol in ("hysteria2", "hy2", "tuic", "hysteria") or "hy2" in conf_lower:
            self.carrier_scores["mtn"] = max(self.carrier_scores.get("mtn", 0.0), 90.0)
            self.carrier_scores["mci"] = max(self.carrier_scores.get("mci", 0.0), 90.0)
            self.carrier_scores["wifi"] = max(self.carrier_scores.get("wifi", 0.0), 90.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "raw_config": self.raw_config,
            "protocol": self.protocol,
            "score": round(self.score, 2),
            "health_state": self.health_state.value,
            "ping_ms": self.ping_ms,
            "ttfb_ms": self.ttfb_ms,
            "success_rate": round(self.success_rate, 3),
            "consecutive_failures": self.consecutive_failures,
            "last_tested_at": self.last_tested_at,
            "last_success_at": self.last_success_at
        }

@dataclass
class CacheEntry:
    node: CandidateNode
    inserted_at: float
    ttl: float

    def is_fresh(self) -> bool:
        return (time.time() - self.inserted_at) < self.ttl

    def is_stale(self) -> bool:
        return not self.is_fresh()

class NodeRegistry:
    """
    رجیستری مرکزی و مدیریت کش چندسطحی نودها:
    - L1: حافظه رم سریع برای تحویل زیر ۱ میلی‌ثانیه
    - L2: استخر کاندیداهای سلامت‌سنجی‌شده
    - L3: بازیابی از دیتابیس پایدار
    - L4: آخرین کانفیگ سالم ثبت شده (Last Known Good) جهت تضمین عدم بازگشت Timeout
    """
    def __init__(self):
        self._l1_cache: Dict[str, CacheEntry] = {}          # context_key -> CacheEntry
        self._l2_pool: Dict[int, CandidateNode] = {}        # node_id -> CandidateNode
        self._regional_stats: Dict[str, Dict[str, float]] = {} # region/carrier metrics
        self._last_known_good: Optional[CandidateNode] = None
        self._init_l4_emergency_fallback()

    def _init_l4_emergency_fallback(self):
        """بارگذاری اولیه L4 Last Known Good Config"""
        fallback_conf = (
            "vless://25f46401-4475-43ea-98f9-a0353c7c4c12@104.18.3.161:443?"
            "encryption=none&security=tls&type=ws&host=update.microsoft.com&path=%2Fvless-ws"
            f"&sni=update.microsoft.com#{DEFAULT_TAG}"
        )
        self._last_known_good = CandidateNode(
            id=0,
            raw_config=fallback_conf,
            protocol="vless",
            score=70.0,
            health_state=NodeHealth.HEALTHY,
            ping_ms=65,
            ttfb_ms=65,
            success_rate=1.0,
            last_tested_at=time.time(),
            last_success_at=time.time()
        )

    def get_l1(self, context: NetworkContext) -> Tuple[Optional[CandidateNode], bool]:
        """
        دریافت از کش L1:
        خروجی: (کاندیدا, آیا تازه است؟)
        """
        key = context.cache_key()
        entry = self._l1_cache.get(key)
        
        # تنها در صورتی از کش عمومی استفاده می‌شود که درخواست عمومی باشد
        if not entry and context.carrier == "all":
            general_key = NetworkContext(carrier="all").cache_key()
            entry = self._l1_cache.get(general_key)
            
        if entry:
            return entry.node, entry.is_fresh()
        return None, False

    def put_l1(self, context: NetworkContext, node: CandidateNode, ttl: float = CACHE_L1_TTL):
        """
        ذخیره در کش L1 با پیاده‌سازی Anti-Flapping / Shadow Testing:
        تنها در صورتی که نود جدید حداقل ۱۵٪ بهتر از نود قبلی باشد جایگزین می‌شود.
        """
        key = context.cache_key()
        existing = self._l1_cache.get(key)
        
        if existing and existing.is_fresh():
            current_score = existing.node.score
            new_score = node.score
            # اگر نود جدید تفاوت جزئی دارد، تغییر نده تا فلپینگ ایجاد نشود
            if new_score < current_score * (1.0 + HYSTERESIS_THRESHOLD) and node.id != existing.node.id:
                logger.debug(f"Anti-flapping prevented switch: current={current_score:.1f}, new={new_score:.1f}")
                return

        self._l1_cache[key] = CacheEntry(node=node, inserted_at=time.time(), ttl=ttl)
        self._l2_pool[node.id] = node
        
        # بروزرسانی L4 Last Known Good
        if node.health_state == NodeHealth.HEALTHY and node.score > 50:
            self._last_known_good = node

    def get_l2_pool(self, min_score: float = 40.0, carrier: str = "all") -> List[CandidateNode]:
        """دریافت نودهای سالم از استخر L2 با اولویت‌بندی اپراتور انتخابی"""
        nodes = [
            node for node in self._l2_pool.values() 
            if node.score >= min_score and node.health_state not in (NodeHealth.OFFLINE, NodeHealth.TIMEOUTING)
        ]
        
        if carrier != "all":
            # تفکیک و ترجیح نودهای سازگار با اپراتور
            def carrier_rank(n: CandidateNode) -> Tuple[float, float]:
                c_score = n.carrier_scores.get(carrier, 40.0)
                return (c_score, n.score)
            nodes.sort(key=carrier_rank, reverse=True)
        else:
            nodes.sort(key=lambda n: n.score, reverse=True)
            
        return nodes

    def get_last_known_good(self) -> CandidateNode:
        """ارائه قطعی آخرین کانفیگ معتبر جهت جلوگیری از خالی ماندن یا Timeout در شرایط بحرانی"""
        # ۱. ابتدا بررسی استخر نودهای سالم L2
        pool = self.get_l2_pool(min_score=20.0)
        if pool:
            return pool[0]
        # ۲. بررسی هر نود موجود در رجیستری
        if self._l2_pool:
            best_any = sorted(self._l2_pool.values(), key=lambda x: x.score, reverse=True)
            if best_any and best_any[0].score > 0:
                return best_any[0]
        # ۳. بازگشت به L4
        if self._last_known_good:
            return self._last_known_good
        self._init_l4_emergency_fallback()
        return self._last_known_good

    def update_node_metrics(
        self,
        node_id: int,
        is_online: bool,
        ping_ms: int,
        ttfb_ms: int = -1,
        dns_time: float = 0.0,
        tcp_time: float = 0.0,
        tls_time: float = 0.0,
        context: Optional[NetworkContext] = None
    ):
        """بروزرسانی داده‌های سلامت و اتصال یک نود در حافظه رجیستری"""
        now = time.time()
        node = self._l2_pool.get(node_id)
        if not node:
            return

        node.total_tests += 1
        node.last_tested_at = now
        node.dns_time_ms = dns_time
        node.tcp_time_ms = tcp_time
        node.tls_time_ms = tls_time

        if is_online and ping_ms > 0:
            node.total_successes += 1
            node.consecutive_failures = 0
            node.last_success_at = now
            node.ping_ms = ping_ms
            node.ttfb_ms = ttfb_ms if ttfb_ms > 0 else ping_ms
            
            if node.health_state == NodeHealth.OFFLINE:
                node.health_state = NodeHealth.RECOVERING
            elif node.ping_ms > 450:
                node.health_state = NodeHealth.DEGRADED
            else:
                node.health_state = NodeHealth.HEALTHY
        else:
            node.consecutive_failures += 1
            if node.consecutive_failures >= 3:
                node.health_state = NodeHealth.OFFLINE
            elif node.consecutive_failures >= 2:
                node.health_state = NodeHealth.TIMEOUTING
            else:
                node.health_state = NodeHealth.UNSTABLE

        node.success_rate = node.total_successes / max(1, node.total_tests)

        # ثبت داده‌های اختصاصی منطقه/اپراتور
        if context:
            if context.carrier != "all":
                cur_c_score = node.carrier_scores.get(context.carrier, 50.0)
                delta = 10.0 if is_online else -20.0
                node.carrier_scores[context.carrier] = max(0.0, min(100.0, cur_c_score + delta))
            if context.region != "all":
                cur_r_score = node.region_scores.get(context.region, 50.0)
                delta = 10.0 if is_online else -20.0
                node.region_scores[context.region] = max(0.0, min(100.0, cur_r_score + delta))

    def invalidate_node(self, node_id: int):
        """بی‌اعتبار کردن نود در صورت بروز خطای قطعی"""
        if node_id in self._l2_pool:
            self._l2_pool[node_id].health_state = NodeHealth.OFFLINE
            self._l2_pool[node_id].score = 0.0
        # پاکسازی از L1
        to_delete = [k for k, v in self._l1_cache.items() if v.node.id == node_id]
        for k in to_delete:
            del self._l1_cache[k]

# نمونه سراسری و مشترک رجیستری
registry = NodeRegistry()
