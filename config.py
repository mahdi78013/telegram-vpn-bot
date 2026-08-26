import os
from pathlib import Path

# مسیر ریشه پروژه
BASE_DIR = Path(__file__).resolve().parent

# توکن ربات تلگرام (از Environment Variables خوانده می‌شود)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# آیدی عددی ادمین اصلی
ADMIN_ID = int(os.getenv("ADMIN_ID", "748538264"))

# تگ پیش‌فرض برای نام سرورها
DEFAULT_TAG = os.getenv("DEFAULT_TAG", "@muntivpn")


# مسیر فایل دیتابیس SQLite
DB_PATH = BASE_DIR / "bot_database.db"

# حداقل و حداکثر تاخیر پیش‌فرض برای ارسال خودکار (به ثانیه) - پیش‌فرض روزی ۳ بار (هر ۸ ساعت = 28800 ثانیه)
DEFAULT_MIN_DELAY = 28800   # 8 ساعت (روزی ۳ بار)
DEFAULT_MAX_DELAY = 28800   # 8 ساعت
DEFAULT_DEST_INTERVAL = 28800 # 28800 ثانیه = روزی ۳ عدد کانفیگ برای هر کانال
DEFAULT_SUBSCRIPTION_INTERVAL = 1800 # 30 دقیقه


# لیست پرچم‌های پیش‌فرض کشورهای پرسرعت برای کانفیگ‌های بدون پرچم
DEFAULT_FLAGS = [
    "🇩🇪", "🇺🇸", "🇫🇮", "🇳🇱", "🇬🇧", "🇫🇷", "🇨🇦", "🇹🇷", 
    "🇸🇪", "🇸🇬", "🇯🇵", "🇵🇱", "🇮🇹", "🇨🇭", "🇦🇹", "🇦🇺",
    "🇪🇸", "🇳🇴", "🇧🇪", "🇷🇴", "🇧🇬", "🇮🇪", "🇺🇦", "🇰🇷"
]

# نگاشت کدهای کشور یا نام‌ها به ایموجی پرچم
COUNTRY_CODE_FLAGS = {
    "DE": "🇩🇪", "GERMANY": "🇩🇪", "GER": "🇩🇪",
    "US": "🇺🇸", "USA": "🇺🇸", "UNITED STATES": "🇺🇸",
    "FI": "🇫🇮", "FINLAND": "🇫🇮",
    "NL": "🇳🇱", "NETHERLANDS": "🇳🇱", "HOL": "🇳🇱",
    "GB": "🇬🇧", "UK": "🇬🇧", "UNITED KINGDOM": "🇬🇧", "ENG": "🇬🇧",
    "FR": "🇫🇷", "FRANCE": "🇫🇷",
    "CA": "🇨🇦", "CANADA": "🇨🇦",
    "TR": "🇹🇷", "TURKEY": "🇹🇷", "TURKIYE": "🇹🇷",
    "SE": "🇸🇪", "SWEDEN": "🇸🇪",
    "SG": "🇸🇬", "SINGAPORE": "🇸🇬",
    "JP": "🇯🇵", "JAPAN": "🇯🇵",
    "PL": "🇵🇱", "POLAND": "🇵🇱",
    "IT": "🇮🇹", "ITALY": "🇮🇹",
    "CH": "🇨🇭", "SWITZERLAND": "🇨🇭",
    "AT": "🇦🇹", "AUSTRIA": "🇦🇹",
    "AU": "🇦🇺", "AUSTRALIA": "🇦🇺",
    "ES": "🇪🇸", "SPAIN": "🇪🇸",
    "NO": "🇳🇴", "NORWAY": "🇳🇴",
    "BE": "🇧🇪", "BELGIUM": "🇧🇪",
    "RO": "🇷🇴", "ROMANIA": "🇷🇴",
    "BG": "🇧🇬", "BULGARIA": "🇧🇬",
    "IE": "🇮🇪", "IRELAND": "🇮🇪",
    "UA": "🇺🇦", "UKRAINE": "🇺🇦",
    "KR": "🇰🇷", "KOREA": "🇰🇷", "SOUTH KOREA": "🇰🇷",
    "RU": "🇷🇺", "RUSSIA": "🇷🇺",
    "AE": "🇦🇪", "UAE": "🇦🇪", "DUBAI": "🇦🇪",
}

# ==============================================================================
# 🚀 تنظیمات موتور دریافت کانفیگ پرسرعت ابری (Config Delivery Engine v2)
# ==============================================================================

# تنظیمات Timeout و مقاومت در برابر قطعی
ENGINE_CONNECT_TIMEOUT = float(os.getenv("ENGINE_CONNECT_TIMEOUT", "2.0")) # ثانیه (Connect کوتاه)
ENGINE_READ_TIMEOUT = float(os.getenv("ENGINE_READ_TIMEOUT", "3.0"))       # ثانیه (Read مستقل)
ENGINE_MAX_RETRIES = int(os.getenv("ENGINE_MAX_RETRIES", "3"))             # حداکثر تلاش مجدد
ENGINE_RETRY_BUDGET_WINDOW = 60                                            # پنجره بودجه تلاش مجدد (ثانیه)

# وزن‌های الگوریتم امتیازدهی تطبیقی (Adaptive Scoring Weights)
SCORE_WEIGHT_LATENCY = float(os.getenv("SCORE_WEIGHT_LATENCY", "0.30"))
SCORE_WEIGHT_STABILITY = float(os.getenv("SCORE_WEIGHT_STABILITY", "0.20"))
SCORE_WEIGHT_SUCCESS_RATE = float(os.getenv("SCORE_WEIGHT_SUCCESS_RATE", "0.20"))
SCORE_WEIGHT_PACKET_LOSS = float(os.getenv("SCORE_WEIGHT_PACKET_LOSS", "0.10"))
SCORE_WEIGHT_RESPONSE_TIME = float(os.getenv("SCORE_WEIGHT_RESPONSE_TIME", "0.10"))
SCORE_WEIGHT_HISTORICAL = float(os.getenv("SCORE_WEIGHT_HISTORICAL", "0.05"))
SCORE_WEIGHT_REGIONAL = float(os.getenv("SCORE_WEIGHT_REGIONAL", "0.05"))

# تنظیمات مدارشکن (Circuit Breaker)
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))         # آستانه خطای متوالی برای باز شدن مدار
CB_COOLDOWN_SECONDS = int(os.getenv("CB_COOLDOWN_SECONDS", "60"))          # زمان خنک‌سازی پیش از تست مجدد
CB_HALF_OPEN_ATTEMPTS = int(os.getenv("CB_HALF_OPEN_ATTEMPTS", "1"))

# تنظیمات ضد نوسان (Anti-Flapping / Hysteresis)
HYSTERESIS_THRESHOLD = float(os.getenv("HYSTERESIS_THRESHOLD", "0.15"))    # ۱۵٪ بهبود الزامی برای تغییر نود اصلی

# زمان انقضای لایه‌های کش چندسطحی (Cache TTLs in Seconds)
CACHE_L1_TTL = int(os.getenv("CACHE_L1_TTL", "300"))                       # حافظه L1: ۵ دقیقه
CACHE_L2_TTL = int(os.getenv("CACHE_L2_TTL", "1800"))                      # حافظه برنامه L2: ۳۰ دقیقه
CACHE_L3_TTL = int(os.getenv("CACHE_L3_TTL", "86400"))                     # پایگاه داده L3: ۲۴ ساعت

# کاوش موازی و کنترل نرخ (Parallel Probing & Rate Limiting)
MAX_PARALLEL_PROBES = int(os.getenv("MAX_PARALLEL_PROBES", "8"))
PROBE_RATE_LIMIT = float(os.getenv("PROBE_RATE_LIMIT", "1.5"))

# فواصل زمانی پایش و رفرش خودکار (Health Monitor & Auto-Refresh)
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))      # پایش سلامت هر ۳۰ ثانیه
SOURCE_REFRESH_INTERVAL = int(os.getenv("SOURCE_REFRESH_INTERVAL", "1800"))# رفرش مخازن هر ۳۰ دقیقه
OFFLINE_RECHECK_INTERVAL = int(os.getenv("OFFLINE_RECHECK_INTERVAL", "300"))# تست مجدد نودهای قطع هر ۵ دقیقه
