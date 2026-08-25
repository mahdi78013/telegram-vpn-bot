import os
import asyncio
import json
import logging
import re
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

from parser import decode_base64_safe
from dns_resolver import resolve_host

logger = logging.getLogger("PingTester")

# حداکثر پینگ مجاز برای تایید سلامت سرور (بر حسب میلی‌ثانیه)
MAX_ACCEPTABLE_PING_MS = 650

@dataclass
class PingResult:
    """
    نتیجه دقیق و ساختاریافته سنجش اتصال یک کانفیگ
    این کلاس با پشتیبانی از __iter__ کاملاً با کد‌های قبلی (tuple unpacking) سازگار است.
    """
    is_online: bool
    ping_ms: int
    ttfb_ms: int = -1
    dns_time_ms: float = 0.0
    tcp_time_ms: float = 0.0
    tls_time_ms: float = 0.0
    error_reason: str = ""

    def __iter__(self):
        yield self.is_online
        yield self.ping_ms

    def __getitem__(self, item):
        if item == 0:
            return self.is_online
        elif item == 1:
            return self.ping_ms
        raise IndexError("PingResult index out of range")

def extract_host_port(config: str) -> Optional[Dict[str, Any]]:
    """
    استخراج آدرس سرور (IP یا دامنه)، پورت و تنظیمات عمیق TLS/SNI/WS از تمام پروتکل‌ها
    """
    config = config.strip()
    if not config:
        return None
        
    # پروتکل VMess
    if config.lower().startswith("vmess://"):
        try:
            raw_b64 = config[len("vmess://"):]
            decoded_json = decode_base64_safe(raw_b64)
            data = json.loads(decoded_json)
            host = str(data.get("add", "")).strip()
            port = int(data.get("port", 443))
            net = str(data.get("net", "tcp")).lower()
            path = str(data.get("path", "/")).strip() or "/"
            host_hdr = str(data.get("host", host)).strip() or host
            tls = str(data.get("tls", "")).lower() == "tls"
            sni = str(data.get("sni", host_hdr)).strip() or host_hdr
            if host and port:
                return {
                    "host": host,
                    "port": port,
                    "net": net,
                    "path": path,
                    "host_hdr": host_hdr,
                    "use_tls": tls,
                    "sni": sni,
                    "protocol": "vmess"
                }
        except Exception as e:
            logger.debug(f"Error parsing vmess: {e}")
            return None

    # پروتکل‌های URL مانند vless, trojan, ss, tuic, hysteria, hy2
    try:
        clean_url = config.split("#", 1)[0]
        parsed = urllib.parse.urlsplit(clean_url)
        
        proto = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
        
        if not port:
            port = 443 if proto in ("vless", "trojan", "hysteria", "hysteria2", "hy2", "tuic") else 80
            
        # فرمت SS Base64 قدیمی
        if proto == "ss" and not host and parsed.netloc:
            try:
                decoded_netloc = decode_base64_safe(parsed.netloc)
                if "@" in decoded_netloc:
                    _, server_part = decoded_netloc.split("@", 1)
                    if ":" in server_part:
                        h, p = server_part.split(":", 1)
                        host = h
                        port = int(p)
            except Exception:
                pass
                
        if not host:
            return None
            
        query = urllib.parse.parse_qs(parsed.query)
        net = query.get("type", ["tcp"])[0].lower()
        path = query.get("path", ["/"])[0] or "/"
        host_hdr = query.get("host", [host])[0] or host
        security = query.get("security", [""])[0].lower()
        sni = query.get("sni", [host_hdr])[0] or host_hdr
        use_tls = security in ("tls", "reality") or proto in ("trojan", "hysteria", "hysteria2", "hy2")
        
        return {
            "host": host,
            "port": int(port),
            "net": net,
            "path": path,
            "host_hdr": host_hdr,
            "use_tls": use_tls,
            "sni": sni,
            "protocol": proto
        }
    except Exception as e:
        logger.debug(f"Error parsing url config: {e}")
        return None

class QuicUdpClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.received = asyncio.Event()
        self.data = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.data = data
        self.received.set()

    def error_received(self, exc):
        pass

async def ping_quic_udp(
    host: str, 
    port: int, 
    timeout: float = 2.0, 
    dns_time: float = 0.0,
    resolved_ip: Optional[str] = None
) -> PingResult:
    """
    تست پروب هوشمند QUIC/UDP برای پروتکل‌های Hysteria 2, Hysteria 1 و TUIC
    """
    loop = asyncio.get_running_loop()
    target_ip = resolved_ip or host
    start_time = time.perf_counter()
    transport = None
    try:
        # ساخت یک پکت استاندارد کاوشگر QUIC Initial (RFC 9000)
        quic_probe = bytearray(1200)
        quic_probe[0] = 0xc0  # Long header, Initial
        quic_probe[1:5] = (1).to_bytes(4, byteorder='big') # Version 1
        quic_probe[5] = 8 # DCID Len
        quic_probe[6:14] = os.urandom(8) # Random DCID
        quic_probe[14] = 0 # SCID Len
        quic_probe[15] = 0 # Token Length
        quic_probe[16:18] = (1180).to_bytes(2, byteorder='big') # Length

        protocol = QuicUdpClientProtocol()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            remote_addr=(target_ip, port)
        )
        transport.sendto(bytes(quic_probe))
        
        try:
            await asyncio.wait_for(protocol.received.wait(), timeout=timeout)
            rtt = max(1, int((time.perf_counter() - start_time) * 1000))
            if rtt <= MAX_ACCEPTABLE_PING_MS:
                return PingResult(
                    is_online=True, 
                    ping_ms=rtt, 
                    ttfb_ms=rtt, 
                    dns_time_ms=dns_time, 
                    tcp_time_ms=rtt
                )
            return PingResult(is_online=False, ping_ms=-1, error_reason="high_ping")
        except (asyncio.TimeoutError, TimeoutError):
            # فال‌بک به تست اتصال سوکت
            conn_coro = asyncio.open_connection(target_ip, port)
            reader, writer = await asyncio.wait_for(conn_coro, timeout=timeout * 0.7)
            writer.close()
            await writer.wait_closed()
            rtt = max(1, int((time.perf_counter() - start_time) * 1000))
            return PingResult(
                is_online=True, 
                ping_ms=rtt, 
                ttfb_ms=rtt, 
                dns_time_ms=dns_time, 
                tcp_time_ms=rtt
            )
    except Exception as e:
        return PingResult(is_online=False, ping_ms=-1, dns_time_ms=dns_time, error_reason=str(e))
    finally:
        if transport:
            transport.close()

async def ping_single_config(
    config: str, 
    timeout: float = 2.5,
    connect_timeout: float = 1.5,
    read_timeout: float = 2.0
) -> PingResult:
    """
    تستر عمیق، چندلایه‌ای و فوق‌دقیق پروتکل با تفکیک Connect Timeout و Read Timeout:
    1. تفکیک سریع DNS مقاوم در برابر اختلال (Multi-Resolver)
    2. تست UDP/QUIC برای Hysteria 2 و TUIC
    3. تست هندشیک وب‌سوکت واقعی (101 Switching Protocols) + اندازه‌گیری TTFB
    4. تست دست‌تکانی کامل TLS 1.3 / Reality
    5. تست اتصال مستقیم TCP
    خروجی: شیء PingResult (قابل Unpack به صورت is_online, ping_ms)
    """
    info = extract_host_port(config)
    if not info:
        return PingResult(is_online=False, ping_ms=-1, error_reason="invalid_config_format")
        
    host = info["host"]
    port = info["port"]
    net = info["net"]
    path = info["path"]
    host_hdr = info["host_hdr"]
    use_tls = info["use_tls"]
    sni = info["sni"]
    proto = info.get("protocol", "").lower()

    # گام ۱: تفکیک امن و پرسرعت DNS
    resolved_ip, dns_time = await resolve_host(host, timeout=1.0)
    target_host = resolved_ip if resolved_ip else host
    
    # پروتکل‌های مبتنی بر QUIC/UDP (Hysteria 2 / TUIC)
    if proto in ("hysteria", "hysteria2", "hy2", "tuic") or net in ("quic", "kcp", "hy2"):
        return await ping_quic_udp(
            host=host, 
            port=port, 
            timeout=connect_timeout, 
            dns_time=dns_time, 
            resolved_ip=target_host
        )
    
    overall_start = time.perf_counter()
    writer = None
    tcp_time = 0.0
    tls_time = 0.0
    
    try:
        # لایه ۱: اگر وب‌سوکت است
        if net == "ws":
            ssl_ctx = None
            if use_tls:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            t_tcp0 = time.perf_counter()
            conn_coro = asyncio.open_connection(
                target_host, 
                port, 
                ssl=ssl_ctx, 
                server_hostname=sni if use_tls else None
            )
            reader, writer = await asyncio.wait_for(conn_coro, timeout=connect_timeout)
            tcp_time = (time.perf_counter() - t_tcp0) * 1000

            # ارسال هدر Upgrade به وب‌سوکت
            ws_req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_hdr}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            t_req0 = time.perf_counter()
            writer.write(ws_req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=read_timeout)
            ttfb = int((time.perf_counter() - t_req0) * 1000)
            resp_str = resp_line.decode('utf-8', errors='ignore')
            rtt = max(1, int((time.perf_counter() - overall_start) * 1000))

            # سرور زنده باید با کد 101 یا خطای مشخص WebSocket پاسخ دهد
            if "101" in resp_str or ("400" in resp_str and "websocket" in resp_str.lower()):
                if rtt <= MAX_ACCEPTABLE_PING_MS:
                    return PingResult(
                        is_online=True, 
                        ping_ms=rtt, 
                        ttfb_ms=ttfb, 
                        dns_time_ms=dns_time, 
                        tcp_time_ms=tcp_time
                    )
                else:
                    return PingResult(is_online=False, ping_ms=-1, error_reason="high_latency")
            else:
                return PingResult(is_online=False, ping_ms=-1, error_reason=f"backend_bad_resp_{resp_str.strip()[:20]}")

        # لایه ۲: اگر TLS / Reality است
        elif use_tls:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            
            t_tls0 = time.perf_counter()
            conn_coro = asyncio.open_connection(target_host, port, ssl=ssl_ctx, server_hostname=sni)
            reader, writer = await asyncio.wait_for(conn_coro, timeout=connect_timeout)
            tls_time = (time.perf_counter() - t_tls0) * 1000
            rtt = max(1, int((time.perf_counter() - overall_start) * 1000))
            
            if rtt <= MAX_ACCEPTABLE_PING_MS:
                return PingResult(
                    is_online=True, 
                    ping_ms=rtt, 
                    ttfb_ms=int(tls_time), 
                    dns_time_ms=dns_time, 
                    tcp_time_ms=tls_time, 
                    tls_time_ms=tls_time
                )
            else:
                return PingResult(is_online=False, ping_ms=-1, error_reason="high_latency")

        # لایه ۳: تست اتصال مستقیم TCP
        else:
            t_tcp0 = time.perf_counter()
            conn_coro = asyncio.open_connection(target_host, port)
            reader, writer = await asyncio.wait_for(conn_coro, timeout=connect_timeout)
            tcp_time = (time.perf_counter() - t_tcp0) * 1000
            rtt = max(1, int((time.perf_counter() - overall_start) * 1000))
            
            if rtt <= MAX_ACCEPTABLE_PING_MS:
                return PingResult(
                    is_online=True, 
                    ping_ms=rtt, 
                    ttfb_ms=int(tcp_time), 
                    dns_time_ms=dns_time, 
                    tcp_time_ms=tcp_time
                )
            else:
                return PingResult(is_online=False, ping_ms=-1, error_reason="high_latency")
                
    except (asyncio.TimeoutError, TimeoutError):
        return PingResult(is_online=False, ping_ms=-1, dns_time_ms=dns_time, error_reason="timeout")
    except (ConnectionRefusedError, socket.gaierror, OSError, ssl.SSLError) as e:
        return PingResult(is_online=False, ping_ms=-1, dns_time_ms=dns_time, error_reason=str(e))
    except Exception as e:
        return PingResult(is_online=False, ping_ms=-1, dns_time_ms=dns_time, error_reason=str(e))
    finally:
        if writer:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            except Exception:
                pass

async def verify_config_stability_3x(
    config: str, 
    required_passes: int = 3, 
    timeout: float = 2.0, 
    delay_between_probes: float = 0.2
) -> Tuple[bool, int, str]:
    """
    تست ۳ مرحله‌ای پایداری و پینگ پیش از ارسال به کانال (Triple Verification):
    کانفیگ ۳ بار متوالی با فواصل کوتاه تست می‌شود.
    تنها در صورتی تایید می‌شود که در هر ۳ تلاش ۱۰۰٪ موفق باشد و تاخیر پایدار داشته باشد.
    خروجی: (تایید نهایی, میانگین پینگ, گزارش جزییات)
    """
    pings = []
    for i in range(1, required_passes + 1):
        res = await ping_single_config(config, timeout=timeout, connect_timeout=1.5, read_timeout=1.5)
        if not res.is_online or res.ping_ms <= 0:
            return False, -1, f"شکست در مرحله {i} از {required_passes} ({res.error_reason})"
        pings.append(res.ping_ms)
        if i < required_passes:
            await asyncio.sleep(delay_between_probes)
            
    avg_ping = sum(pings) // len(pings)
    detail_str = f"تایید کامل ۳ از ۳ ({', '.join(f'{p}ms' for p in pings)}) -> میانگین: {avg_ping}ms"
    return True, avg_ping, detail_str

async def ping_configs_batch(
    configs: List[Dict[str, Any]], 
    concurrency: int = 25, 
    timeout: float = 2.0
) -> List[Tuple[int, bool, int]]:
    """
    تست دسته‌ای کانفیگ‌ها با مدیریت همزمانی بالا
    خروجی: لیستی از (config_id, is_online, ping_ms)
    """
    semaphore = asyncio.Semaphore(concurrency)
    
    async def worker(item: Dict[str, Any]) -> Tuple[int, bool, int]:
        cid = item["id"]
        raw_conf = item["raw_config"]
        async with semaphore:
            res = await ping_single_config(raw_conf, timeout=timeout)
            return cid, res.is_online, res.ping_ms
            
    tasks = [worker(item) for item in configs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_results = []
    for idx, res in enumerate(results):
        if isinstance(res, tuple):
            valid_results.append(res)
        else:
            cid = configs[idx]["id"]
            valid_results.append((cid, False, -1))
            
    return valid_results

async def verify_config_is_completely_dead_10x(
    config: str,
    total_tests: int = 10,
    timeout: float = 1.5,
    delay_between_tests: float = 0.2
) -> bool:
    """
    تست فوق‌سخت‌گیرانه ۱۰ مرحله‌ای برای تایید قطعی سوختن/فیلتر شدن سرور:
    سیستم ۱۰ بار پشت سر هم از سرور پینگ می‌گیرد.
    اگر حتی ۱ بار از ۱۰ بار پینگ موفق دهد، سرور زنده محسوب شده و حذف نمی‌شود (خروجی False).
    تنها در صورتی که در تمام ۱۰ بار متوالی شکست بخورد، تایید می‌شود که ۱۰۰٪ سوخته است (خروجی True).
    """
    for i in range(1, total_tests + 1):
        res = await ping_single_config(config, timeout=timeout)
        if res.is_online and res.ping_ms > 0:
            return False
        if i < total_tests:
            await asyncio.sleep(delay_between_tests)
            
    return True
