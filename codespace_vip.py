import html
import uuid
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger("CodespaceVIP")

# شناسه پیش‌فرض و پارامترهای پروتکل
DEFAULT_UUID = "f12abdbd-23a8-414b-a89e-c447be5ba57d"
DEFAULT_WSPATH = "/vless-ws"

async def get_latest_codespace_config(tag: str = "@Internet_azad369") -> Dict[str, Any]:
    """
    تولید و دریافت تازه ترین کانفیگ سرورهای ابری Codespaces
    مخصوص دور زدن اختلالات و فیلترینگ شدید نت ملی
    """
    user_id = DEFAULT_UUID
    
    # دامنه‌های فعال و تونل‌های ضد فیلتر
    tunnel_domain = "beam-spell-cameras-webmasters.trycloudflare.com"
    
    # کانفیگ مستقیم پرسرعت
    link_direct = (
        f"vless://{user_id}@{tunnel_domain}:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws"
        f"#⚡VIP-Codespace-NationalNet"
    )
    
    # کانفیگ همراه اول با آی‌پی تمیز
    link_mci = (
        f"vless://{user_id}@104.18.2.1:443?"
        f"encryption=none&security=tls&type=ws&host={tunnel_domain}&path=%2Fvless-ws&sni={tunnel_domain}"
        f"#⚡VIP-Codespace-MCI"
    )
    
    # کانفیگ ایرانسل با آی‌پی تمیز
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
        "tag": tag
    }

def format_codespace_vip_message(config_data: Dict[str, Any]) -> str:
    """
    قالب‌بندی فوق‌العاده شیک و خوانا مخصوص پیوی ادمین
    """
    direct_conf = html.escape(config_data["direct"])
    mci_conf = html.escape(config_data["mci"])
    mtn_conf = html.escape(config_data["mtn"])
    tag = config_data.get("tag", "@Internet_azad369")
    
    msg = (
        "🚀 <b>کانفیگ اختصاصی نت ملی (Codespace VIP)</b>\n\n"
        "📍 <b>موقعیت:</b> 🇩🇪 آلمان (سرور ابری گیگابیتی ضد فیلتر)\n"
        "⚡ <b>پروتکل:</b> <code>VLESS-WebSocket-TLS</code> (بدون نیاز به فرگمنت)\n"
        "🛡️ <b>وضعیت اتصال:</b> 🟢 فعال و بدون محدودیت\n"
        "-------------------------------------\n\n"
        "📋 <b>کانفیگ مستقیم (همه اپراتورها):</b>\n"
        f"<pre><code class=\"language-copy\">{direct_conf}</code></pre>\n\n"
        "📱 <b>کانفیگ بهینه‌شده همراه اول:</b>\n"
        f"<pre><code class=\"language-copy\">{mci_conf}</code></pre>\n\n"
        "📶 <b>کانفیگ بهینه‌شده ایرانسل و رایتل:</b>\n"
        f"<pre><code class=\"language-copy\">{mtn_conf}</code></pre>\n\n"
        "-------------------------------------\n"
        "💡 <i>این کانفیگ مستقیماً از سرور ابری اختصاصی شما تولید شده و فقط برای شما ارسال گردیده است.</i>\n\n"
        f"👑 <b>{tag}</b>"
    )
    return msg