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
    # تنها نام کانفیگ به همراه پرچم و تگ کانال تنظیم می‌شود
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
    
    # بازسازی کانفیگ بدون دستکاری پارامترهای اتصال، PBK یا SNI سرور
    new_config = f"{base_part}#{new_remark}"
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
        
    operators_cycle = ["📡 همراه اول", "📱 ایرانسل", "🌐 تمام اپراتورها", "📶 مخابرات / رایتل"]
    return operators_cycle[index % len(operators_cycle)]

def extract_configs_from_text(raw_text: str) -> List[str]:
    """
    استخراج جامع و هوشمند تمام کانفیگ‌های معتبر از انواع فرمت‌های متنی و سابسکریپشن:
    1. استخراج مستقیم با ریجکس
    2. دیکود چندمرحله‌ای کل متن Base64
    3. بررسی خط‌به‌خط رشته‌های Base64
    """
    if not raw_text:
        return []
    
    proto_pattern = r"(?:vmess|vless|trojan|ss|ssr|tuic|hysteria|hysteria2|hy2|wireguard|wg)://[^\s<>\"']+"
    cleaned_configs: List[str] = []
    seen = set()
    
    def add_match(c: str):
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            cleaned_configs.append(c)
            
    # مرحله ۱: استخراج مستقیم از متن خام
    matches = re.findall(proto_pattern, raw_text, re.IGNORECASE)
    for m in matches:
        add_match(m)
        
    # مرحله ۲: دیکود کامل متن اگر Base64 باشد
    if not cleaned_configs or len(cleaned_configs) < 5:
        decoded_full = decode_base64_safe(raw_text)
        if decoded_full:
            m_full = re.findall(proto_pattern, decoded_full, re.IGNORECASE)
            for m in m_full:
                add_match(m)
                
    # مرحله ۳: بررسی خط‌به‌خط برای لینک‌ها یا خطوط تک‌خطی Base64
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(line.lower().startswith(p) for p in ("vmess://", "vless://", "trojan://", "ss://", "tuic://", "hysteria://", "hy2://")):
            add_match(line)
        elif not line.startswith("http://") and not line.startswith("https://") and len(line) > 20:
            dec = decode_base64_safe(line)
            if dec:
                m_line = re.findall(proto_pattern, dec, re.IGNORECASE)
                for m in m_line:
                    add_match(m)
                    
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
        base_part = config.split("#", 1)[0]
        return base_part
