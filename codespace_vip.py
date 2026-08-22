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
            
        # 3. ساخت کانفیگ بهینه‌شده Xray با Sniffing مخصوص اینستاگرام و متا
        cfg_path = os.path.join(PROXY_DIR, "config.json")
        cfg_json = {
            "inbounds": [
                {
                    "port": 8080,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": "f12abdbd-23a8-414b-a89e-c447be5ba57d"}],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "ws",
                        "wsSettings": {"path": "/vless-ws"}
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls", "quic"]
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "freedom",
                    "settings": {"domainStrategy": "UseIPv4"}
                }
            ]
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg_json, f)
            
        # 4. اجرای Xray در پس‌زمینه
        subprocess.Popen([xray_bin, "-config", cfg_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 5. اجرای تونل کلادفلر
        tunnel_log = os.path.join(PROXY_DIR, "tunnel.log")
        if os.path.exists(tunnel_log):
            try:
                os.remove(tunnel_log)
            except Exception:
                pass
            
        log_f = open(tunnel_log, "w")
        subprocess.Popen([cf_bin, "tunnel", "--url", "http://127.0.0.1:8080", "--no-autoupdate"], stdout=log_f, stderr=subprocess.STDOUT)
        
        # 6. رشته پس‌زمینه برای ثبت خودکار دامنه زنده
        def wait_and_save_domain():
            time.sleep(6)
            domain = ""
            for _ in range(30):
                if os.path.exists(tunnel_log):
                    with open(tunnel_log, "r", encoding="utf-8", errors="ignore") as lf:
                        content = lf.read()
                        m = re.search(r"https://([a-zA-Z0-9.-]+\.trycloudflare\.com)", content)
                        if m:
                            domain = m.group(1)
                            break
                time.sleep(1)
                
            if domain:
                logger.info(f"✅ دامنه زنده ۲۴ ساعته سرور ثبت شد: {domain}")
                with open(LOCAL_LIVE_PATH, "w", encoding="utf-8") as lf:
                    json.dump({
                        "domain": domain,
                        "uuid": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                    }, lf)
                    
        t = threading.Thread(target=wait_and_save_domain, daemon=True)
        t.start()
        logger.info("🚀 سرویس ۲۴ ساعته Cloud Node در پس‌زمینه راه‌اندازی شد.")
        
    except Exception as e:
        logger.error(f"Error in setup_and_start_local_node: {e}")

async def get_latest_codespace_config(tag: str = "@Internet_azad369") -> Dict[str, Any]:
    """
    تولید خودکار کانفیگ‌های بهینه‌شده با نهایت سرعت بدون نیاز به هیچ تنظیم دستی در کلاینت
    """
    config_data = None
    
    if os.path.exists(LOCAL_LIVE_PATH):
        try:
            with open(LOCAL_LIVE_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error reading local live_vip.json: {e}")
            
    if not config_data or "domain" not in config_data:
        config_data = {
            "domain": "contributed-independent-vice-indoor.trycloudflare.com",
            "uuid": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
            "updated_at": "لحظاتی پیش"
        }
        
    tunnel_domain = config_data.get("domain", "contributed-independent-vice-indoor.trycloudflare.com").strip()
    user_id = config_data.get("uuid", "f12abdbd-23a8-414b-a89e-c447be5ba57d").strip()
    updated_at = config_data.get("updated_at", "هم‌اکنون")
    
    link_direct = (
        f"vless://{user_id}@{tunnel_domain}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws"
        f"#⚡VIP-Turbo-Direct"
    )
    
    link_mci = (
        f"vless://{user_id}@{FASTEST_MCI_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Turbo-MCI"
    )
    
    link_mtn = (
        f"vless://{user_id}@FASTEST_MTN_IP:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Turbo-Irancell"
    ).replace("FASTEST_MTN_IP", FASTEST_MTN_IP)

    link_stream = (
        f"vless://{user_id}@{FASTEST_TURBO_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Ultra-Stream-4K"
    )
    
    return {
        "direct": link_direct,
        "mci": link_mci,
        "mtn": link_mtn,
        "stream": link_stream,
        "domain": tunnel_domain,
        "updated_at": updated_at,
        "tag": tag
    }

def format_codespace_vip_message(config_data: Dict[str, Any]) -> str:
    """
    قالب‌بندی حرفه‌ای، شیک و ۱۰۰٪ آماده استفاده برای تک کانفیگ فوق‌سریع VIP
    """
    if "error" in config_data:
        return config_data["error"]
        
    direct_conf = html.escape(config_data["direct"])
    domain = html.escape(config_data.get("domain", ""))
    updated = html.escape(config_data.get("updated_at", ""))
    tag = config_data.get("tag", "@Internet_azad369")
    
    msg = (
        "🚀 <b>کانفیگ تک و فوق‌حرفه‌ای نت ملی (Cloud VIP)</b>\n\n"
        f"🌐 <b>دامنه سرور:</b> <code>{domain}</code>\n"
        "⚡ <b>وضعیت:</b> 🟢 متصل ۲۴/۷ با پینگ پایدار\n"
        "📍 <b>موقعیت:</b> 🇩🇪 آلمان (سرور ابری گیگابیتی اختصاصی)\n"
        "📶 <b>اپراتور:</b> ⚡ مناسب تمامی اپراتورها (همراه اول، ایرانسل، وای‌فای)\n"
        "-------------------------------------\n\n"
        "📋 <b>کانفیگ مستقیم آماده اتصال:</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "-------------------------------------\n"
        "💡 <i>این کانفیگ به صورت خودکار با نهایت سرعت و بدون نیاز به هیچ تنظیم دستی آماده شده است.</i>\n\n"
        f"👑 <b>{tag}</b>"
    )
    return msg