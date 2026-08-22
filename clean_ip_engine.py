import asyncio
import json
import ssl
import time
import urllib.parse
from parser import decode_base64_safe, encode_base64_safe

# لیست آی‌پی‌های تمیز تایید شده همراه اول و ایرانسل
IRAN_CLEAN_IPS = [
    # MCI (همراه اول)
    "104.18.3.161",
    "104.18.2.161",
    "104.16.148.243",
    "104.16.149.243",
    "172.67.75.123",
    "188.114.96.3",
    "188.114.97.3",
    # Irancell (ایرانسل)
    "104.19.241.93",
    "104.19.242.93",
    "172.64.155.209",
    "104.18.225.52",
    "162.159.138.85"
]

def inject_clean_ip(config: str, clean_ip: str) -> str:
    """
    تزریق آی‌پی تمیز مخصوص اپراتورهای ایران به کانفیگ وب‌سوکت
    با حفظ کامل Host Header و SNI
    """
    config = config.strip()
    if config.startswith("vmess://"):
        try:
            data = json.loads(decode_base64_safe(config[8:]))
            orig_host = data.get("add")
            # اگر host خالی بود از add استفاده می‌کنیم
            if not data.get("host"):
                data["host"] = orig_host
            if not data.get("sni"):
                data["sni"] = orig_host
            data["add"] = clean_ip
            return "vmess://" + encode_base64_safe(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        except Exception:
            return config
    elif config.startswith("vless://") or config.startswith("trojan://"):
        try:
            clean_url = config.split("#", 1)[0]
            remark = config.split("#", 1)[1] if "#" in config else ""
            parsed = urllib.parse.urlsplit(clean_url)
            orig_host = parsed.hostname
            query = urllib.parse.parse_qs(parsed.query)
            
            # ذخیره host و sni
            if "host" not in query:
                query["host"] = [orig_host]
            if "sni" not in query:
                query["sni"] = [orig_host]
                
            new_query_str = urllib.parse.urlencode({k: v[0] for k, v in query.items()})
            new_netloc = f"{parsed.username}@{clean_ip}:{parsed.port or 443}"
            new_url = urllib.parse.urlunsplit((parsed.scheme, new_netloc, parsed.path, new_query_str, ""))
            if remark:
                new_url += f"#{remark}"
            return new_url
        except Exception:
            return config
    return config

print("Clean IP Injector ready.")
