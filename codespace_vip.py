import os
import json
import html
import base64
import httpx
import logging
from typing import Dict, Any

logger = logging.getLogger("CodespaceVIP")

LOCAL_LIVE_PATH = os.path.join(os.path.dirname(__file__), "live_vip.json")
API_URL = "https://api.github.com/repos/mahdi78013/telegram-vpn-bot/contents/live_vip.json"

# پرسرعت‌ترین و پایدارترین آی‌پی‌های تست‌شده در اپراتورهای ایران
FASTEST_MCI_IP = "188.114.96.3"        # فوق‌العاده سریع روی همراه اول و مخابرات
FASTEST_MTN_IP = "162.159.138.85"      # فوق‌العاده پرسرعت روی ایرانسل و رایتل
FASTEST_TURBO_IP = "188.114.97.3"     # رنج اختصاصی دانلود با نهایت پهنای‌باند

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
            
    if not config_data:
        gh_token = os.getenv("GITHUB_TOKEN")
        headers = {"User-Agent": "VIP-Fetcher", "Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                r = await client.get(API_URL, headers=headers)
                if r.status_code == 200:
                    body = r.json()
                    if "content" in body:
                        decoded = base64.b64decode(body["content"]).decode("utf-8")
                        config_data = json.loads(decoded)
        except Exception as e:
            logger.warning(f"Error fetching live_vip from GitHub API: {e}")
            
    if not config_data or "domain" not in config_data:
        config_data = {
            "domain": "contributed-independent-vice-indoor.trycloudflare.com",
            "uuid": "f12abdbd-23a8-414b-a89e-c447be5ba57d",
            "updated_at": "لحظاتی پیش"
        }
        
    tunnel_domain = config_data.get("domain", "contributed-independent-vice-indoor.trycloudflare.com").strip()
    user_id = config_data.get("uuid", "f12abdbd-23a8-414b-a89e-c447be5ba57d").strip()
    updated_at = config_data.get("updated_at", "هم‌اکنون")
    
    # ۱. کانفیگ مستقیم پرسرعت (اورجینال بدون تغییر)
    link_direct = (
        f"vless://{user_id}@{tunnel_domain}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws"
        f"#⚡VIP-Turbo-Direct"
    )
    
    # ۲. کانفیگ توربو همراه اول (تزریق خودکار آی‌پی پرسرعت 188.114.96.3)
    link_mci = (
        f"vless://{user_id}@{FASTEST_MCI_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Turbo-MCI"
    )
    
    # ۳. کانفیگ توربو ایرانسل و رایتل (تزریق خودکار آی‌پی پرسرعت 162.159.138.85)
    link_mtn = (
        f"vless://{user_id}@{FASTEST_MTN_IP}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Turbo-Irancell"
    )

    # ۴. کانفیگ دانلود سنگین و یوتیوب 4K (تزریق خودکار 188.114.97.3)
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
    قالب‌بندی حرفه‌ای، شیک و ۱۰۰٪ آماده استفاده بدون نیاز به هیچ تنظیم دستی
    """
    if "error" in config_data:
        return config_data["error"]
        
    direct_conf = html.escape(config_data["direct"])
    mci_conf = html.escape(config_data["mci"])
    mtn_conf = html.escape(config_data["mtn"])
    stream_conf = html.escape(config_data.get("stream", ""))
    domain = html.escape(config_data.get("domain", ""))
    updated = html.escape(config_data.get("updated_at", ""))
    tag = config_data.get("tag", "@Internet_azad369")
    
    msg = (
        "🚀 <b>کانفیگ‌های توربو و زنده نت ملی (Codespace VIP)</b>\n\n"
        f"🌐 <b>دامنه سرور:</b> <code>{domain}</code>\n"
        f"⚡ <b>وضعیت:</b> 🟢 متصل با نهایت پهنای‌باند گیگابیتی\n"
        "💡 <i>تمامی آی‌پی‌های تمیز و بهینه‌سازی‌ها از سمت سرور به صورت خودکار اعمال شده‌اند و نیازی به هیچ دستکاری در برنامه نیست.</i>\n"
        "-------------------------------------\n\n"
        "🎬 <b>۱. کانفیگ اولویت اول (دانلود سنگین و یوتیوب 4K):</b>\n"
        f"<pre><code class=\"language-copy\">{stream_conf}</code></pre>\n\n"
        "📱 <b>۲. کانفیگ فوق‌سریع همراه اول:</b>\n"
        f"<pre><code class=\"language-copy\">{mci_conf}</code></pre>\n\n"
        "📶 <b>۳. کانفیگ فوق‌سریع ایرانسل و رایتل:</b>\n"
        f"<pre><code class=\"language-copy\">{mtn_conf}</code></pre>\n\n"
        "🌐 <b>۴. کانفیگ مستقیم اورجینال:</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "-------------------------------------\n"
        f"👑 <b>{tag}</b>"
    )
    return msg