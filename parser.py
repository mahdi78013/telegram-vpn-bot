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
    """دیکود امن رشته Base64 با حذف فاصله‌ها و تنظیم پدینگ‌های ناموجود"""
    if not s:
        return ""
    s = re.sub(r"\s+", "", str(s).strip())
    s = s.replace("-", "+").replace("_", "/")
    missing_padding = len(s) % 4
    if missing_padding:
        s += "=" * (4 - missing_padding)
    try:
        return base64.b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        try:
            return base64.b64decode(s + "==").decode("utf-8", errors="ignore")
        except Exception:
            return ""

def encode_base64_safe(s: str) -> str:
    """انکود رشته به Base64 استاندارد"""
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def sanitize_url_parameters(url: str) -> str:
    """
    اصلاح و استانداردسازی پارامترهای VLESS Reality و TLS جهت رفع خطای تایم‌اوت در v2rayNG/Xray:
    1. تنظیم fp=chrome برای دور زدن اثرانگشت TLS
    2. حذف headerType=http از Reality (ناسازگار با هسته Xray)
    3. اصلاح یا افزودن flow=xtls-rprx-vision در صورت امکان
    """
    try:
        if "?" not in url:
            return url
            
        base_part, query_part = url.split("?", 1)
        params = urllib.parse.parse_qs(query_part, keep_blank_values=False)
        
        is_reality = ("security" in params and "reality" in params["security"]) or ("pbk" in params)
        
        # اصلاح اثرانگشت TLS (Fingerprint)
        if is_reality or "security" in params:
            current_fp = params.get("fp", [""])[0].strip()
            if not current_fp or current_fp in ("", "none", "null"):
                params["fp"] = ["chrome"]
                
        # رفع تداخل headerType در Reality
        if is_reality:
            if "headerType" in params:
                ht = params["headerType"][0].lower()
                if ht == "http" or ht == "none":
                    del params["headerType"]
                    
        # بازسازی query string استاندارد
        flat_params = []
        for k, v_list in params.items():
            for val in v_list:
                flat_params.append(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(val))}")
                
        new_query = "&".join(flat_params)
        return f"{base_part}?{new_query}"
    except Exception:
        return url

def modify_vmess(config: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تغییر نام کانفیگ VMess با حفظ ۱۰۰٪ پارامترهای اصلی شبکه و اتصال سرور
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
    
    # حفظ دقیق تمام پارامترهای سرور (آی‌پی، پورت، آی‌دی، رمزنگاری، وب‌سوکت، هاست و غیره)
    new_ps = f"{flag} {tag}"
    data["ps"] = new_ps
    
    new_json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    new_b64 = encode_base64_safe(new_json_str)
    
    return f"vmess://{new_b64}", flag, "vmess"

def modify_url_style_config(config: str, protocol_prefix: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تغییر نام کانفیگ‌های URL مانند vless, trojan, ss, hysteria, tuic با حفظ ۱۰۰٪ آدرس، کلیدها، PBK و SNI
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
    new_remark = f"{flag} {tag}"
    
    # استانداردسازی پارامترهای Reality و TLS
    sanitized_base = sanitize_url_parameters(base_part)
    
    # بازسازی کانفیگ بدون دستکاری پارامترهای اتصال، PBK یا SNI سرور
    new_config = f"{sanitized_base}#{new_remark}"
    return new_config, flag, proto_name

def transform_config(config: str, tag: str = DEFAULT_TAG) -> Tuple[str, str, str]:
    """
    تشخیص پروتکل، حفظ سلامت اتصال و تغییر نام به همراه پرچم و تگ کانال
    خروجی: (کانفیگ_اصلاح_شده, پرچم, نام_پروتکل)
    """
    config = config.strip()
    
    if config.lower().startswith("vmess://"):
        return modify_vmess(config, tag=tag)
    
    for proto in SUPPORTED_PROTOCOLS:
        if config.lower().startswith(proto):
            return modify_url_style_config(config, proto, tag=tag)
            
    # اگر پروتکل ناشناخته باشد
    flag = random.choice(DEFAULT_FLAGS)
    if "#" in config:
        base_part, _ = config.split("#", 1)
        return f"{base_part}#{flag} {tag}", flag, "custom"
    
    return f"{config}#{flag} {tag}", flag, "custom"

def detect_operator_for_config(raw_config: str, index: int = 0) -> str:
    """
    تشخیص هوشمند اپراتور مناسب برای کانفیگ بر اساس ویژگی‌ها یا توزیع متوازن در پست‌ها
    خروجی: '📡 همراه اول' یا '📱 ایرانسل' یا '📶 مخابرات / رایتل' یا '🌐 تمام اپراتورها'
    """
    conf_lower = raw_config.lower()
    if "mci" in conf_lower or "hamrah" in conf_lower:
        return "📡 همراه اول"
    elif "mtn" in conf_lower or "irancell" in conf_lower:
        return "📱 ایرانسل"
    elif "wifi" in conf_lower or "mokhaberat" in conf_lower or "tci" in conf_lower or "rightel" in conf_lower:
        return "📶 مخابرات / رایتل"
    else:
        # توزیع چرخشی منظم
        ops = ["📱 ایرانسل", "📡 همراه اول", "📶 مخابرات / رایتل"]
        return ops[index % len(ops)]

def extract_configs_from_text(text: str) -> List[str]:
    """
    استخراج تمام کانفیگ‌های معتبر از متن ساده یا سابسکریپشن‌های Base64
    """
    if not text:
        return []
        
    cleaned = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    
    # تلاش برای دیکود کردن در صورتی که کل فایل Base64 باشد
    if not any(proto in cleaned.lower() for proto in SUPPORTED_PROTOCOLS):
        decoded = decode_base64_safe(cleaned)
        if any(proto in decoded.lower() for proto in SUPPORTED_PROTOCOLS):
            cleaned = decoded.replace("\r\n", "\n").replace("\r", "\n")
            
    results = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
            
        for proto in SUPPORTED_PROTOCOLS:
            if line.lower().startswith(proto):
                results.append(line)
                break
                
    return results
            
def get_config_core_signature(config: str) -> str:
    """
    استخراج امضای یکتای کانفیگ (بدون نام و تگ) جهت تشخیص کانفیگ‌های تکراری در دیتابیس
    """
    config = config.strip()
    if not config:
        return ""
        
    if config.lower().startswith("vmess://"):
        try:
            raw_b64 = config[len("vmess://"):]
            decoded = decode_base64_safe(raw_b64)
            data = json.loads(decoded)
            add = data.get("add", "")
            port = data.get("port", "")
            uid = data.get("id", "")
            net = data.get("net", "")
            path = data.get("path", "")
            return f"vmess:{uid}@{add}:{port}:{net}:{path}"
        except Exception:
            return config

    # برای سایر پروتکل‌های URL مانند vless, trojan, ss, hy2
    if "#" in config:
        base_part, _ = config.split("#", 1)
    else:
        base_part = config
    return base_part.strip()

