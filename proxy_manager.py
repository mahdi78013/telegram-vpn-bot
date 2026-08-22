import asyncio
import logging
import re
import urllib.parse
import time
from typing import List, Tuple
import httpx

logger = logging.getLogger("ProxyManager")

DEFAULT_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/SoliSpirit/mtproto/master/all_proxies.txt",
    "https://raw.githubusercontent.com/Grim1313/mtproto-for-telegram/master/all_proxies.txt",
    "https://raw.githubusercontent.com/ALIILAPRO/MTProtoProxy/main/mtproto.txt",
]

FALLBACK_PROXIES = [
    "https://t.me/proxy?server=new.lambforkebeb.co.uk&port=2096&secret=eeNEgYdJvXrFGRMCIMJdCQ",
    "https://t.me/proxy?server=rain.lavazemi2.co.uk&port=2053&secret=eeNEgYdJvXrFGRMCIMJdCQ",
    "https://t.me/proxy?server=star.talebi.co.uk&port=2096&secret=eeNEgYdJvXrFGRMCIMJdCQ",
]

_cached_proxies: List[str] = list(FALLBACK_PROXIES)
_last_fetch_time: float = 0.0

async def ping_proxy(proxy_url: str, timeout: float = 2.0) -> Tuple[bool, int, str]:
    """تست پینگ اتصال TCP برای سرور پروکسی MTProto"""
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        params = urllib.parse.parse_qs(parsed.query)
        server = params.get("server", [""])[0]
        port = int(params.get("port", ["443"])[0])
        if not server:
            return False, -1, proxy_url
            
        t0 = time.perf_counter()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server, port),
            timeout=timeout
        )
        t1 = time.perf_counter()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        ping_ms = int((t1 - t0) * 1000)
        return True, ping_ms, proxy_url
    except Exception:
        return False, -1, proxy_url

async def fetch_and_test_live_proxies(limit: int = 40) -> List[str]:
    """دریافت و تست زنده پروکسی‌ها و انتخاب سریع‌ترین‌ها"""
    global _cached_proxies, _last_fetch_time
    all_raw = []
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in DEFAULT_PROXY_SOURCES:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    matches = re.findall(
                        r"(?:tg://proxy\?[^\s<>\"']+|https://t\.me/proxy\?[^\s<>\"']+)",
                        r.text,
                        re.IGNORECASE
                    )
                    all_raw.extend(matches)
            except Exception as e:
                logger.warning(f"خطا در دریافت پروکسی از {url}: {e}")
                
    if not all_raw:
        return _cached_proxies
        
    # حذف تکراری‌ها و استانداردسازی به https://t.me/proxy?...
    unique_proxies = []
    seen = set()
    for p in all_raw:
        norm = p.strip().replace("tg://proxy?", "https://t.me/proxy?")
        if norm not in seen:
            seen.add(norm)
            unique_proxies.append(norm)
            
    # اجرای تست پینگ موازی
    tasks = [ping_proxy(p) for p in unique_proxies[:limit]]
    results = await asyncio.gather(*tasks)
    
    online = [r for r in results if r[0]]
    online.sort(key=lambda x: x[1])
    
    if online:
        best_proxies = [r[2] for r in online[:5]]
        _cached_proxies = best_proxies
        _last_fetch_time = time.time()
        logger.info(f"تعداد {len(best_proxies)} پروکسی پرسرعت تلگرام به‌روزرسانی و ذخیره شد.")
        return _cached_proxies
        
    return _cached_proxies

def get_current_top_proxies(count: int = 3) -> List[str]:
    """دریافت ۳ پروکسی برتر به صورت فوری از حافظه کش"""
    global _cached_proxies
    if len(_cached_proxies) < count:
        return FALLBACK_PROXIES[:count]
    return _cached_proxies[:count]

def format_proxies_text(proxies: List[str]) -> str:
    """قالب‌بندی خط پروکسی‌ها بر اساس درخواست کاربر: « پروکسی » « پروکسی » « پروکسی »"""
    if not proxies:
        proxies = FALLBACK_PROXIES
        
    links = []
    for p in proxies[:3]:
        links.append(f"« <a href=\"{p}\">پروکسی</a> »")
        
    return f"💎 <b>پروکسی‌های پرسرعت تلگرام :</b>\n{' '.join(links)}"
