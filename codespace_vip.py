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

async def get_latest_codespace_config(tag: str = "@Internet_azad369") -> Dict[str, Any]:
    """
    دریافت خودکار و زنده آخرین کانفیگ فعال سرور Codespaces
    """
    config_data = None
    
    # 1. تلاش برای خواندن مستقیم از فایل محلی داخل ریپازیتوری
    if os.path.exists(LOCAL_LIVE_PATH):
        try:
            with open(LOCAL_LIVE_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error reading local live_vip.json: {e}")
            
    # 2. دریافت آنلاین از GitHub API
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
    
    link_direct = (
        f"vless://{user_id}@{tunnel_domain}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws"
        f"#⚡VIP-Codespace-Live"
    )
    
    link_mci = (
        f"vless://{user_id}@104.18.2.1:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Codespace-MCI"
    )
    
    link_mtn = (
        f"vless://{user_id}@172.67.180.1:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Codespace-Irancell"
    )
    
    return {
        "direct": link_direct,
        "mci": link_mci,
        "mtn": link_mtn,
        "domain": tunnel_domain,
        "updated_at": updated_at,
        "tag": tag
    }

def format_codespace_vip_message(config_data: Dict[str, Any]) -> str:
    """
    قالب‌بندی خوانا و آماده کپی برای پیوی ادمین
    """
    if "error" in config_data:
        return config_data["error"]
        
    direct_conf = html.escape(config_data["direct"])
    mci_conf = html.escape(config_data["mci"])
    mtn_conf = html.escape(config_data["mtn"])
    domain = html.escape(config_data.get("domain", ""))
    updated = html.escape(config_data.get("updated_at", ""))
    tag = config_data.get("tag", "@Internet_azad369")
    
    msg = (
        "🚀 <b>کانفیگ زنده نت ملی (Codespace VIP)</b>\n\n"
        f"🌐 <b>دامنه فعال تونل:</b> <code>{domain}</code>\n"
        f"⏱️ <b>زمان همگام‌سازی:</b> <code>{updated}</code>\n"
        "📍 <b>موقعیت:</b> 🇩🇪 آلمان (سرور ابری گیگابیتی اختصاصی)\n"
        "⚡ <b>پروتکل:</b> <code>VLESS-WebSocket-TLS</code> (بدون فرگمنت)\n"
        "-------------------------------------\n\n"
        "📋 <b>کانفیگ مستقیم (همه اپراتورها):</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "📱 <b>کانفیگ بهینه‌شده همراه اول:</b>\n"
        f"<pre><code class=\"language-copy\">{mci_conf}</code></pre>\n\n"
        "📶 <b>کانفیگ بهینه‌شده ایرانسل و رایتل:</b>\n"
        f"<pre><code class=\"language-copy\">{mtn_conf}</code></pre>\n\n"
        "-------------------------------------\n"
        "💡 <i>این کانفیگ به صورت خودکار از سرور فعال Codespaces شما دریافت شده است.</i>\n\n"
        f"👑 <b>{tag}</b>"
    )
    return msg