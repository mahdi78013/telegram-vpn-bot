import asyncio
import io
import logging
import os
import sys
from typing import Dict, Any

# اطمینان از قرارگیری مسیر پروژه در ماژول‌های قابل دسترس
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_ID, DEFAULT_TAG, DEFAULT_MIN_DELAY, DEFAULT_MAX_DELAY
from database import (
    init_db,
    get_setting,
    set_setting,
    get_all_settings,
    get_stats,
    add_configs_bulk,
    clear_all_configs,
    export_all_configs,
    delete_dead_configs,
    get_configs_for_health_check,
    update_configs_ping_bulk,
    get_active_source_urls,
)
from parser import extract_configs_from_text
from scheduler import (
    start_scheduler,
    stop_scheduler,
    send_single_post,
    get_next_post_countdown,
)
from tester import ping_configs_batch
from harvester import harvest_and_store_online_configs

# تنظیمات لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AutoVpnBot")

# وضعیت‌های ConversationHandler
(
    STATE_WAIT_CONFIGS,
    STATE_WAIT_CHANNEL,
    STATE_WAIT_MIN_DELAY,
    STATE_WAIT_MAX_DELAY,
    STATE_WAIT_TAG,
    STATE_WAIT_HEADER,
    STATE_WAIT_FOOTER,
    STATE_WAIT_SUB_LINK,
) = range(8)

def is_admin(user_id: int) -> bool:
    """بررسی اینکه آیا کاربر ادمین است یا خیر"""
    return user_id == ADMIN_ID

def build_main_keyboard(auto_send_on: bool, auto_harvest_on: bool = False, batch_size: str = "3") -> InlineKeyboardMarkup:
    """ساخت کیبورد منوی اصلی پنل مدیریت (حالت تمام خودکار، ابری و ارسال گروهی)"""
    toggle_send_text = "🟢 ارسال کانال: [روشن]" if auto_send_on else "🔴 ارسال کانال: [خاموش]"
    toggle_harvest_text = "🟢 دریافت ابری: [روشن]" if auto_harvest_on else "🔴 دریافت ابری: [خاموش]"
    
    keyboard = [
        [
            InlineKeyboardButton("📊 آمار و وضعیت سیستم", callback_data="btn_stats"),
            InlineKeyboardButton("⚡ " + toggle_send_text, callback_data="btn_toggle_auto"),
        ],
        [
            InlineKeyboardButton("🌐 دریافت فوری سرورهای آنلاین", callback_data="btn_harvest_now"),
            InlineKeyboardButton("🔄 " + toggle_harvest_text, callback_data="btn_toggle_auto_harvest"),
        ],
        [
            InlineKeyboardButton("📤 ارسال تستی برای من (ادمین)", callback_data="btn_test_send_admin"),
            InlineKeyboardButton("📤 ارسال تستی به کانال", callback_data="btn_test_send"),
        ],
        [
            InlineKeyboardButton(f"📦 ارسال گروهی: [{batch_size} سرور در هر پست]", callback_data="btn_cycle_batch_size"),
            InlineKeyboardButton("🔍 تست پینگ فوری", callback_data="btn_ping_all"),
        ],
        [
            InlineKeyboardButton("🧹 حذف سرورهای قطع/مرده", callback_data="btn_clear_dead"),
            InlineKeyboardButton("📢 تنظیم کانال مقصد", callback_data="btn_set_channel"),
        ],
        [
            InlineKeyboardButton("⏱️ تنظیم زمان‌بندی ارسال", callback_data="btn_set_delay"),
            InlineKeyboardButton("🏷️ تغییر تگ و نام کانفیگ", callback_data="btn_set_tag"),
        ],
        [
            InlineKeyboardButton("✍️ ویرایش متن هدر/فوتر", callback_data="btn_edit_text"),
            InlineKeyboardButton("🗑️ پاکسازی کل سرورها", callback_data="btn_clear_configs"),
        ],
        [
            InlineKeyboardButton("🔄 بروزرسانی منو", callback_data="btn_main_menu"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_cancel_keyboard() -> InlineKeyboardMarkup:
    """دکمه انصراف و بازگشت"""
    keyboard = [
        [InlineKeyboardButton("❌ انصراف و بازگشت به منو", callback_data="btn_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def get_main_menu_text() -> str:
    """متن صفحه اصلی پنل مدیریت با گزارش سلامت پینگ و دریافت ابری"""
    settings = await get_all_settings()
    stats = await get_stats()
    
    channel = settings.get("channel_id", "تنظیم نشده")
    auto_send = "فعال 🟢" if settings.get("auto_send", "0") == "1" else "غیرفعال 🔴"
    auto_harvest = "فعال 🟢" if settings.get("auto_harvest", "0") == "1" else "غیرفعال 🔴"
    batch_size = settings.get("batch_size", "3")
    min_d = int(settings.get("min_delay", str(DEFAULT_MIN_DELAY))) // 60
    max_d = int(settings.get("max_delay", str(DEFAULT_MAX_DELAY))) // 60
    harvest_h = settings.get("harvest_interval_hours", "2")
    tag = settings.get("tag", DEFAULT_TAG)
    
    countdown = get_next_post_countdown()
    next_post_str = f"{countdown} ثانیه دیگر" if countdown is not None else "در حال تعلیق"
    
    text = (
        "👑 **پنل مدیریت ربات خودکار ارسال VPN (نسخه هوشمند مهسا و گروهی)**\n\n"
        f"📢 **کانال مقصد:** `{channel}`\n"
        f"⚡ **وضعیت ارسال خودکار کانال:** {auto_send}\n"
        f"🌐 **دریافت خودکار از گیت‌هاب:** {auto_harvest} (هر `{harvest_h}` ساعت)\n"
        f"📦 **تعداد سرور در هر پست:** `{batch_size}` عدد (دسته‌ای)\n"
        f"⏱️ **بازه زمانی تصادفی ارسال:** از `{min_d}` تا `{max_d}` دقیقه\n"
        f"🏷️ **تگ و نام سرورها:** `{tag}`\n"
        f"⏳ **ارسال بعدی:** `{next_post_str}`\n\n"
        "📊 **وضعیت سلامت و پینگ سرورها:**\n"
        f"• کل کانفیگ‌های موجود: `{stats['total_configs']}` عدد\n"
        f"• سرورهای آنلاین و متصل: 🟢 `{stats['online_configs']}` عدد\n"
        f"• سرورهای قطع / فیلترشده: 🔴 `{stats['offline_configs']}` عدد\n"
        f"• در صف تست اولیه: ⏳ `{stats['untested_configs']}` عدد\n\n"
        "🔄 **وضعیت دور و ارسال‌ها:**\n"
        f"• دور فعلی ارسال: `دور {stats['current_cycle']}`\n"
        f"• ارسال شده در این دور: `{stats['sent_in_current_cycle']}` عدد\n"
        f"• باقی‌مانده سالم در دور فعلی: `{stats['remaining_in_cycle']}` عدد\n"
        f"• کل ارسال‌های تاریخچه: `{stats['total_lifetime_sent']}` پست\n\n"
        "👇 از گزینه‌های زیر جهت مدیریت استفاده کنید:"
    )
    return text

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر دستور /start یا /admin"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(
            "⛔ شما دسترسی لازم برای استفاده از این ربات را ندارید.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
        
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    auto_harvest_on = settings.get("auto_harvest", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, auto_harvest_on, batch_size)
    
    await update.message.reply_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازگشت به منوی اصلی از طریق دکمه شیشه‌ای"""
    query = update.callback_query
    await query.answer()
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    auto_harvest_on = settings.get("auto_harvest", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, auto_harvest_on, batch_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_toggle_auto_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """روشن یا خاموش کردن ارسال خودکار"""
    query = update.callback_query
    
    cur_status = await get_setting("auto_send", "0")
    new_status = "0" if cur_status == "1" else "1"
    
    channel = await get_setting("channel_id", "")
    if new_status == "1" and not channel:
        await query.answer("⚠️ ابتدا باید کانال مقصد را در تنظیمات ثبت کنید!", show_alert=True)
        return
        
    await set_setting("auto_send", new_status)
    
    status_text = "فعال شد 🟢" if new_status == "1" else "غیرفعال شد 🔴"
    await query.answer(f"ارسال خودکار {status_text}")
    
    settings = await get_all_settings()
    auto_harvest_on = settings.get("auto_harvest", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(new_status == "1", auto_harvest_on, batch_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_toggle_auto_harvest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """روشن یا خاموش کردن دریافت خودکار ابری دوره‌ای"""
    query = update.callback_query
    
    cur_status = await get_setting("auto_harvest", "0")
    new_status = "0" if cur_status == "1" else "1"
    
    await set_setting("auto_harvest", new_status)
    status_text = "فعال شد 🟢" if new_status == "1" else "غیرفعال شد 🔴"
    await query.answer(f"دریافت خودکار ابری {status_text}")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, new_status == "1", batch_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_cycle_batch_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغییر تعداد سرورهای ارسالی در هر پست گروهی (۱، ۲، ۳، ۵)"""
    query = update.callback_query
    
    cur_size = await get_setting("batch_size", "3")
    next_sizes = {"1": "2", "2": "3", "3": "5", "5": "1"}
    new_size = next_sizes.get(cur_size, "3")
    
    await set_setting("batch_size", new_size)
    await query.answer(f"تعداد سرور در هر پست به {new_size} عدد تغییر یافت! 📦")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    auto_harvest_on = settings.get("auto_harvest", "0") == "1"
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, auto_harvest_on, new_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_harvest_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت فوری سرورها از منابع ابری با ثبت کامل در صف و تست مداوم"""
    query = update.callback_query
    await query.answer("در حال دریافت تمام کانفیگ‌ها از منابع ابری...")
    
    await query.edit_message_text(
        "⏳ **در حال استخراج تمامی سرورها از مخازن آنلاین و اضافه کردن به دیتابیس...**\n"
        "لطفاً چند لحظه صبر کنید...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    sources = await get_active_source_urls()
    report = await harvest_and_store_online_configs(sources=sources, instant_test_count=60)
    
    stats = await get_stats()
    result_text = (
        "🎉 **عملیات استخراج ابری با موفقیت انجام شد!**\n\n"
        f"📥 **کل سرورهای شناسایی شده در مخازن:** `{report['total_fetched']}` عدد\n"
        f"➕ **سرورهای جدید اضافه شده به دیتابیس:** `{report['new_added']}` عدد\n"
        f"⚠️ **سرورهای تکراری (رد شده):** `{report['duplicates']}` عدد\n"
        f"🟢 **سرورهای آنلاین در تست اولیه:** `{report['instant_online']}` عدد\n"
        f"⏳ **در صف تست فعال ۲۴/۷:** `{stats['untested_configs']}` عدد\n\n"
        f"📊 **موجودی کل مخزن:** `{stats['total_configs']}` عدد (🟢 آنلاین: `{stats['online_configs']}`)\n\n"
        "⚡ *موتور تستر ۲۴ ساعته در پس‌زمینه به صورت مداوم در حال تست پینگ و تایید آنلاین بودن تک‌تک بقیه سرورهاست.*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📤 ارسال تستی به کانال", callback_data="btn_test_send"),
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=result_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش صفحه آمار دقیق"""
    query = update.callback_query
    await query.answer()
    
    stats = await get_stats()
    settings = await get_all_settings()
    
    countdown = get_next_post_countdown()
    next_post_str = f"{countdown} ثانیه دیگر" if countdown is not None else "غیرفعال / در انتظار"
    
    stats_text = (
        "📊 **گزارش جامع وضعیت سلامت و ارسال سرورها:**\n\n"
        f"🔢 **تعداد کل کانفیگ‌ها:** `{stats['total_configs']}`\n"
        f"🟢 **سرورهای آنلاین و پینگ‌دار:** `{stats['online_configs']}`\n"
        f"🔴 **سرورهای قطع یا فیلتر:** `{stats['offline_configs']}`\n"
        f"⏳ **در صف تست:** `{stats['untested_configs']}`\n\n"
        f"🔄 **شماره دور جاری (Cycle):** `{stats['current_cycle']}`\n"
        f"📤 **ارسال شده در دور فعلی:** `{stats['sent_in_current_cycle']}`\n"
        f"⏳ **باقی‌مانده در دور فعلی:** `{stats['remaining_in_cycle']}`\n"
        f"🏆 **کل سرورهای ارسال شده (مجموع):** `{stats['total_lifetime_sent']}`\n"
        f"🕒 **آخرین زمان ارسال:** `{stats['last_sent_time']}`\n"
        f"⏰ **زمان تا ارسال رندوم بعدی:** `{next_post_str}`\n\n"
        f"📢 **کانال متصل:** `{settings.get('channel_id', 'تعیین نشده')}`\n"
        f"🏷️ **تگ فعال کانفیگ‌ها:** `{settings.get('tag', DEFAULT_TAG)}`\n"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 بروزرسانی آمار", callback_data="btn_stats"),
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=stats_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_test_send_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال تستی یک کانفیگ آنلاین با اعتبارسنجی ۳ مرحله‌ای مستقیماً برای ادمین در ربات"""
    query = update.callback_query
    try:
        await query.answer("⏳ در حال ارزیابی ۳ مرحله‌ای پایداری و ارسال سرور به پیوی...")
    except Exception:
        pass
    
    admin_chat_id = str(ADMIN_ID)
    success, msg = await send_single_post(context.bot, admin_chat_id, is_test=True)
    if not success:
        try:
            await context.bot.send_message(chat_id=admin_chat_id, text=msg)
        except Exception:
            pass

async def cb_test_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال تستی یک کانفیگ آنلاین به کانال"""
    query = update.callback_query
    try:
        await query.answer("⏳ در حال تست پینگ و ارسال تستی به کانال...")
    except Exception:
        pass
    
    channel = await get_setting("channel_id", "")
    if not channel:
        try:
            await query.answer("⚠️ ابتدا کانال مقصد را تنظیم کنید.", show_alert=True)
        except Exception:
            pass
        return
        
    success, msg = await send_single_post(context.bot, channel, is_test=True)
    try:
        await query.answer(msg, show_alert=True)
    except Exception:
        pass

async def cb_ping_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تست پینگ سریع تمام سرورهای موجود در دیتابیس"""
    query = update.callback_query
    await query.answer("تست سلامت تمام سرورها شروع شد...")
    
    await query.edit_message_text(
        "⏳ **در حال تست پینگ و سلامت سرورها با الگوریتم TCP/TLS...**\nلطفاً چند لحظه صبر کنید...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # دریافت سرورها
    configs = await get_configs_for_health_check(limit=500)
    if not configs:
        await query.edit_message_text(
            "⚠️ هیچ سروری در دیتابیس موجود نیست.",
            reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1")
        )
        return
        
    results = await ping_configs_batch(configs, concurrency=20, timeout=2.5)
    await update_configs_ping_bulk(results)
    
    stats = await get_stats()
    result_text = (
        "✅ **تست پینگ کامل شد!**\n\n"
        f"📊 **تعداد تست شده:** `{len(results)}` عدد\n"
        f"🟢 **سرورهای متصل و سالم:** `{stats['online_configs']}`\n"
        f"🔴 **سرورهای قطع/فیلتر:** `{stats['offline_configs']}`\n\n"
        "✨ کانال شما فقط از بین سرورهای آنلاین تغذیه خواهد شد."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🧹 پاکسازی سرورهای قطع", callback_data="btn_clear_dead"),
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=result_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_clear_dead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف سرورهای غیرفعال و بدون پینگ"""
    query = update.callback_query
    deleted = await delete_dead_configs()
    await query.answer(f"تعداد {deleted} سرور قطع حذف شدند!", show_alert=True)
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(await get_setting("auto_send", "0") == "1")
    
    await query.edit_message_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# ----------------- بخش افزودن کانفیگ (متن یا فایل) -----------------

async def cb_start_add_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند دریافت کانفیگ"""
    query = update.callback_query
    await query.answer()
    
    guide_text = (
        "📥 **افزودن کانفیگ‌های جدید:**\n\n"
        "شما می‌توانید کانفیگ‌ها را به دو روش اضافه کنید:\n"
        "1️⃣ **ارسال فایل متنی (`.txt`):** فایل حاوی ده‌ها یا صدها کانفیگ را همینجا ارسال کنید.\n"
        "2️⃣ **ارسال مستقیم به صورت متن:** کانفیگ‌ها را کپی کرده و داخل چت بفرستید.\n\n"
        "✨ تمام پروتکل‌های `vless`, `vmess`, `trojan`, `ss`, `hysteria2`, `tuic` و... پشتیبانی می‌شوند.\n"
        "✨ سیستم خودکار نام‌ها و پرچم‌ها را بازنویسی کرده و سلامت آنها را پایش می‌کند."
    )
    
    await query.edit_message_text(
        text=guide_text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_CONFIGS

async def handle_receive_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت متن یا فایل کانفیگ و درج در دیتابیس"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    raw_content = ""
    
    if update.message.document:
        doc = update.message.document
        if doc.file_name and not doc.file_name.endswith(('.txt', '.conf', '.json', '.yaml')):
            await update.message.reply_text("⚠️ لطفاً یک فایل متنی معتبر (`.txt`) ارسال کنید.")
            return STATE_WAIT_CONFIGS
            
        file_obj = await context.bot.get_file(doc.file_id)
        file_bytes = io.BytesIO()
        await file_obj.download_to_memory(file_bytes)
        raw_content = file_bytes.getvalue().decode('utf-8', errors='ignore')
    elif update.message.text:
        raw_content = update.message.text
        
    configs_list = extract_configs_from_text(raw_content)
    
    if not configs_list:
        await update.message.reply_text(
            "❌ هیچ کانفیگ معتبری در پیام ارسالی یافت نشد! لطفاً کانفیگ‌های صحیح ارسال کنید یا دکمه انصراف را بزنید.",
            reply_markup=build_cancel_keyboard()
        )
        return STATE_WAIT_CONFIGS
        
    added, dupes = await add_configs_bulk(configs_list)
    stats = await get_stats()
    
    report_text = (
        "✅ **کانفیگ‌ها با موفقیت ذخیره شدند!**\n\n"
        f"📥 **تعداد شناسایی شده:** `{len(configs_list)}`\n"
        f"➕ **تعداد اضافه شده جدید:** `{added}`\n"
        f"⚠️ **تعداد تکراری (رد شده):** `{dupes}`\n"
        f"📊 **موجودی کل سرورها:** `{stats['total_configs']}` عدد\n\n"
        "🔍 تستر پس‌زمینه بلافاصله شروع به بررسی پینگ کانفیگ‌های جدید می‌کند."
    )
    
    settings = await get_all_settings()
    reply_markup = build_main_keyboard(settings.get("auto_send", "0") == "1")
    
    await update.message.reply_text(
        text=report_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

# ----------------- بخش تنظیم کانال مقصد -----------------

async def cb_start_set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست یوزرنیم یا آیدی کانال"""
    query = update.callback_query
    await query.answer()
    
    cur_channel = await get_setting("channel_id", "تنظیم نشده")
    
    text = (
        "📢 **تنظیم کانال تلگرام برای ارسال خودکار:**\n\n"
        f"کانال فعلی: `{cur_channel}`\n\n"
        "💡 **راهنما:**\n"
        "1. ربات را در کانال خود **ادمین (Admin)** با دسترسی ارسال پیام کنید.\n"
        "2. سپس آیدی عمومی کانال (مثلاً `@MyChannel`) یا آیدی عددی کانال خصوصی (مثلاً `-1001234567890`) را به این چت ارسال کنید."
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_CHANNEL

async def handle_receive_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اعتبارسنجی و ذخیره کانال"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    channel_input = update.message.text.strip()
    
    try:
        chat = await context.bot.get_chat(channel_input)
        bot_member = await chat.get_member(context.bot.id)
        
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⚠️ ربات عضو کانال هست اما **ادمین** نشده است! لطفاً ابتدا به ربات دسترسی ادمین بدهید و مجدد آیدی را ارسال کنید.",
                reply_markup=build_cancel_keyboard()
            )
            return STATE_WAIT_CHANNEL
            
        channel_id_to_save = f"@{chat.username}" if chat.username else str(chat.id)
        await set_setting("channel_id", channel_id_to_save)
        
        await update.message.reply_text(
            f"✅ **کانال با موفقیت ثبت شد!**\nنام کانال: `{chat.title}`\nشناسه: `{channel_id_to_save}`",
            reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1"),
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا در اتصال به کانال: `{str(e)}`\n\n"
            "مطمئن شوید ربات را در کانال ادمین کرده‌اید و آیدی را درست فرستاده‌اید.",
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        return STATE_WAIT_CHANNEL

# ----------------- بخش تنظیم زمان‌بندی (Intervals) -----------------

async def cb_start_set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیم بازه زمانی تصادفی"""
    query = update.callback_query
    await query.answer()
    
    min_d = int(await get_setting("min_delay", str(DEFAULT_MIN_DELAY))) // 60
    max_d = int(await get_setting("max_delay", str(DEFAULT_MAX_DELAY))) // 60
    
    text = (
        "⏱️ **تنظیم بازه زمانی ارسال رندوم:**\n\n"
        f"بازه فعلی: بین `{min_d}` دقیقه تا `{max_d}` دقیقه به صورت تصادفی.\n\n"
        "لطفاً **حداقل زمان** بین دو ارسال را به **دقیقه** وارد کنید (مثلاً عدد `1`):"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_MIN_DELAY

async def handle_receive_min_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت حداقل زمان و درخواست حداکثر زمان"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("⚠️ لطفاً یک عدد صحیح مثبت (حداقل 1) وارد کنید.")
        return STATE_WAIT_MIN_DELAY
        
    context.user_data["temp_min_delay"] = int(text) * 60
    
    await update.message.reply_text(
        f"✅ حداقل زمان: `{text}` دقیقه تنظیم شد.\n\n"
        "حالا لطفاً **حداکثر زمان** بین دو ارسال را به **دقیقه** وارد کنید (مثلاً عدد `10`):",
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_MAX_DELAY

async def handle_receive_max_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت حداکثر زمان و ذخیره نهایی"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("⚠️ لطفاً یک عدد صحیح مثبت وارد کنید.")
        return STATE_WAIT_MAX_DELAY
        
    max_delay_sec = int(text) * 60
    min_delay_sec = context.user_data.get("temp_min_delay", DEFAULT_MIN_DELAY)
    
    if min_delay_sec > max_delay_sec:
        min_delay_sec, max_delay_sec = max_delay_sec, min_delay_sec
        
    await set_setting("min_delay", str(min_delay_sec))
    await set_setting("max_delay", str(max_delay_sec))
    
    await update.message.reply_text(
        f"✅ بازه زمانی با موفقیت تنظیم شد:\n"
        f"🕒 بین `{min_delay_sec // 60}` تا `{max_delay_sec // 60}` دقیقه به صورت تصادفی.",
        reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1"),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

# ----------------- بخش تغییر تگ سرورها -----------------

async def cb_start_set_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست تگ جدید"""
    query = update.callback_query
    await query.answer()
    
    cur_tag = await get_setting("tag", DEFAULT_TAG)
    
    text = (
        "🏷️ **تغییر نام و تگ سرورها:**\n\n"
        f"تگ فعلی: `{cur_tag}`\n\n"
        "لطفاً آیدی یا نام جدیدی که می‌خواهید روی سرورها قرار گیرد را بفرستید (مثلاً `@FreeVpn_Internetazad`):"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_TAG

async def handle_receive_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره تگ جدید"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    new_tag = update.message.text.strip()
    if not new_tag:
        await update.message.reply_text("⚠️ تگ نمی‌تواند خالی باشد.")
        return STATE_WAIT_TAG
        
    await set_setting("tag", new_tag)
    
    await update.message.reply_text(
        f"✅ تگ سرورها با موفقیت به `{new_tag}` تغییر یافت.",
        reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1"),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

# ----------------- بخش ویرایش متن هدر و فوتر -----------------

async def cb_start_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ویرایش متن هدر و فوتر پیام کانال"""
    query = update.callback_query
    await query.answer()
    
    cur_header = await get_setting("custom_header", "🚀 **سرور جدید و پرسرعت**")
    cur_footer = await get_setting("custom_footer", f"🆔 {DEFAULT_TAG}\n🌐 اینترنت آزاد برای همه")
    
    text = (
        "✍️ **ویرایش متن پست‌های ارسالی:**\n\n"
        f"📌 **هدر فعلی:**\n{cur_header}\n\n"
        f"📌 **فوتر فعلی:**\n{cur_footer}\n\n"
        "لطفاً متن جدید برای **هدر (تیتر بالای سرور)** را ارسال کنید:"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_HEADER

async def handle_receive_header(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت هدر و درخواست فوتر"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    context.user_data["temp_header"] = update.message.text.strip()
    
    await update.message.reply_text(
        "✅ هدر دریافت شد.\nحالا لطفاً متن جدید برای **فوتر (پایین سرور)** را ارسال کنید:",
        reply_markup=build_cancel_keyboard()
    )
    return STATE_WAIT_FOOTER

async def handle_receive_footer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره هدر و فوتر جدید"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    header = context.user_data.get("temp_header", "🚀 **سرور پرسرعت و رایگان**")
    footer = update.message.text.strip()
    
    await set_setting("custom_header", header)
    await set_setting("custom_footer", footer)
    
    await update.message.reply_text(
        "✅ متن هدر و فوتر پیام‌ها با موفقیت ذخیره شد!",
        reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1")
    )
    return ConversationHandler.END

# ----------------- بخش تنظیم لینک سابسکریپشن -----------------

async def cb_start_set_sub_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست لینک جدید سابسکریپشن"""
    query = update.callback_query
    await query.answer()
    
    cur_sub = await get_setting("sub_link", "پیش‌فرض خودکار")
    
    text = (
        "🔗 **تنظیم لینک سابسکریپشن اختصاصی کانال:**\n\n"
        f"📌 **لینک فعلی:**\n`{cur_sub}`\n\n"
        "💡 **راهنما:**\n"
        "این لینک زیر هر پست کانال درج می‌شود تا کاربران در صورت قطعی بتوانند با یک کلیک سابسکریپشن خود را آپدیت کنند.\n\n"
        "لطفاً لینک سابسکریپشن جدید را ارسال کنید (یا کلمه `default` را بفرستید تا به حالت پیش‌فرض هوشمند مهسا بازگردد):"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_SUB_LINK

async def handle_receive_sub_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره لینک سابسکریپشن جدید"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    new_sub = update.message.text.strip()
    if not new_sub:
        await update.message.reply_text("⚠️ لینک نمی‌تواند خالی باشد.")
        return STATE_WAIT_SUB_LINK
        
    tag = await get_setting("tag", DEFAULT_TAG)
    tag_clean = tag.replace("@", "")
    if new_sub.lower() == "default":
        new_sub = f"https://raw.githubusercontent.com/hiddify/hiddify-app/refs/heads/main/test.configs/mahsa#{tag_clean}"
        
    await set_setting("sub_link", new_sub)
    
    await update.message.reply_text(
        f"✅ **لینک سابسکریپشن با موفقیت ذخیره شد!**\n\n`{new_sub}`",
        reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1"),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

# ----------------- بخش بکاپ و پاکسازی -----------------

async def cb_export_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال فایل متنی شامل تمام کانفیگ‌های ذخیره شده"""
    query = update.callback_query
    await query.answer()
    
    configs = await export_all_configs()
    if not configs:
        await query.answer("مخزن کانفیگ‌ها خالی است!", show_alert=True)
        return
        
    content = "\n".join(configs)
    bio = io.BytesIO(content.encode('utf-8'))
    bio.name = "vpn_configs_backup.txt"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=bio,
        caption=f"📁 فایل بکاپ شامل `{len(configs)}` کانفیگ ذخیره شده.",
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_confirm_clear_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید پاکسازی کل کانفیگ‌ها"""
    query = update.callback_query
    await query.answer()
    
    text = "⚠️ **آیا مطمئن هستید که می‌خواهید تمام سرورهای ذخیره شده را حذف کنید؟**\nاین عمل غیرقابل بازگشت است."
    keyboard = [
        [
            InlineKeyboardButton("🔴 بله، حذف کن", callback_data="btn_do_clear"),
            InlineKeyboardButton("🟢 خیر، بازگشت", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_do_clear_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرای حذف تمام کانفیگ‌ها"""
    query = update.callback_query
    await clear_all_configs()
    await query.answer("کل سرورها حذف شدند!", show_alert=True)
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(await get_setting("auto_send", "0") == "1")
    
    await query.edit_message_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو مکالمه و بازگشت به منوی اصلی"""
    query = update.callback_query
    await query.answer("عملیات لغو شد.")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on)
    
    await query.edit_message_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def post_init(application: Application):
    """اجرا پس از استارت بات و راه‌اندازی تسک‌های پس‌زمینه"""
    await init_db()
    # شروع حلقه زمان‌بندی خودکار و پایش سلامت سرورها در پس‌زمینه
    start_scheduler(application.bot)
    logger.info("ربات، دیتابیس و سیستم تستر سلامت با موفقیت راه‌اندازی شدند.")

def main():
    """نقطه شروع اجرای برنامه"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # مکالمه افزودن کانفیگ
    conv_add_configs = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_add_configs, pattern="^btn_add_configs$")],
        states={
            STATE_WAIT_CONFIGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_configs),
                MessageHandler(filters.Document.ALL, handle_receive_configs),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه تنظیم کانال
    conv_set_channel = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_channel, pattern="^btn_set_channel$")],
        states={
            STATE_WAIT_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_channel),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه تنظیم بازه زمانی
    conv_set_delay = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_delay, pattern="^btn_set_delay$")],
        states={
            STATE_WAIT_MIN_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_min_delay),
            ],
            STATE_WAIT_MAX_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_max_delay),
            ],
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه تغییر تگ
    conv_set_tag = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_tag, pattern="^btn_set_tag$")],
        states={
            STATE_WAIT_TAG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_tag),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه ویرایش متن هدر/فوتر
    conv_edit_text = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_edit_text, pattern="^btn_edit_text$")],
        states={
            STATE_WAIT_HEADER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_header),
            ],
            STATE_WAIT_FOOTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_footer),
            ],
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه تنظیم لینک سابسکریپشن
    conv_set_sub_link = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_sub_link, pattern="^btn_set_sub_link$")],
        states={
            STATE_WAIT_SUB_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_sub_link),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # افزودن هندلرها
    application.add_handler(CommandHandler(["start", "admin", "panel"], cmd_start))
    application.add_handler(conv_add_configs)
    application.add_handler(conv_set_channel)
    application.add_handler(conv_set_delay)
    application.add_handler(conv_set_tag)
    application.add_handler(conv_edit_text)
    application.add_handler(conv_set_sub_link)
    
    application.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^btn_main_menu$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_auto_send, pattern="^btn_toggle_auto$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_auto_harvest, pattern="^btn_toggle_auto_harvest$"))
    application.add_handler(CallbackQueryHandler(cb_cycle_batch_size, pattern="^btn_cycle_batch_size$"))
    application.add_handler(CallbackQueryHandler(cb_harvest_now, pattern="^btn_harvest_now$"))
    application.add_handler(CallbackQueryHandler(cb_stats, pattern="^btn_stats$"))
    application.add_handler(CallbackQueryHandler(cb_test_send, pattern="^btn_test_send$"))
    application.add_handler(CallbackQueryHandler(cb_test_send_admin, pattern="^btn_test_send_admin$"))
    application.add_handler(CallbackQueryHandler(cb_ping_all, pattern="^btn_ping_all$"))
    application.add_handler(CallbackQueryHandler(cb_clear_dead, pattern="^btn_clear_dead$"))
    application.add_handler(CallbackQueryHandler(cb_export_configs, pattern="^btn_export_configs$"))
    application.add_handler(CallbackQueryHandler(cb_confirm_clear_configs, pattern="^btn_clear_configs$"))
    application.add_handler(CallbackQueryHandler(cb_do_clear_configs, pattern="^btn_do_clear$"))
    application.add_handler(CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$"))
    
    logger.info("در حال شروع دریافت پیام‌ها (Polling)...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
