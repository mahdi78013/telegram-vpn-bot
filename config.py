import os
from pathlib import Path

# مسیر ریشه پروژه
BASE_DIR = Path(__file__).resolve().parent

# توکن ربات تلگرام (از Environment Variables خوانده می‌شود)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# آیدی عددی ادمین اصلی
ADMIN_ID = int(os.getenv("ADMIN_ID", "748538264"))

# تگ پیش‌فرض برای نام سرورها
DEFAULT_TAG = os.getenv("DEFAULT_TAG", "@Internet_azad369")

# مسیر فایل دیتابیس SQLite
DB_PATH = BASE_DIR / "bot_database.db"

# حداقل و حداکثر تاخیر پیش‌فرض برای ارسال خودکار (به ثانیه) - پیش‌فرض روزی ۳ بار (هر ۸ ساعت = 28800 ثانیه)
DEFAULT_MIN_DELAY = 28800   # 8 ساعت (روزی ۳ بار)
DEFAULT_MAX_DELAY = 28800   # 8 ساعت
DEFAULT_DEST_INTERVAL = 28800 # 28800 ثانیه = روزی ۳ عدد کانفیگ برای هر کانال

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
