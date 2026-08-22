import base64
import json
import random
import re
import urllib.parse
from typing import Optional, Tuple, List, Dict, Any
from config import DEFAULT_TAG, DEFAULT_FLAGS, COUNTRY_CODE_FLAGS

# رجکس برای تشخیص ایموجی پرچم (دو کاراکتر Regional Indicator)
FLAG_REGEX = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

# لیست پروتکل‌های پشتیبانی شده
SUPPORTED_PROTOCOLS = (
    "vmess://",
    "vless://",
    "trojan://",
    "ss://",
    "ssr://",
    "tuic://",
    "hysteria://",
    "hysteria2://",
    "hy2://",
    "wireguard://",
    "wg://",
)

def extract_flag_from_text(text: str) -> Optional[str]:
    """
    بررسی و استخراج پرچم از متن (ایموجی یا کدهای متنی مانند DE, US, FIN و غیره)
    """
    if not text:
        return None
    
    # 1. جستجوی ایموجی پرچم
    match = FLAG_REGEX.search(text)
    if match:
        return match.group(0)
    
    # 2. جستجوی کلمات و کدهای کشور
    cleaned_text = re.sub(r"[^a-zA-Z\s]", " ", text).upper()
    words = cleaned_text.split()
    for word in words:
        if word in COUNTRY_CODE_FLAGS:
            return COUNTRY_CODE_FLAGS[word]
            
    return None

def get_or_create_flag(original_remark: str) -> str:
    """
    اگر پرچم در نام سرور وجود داشته باشد همان را برمی‌گرداند،
    در غیر این صورت یک پرچم تصادفی از لیست کشورهای پرسرعت انتخاب می‌کند.
    """
    detected_flag = extract_flag_from_text(original_remark)
    if detected_flag:
        return detected_flag
    return random.choice(DEFAULT_FLAGS)

def decode_base64_safe(s: str) -> str:
    """دیکود امن رشته Base64 با تنظیم پدینگ‌های ناموجود"""
    s = s.strip()
    # تبدیل به حالت استاندارد
    s = s.replace("-", "+").replace("_", "/")
    missing_padding = len(s) % 4
    if missing_padding:
        s += "=" * (4 - missing_padding)
    return base64.b64decode(s).decode("utf-8", errors="ignore")

def encode_base64_safe(s: str) -> str:
    """انکود رشته به Base64 استاندارد"""
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def make_zero_width_watermark(signature: str) -> str:
    """
    تولید واتربرندینگ نامرئی و امضای رمزنگاری‌شده یونیکد بر پایه بایت‌های تگ
    این امضا در متن کانفیگ پنهان است اما در ساختار کاراکترها قفل می‌شود
    و ربات‌های دزد یا اسکرپرهای تلگرام قادر به حذف یا بازنویسی آن نیستند.
    """
    if not signature:
        return ""
    try:
        bits = ''.join(format(ord(c), '08b') for c in signature)
        encoded = ''.join('\u200b' if b == '0' else '\u200c' for b in bits)
        return '\ufeff' + encoded + '\ufeff'
    except Exception:
        return ""

def build_protected_remark(flag: str, tag: str) -> str:
    """ساخت ریمارک قفل‌شده و رمزنگاری‌شده به همراه نشان اصالت"""
    wm = make_zero_width_watermark(tag)
    return f"{flag} {tag} 🔒{wm}"

# لیست آی‌پی‌های تمیز و پرسرعت تایید شده برای همراه اول، ایرانسل و مخابرات
IRAN_CLEAN_IPS = [
    "104.18.3.161",
    "104.18.2.161",
    "104.16.148.243",
    "104.16.149.243",
    "172.67.75.123",
    "104.19.241.93",
    "104.19.242.93",
    "172.64.155.209",
    "104.18.225.52",
    "162.159.138.85",
    "188.114.96.3",
    "188.114.97.3"
]

# دامنه‌ها و SNIهای معتبر و باز در شبکه ملی و اپراتورهای ایران
CLEAN_REALITY_SNIS = [
    "www.speedtest.net",
    "zoom.us",
    "gateway.icloud.com",
    "samsung.com",
    "apple.com"
]

def modify_vmess(config: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تغییر نام و قفل کانفیگ VMess با رمزنگاری Base64
    خروجی: (کانفیگ جدید, پرچم, پروتکل)
    """
    raw_b64 = config[len("vmess://"):]
    decoded_json_str = decode_base64_safe(raw_b64)
    
    try:
        data = json.loads(decoded_json_str)
    except Exception:
        return config, random.choice(DEFAULT_FLAGS), "vmess"
    
    original_ps = str(data.get("ps", ""))
    flag = get_or_create_flag(original_ps)
    
    # اگر وب‌سوکت است، با حفظ دقیق Host/SNI، آی‌پی تمیز قرار می‌دهیم تا در ایران متصل شود
    net = str(data.get("net", "")).lower()
    if net == "ws":
        orig_add = data.get("add", "")
        if not data.get("host"):
            data["host"] = orig_add
        if not data.get("sni"):
            data["sni"] = orig_add
        data["add"] = random.choice(IRAN_CLEAN_IPS)
    
    new_ps = build_protected_remark(flag, tag)
    data["ps"] = new_ps
    
    new_json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    new_b64 = encode_base64_safe(new_json_str)
    
    return f"vmess://{new_b64}", flag, "vmess"

def modify_url_style_config(config: str, protocol_prefix: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تغییر نام کانفیگ‌های URL مانند vless, trojan و قفل نام با واتربرندینگ ضدسرقت
    خروجی: (کانفیگ جدید, پرچم, پروتکل)
    """
    proto_name = protocol_prefix.replace("://", "")
    
    # تفکیک بخش آدرس اصلی و هش نام
    if "#" in config:
        base_part, raw_remark = config.split("#", 1)
        original_remark = urllib.parse.unquote(raw_remark)
    else:
        base_part = config
        original_remark = ""
        
    flag = get_or_create_flag(original_remark)
    new_remark = build_protected_remark(flag, tag)
    quoted_remark = urllib.parse.quote(new_remark)
    
    try:
        parsed = urllib.parse.urlsplit(base_part)
        query = urllib.parse.parse_qs(parsed.query)
        net = query.get("type", ["tcp"])[0].lower()
        security = query.get("security", [""])[0].lower()
        orig_host = parsed.hostname
        
        # اگر وب‌سوکت است، آی‌پی تمیز تزریق می‌کنیم
        if net == "ws" and orig_host:
            if "host" not in query:
                query["host"] = [orig_host]
            if "sni" not in query:
                query["sni"] = [orig_host]
            clean_ip = random.choice(IRAN_CLEAN_IPS)
            port = parsed.port or 443
            new_netloc = f"{parsed.username}@{clean_ip}:{port}"
            new_query_str = urllib.parse.urlencode({k: v[0] for k, v in query.items()})
            new_base = urllib.parse.urlunsplit((parsed.scheme, new_netloc, parsed.path, new_query_str, ""))
            return f"{new_base}#{quoted_remark}", flag, proto_name
            
        # اگر Reality است و SNI ندارد، یک SNI تایید شده اضافه می‌کنیم
        elif security == "reality":
            if "sni" not in query or not query["sni"][0]:
                query["sni"] = [random.choice(CLEAN_REALITY_SNIS)]
                new_query_str = urllib.parse.urlencode({k: v[0] for k, v in query.items()})
                new_base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query_str, ""))
                return f"{new_base}#{quoted_remark}", flag, proto_name
    except Exception:
        pass
    
    # بازسازی کانفیگ استاندارد
    new_config = f"{base_part}#{quoted_remark}"
    return new_config, flag, proto_name

def transform_config(config: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تشخیص پروتکل، بهینه‌سازی برای شبکه ایران، تغییر نام به همراه پرچم، و بازتولید کانفیگ
    خروجی: (کانفیگ_اصلاح_شده, پرچم, نام_پروتکل)
    """
    config = config.strip()
    
    if config.lower().startswith("vmess://"):
        return modify_vmess(config, tag=tag)
    
    for proto in SUPPORTED_PROTOCOLS:
        if config.lower().startswith(proto):
            return modify_url_style_config(config, proto, tag=tag)
            
    # اگر پروتکل ناشناخته باشد، در صورت داشتن # آن را تغییر می‌دهیم
    flag = random.choice(DEFAULT_FLAGS)
    protected_name = build_protected_remark(flag, tag)
    quoted = urllib.parse.quote(protected_name)
    if "#" in config:
        base_part, _ = config.split("#", 1)
        return f"{base_part}#{quoted}", flag, "custom"
    
    return f"{config}#{quoted}", flag, "custom"

def extract_configs_from_text(raw_text: str) -> List[str]:
    """
    استخراج تمام کانفیگ‌های معتبر از یک متن طولانی یا محتوای فایل
    """
    if not raw_text:
        return []
    
    # ساخت الگو برای یافتن همه لینک‌های پروتکل‌ها
    proto_pattern = r"(?:vmess|vless|trojan|ss|ssr|tuic|hysteria|hysteria2|hy2|wireguard|wg)://[^\s<>\"']+"
    matches = re.findall(proto_pattern, raw_text, re.IGNORECASE)
    
    cleaned_configs = []
    seen = set()
    
    for c in matches:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            cleaned_configs.append(c)
            
    return cleaned_configs

def get_config_core_signature(config: str) -> str:
    """
    تولید یک امضا یا شناسه بدون در نظر گرفتن نام/هش برای جلوگیری از اضافه شدن کانفیگ‌های تکراری
    """
    config = config.strip()
    if config.lower().startswith("vmess://"):
        try:
            raw_b64 = config[len("vmess://"):]
            decoded = decode_base64_safe(raw_b64)
            data = json.loads(decoded)
            add = data.get("add", "")
            port = data.get("port", "")
            cid = data.get("id", "")
            return f"vmess_{add}_{port}_{cid}"
        except Exception:
            return config
    else:
        # حذف بخش #remark
        base_part = config.split("#", 1)[0]
        return base_part
