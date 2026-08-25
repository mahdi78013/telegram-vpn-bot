import asyncio
import base64
import logging
import random
import time
from typing import List, Dict, Any, Tuple, Optional
import httpx

from config import (
    DEFAULT_TAG,
    ENGINE_CONNECT_TIMEOUT,
    ENGINE_READ_TIMEOUT,
    ENGINE_MAX_RETRIES,
)
from database import (
    add_configs_bulk,
    update_configs_ping_bulk,
    get_setting,
)
from parser import extract_configs_from_text, decode_base64_safe
from tester import ping_configs_batch
from node_registry import CandidateNode, NodeHealth, registry

logger = logging.getLogger("CloudHarvester")

DEFAULT_SUBSCRIPTION_SOURCES = [
    {
        "name": "MahsaNet MTN/MCI Active VLESS Reality",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt"
    },
    {
        "name": "MahsaNet App Sub",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/app/sub.txt"
    },
    {
        "name": "MahsaNet Segment Active",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/segment/test_sub.txt"
    },
    {
        "name": "ALIILAPRO Live VLESS Reality Stream",
        "url": "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt"
    },
    {
        "name": "Yebekhe TVC Multi-Protocol",
        "url": "https://raw.githubusercontent.com/yebekhe/TVC/main/subscriptions/xray/base64/mix"
    },
    {
        "name": "Yebekhe Reality Normal",
        "url": "https://raw.githubusercontent.com/yebekhe/TelegramV2rayCollector/main/sub/normal/reality"
    },
    {
        "name": "Soroush Mirzaei Reality Stream",
        "url": "https://raw.githubusercontent.com/soroushmirzaei/telegram-v2ray-configs/main/sub/reality"
    },
    {
        "name": "Barry-Far V2Ray Sub 1",
        "url": "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub1.txt"
    },
    {
        "name": "Epodonios All Configs",
        "url": "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt"
    },
    {
        "name": "MFUU Verified Fast Nodes",
        "url": "https://raw.githubusercontent.com/mfuu/v2ray/master/v2ray"
    }
]

# کلاینت سراسری با Connection Pooling و Keep-Alive جهت کاهش هزینه Handshake
_SHARED_CLIENT: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None or _SHARED_CLIENT.is_closed:
        timeout_config = httpx.Timeout(
            connect=ENGINE_CONNECT_TIMEOUT,
            read=ENGINE_READ_TIMEOUT,
            write=3.0,
            pool=5.0
        )
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
        _SHARED_CLIENT = httpx.AsyncClient(
            timeout=timeout_config,
            limits=limits,
            follow_redirects=True,
            verify=False
        )
    return _SHARED_CLIENT

async def fetch_source_content(
    url: str, 
    timeout: float = 5.0, 
    max_depth: int = 2,
    max_retries: int = ENGINE_MAX_RETRIES
) -> str:
    """
    دانلود محتوای سابسکریپشن با استراتژی ضدتایم‌اوت:
    - تفکیک Connect Timeout و Read Timeout
    - تلاش مجدد با Exponential Backoff و Random Jitter
    - پردازش متاساب‌ها و دیکود امن Base64
    """
    headers = {
        "User-Agent": "Hiddify/2.5.7 (Android; Mobile; fa-IR)"
    }
    client = get_http_client()
    
    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.debug(f"Attempt {attempt}: HTTP {resp.status_code} for {url}")
                if attempt < max_retries:
                    backoff = (0.4 * (2 ** (attempt - 1))) + random.uniform(0.1, 0.3)
                    await asyncio.sleep(backoff)
                    continue
                return ""
                
            text = resp.text.strip()
            if not text:
                return ""
                
            # پردازش لینک‌های متاسابسکریپشن
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            nested_urls = [
                l for l in lines 
                if (l.startswith("http://") or l.startswith("https://")) 
                and not any(p in l for p in ("vless://", "vmess://", "trojan://", "ss://"))
            ]
            
            if nested_urls and max_depth > 0:
                logger.info(f"متا سابسکریپشن شناسایی شد ({url})؛ دریافت همزمان {len(nested_urls)} زیرمجموعه...")
                inner_tasks = [
                    fetch_source_content(n_url, timeout=timeout, max_depth=max_depth - 1, max_retries=1) 
                    for n_url in nested_urls[:6]  # کنترل نرخ و همزمانی
                ]
                inner_results = await asyncio.gather(*inner_tasks, return_exceptions=True)
                valid_parts = [r for r in inner_results if isinstance(r, str) and r]
                return "\n".join(valid_parts)
                
            # بررسی رشته Base64 استاندارد
            if not any(proto in text for proto in ("vless://", "vmess://", "trojan://", "ss://")):
                try:
                    decoded = decode_base64_safe(text)
                    if any(proto in decoded for proto in ("vless://", "vmess://", "trojan://", "ss://")):
                        return decoded
                except Exception:
                    pass
                    
            return text
            
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, asyncio.TimeoutError) as e:
            logger.debug(f"Attempt {attempt} failed for {url}: {e}")
            if attempt < max_retries:
                # محاسبه تاخیر با Exponential Backoff و Jitter
                backoff = (0.5 * (2 ** (attempt - 1))) + random.uniform(0.1, 0.4)
                await asyncio.sleep(backoff)
            else:
                logger.warning(f"All {max_retries} attempts failed for source {url}")
                return ""
        except Exception as e:
            logger.error(f"Unexpected error fetching source {url}: {e}")
            return ""
            
    return ""

async def fetch_all_sources(sources: List[str] = None) -> List[str]:
    """
    دانلود همزمان تمام منابع و استخراج لیست تمامی کانفیگ‌ها با کنترل همزمانی
    """
    if not sources:
        sources = [s["url"] for s in DEFAULT_SUBSCRIPTION_SOURCES]
        
    tasks = [fetch_source_content(url) for url in sources]
    contents = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_raw_configs = []
    seen = set()
    
    for c in contents:
        if isinstance(c, str) and c:
            configs = extract_configs_from_text(c)
            for conf in configs:
                if conf not in seen:
                    seen.add(conf)
                    all_raw_configs.append(conf)
                    
    return all_raw_configs

async def harvest_and_store_online_configs(
    sources: List[str] = None,
    instant_test_count: int = 60,
    test_timeout: float = 2.0
) -> Dict[str, Any]:
    """
    اجرای فرآیند دریافت خودکار سرورها و تزریق فوری به کش و رجیستری:
    1. دانلود همزمان سرورها از منابع ابری
    2. ذخیره ۱۰۰٪ تمامی سرورهای جدید در دیتابیس (L3)
    3. تست فوری دسته اولیه و تزریق نودهای پرسرعت به استخر L2 و کش L1
    """
    logger.info("شروع عملیات دریافت خودکار سرورها از منابع ابری...")
    
    # 1. دانلود تمام کانفیگ‌ها
    fetched_configs = await fetch_all_sources(sources)
    total_fetched = len(fetched_configs)
    logger.info(f"تعداد {total_fetched} کانفیگ از منابع آنلاین دریافت شد.")
    
    if not fetched_configs:
        return {
            "total_fetched": 0,
            "new_added": 0,
            "duplicates": 0,
            "instant_tested": 0,
            "instant_online": 0,
            "untested_queued": 0
        }
        
    # 2. اضافه کردن تمام کانفیگ‌های جدید به دیتابیس
    added, dupes = await add_configs_bulk(fetched_configs)
    logger.info(f"ثبت در دیتابیس انجام شد: {added} سرور جدید اضافه شد | {dupes} تکراری رد شد.")
    
    # 3. تست فوری یک دسته برای تامین سرورهای اولیه
    candidates = fetched_configs[:instant_test_count]
    temp_items = [{"id": idx, "raw_config": conf} for idx, conf in enumerate(candidates)]
    
    ping_results = await ping_configs_batch(temp_items, concurrency=25, timeout=test_timeout)
    
    online_count = 0
    offline_count = 0
    for idx, is_online, ping_ms in ping_results:
        conf_str = candidates[idx]
        proto = conf_str.split("://", 1)[0] if "://" in conf_str else "custom"
        
        if is_online and ping_ms > 0:
            online_count += 1
            # ثبت در استخر L2 رجیستری
            node = CandidateNode(
                id=idx + 1000,
                raw_config=conf_str,
                protocol=proto,
                score=max(30.0, 100.0 - (ping_ms / 6.5)),
                health_state=NodeHealth.HEALTHY,
                ping_ms=ping_ms,
                ttfb_ms=ping_ms,
                last_tested_at=time.time(),
                last_success_at=time.time()
            )
            registry._l2_pool[node.id] = node
        else:
            offline_count += 1
            
    return {
        "total_fetched": total_fetched,
        "new_added": added,
        "duplicates": dupes,
        "instant_tested": len(candidates),
        "instant_online": online_count,
        "untested_queued": max(0, added - online_count)
    }
