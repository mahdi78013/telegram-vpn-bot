import asyncio
import base64
import logging
from typing import List, Dict, Any, Tuple
import httpx

from config import DEFAULT_TAG
from database import (
    add_configs_bulk,
    update_configs_ping_bulk,
    get_setting,
)
from parser import extract_configs_from_text, decode_base64_safe
from tester import ping_configs_batch

logger = logging.getLogger("CloudHarvester")

# لیست منابع سابسکریپشن اختصاصی و انحصاری مهسا نت
DEFAULT_SUBSCRIPTION_SOURCES = [
    {
        "name": "Mahsa Official Meta-Subscription (Hiddify)",
        "url": "https://raw.githubusercontent.com/hiddify/hiddify-app/refs/heads/main/test.configs/mahsa"
    },
    {
        "name": "MahsaNet MTN/MCI Active VLESS Reality",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt"
    },
    {
        "name": "MahsaNet Segment Direct Bypass Nodes",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/segment/test_sub.txt"
    }
]

async def fetch_source_content(url: str, timeout: float = 12.0, max_depth: int = 2) -> str:
    """
    دانلود محتوای لینک سابسکریپشن، پردازش متاساب‌ها (Meta-Sub) و دیکود Base64
    """
    headers = {
        "User-Agent": "Hiddify/2.5.7 (Android; Mobile; fa-IR)"
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return ""
                
            text = resp.text.strip()
            if not text:
                return ""
                
            # در صورتی که محتوا شامل لینک‌های سابسکریپشن دیگر باشد (مانند متاساب مهسا)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            nested_urls = [
                l for l in lines 
                if (l.startswith("http://") or l.startswith("https://")) 
                and not any(p in l for p in ("vless://", "vmess://", "trojan://", "ss://"))
            ]
            
            if nested_urls and max_depth > 0:
                logger.info(f"متا سابسکریپشن شناسایی شد ({url})؛ در حال دریافت {len(nested_urls)} زیرمجموعه...")
                inner_tasks = [fetch_source_content(n_url, timeout=timeout, max_depth=max_depth - 1) for n_url in nested_urls]
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
    except Exception as e:
        logger.error(f"Error fetching source {url}: {e}")
        return ""

async def fetch_all_sources(sources: List[str] = None) -> List[str]:
    """
    دانلود همزمان تمام منابع و استخراج لیست تمامی کانفیگ‌ها
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
    اجرای فرآیند دریافت تمام هزاران سرور و ارسال به صف تست دائمی:
    1. دانلود تمام سرورها از منابع ابری
    2. ذخیره ۱۰۰٪ تمامی سرورهای جدید در دیتابیس با وضعیت صف تست (Queue)
    3. تست فوری یک دسته جهت آنلاین شدن بلافاصله اولین سرورها
    4. تستر پس‌زمینه ۲۴ ساعته بقیه هزاران سرور را به صورت پیوسته تست و تایید می‌کند.
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
        if is_online and ping_ms > 0:
            online_count += 1
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
