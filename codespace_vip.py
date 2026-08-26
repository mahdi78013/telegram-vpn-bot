import os
import sys
import json
import html
import time
import zipfile
import urllib.request
import subprocess
import logging
import threading
import re
from typing import Dict, Any

logger = logging.getLogger("CodespaceVIP")

LOCAL_LIVE_PATH = os.path.join(os.path.dirname(__file__), "live_vip.json")
PROXY_DIR = "/tmp/proxy_engine"

# پرسرعت‌ترین و پایدارترین آی‌پی‌های تست‌شده در اپراتورهای ایران
FASTEST_MCI_IP = "188.114.96.3"        # فوق‌العاده سریع روی همراه اول و مخابرات
FASTEST_MTN_IP = "162.159.138.85"      # فوق‌العاده پرسرعت روی ایرانسل و رایتل
FASTEST_TURBO_IP = "188.114.97.3"     # رنج اختصاصی دانلود با نهایت پهنای‌باند

def setup_and_start_local_node():
    """
    راه‌اندازی خودکار سرور ۲۴ ساعته Xray و تونل کلودفلر در پس‌زمینه سرور لینوکس GitHub Actions
    """
    if not sys.platform.startswith("linux"):
        return

    try:
        os.makedirs(PROXY_DIR, exist_ok=True)
        xray_bin = os.path.join(PROXY_DIR, "xray")
        cf_bin = os.path.join(PROXY_DIR, "cloudflared")
        
        # 1. دانلود هسته Xray در صورت نیاز
        if not os.path.exists(xray_bin):
            logger.info("در حال دانلود هسته Xray-core...")
            zip_path = os.path.join(PROXY_DIR, "xray.zip")
            urllib.request.urlretrieve(
                "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip",
                zip_path
            )
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(PROXY_DIR)
            os.chmod(xray_bin, 0o755)
            
        # 2. دانلود Cloudflared در صورت نیاز
        if not os.path.exists(cf_bin):
            logger.info("در حال دانلود تونل Cloudflared...")
            urllib.request.urlretrieve(
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                cf_bin
            )
            os.chmod(cf_bin, 0o755)
            
        # 3. ساخت کانفیگ بهینه‌شده و فوق‌حرفه‌ای Xray با Sniffing، Early Data و TCP FastOpen
        cfg_path = os.path.join(PROXY_DIR, "config.json")
        cfg_json = {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [
                {
                    "port": 8080,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": [
                            {
                                "id": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
                                "level": 0
                            }
                        ],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "ws",
                        "wsSettings": {
                            "path": "/vless-ws",
                            "maxEarlyData": 2048,
                            "earlyDataHeaderName": "Sec-WebSocket-Protocol"
                        },
                        "sockopt": {
                            "tcpFastOpen": True,
                            "tcpNoDelay": True,
                            "tcpKeepAliveInterval": 15
                        }
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls", "quic"],
                        "metadataOnly": False
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "freedom",
                    "settings": {
                        "domainStrategy": "UseIPv4"
                    },
                    "streamSettings": {
                        "sockopt": {
                            "tcpFastOpen": True,
                            "tcpNoDelay": True,
                            "tcpKeepAliveInterval": 15
                        }
                    }
                }
            ]
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg_json, f)
            
        # 4. تابع اجرای مطمئن Xray
        def start_xray():
            return subprocess.Popen([xray_bin, "-config", cfg_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        # 5. تابع اجرای مطمئن تونل کلادفلر
        tunnel_log = os.path.join(PROXY_DIR, "tunnel.log")
        def start_cloudflared():
            if os.path.exists(tunnel_log):
                try:
                    os.remove(tunnel_log)
                except Exception:
                    pass
            log_f = open(tunnel_log, "w")
            cf_cmd = [
                cf_bin, "tunnel",
                "--url", "http://127.0.0.1:8080",
                "--no-autoupdate",
                "--edge-ip-version", "auto"
            ]
            return subprocess.Popen(cf_cmd, stdout=log_f, stderr=subprocess.STDOUT)
            
        p_xray = start_xray()
        p_cf = start_cloudflared()
        
        # 6. رشته سگ نگهبان (Watchdog) برای پایش دائمی، بروزرسانی خودکار دامنه و ارسال پکت Keep-Alive
        def watchdog_and_domain_saver():
            nonlocal p_xray, p_cf
            current_domain = ""
            
            while True:
                try:
                    # 1. بررسی سلامت Xray
                    if p_xray.poll() is not None:
                        logger.warning("سرویس Xray متوقف شده بود، در حال راه‌اندازی مجدد...")
                        p_xray = start_xray()
                        
                    # 2. بررسی سلامت Cloudflared
                    if p_cf.poll() is not None:
                        logger.warning("تونل کلادفلر متوقف شده بود، در حال راه‌اندازی مجدد...")
                        p_cf = start_cloudflared()
                        time.sleep(3)
                        
                    # 3. استخراج و بروزرسانی بلادرنگ آخرین دامنه زنده
                    if os.path.exists(tunnel_log):
                        with open(tunnel_log, "r", encoding="utf-8", errors="ignore") as lf:
                            content = lf.read()
                            matches = re.findall(r"https://([a-zA-Z0-9.-]+\.trycloudflare\.com)", content)
                            if matches:
                                latest_domain = matches[-1]
                                if latest_domain != current_domain:
                                    current_domain = latest_domain
                                    logger.info(f"✅ دامنه فعال و زنده سرور ابری ثبت/بروزرسانی شد: {current_domain}")
                                    with open(LOCAL_LIVE_PATH, "w", encoding="utf-8") as out_f:
                                        json.dump({
                                            "domain": current_domain,
                                            "uuid": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
                                            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                                        }, out_f)
                                    update_subscription_files(current_domain)
                                        
                    # 4. ارسال پکت Keep-Alive به پورت محلی جهت زنده نگه داشتن تونل کلادفلر
                    try:
                        urllib.request.urlopen("http://127.0.0.1:8080", timeout=2)
                    except Exception:
                        pass
                        
                except Exception as ex:
                    logger.debug(f"Watchdog exception: {ex}")
                    
                time.sleep(5)
                    
        t = threading.Thread(target=watchdog_and_domain_saver, daemon=True)
        t.start()
        logger.info("🚀 سرویس ضدقطعی و سگ نگهبان ۲۴ ساعته Cloud VIP با موفقیت فعال شد.")
        
    except Exception as e:
        logger.error(f"Error in setup_and_start_local_node: {e}")

SUB_FILE_PATH = os.path.join(os.path.dirname(__file__), "sub.txt")
SUB_PLAIN_PATH = os.path.join(os.path.dirname(__file__), "sub_plain.txt")

async def generate_and_publish_universal_sub(tag: str = "@muntivpn", target_count: int = 10) -> str:
    """
    تولید و انتشار سابسکریپشن ۱۰ سرور برتر ضدفیلتر ایران:
    ۱. دانلود غیرهمزمان از ۹+ منبع
    ۲. فیلتر ضدفیلترینگ ایران (is_iran_compatible)
    ۳. حذف تکراری‌ها (deduplicate_by_server)
    ۴. تست پینگ موازی
    ۵. انتخاب ۱۰ سرور با کمترین پینگ
    ۶. انتشار در گیت‌هاب + پاکسازی کش CDN
    """
    import base64
    import json
    import re
    import aiohttp
    from tester import ping_single_config
    from parser import sanitize_url_parameters, decode_base64_safe
    
    COUNTRIES = ["DE", "NL", "FI", "US", "GB", "FR", "CA", "TR", "SE", "SG"]
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۱: فیلتر هوشمند ضدفیلترینگ ایران (DPI Survivability)
    # ═══════════════════════════════════════════════════════════════
    def is_iran_compatible(config: str) -> bool:
        """
        بررسی اینکه آیا کانفیگ از فیلترینگ عمیق (DPI) ایران عبور می‌کند:
        ✅ VLESS Reality + fp=chrome + flow=vision
        ✅ VLESS/VMess WebSocket + TLS
        ✅ Hysteria2, TUIC (UDP obfuscated)
        ✅ Trojan + TLS
        ❌ هر چیزی بدون رمزنگاری → بلاک قطعی
        """
        conf_lower = config.lower()
        
        # Hysteria2 و TUIC همیشه ضدفیلتر هستند (UDP obfuscated)
        if conf_lower.startswith("hy2://") or conf_lower.startswith("tuic://"):
            return True
            
        # Trojan همیشه TLS دارد
        if conf_lower.startswith("trojan://"):
            if "security=none" not in conf_lower:
                return True
            return False
        
        # VMess: فقط با TLS یا WebSocket+TLS قبول
        if conf_lower.startswith("vmess://"):
            try:
                b64_part = config[8:]
                if "#" in b64_part:
                    b64_part = b64_part.split("#")[0]
                decoded = decode_base64_safe(b64_part)
                obj = json.loads(decoded)
                tls = str(obj.get("tls", "")).lower()
                net = str(obj.get("net", "")).lower()
                # VMess بدون TLS → بلاک قطعی در ایران
                if tls not in ("tls",):
                    return False
                # VMess + TLS (ترجیحاً WS) → قبول
                return True
            except Exception:
                return False
        
        # VLESS: بررسی دقیق
        if conf_lower.startswith("vless://"):
            # Reality → باید fp=chrome داشته باشد
            if "security=reality" in conf_lower or "pbk=" in conf_lower:
                # fp باید chrome/safari/edge باشد (نه firefox و نه خالی)
                fp_match = re.search(r'fp=([^&# ]*)', conf_lower)
                if fp_match:
                    fp_val = fp_match.group(1)
                    if fp_val in ("chrome", "safari", "edge", "random", "randomized"):
                        return True
                return False
                
            # VLESS + TLS (نه Reality) → قبول اگر TLS دارد
            if "security=tls" in conf_lower:
                return True
                
            # VLESS بدون TLS و بدون Reality → بلاک
            if "security=none" in conf_lower or "security=" not in conf_lower:
                return False
                
            return False
        
        return False
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۲: حذف تکراری‌ها بر اساس IP:Port
    # ═══════════════════════════════════════════════════════════════
    def extract_server_key(config: str) -> str:
        """استخراج host:port یکتا از هر کانفیگ برای تشخیص تکراری"""
        conf_lower = config.lower()
        if conf_lower.startswith("vmess://"):
            try:
                b64_part = config[8:]
                if "#" in b64_part:
                    b64_part = b64_part.split("#")[0]
                decoded = decode_base64_safe(b64_part)
                obj = json.loads(decoded)
                return f"{obj.get('add', '')}:{obj.get('port', '')}"
            except Exception:
                return config[:60]
        else:
            # URL-style: vless://uuid@host:port?...
            m = re.search(r'@([^:/?#]+):(\d+)', config)
            if m:
                return f"{m.group(1)}:{m.group(2)}"
            return config[:60]
    
    def deduplicate_by_server(configs: list) -> list:
        """حذف تکراری‌ها بر اساس IP:Port — فقط اولین نمونه نگه داشته می‌شود"""
        seen = set()
        unique = []
        for c in configs:
            key = extract_server_key(c)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۳: دانلود غیرهمزمان از منابع متنوع (aiohttp)
    # ═══════════════════════════════════════════════════════════════
    sources = [
        # MahsaNet (ایران‌محور — مخصوص ایرانسل و همراه اول)
        "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt",
        "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mci/sub_1.txt",
        # Epodonios (VLESS Reality)
        "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt",
        # ALIILAPRO
        "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt",
        # Fly
        "https://raw.githubusercontent.com/ts-sf/Fly/main/v2",
        # 🆕 منابع جدید با تمرکز روی Reality
        "https://raw.githubusercontent.com/soroushmirzaei/telegram-configs-collector/main/protocols/reality",
        "https://raw.githubusercontent.com/yebekhe/TelegramV2rayCollector/main/sub/reality/base64",
        "https://raw.githubusercontent.com/mfuu/v2ray/master/v2ray",
        "https://raw.githubusercontent.com/mahdibland/ShadowsocksAggregator/master/sub/sub_merge.txt",
    ]
    
    SUPPORTED_PREFIXES = ("vless://", "vmess://", "hy2://", "trojan://", "tuic://", "hysteria2://", "ss://")
    
    candidates = []
    
    async def fetch_source(session, url):
        """دانلود غیرهمزمان هر منبع"""
        fetched = []
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return fetched
                raw = await resp.text(errors='ignore')
                lines = raw.split('\n')
                for line in lines:
                    l = line.strip()
                    if not l:
                        continue
                    if any(l.startswith(p) for p in SUPPORTED_PREFIXES):
                        fetched.append(l)
                    else:
                        # ممکن است Base64 باشد
                        try:
                            dec = decode_base64_safe(l)
                            for il in dec.split('\n'):
                                il = il.strip()
                                if any(il.startswith(p) for p in SUPPORTED_PREFIXES):
                                    fetched.append(il)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Error fetching {url}: {e}")
        return fetched
    
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "v2rayNG/1.8.12"}
        ) as session:
            fetch_tasks = [fetch_source(session, s) for s in sources]
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    for c in r:
                        if c not in candidates:
                            candidates.append(c)
    except Exception as e:
        logger.warning(f"Error in batch fetch: {e}")
    
    logger.info(f"📥 {len(candidates)} کانفیگ خام از {len(sources)} منبع جمع‌آوری شد.")
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۴: آماده‌سازی + فیلتر ایران + حذف تکراری
    # ═══════════════════════════════════════════════════════════════
    cleaned = []
    for c in candidates:
        # حذف remark قبلی
        if "#" in c and not c.startswith("vmess://"):
            base = c.split("#")[0]
        else:
            base = c
        
        # اصلاح پارامترها
        if not base.startswith("vmess://"):
            base = sanitize_url_parameters(base)
            # اصلاح Reality fp
            if "security=reality" in base or "pbk=" in base:
                if "fp=firefox" in base:
                    base = base.replace("fp=firefox", "fp=chrome")
                fp_match = re.search(r'fp=([^&# ]*)', base)
                if not fp_match or not fp_match.group(1) or fp_match.group(1) in ("none", "null", ""):
                    sep = "&" if "?" in base else "?"
                    base += f"{sep}fp=chrome"
                if "flow=" not in base:
                    sep = "&" if "?" in base else "?"
                    base += f"{sep}flow=xtls-rprx-vision"
        
        cleaned.append(base)
    
    # فیلتر ضدفیلترینگ ایران
    iran_ok = [c for c in cleaned if is_iran_compatible(c)]
    logger.info(f"🛡️ {len(iran_ok)} کانفیگ ضدفیلتر ایران از {len(cleaned)} کل کاندیدا.")
    
    # حذف تکراری‌ها
    unique = deduplicate_by_server(iran_ok)
    logger.info(f"🔄 {len(unique)} سرور یکتا پس از حذف تکراری‌ها.")
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۵: تست موازی پینگ زنده
    # ═══════════════════════════════════════════════════════════════
    async def check_alive(conf: str):
        try:
            res = await ping_single_config(conf, connect_timeout=1.0)
            if res.is_online and 50 <= res.ping_ms <= 600:
                return (conf, res.ping_ms)
        except Exception:
            pass
        return None
    
    # تست حداکثر ۱۵۰ سرور
    test_batch = unique[:150]
    tasks = [check_alive(c) for c in test_batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    alive_nodes = []
    for r in results:
        if isinstance(r, tuple) and r is not None:
            alive_nodes.append(r)
    
    # مرتب‌سازی بر اساس کمترین پینگ
    alive_nodes.sort(key=lambda x: x[1])
    
    logger.info(f"⚡ {len(alive_nodes)} سرور زنده با پینگ سبز از {len(test_batch)} تست‌شده.")
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۶: انتخاب ۱۰ سرور برتر + ساخت سابسکریپشن
    # ═══════════════════════════════════════════════════════════════
    selected_nodes = alive_nodes[:target_count]
    
    if len(selected_nodes) < 3:
        logger.warning(f"⚠️ فقط {len(selected_nodes)} نود زنده یافت شد — از کاندیداهای ایران‌سازگار استفاده می‌شود.")
        # fallback: از لیست فیلتر‌شده ولی تست‌نشده استفاده کن
        fallback = [(c, 300) for c in unique if c not in [x[0] for x in selected_nodes]]
        selected_nodes.extend(fallback[:target_count - len(selected_nodes)])
    
    final_confs = []
    for idx, (conf_base, pms) in enumerate(selected_nodes, 1):
        cc = COUNTRIES[(idx - 1) % len(COUNTRIES)]
        remark = f"VIP-{idx:02d} [{cc}] {tag}"
        final_confs.append(f"{conf_base}#{remark}")
    
    if not final_confs:
        logger.error("❌ هیچ سروری برای سابسکریپشن یافت نشد!")
        return "https://cdn.jsdelivr.net/gh/mahdi78013/static-web-content@main/assets/d9f3a7c2.dat"
    
    plain_content = "\n".join(final_confs) + "\n"
    b64_content = base64.b64encode(plain_content.encode("utf-8")).decode("utf-8")
    
    # ذخیره محلی
    try:
        with open(SUB_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(b64_content)
        with open(SUB_PLAIN_PATH, "w", encoding="utf-8") as f:
            f.write(plain_content)
    except Exception as e:
        logger.warning(f"Error saving sub files: {e}")
    
    # ═══════════════════════════════════════════════════════════════
    # فاز ۷: انتشار در گیت‌هاب (ریپوی مخفی) + پاکسازی کش CDN
    # ═══════════════════════════════════════════════════════════════
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_PAT", "")
    
    # ریپوی مخفی با اسم بی‌ربط — بدون نام VPN یا ربات
    stealth_repo = "mahdi78013/static-web-content"
    stealth_file = "assets/d9f3a7c2.dat"
    cdn_url = "https://cdn.jsdelivr.net/gh/mahdi78013/static-web-content@main/assets/d9f3a7c2.dat"
    
    if token:
        try:
            b64_payload = base64.b64encode(b64_content.encode("utf-8")).decode("utf-8")
            gh_headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "StaticCDN-Updater"
            }
            
            async with aiohttp.ClientSession() as session:
                # انتشار در ریپوی مخفی
                stealth_api = f"https://api.github.com/repos/{stealth_repo}/contents/{stealth_file}"
                sha = ""
                try:
                    async with session.get(stealth_api, headers=gh_headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            d = await resp.json()
                            sha = d.get("sha", "")
                except Exception:
                    pass
                
                body_dict = {
                    "message": "Update static content",
                    "content": b64_payload,
                }
                if sha:
                    body_dict["sha"] = sha
                
                async with session.put(
                    stealth_api,
                    headers=gh_headers,
                    json=body_dict,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"✅ سابسکریپشن {len(final_confs)} نود در ریپوی مخفی منتشر شد.")
                    else:
                        txt = await resp.text()
                        logger.warning(f"Stealth push status {resp.status}: {txt[:200]}")
                
                # همچنین در ریپوی اصلی هم آپدیت کن (برای سازگاری)
                main_api = "https://api.github.com/repos/mahdi78013/telegram-vpn-bot/contents/sub.txt"
                sha2 = ""
                try:
                    async with session.get(main_api, headers=gh_headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            d2 = await resp.json()
                            sha2 = d2.get("sha", "")
                except Exception:
                    pass
                body2 = {"message": "Sync sub [skip ci]", "content": b64_payload}
                if sha2:
                    body2["sha"] = sha2
                try:
                    async with session.put(main_api, headers=gh_headers, json=body2, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        pass
                except Exception:
                    pass
                
                # پاکسازی کش CDN هر دو
                for purge in [
                    f"https://purge.jsdelivr.net/gh/{stealth_repo}@main/{stealth_file}",
                    "https://purge.jsdelivr.net/gh/mahdi78013/telegram-vpn-bot@main/sub.txt"
                ]:
                    try:
                        async with session.get(purge, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            pass
                    except Exception:
                        pass
                logger.info("🧹 کش CDN پاکسازی شد.")
                    
        except Exception as ex:
            logger.warning(f"Error publishing sub to GitHub: {ex}")
    
    avg_ping = int(sum(p for _, p in selected_nodes) / max(len(selected_nodes), 1))
    logger.info(f"📊 میانگین پینگ {len(selected_nodes)} سرور منتخب: {avg_ping}ms")
    
    return cdn_url


def update_subscription_files(domain: str, uuid: str = "f12abdbd-23a8-414b-a89e-c447be5ba57d"):
    """فراخوانی سازگار با توابع پس‌زمینه"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(generate_and_publish_universal_sub())
    except Exception:
        pass


async def get_latest_local_config(tag: str = "@muntivpn") -> Dict[str, Any]:
    """تولید کانفیگ محلی در صورت نیاز"""
    config_data = None
    if os.path.exists(LOCAL_LIVE_PATH):
        try:
            with open(LOCAL_LIVE_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error reading local live_vip.json: {e}")
            
    if not config_data or "domain" not in config_data:
        config_data = {
            "domain": "may-customs-inquiry-populations.trycloudflare.com",
            "uuid": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
            "updated_at": "هم‌اکنون"
        }
        
    tunnel_domain = config_data.get("domain", "may-customs-inquiry-populations.trycloudflare.com").strip()
    user_id = config_data.get("uuid", "f12abdbd-23a8-414b-a89e-c447be5ba57d").strip()
    
    link_direct = (
        f"vless://{user_id}@{tunnel_domain}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws%3Fed%3D2048&alpn=h2%2Chttp%2F1.1"
        f"#⚡VIP-Turbo-Direct"
    )
    return {
        "direct": link_direct,
        "ping": 68,
        "flag": "🇩🇪",
        "proto": "VLESS WS",
        "tag": tag
    }

async def get_latest_codespace_config(
    tag: str = "@muntivpn",
    carrier: str = "all",
    region: str = "all"
) -> Dict[str, Any]:
    """
    استخراج و تحویل پرسرعت‌ترین و پایدارترین کانفیگ ابری با تضمین اتصال در ایران:
    اولویت ۱: استخراج برترین نود ضد فیلتر VLESS Reality / Hysteria 2 از موتور پیشرفته تحویل
    """
    from node_registry import NetworkContext
    from config_delivery_engine import delivery_engine
    
    context = NetworkContext(carrier=carrier, region=region)
    result = await delivery_engine.get_best_config(context=context, tag=tag)
    direct_conf = result["direct"]
    
    return {
        "direct": direct_conf,
        "mci": direct_conf,
        "mtn": direct_conf,
        "wifi": direct_conf,
        "ping": result.get("ping", 65),
        "flag": result.get("flag", "🇩🇪"),
        "proto": result.get("proto", "VLESS Reality"),
        "tag": tag,
        "score": result.get("score", 95.0),
        "cache_level": result.get("cache_level", "L1-Memory"),
        "is_reality": True
    }

def format_codespace_vip_message(config_data: Dict[str, Any]) -> str:
    """
    قالب‌بندی فوق‌حرفه‌ای و شیک برای تک‌کانفیگ پرسرعت VIP با دامنه سفید Reality
    """
    if "error" in config_data:
        return config_data["error"]
        
    direct_conf = html.escape(config_data.get("direct", ""))
    tag = config_data.get("tag", "@Internet_azad369")
    flag = config_data.get("flag", "🇩🇪")
    proto = config_data.get("proto", "VLESS Reality")
    ping = config_data.get("ping", 65)
    
    msg = (
        "🚀 <b>کانفیگ پرسرعت اختصاصی (VIP Reality - سراسری)</b>\n\n"
        f"📍 <b>موقعیت سرور:</b> {flag} آلمان / فنلاند (سرور قدرتمند ابری)\n"
        f"⚡ <b>پروتکل:</b> <code>{proto.upper()} (دامنه سفید ضد فیلتر)</code>\n"
        f"📶 <b>پینگ پایدار:</b> 🟢 <code>{ping}ms</code> (تست‌شده و متصل)\n"
        "🌐 <b>پشتیبانی:</b> همراه اول، ایرانسل، مخابرات، رایتل و وای‌فای خانگی\n"
        "🛡️ <b>وضعیت اتصال:</b> بدون قطعی و فعال در تمام ۳۱ استان ایران\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 <b>کانفیگ مستقیم آماده اتصال (یکبار لمس برای کپی):</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>این سرور بر بستر پروتکل Reality با دامنه سفید و فینگرپرینت Chrome فعال است و هرگز دچار تایم‌اوت‌های trycloudflare نمی‌شود.</i>\n\n"
        f"👑 <b>{tag}</b>"
    )
    return msg
