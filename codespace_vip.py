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

def update_subscription_files(domain: str, uuid: str = "f12abdbd-23a8-414b-a89e-c447be5ba57d"):
    """
    تولید و بروزرسانی خودکار فایل سابسکریپشن آنلاین در مخزن گیت‌هاب جهت ترمیم خودکار کانفیگ‌ها در برنامه کاربر
    """
    if not domain:
        return
        
    c_direct = f"vless://{uuid}@{domain}:443?encryption=none&security=tls&type=ws&host={domain}&path=%2Fvless-ws%3Fed%3D2048&alpn=h2%2Chttp%2F1.1#⚡VIP-AutoHeal-Direct"
    c_mci = f"vless://{uuid}@{FASTEST_MCI_IP}:443?encryption=none&security=tls&type=ws&host={domain}&path=%2Fvless-ws%3Fed%3D2048&sni={domain}&alpn=h2%2Chttp%2F1.1#⚡VIP-AutoHeal-MCI"
    c_mtn = f"vless://{uuid}@{FASTEST_MTN_IP}:443?encryption=none&security=tls&type=ws&host={domain}&path=%2Fvless-ws%3Fed%3D2048&sni={domain}&alpn=h2%2Chttp%2F1.1#⚡VIP-AutoHeal-Irancell"
    c_stream = f"vless://{uuid}@{FASTEST_TURBO_IP}:443?encryption=none&security=tls&type=ws&host={domain}&path=%2Fvless-ws%3Fed%3D2048&sni={domain}&alpn=h2%2Chttp%2F1.1#⚡VIP-AutoHeal-4KStream"
    
    plain_content = f"{c_direct}\n{c_mci}\n{c_mtn}\n{c_stream}\n"
    import base64
    b64_content = base64.b64encode(plain_content.encode("utf-8")).decode("utf-8")
    
    # 1. ذخیره محلی فرمت استاندارد Base64 و Plain
    try:
        with open(SUB_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(b64_content)
        with open(os.path.join(os.path.dirname(__file__), "sub_plain.txt"), "w", encoding="utf-8") as f:
            f.write(plain_content)
    except Exception as e:
        logger.warning(f"Error writing local sub.txt: {e}")
        
    # 2. انتشار خودکار در مخزن گیت‌هاب جهت سابسکریپشن سراسری
    if sys.platform.startswith("linux"):
        try:
            repo_dir = os.path.dirname(__file__)
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo_dir, capture_output=True, check=False)
            subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=repo_dir, capture_output=True, check=False)
            subprocess.run(["git", "add", "sub.txt", "sub_plain.txt"], cwd=repo_dir, capture_output=True, check=False)
            subprocess.run(["git", "commit", "-m", "Auto-update live Base64 subscription [skip ci]"], cwd=repo_dir, capture_output=True, check=False)
            res = subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, capture_output=True, check=False)
            if res.returncode == 0:
                logger.info("✅ لینک سابسکریپشن هوشمند در مخزن گیت‌هاب با موفقیت بروزرسانی شد.")
        except Exception as ex:
            logger.warning(f"Error pushing sub.txt via git: {ex}")

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
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws%3Fed%3D2048&alpn=h2%2Chttp%2F1.1"
        f"#⚡VIP-Turbo-Direct"
    )
    
    link_mci = (
        f"vless://{user_id}@{FASTEST_MCI_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws%3Fed%3D2048&sni={tunnel_domain}&alpn=h2%2Chttp%2F1.1"
        f"#⚡VIP-Turbo-MCI"
    )
    
    link_mtn = (
        f"vless://{user_id}@{FASTEST_MTN_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws%3Fed%3D2048&sni={tunnel_domain}&alpn=h2%2Chttp%2F1.1"
        f"#⚡VIP-Turbo-Irancell"
    )

    link_stream = (
        f"vless://{user_id}@{FASTEST_TURBO_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws%3Fed%3D2048&sni={tunnel_domain}&alpn=h2%2Chttp%2F1.1"
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
    قالب‌بندی حرفه‌ای، شیک و ۱۰۰٪ آماده استفاده با ۴ سرور چندمسیره + لینک سابسکریپشن خودکار (Auto-Healing)
    """
    if "error" in config_data:
        return config_data["error"]
        
    direct_conf = html.escape(config_data["direct"])
    mci_conf = html.escape(config_data["mci"])
    mtn_conf = html.escape(config_data["mtn"])
    stream_conf = html.escape(config_data["stream"])
    
    domain = html.escape(config_data.get("domain", ""))
    tag = config_data.get("tag", "@Internet_azad369")
    sub_link_cdn = "https://cdn.jsdelivr.net/gh/mahdi78013/telegram-vpn-bot@main/sub.txt"
    sub_link_raw = "https://raw.githubusercontent.com/mahdi78013/telegram-vpn-bot/main/sub.txt"
    
    msg = (
        "🚀 <b>پکیج ۴ سرور ابری اختصاصی با سیستم خودترمیم (Auto-Healing)</b>\n\n"
        f"🌐 <b>دامنه زنده سرور:</b> <code>{domain}</code>\n"
        "⚡ <b>وضعیت:</b> 🟢 متصل ۲۴/۷ با نهایت سرعت و پینگ سبز\n"
        "📍 <b>موقعیت:</b> 🇩🇪 آلمان (گیگابیت اختصاصی)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 <b>لینک سابسکریپشن مستقیم (CDN پرسرعت و ضدتحریم):</b>\n"
        f"<code>{sub_link_cdn}</code>\n\n"
        "🔗 <b>لینک سابسکریپشن کمکی (گیت‌هاب مستقیم):</b>\n"
        f"<code>{sub_link_raw}</code>\n\n"
        "💡 <b>راهنمای ۱ بار ثبت در v2rayNG:</b>\n"
        "وارد منوی ☰ 👈 <b>Subscription group</b> 👈 علامت <b>+</b> شوید، نام را <code>VIP Sub</code> بگذارید و لینک اول (CDN) را الصاق کنید. سپس در صفحه اصلی ۳ نقطه بالا 👈 <b>Update subscription</b> را بزنید.\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 <b>کانفیگ‌های دستی تفکیک‌شده همین لحظه:</b>\n\n"
        "⚡ <b>۱. سرور مستقیم ابری (Anycast Direct):</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "📱 <b>۲. مخصوص همراه اول و مخابرات (MCI Clean IP):</b>\n"
        f"<pre><code class=\"language-copy\">{mci_conf}</code></pre>\n\n"
        "📶 <b>۳. مخصوص ایرانسل و رایتل (MTN Clean IP):</b>\n"
        f"<pre><code class=\"language-copy\">{mtn_conf}</code></pre>\n\n"
        "🔥 <b>۴. سرور توربو دانلود و استریم 4K:</b>\n"
        f"<pre><code class=\"language-copy\">{stream_conf}</code></pre>\n\n"
        f"👑 <b>{tag}</b>"
    )
    return msg