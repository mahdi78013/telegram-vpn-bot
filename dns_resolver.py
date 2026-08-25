import asyncio
import logging
import socket
import time
from typing import Optional, List, Tuple, Dict
import httpx

logger = logging.getLogger("DNSResolver")

# کش محلی برای دامنه‌ها به همراه زمان انقضا
_DNS_CACHE: Dict[str, Tuple[List[str], float]] = {}
_DNS_CACHE_TTL = 300.0  # ۵ دقیقه

# سرویس‌های DNS over HTTPS برای حل امن و سریع در صورت اختلال DNS داخلی
DOH_RESOLVERS = [
    "https://1.1.1.1/dns-query",
    "https://dns.google/resolve",
    "https://9.9.9.9/dns-query",
]

async def resolve_host_doh(host: str, timeout: float = 1.2) -> List[str]:
    """
    حل نام دامنه از طریق پروتکل DNS-over-HTTPS (DoH) جهت عبور از مسدودی و آلودگی DNS
    """
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        for resolver in DOH_RESOLVERS:
            try:
                headers = {"accept": "application/dns-json"}
                params = {"name": host, "type": "A"}
                resp = await client.get(resolver, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    answers = data.get("Answer", [])
                    ips = [ans["data"] for ans in answers if ans.get("type") == 1]
                    if ips:
                        return ips
            except Exception as e:
                logger.debug(f"DoH resolver {resolver} failed for {host}: {e}")
                continue
    return []

async def resolve_host(
    host: str, 
    timeout: float = 1.0, 
    prefer_ipv6: bool = False
) -> Tuple[Optional[str], float]:
    """
    تفکیک چندلایه‌ای نام دامنه با مقاومت در برابر Timeout:
    1. بررسی حافظه کش سریع (L1 Memory Cache)
    2. تفکیک موازی سیستمی با تایم‌اوت کوتاه
    3. Failover خودکار به DoH (Cloudflare/Google/Quad9)
    خروجی: (آی‌پی نهایی, زمان تفکیک به میلی‌ثانیه)
    """
    host = host.strip()
    if not host:
        return None, 0.0

    # اگر از قبل IP عددی باشد
    try:
        socket.inet_aton(host)
        return host, 0.1
    except socket.error:
        pass

    # ۱. بررسی کش محلی
    now = time.time()
    if host in _DNS_CACHE:
        ips, exp = _DNS_CACHE[host]
        if now < exp and ips:
            return ips[0], 0.2

    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()

    # ۲. حل سیستمی با محدودیت زمانی
    try:
        family = socket.AF_INET6 if prefer_ipv6 else socket.AF_INET
        addr_info = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM),
            timeout=timeout
        )
        ips = list({info[4][0] for info in addr_info if info and info[4]})
        if ips:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _DNS_CACHE[host] = (ips, now + _DNS_CACHE_TTL)
            return ips[0], elapsed_ms
    except Exception as e:
        logger.debug(f"System DNS resolution failed or timed out for {host}: {e}")

    # ۳. فال‌بک موازی و هوشمند به DoH
    try:
        doh_ips = await resolve_host_doh(host, timeout=timeout * 1.5)
        if doh_ips:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _DNS_CACHE[host] = (doh_ips, now + _DNS_CACHE_TTL)
            logger.info(f"✅ دامنه {host} با موفقیت توسط DoH به {doh_ips[0]} تبدیل شد ({int(elapsed_ms)}ms).")
            return doh_ips[0], elapsed_ms
    except Exception as e:
        logger.debug(f"DoH fallback failed for {host}: {e}")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return None, elapsed_ms

def clear_dns_cache():
    """پاکسازی حافظه کش DNS"""
    global _DNS_CACHE
    _DNS_CACHE.clear()
