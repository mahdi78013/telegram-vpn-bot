import logging
import asyncio
import io
from typing import Dict, Any, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMemberUpdated,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    ADMIN_ID,
    DEFAULT_TAG,
    DEFAULT_MIN_DELAY,
    DEFAULT_MAX_DELAY,
)
from database import (
    init_db,
    get_setting,
    set_setting,
    get_all_settings,
    get_stats,
    add_configs_bulk,
    clear_all_configs,
    delete_dead_configs,
    get_configs_for_health_check,
    update_configs_ping_bulk,
    get_active_source_urls,
    get_all_destinations,
    get_active_destinations,
    add_destination,
    toggle_destination,
    delete_destination,
)
from parser import extract_configs_from_text
from tester import ping_configs_batch
from scheduler import (
    start_scheduler,
    send_single_post,
    get_next_post_countdown,
)
from harvester import harvest_and_store_online_configs

# تنظیمات لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AutoVpnBot")

# وضعیت‌های مکالمه (ConversationHandler)
(
    STATE_WAIT_CONFIGS,
    STATE_WAIT_ADD_DEST,
    STATE_WAIT_DELAY,
    STATE_WAIT_TAG,
) = range(4)

def is_admin(user_id: int) -> bool:
    """بررسی ادمین بودن کاربر"""
    return user_id == ADMIN_ID

def build_main_keyboard(auto_send_on: bool, batch_size: str = "3") -> InlineKeyboardMarkup:
    """ساخت کیبورد اصلی شیک، فوق‌العاده خلوت و بهینه"""
    toggle_send_text = "🟢 ارسال خودکار: [روشن]" if auto_send_on else "🔴 ارسال خودکار: [خاموش]"
    
    keyboard = [
        [
            InlineKeyboardButton(f"⚡ {toggle_send_text}", callback_data="btn_toggle_auto"),
            InlineKeyboardButton(f"📦 تعداد سرور: [{batch_size} عدد]", callback_data="btn_cycle_batch_size"),
        ],
        [
            InlineKeyboardButton("🔍 تست پینگ و سلامت سرورها", callback_data="btn_ping_all"),
            InlineKeyboardButton("🌐 دریافت فوری سرورهای آنلاین", callback_data="btn_harvest_now"),
        ],
        [
            InlineKeyboardButton("📢 مدیریت کانال‌ها و گروه‌ها", callback_data="btn_manage_destinations"),
            InlineKeyboardButton("⏱️ تنظیم زمان‌بندی ارسال", callback_data="btn_set_delay"),
        ],
        [
            InlineKeyboardButton("🏷️ تغییر تگ و نام سرورها", callback_data="btn_set_tag"),
            InlineKeyboardButton("📤 ارسال تستی برای من (ادمین)", callback_data="btn_test_send_admin"),
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
    """متن صفحه اصلی پنل مدیریت"""
    settings = await get_all_settings()
    stats = await get_stats()
    destinations = await get_all_destinations()
    active_dests = [d for d in destinations if d.get("is_active") == 1]
    
    auto_send = "فعال 🟢" if settings.get("auto_send", "0") == "1" else "غیرفعال 🔴"
    batch_size = settings.get("batch_size", "3")
    min_d = int(settings.get("min_delay", str(DEFAULT_MIN_DELAY))) // 60
    tag = settings.get("tag", DEFAULT_TAG)
    
    countdown = get_next_post_countdown()
    next_post_str = f"{countdown} ثانیه دیگر" if countdown is not None else "در حال تعلیق"
    
    text = (
        "👑 **پنل مدیریت ربات خودکار ارسال VPN**\n\n"
        f"⚡ **وضعیت ارسال خودکار:** {auto_send}\n"
        f"📦 **تعداد سرور در هر پست:** `{batch_size}` عدد (دسته‌ای)\n"
        f"📢 **مقاصد فعال (کانال/گروه):** `{len(active_dests)}` مورد از `{len(destinations)}`\n"
        f"⏱️ **فاصله زمانی ارسال:** هر `{min_d}` دقیقه یکبار\n"
        f"🏷️ **تگ سرورها:** `{tag}`\n"
        f"⏳ **ارسال بعدی:** `{next_post_str}`\n\n"
        "📊 **وضعیت سلامت سرورها:**\n"
        f"• کل کانفیگ‌های موجود: `{stats['total_configs']}` عدد\n"
        f"• سرورهای متصل و آنلاین: 🟢 `{stats['online_configs']}` عدد\n"
        f"• سرورهای قطع / فیلتر: 🔴 `{stats['offline_configs']}` عدد\n"
        f"• در صف تست اولیه: ⏳ `{stats['untested_configs']}` عدد\n\n"
        f"🔄 **آمار دور ارسال:** دور `{stats['current_cycle']}` | ارسال تاریخچه: `{stats['total_lifetime_sent']}` پست\n\n"
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
    batch_size = settings.get("batch_size", "3")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, batch_size)
    
    await update.message.reply_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازگشت به منوی اصلی"""
    query = update.callback_query
    await query.answer()
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, batch_size)
    
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
    
    active_dests = await get_active_destinations()
    if new_status == "1" and not active_dests:
        await query.answer("⚠️ ابتدا حداقل یک کانال یا گروه را فعال کنید!", show_alert=True)
        return
        
    await set_setting("auto_send", new_status)
    status_text = "فعال شد 🟢" if new_status == "1" else "غیرفعال شد 🔴"
    await query.answer(f"ارسال خودکار {status_text}")
    
    settings = await get_all_settings()
    batch_size = settings.get("batch_size", "3")
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(new_status == "1", batch_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_cycle_batch_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغییر تعداد سرورهای ارسالی در هر پست (۱، ۲، ۳، ۵)"""
    query = update.callback_query
    
    cur_size = await get_setting("batch_size", "3")
    next_sizes = {"1": "2", "2": "3", "3": "5", "5": "1"}
    new_size = next_sizes.get(cur_size, "3")
    
    await set_setting("batch_size", new_size)
    await query.answer(f"تعداد سرور در هر پست به {new_size} عدد تنظیم شد! 📦")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, new_size)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_ping_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تست پینگ و ارزیابی سلامت تمامی سرورها با گزارش دقیق"""
    query = update.callback_query
    await query.answer("در حال تست پینگ و سلامت سرورها...")
    
    await query.edit_message_text(
        "⏳ **در حال تست زنده پینگ و سلامت سرورها با پروتکل TCP/TLS...**\nلطفاً چند لحظه صبر کنید...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    configs = await get_configs_for_health_check(limit=500)
    if not configs:
        await query.edit_message_text(
            "⚠️ هیچ سروری در دیتابیس موجود نیست.",
            reply_markup=build_main_keyboard(await get_setting("auto_send", "0") == "1")
        )
        return
        
    results = await ping_configs_batch(configs, concurrency=25, timeout=2.0)
    await update_configs_ping_bulk(results)
    
    stats = await get_stats()
    online_pings = [r[2] for r in results if r[1] and r[2] > 0]
    avg_ping = int(sum(online_pings) / len(online_pings)) if online_pings else 0
    
    result_text = (
        "🔍 **گزارش تست پینگ و سلامت سرورها:**\n\n"
        f"📊 **کل سرورهای موجود:** `{stats['total_configs']}` عدد\n"
        f"🟢 **سرورهای سالم و آنلاین:** `{stats['online_configs']}` عدد\n"
        f"🔴 **سرورهای قطع یا فیلتر:** `{stats['offline_configs']}` عدد\n"
        f"⚡ **میانگین پینگ سرورهای متصل:** `{avg_ping}ms`\n\n"
        "✨ *پست‌های ارسالی تنها از سرورهای باکیفیت و متصل تغذیه می‌شوند.*"
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
    """حذف سرورهای قطع"""
    query = update.callback_query
    deleted = await delete_dead_configs()
    await query.answer(f"تعداد {deleted} سرور قطع حذف شدند! 🧹", show_alert=True)
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(await get_setting("auto_send", "0") == "1")
    
    await query.edit_message_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_harvest_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت فوری سرورهای آنلاین از مخازن ابری"""
    query = update.callback_query
    await query.answer("در حال دریافت کانفیگ‌های تازه از مخازن آنلاین...")
    
    await query.edit_message_text(
        "⏳ **در حال دریافت سرورهای تازه از سابسکریپشن‌های آنلاین...**\nلطفاً چند لحظه صبر کنید...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    sources = await get_active_source_urls()
    report = await harvest_and_store_online_configs(sources=sources, instant_test_count=60)
    stats = await get_stats()
    
    result_text = (
        "🎉 **دریافت ابری با موفقیت انجام شد!**\n\n"
        f"📥 **سرورهای دریافت شده:** `{report['total_fetched']}` عدد\n"
        f"➕ **سرورهای جدید اضافه شده:** `{report['new_added']}` عدد\n"
        f"⚠️ **سرورهای تکراری:** `{report['duplicates']}` عدد\n"
        f"🟢 **سرورهای آنلاین تایید شده:** `{report['instant_online']}` عدد\n"
        f"📊 **موجودی کل مخزن:** `{stats['total_configs']}` عدد"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📤 ارسال تستی برای من", callback_data="btn_test_send_admin"),
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=result_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_test_send_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال تستی یک بسته سرور مستقیماً به پیوی ادمین"""
    query = update.callback_query
    try:
        await query.answer("⏳ در حال آماده‌سازی و ارسال سرور به پیوی شما...")
    except Exception:
        pass
    
    admin_chat_id = str(ADMIN_ID)
    success, msg = await send_single_post(context.bot, admin_chat_id, is_test=True)
    if not success:
        try:
            await context.bot.send_message(chat_id=admin_chat_id, text=msg)
        except Exception:
            pass

# ----------------- بخش مدیریت کانال‌ها و گروه‌های مقصد (Multi-Destination) -----------------

async def build_destinations_keyboard() -> InlineKeyboardMarkup:
    """ساخت کیبورد مدیریت کانال‌ها و گروه‌ها با دکمه‌های روشن/خاموش"""
    destinations = await get_all_destinations()
    keyboard = []
    
    for d in destinations:
        did = d["id"]
        title = d.get("title") or d["chat_id"]
        is_active = d.get("is_active", 1) == 1
        status_icon = "🟢" if is_active else "🔴"
        chat_type_icon = "📢" if d.get("chat_type") == "channel" else "👥"
        
        btn_text = f"{status_icon} {chat_type_icon} {title} ({d['chat_id']})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"btn_toggle_dest_{did}")])
        
    keyboard.append([
        InlineKeyboardButton("➕ افزودن دستی کانال یا گروه", callback_data="btn_start_add_dest"),
        InlineKeyboardButton("🗑️ حذف یک مقصد", callback_data="btn_show_delete_dest"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="btn_main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)

async def cb_manage_destinations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پنل مدیریت کانال‌ها و گروه‌های مقصد"""
    query = update.callback_query
    await query.answer()
    
    destinations = await get_all_destinations()
    active_count = len([d for d in destinations if d.get("is_active") == 1])
    
    text = (
        "📢 **مدیریت کانال‌ها و گروه‌های مقصد:**\n\n"
        "💡 **راهنمای خودکار:**\n"
        "هر زمان ربات را در هر کانال یا گروهی **ادمین** کنید، به طور خودکار به این لیست اضافه می‌شود!\n\n"
        f"📌 **تعداد کل مقاصد:** `{len(destinations)}` (🟢 فعال: `{active_count}`)\n\n"
        "روی هر کدام کلیک کنید تا ارسال به آن **روشن (🟢)** یا **خاموش (🔴)** شود:"
    )
    
    reply_markup = await build_destinations_keyboard()
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_toggle_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغییر وضعیت فعال/غیرفعال یک مقصد با یک لمس"""
    query = update.callback_query
    dest_id_str = query.data.replace("btn_toggle_dest_", "")
    
    if dest_id_str.isdigit():
        dest_id = int(dest_id_str)
        new_status = await toggle_destination(dest_id)
        status_msg = "روشن شد 🟢" if new_status == 1 else "خاموش شد 🔴"
        await query.answer(f"وضعیت مقصد: {status_msg}")
        
    reply_markup = await build_destinations_keyboard()
    destinations = await get_all_destinations()
    active_count = len([d for d in destinations if d.get("is_active") == 1])
    
    text = (
        "📢 **مدیریت کانال‌ها و گروه‌های مقصد:**\n\n"
        "💡 **راهنمای خودکار:**\n"
        "هر زمان ربات را در هر کانال یا گروهی **ادمین** کنید، به طور خودکار به این لیست اضافه می‌شود!\n\n"
        f"📌 **تعداد کل مقاصد:** `{len(destinations)}` (🟢 فعال: `{active_count}`)\n\n"
        "روی هر کدام کلیک کنید تا ارسال به آن **روشن (🟢)** یا **خاموش (🔴)** شود:"
    )
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_show_delete_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست مقاصد جهت حذف"""
    query = update.callback_query
    await query.answer()
    
    destinations = await get_all_destinations()
    keyboard = []
    for d in destinations:
        did = d["id"]
        title = d.get("title") or d["chat_id"]
        keyboard.append([InlineKeyboardButton(f"❌ حذف {title}", callback_data=f"btn_del_dest_{did}")])
        
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست مقاصد", callback_data="btn_manage_destinations")])
    
    await query.edit_message_text(
        text="🗑️ **برای حذف، روی کانال یا گروه مورد نظر کلیک کنید:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_do_delete_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف قطعی یک مقصد"""
    query = update.callback_query
    dest_id_str = query.data.replace("btn_del_dest_", "")
    
    if dest_id_str.isdigit():
        await delete_destination(int(dest_id_str))
        await query.answer("مقصد با موفقیت حذف شد! 🗑️", show_alert=True)
        
    reply_markup = await build_destinations_keyboard()
    await query.edit_message_text(
        text="📢 **مدیریت کانال‌ها و گروه‌های مقصد:**\nمقصد مورد نظر حذف شد.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_start_add_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند افزودن دستی مقصد"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "➕ **افزودن دستی کانال یا گروه مقصد:**\n\n"
        "💡 لطفاً آیدی کانال یا گروه را بفرستید (مثلاً `@Internet_azad369` یا `-1001234567890`).\n\n"
        "*(مطمئن شوید قبل از ارسال، ربات را در آنجا ادمین کرده‌اید)*"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_ADD_DEST

async def handle_receive_add_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اعتبارسنجی و ثبت دستی کانال یا گروه"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    chat_input = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(chat_input)
        bot_member = await chat.get_member(context.bot.id)
        
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⚠️ ربات در این مقصد عضو هست اما **ادمین** نشده است! لطفاً ابتدا دسترسی ادمین بدهید و مجدد آیدی را بفرستید.",
                reply_markup=build_cancel_keyboard()
            )
            return STATE_WAIT_ADD_DEST
            
        chat_id_to_save = f"@{chat.username}" if chat.username else str(chat.id)
        chat_type = "channel" if chat.type == "channel" else "group"
        
        await add_destination(chat_id_to_save, chat.title or "بدون عنوان", chat_type)
        
        reply_markup = await build_destinations_keyboard()
        await update.message.reply_text(
            f"✅ **مقصد با موفقیت ثبت شد!**\n📌 نام: `{chat.title}`\n🆔 شناسه: `{chat_id_to_save}`",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا در اتصال به مقصد: `{str(e)}`\n\nمطمئن شوید ربات را در کانال/گروه ادمین کرده‌اید.",
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        return STATE_WAIT_ADD_DEST

async def cb_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ثبت خودکار به محض ادمین شدن ربات در هر کانال یا گروه"""
    chat_member = update.my_chat_member
    if not chat_member:
        return
        
    chat = chat_member.chat
    new_status = chat_member.new_chat_member.status
    
    if new_status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        chat_id_to_save = f"@{chat.username}" if chat.username else str(chat.id)
        chat_type = "channel" if chat.type == "channel" else "group"
        title = chat.title or "بدون نام"
        
        await add_destination(chat_id_to_save, title, chat_type)
        logger.info(f"ربات به کانال/گروه {title} ({chat_id_to_save}) اضافه و ثبت خودکار شد.")
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🎉 **کانال/گروه جدید شناسایی شد!**\n\n"
                    f"📌 **نام:** `{title}`\n"
                    f"🆔 **شناسه:** `{chat_id_to_save}`\n"
                    f"📂 **نوع:** `{'کانال' if chat_type == 'channel' else 'گروه'}`\n\n"
                    "✅ این مقصد به طور خودکار به لیست ارسال‌های فعال ربات افزوده شد."
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

# ----------------- بخش تنظیم سرعت / زمان‌بندی ارسال -----------------

async def cb_start_set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست فاصله زمانی ارسال به دقیقه"""
    query = update.callback_query
    await query.answer()
    
    min_d = int(await get_setting("min_delay", str(DEFAULT_MIN_DELAY))) // 60
    
    text = (
        "⏱️ **تنظیم زمان‌بندی و سرعت ارسال:**\n\n"
        f"فاصله فعلی: **هر `{min_d}` دقیقه یک پست**\n\n"
        "لطفاً فاصله زمانی جدید را به **دقیقه** وارد کنید:\n"
        "(مثلاً عدد `1` یعنی هر ۱ دقیقه، عدد `3` یعنی هر ۳ دقیقه، یا `0.5` یعنی هر ۳۰ ثانیه):"
    )
    
    await query.edit_message_text(
        text=text,
        reply_markup=build_cancel_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    return STATE_WAIT_DELAY

async def handle_receive_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره فاصله زمانی جدید"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text.strip().replace("٫", ".")
    try:
        minutes = float(text)
        if minutes <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر (مثلاً 1 یا 2 یا 5) وارد کنید.")
        return STATE_WAIT_DELAY
        
    seconds = max(15, int(minutes * 60))
    await set_setting("min_delay", str(seconds))
    await set_setting("max_delay", str(seconds + 15))
    
    min_display = f"{minutes:g}"
    await update.message.reply_text(
        f"✅ **زمان‌بندی ارسال با موفقیت تنظیم شد!**\n🕒 سرورها **هر {min_display} دقیقه یکبار** ارسال خواهند شد.",
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
        "لطفاً آیدی جدیدی که می‌خواهید روی سرورها قرار گیرد را بفرستید (مثلاً `@Internet_azad369`):"
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

async def cb_cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو مکالمه و بازگشت به منوی اصلی"""
    query = update.callback_query
    await query.answer("عملیات لغو شد.")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, batch_size)
    
    await query.edit_message_text(
        text=menu_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def post_init(application: Application):
    """راه‌اندازی دیتابیس و تسک‌های پس‌زمینه"""
    await init_db()
    start_scheduler(application.bot)
    logger.info("ربات، دیتابیس و زمان‌بند هوشمند با موفقیت راه‌اندازی شدند.")

def main():
    """نقطه شروع اجرای برنامه"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # مکالمه افزودن دستی مقصد
    conv_add_dest = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_add_dest, pattern="^btn_start_add_dest$")],
        states={
            STATE_WAIT_ADD_DEST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_add_dest),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
    )
    
    # مکالمه تنظیم زمان‌بندی
    conv_set_delay = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_delay, pattern="^btn_set_delay$")],
        states={
            STATE_WAIT_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_delay),
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
    
    # افزودن هندلرها
    application.add_handler(CommandHandler(["start", "admin", "panel"], cmd_start))
    application.add_handler(ChatMemberHandler(cb_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(conv_add_dest)
    application.add_handler(conv_set_delay)
    application.add_handler(conv_set_tag)
    
    application.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^btn_main_menu$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_auto_send, pattern="^btn_toggle_auto$"))
    application.add_handler(CallbackQueryHandler(cb_cycle_batch_size, pattern="^btn_cycle_batch_size$"))
    application.add_handler(CallbackQueryHandler(cb_harvest_now, pattern="^btn_harvest_now$"))
    application.add_handler(CallbackQueryHandler(cb_test_send_admin, pattern="^btn_test_send_admin$"))
    application.add_handler(CallbackQueryHandler(cb_ping_all, pattern="^btn_ping_all$"))
    application.add_handler(CallbackQueryHandler(cb_clear_dead, pattern="^btn_clear_dead$"))
    application.add_handler(CallbackQueryHandler(cb_manage_destinations, pattern="^btn_manage_destinations$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_dest, pattern="^btn_toggle_dest_"))
    application.add_handler(CallbackQueryHandler(cb_show_delete_dest, pattern="^btn_show_delete_dest$"))
    application.add_handler(CallbackQueryHandler(cb_do_delete_dest, pattern="^btn_del_dest_"))
    application.add_handler(CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$"))
    
    logger.info("در حال اجرای ربات...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
