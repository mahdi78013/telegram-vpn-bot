import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import asyncio
import io
import html
from typing import Dict, Any, List, Optional, Tuple

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
    get_destination_by_id,
    set_destination_interval,
    update_destination_last_sent,
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
    reset_destination_timers,
)
from harvester import harvest_and_store_online_configs
from codespace_vip import get_latest_codespace_config, format_codespace_vip_message

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
    STATE_WAIT_DEST_DELAY,
) = range(5)

def is_admin(user_id: int) -> bool:
    """بررسی ادمین بودن کاربر (شامل آیدی‌های اصلی و جدید)"""
    from config import ADMIN_IDS, ADMIN_ID
    return user_id == ADMIN_ID or user_id in ADMIN_IDS or str(user_id) in ["748538264", "6615827337"]

def build_main_keyboard(auto_send_on: bool, batch_size: str = "3", source_mode: str = "vip") -> InlineKeyboardMarkup:
    """ساخت کیبورد اصلی شیک، کامل و بهینه برای ادمین"""
    toggle_send_text = "🟢 ارسال خودکار: [روشن]" if auto_send_on else "🔴 ارسال خودکار: [خاموش]"
    source_toggle_text = "🚀 منبع: [نت ملی VIP]" if source_mode == "vip" else "🌐 منبع: [مهسا نت]"
    
    keyboard = [
        [
            InlineKeyboardButton(f"⚡ {toggle_send_text}", callback_data="btn_toggle_auto"),
            InlineKeyboardButton(f"📦 تعداد سرور: [{batch_size} عدد]", callback_data="btn_cycle_batch_size"),
        ],
        [
            InlineKeyboardButton(f"📡 {source_toggle_text}", callback_data="btn_toggle_source_mode"),
            InlineKeyboardButton("📊 تست پینگ زنده سرورها", callback_data="btn_ping_all"),
        ],
        [
            InlineKeyboardButton("📢 مدیریت کانال‌ها و گروه‌ها", callback_data="btn_manage_destinations"),
            InlineKeyboardButton("⏱️ تنظیم زمان‌بندی ارسال", callback_data="btn_set_delay"),
        ],
        [
            InlineKeyboardButton("🏷️ تغییر تگ و نام سرورها", callback_data="btn_set_tag"),
            InlineKeyboardButton("📤 ارسال تستی (مخزن مهسا نت)", callback_data="btn_test_send_admin"),
        ],
        [
            InlineKeyboardButton("🌐🔗 لینک سابسکریپشن سراسری", callback_data="btn_universal_sub"),
            InlineKeyboardButton("🛡️ تست ساخت اکانت WireGuard", callback_data="btn_get_wireguard"),
        ],
        [
            InlineKeyboardButton("🔄 نوسازی فوری سابسکریپشن", callback_data="btn_force_refresh_sub"),
            InlineKeyboardButton("⚙️ تنظیمات پیشرفته", callback_data="btn_advanced_settings"),
        ],
        [
            InlineKeyboardButton("📈 داشبورد تله‌متری و سلامت", callback_data="btn_metrics_dashboard"),
            InlineKeyboardButton("📱 منوی کاربران (تست اپراتورها)", callback_data="btn_user_menu_view"),
        ],
        [
            InlineKeyboardButton("🔄 بروزرسانی منو", callback_data="btn_main_menu"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_user_menu_keyboard() -> InlineKeyboardMarkup:
    """منوی کاربران جهت دریافت سرور متناسب با اپراتور، سابسکریپشن و وایرگارد"""
    keyboard = [
        [
            InlineKeyboardButton("📡 کانفیگ همراه اول", callback_data="btn_user_mci"),
            InlineKeyboardButton("📱 کانفیگ ایرانسل", callback_data="btn_user_mtn"),
        ],
        [
            InlineKeyboardButton("📶 مخابرات / رایتل", callback_data="btn_user_wifi"),
            InlineKeyboardButton("💎 پروکسی پرسرعت تلگرام", callback_data="btn_user_proxy"),
        ],
        [
            InlineKeyboardButton("🛡️ دریافت کانفیگ اختصاصی وایرگارد (WireGuard)", callback_data="btn_get_wireguard"),
        ],
        [
            InlineKeyboardButton("🌐🔗 دریافت لینک سابسکریپشن یکپارچه (همه اپراتورها)", callback_data="btn_universal_sub")
        ],
        [
            InlineKeyboardButton("👑 عضویت در کانال Munti VPN", url="https://t.me/muntivpn")
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
    source_mode = settings.get("source_mode", "mahsa")
    source_title = "🚀 نت ملی VIP (۲۴ ساعته ابری)" if source_mode == "vip" else "🌐 مخازن آنلاین (مهسا نت)"
    
    try:
        raw_min = settings.get("min_delay", str(DEFAULT_MIN_DELAY))
        min_d_sec = int(float(raw_min))
        if min_d_sec < 60:
            delay_str = f"هر `{min_d_sec}` ثانیه یکبار"
        else:
            delay_str = f"هر `{min_d_sec // 60}` دقیقه یکبار"
    except Exception:
        delay_str = "هر `1` دقیقه یکبار"
        
    tag = settings.get("tag", DEFAULT_TAG)
    
    countdown = get_next_post_countdown()
    next_post_str = f"{countdown} ثانیه دیگر" if countdown is not None else "در حال تعلیق"
    
    text = (
        "👑 **پنل مدیریت ربات خودکار ارسال VPN**\n\n"
        f"⚡ **وضعیت ارسال خودکار:** {auto_send}\n"
        f"📡 **منبع ارسال کانال:** {source_title}\n"
        f"📦 **تعداد سرور در هر پست:** `{batch_size}` عدد (دسته‌ای)\n"
        f"📢 **مقاصد فعال (کانال/گروه):** `{len(active_dests)}` مورد از `{len(destinations)}`\n"
        f"⏱️ **سرعت ارسال:** {delay_str}\n"
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
        user_text = (
            f"سلام {user.first_name} عزیز! 🌹\n\n"
            "🔮 به سامانه هوشمند **Munti VPN** خوش آمدید.\n"
            "⚡ سرورها و پروکسی‌های ما توسط **هوش مصنوعی** پایش می‌شوند و پینگ سبز دارند.\n\n"
            "👇 **لطفاً اپراتور سیم‌کارت خود را انتخاب کنید:**"
        )

        await update.message.reply_text(
            text=user_text,
            reply_markup=build_user_menu_keyboard(),
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
    source_mode = settings.get("source_mode", "vip")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, batch_size, source_mode)
    
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
    source_mode = settings.get("source_mode", "vip")
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(new_status == "1", batch_size, source_mode)
    
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
    source_mode = settings.get("source_mode", "vip")
    
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, new_size, source_mode)
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def cb_toggle_source_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سوییچ هوشمند منبع ارسال خودکار کانال بین نت ملی VIP و مخازن آنلاین"""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ فقط مخصوص مدیریت است.", show_alert=True)
        return
        
    current = await get_setting("source_mode", "mahsa")
    new_mode = "vip" if current == "mahsa" else "mahsa"
    await set_setting("source_mode", new_mode)
    
    label = "🚀 نت ملی VIP" if new_mode == "vip" else "🌐 مخازن آنلاین مهسا نت"
    await query.answer(f"منبع ارسال کانال به «{label}» تغییر یافت.")
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    menu_text = await get_main_menu_text()
    reply_markup = build_main_keyboard(auto_send_on, batch_size, new_mode)
    
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

async def cb_admin_codespace_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تولید و ارسال فوری کانفیگ پرسرعت ابری اختصاصی مستقیماً به پیوی ادمین از طریق Engine"""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ این قابلیت منحصراً برای مدیریت تعریف شده است.", show_alert=True)
        return
        
    try:
        await query.answer("⏳ در حال دریافت تازه‌ترین کانفیگ ابری با حداکثر سرعت...")
    except Exception:
        pass
        
    try:
        from config_delivery_engine import delivery_engine
        from node_registry import NetworkContext
        
        tag = await get_setting("tag", DEFAULT_TAG)
        ctx = NetworkContext(carrier="all", region="all")
        result = await delivery_engine.get_best_config(context=ctx, tag=tag)
        
        msg = format_codespace_vip_message(result)
        admin_chat_id = str(ADMIN_ID)
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Error in cb_admin_codespace_vip: {e}")
        try:
            await query.message.reply_text(f"❌ خطا در آماده‌سازی کانفیگ: {e}")
        except Exception:
            pass

async def cb_metrics_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش داشبورد عملکردی و تله‌متری بلادرنگ (Observability Dashboard)"""
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("⛔ فقط مخصوص مدیریت است.", show_alert=True)
        return
        
    await query.answer("در حال محاسبه شاخص‌های تله‌متری...")
    
    from health_monitor import metrics_collector
    from node_registry import registry, NodeHealth
    
    summary = metrics_collector.get_summary()
    pool = list(registry._l2_pool.values())
    
    healthy_count = sum(1 for n in pool if n.health_state == NodeHealth.HEALTHY)
    degraded_count = sum(1 for n in pool if n.health_state == NodeHealth.DEGRADED)
    unstable_count = sum(1 for n in pool if n.health_state in (NodeHealth.UNSTABLE, NodeHealth.TIMEOUTING))
    offline_count = sum(1 for n in pool if n.health_state == NodeHealth.OFFLINE)
    recovering_count = sum(1 for n in pool if n.health_state == NodeHealth.RECOVERING)

    carrier_lines = []
    c_stats = summary.get("carrier_stats", {})
    carrier_names = {"mci": "📡 همراه اول", "mtn": "📱 ایرانسل", "wifi": "📶 مخابرات/رایتل", "all": "🌐 عمومی"}
    for k, v in c_stats.items():
        name = carrier_names.get(k, k)
        carrier_lines.append(f"  • {name}: `{v['requests']}` درخواست (میانگین پینگ: `{v['avg_latency']}ms`)")
    carrier_str = "\n".join(carrier_lines) if carrier_lines else "  • داده‌های کافی برای تفکیک هنوز ثبت نشده است."

    dash_text = (
        "📈 **داشبورد تله‌متری و پایش عملکرد (Engine v2)**\n\n"
        "⚡ **شاخص‌های کلیدی تاخیر (Latency Metrics):**\n"
        f"• میانه تاخیر (P50 Latency): 🟢 `{summary['p50_latency']}ms`\n"
        f"• چارک ۷۵ (P75 Latency): 🟡 `{summary['p75_latency']}ms`\n"
        f"• صدک ۹۵ (P95 Latency): 🟠 `{summary['p95_latency']}ms`\n"
        f"• صدک ۹۹ (P99 Latency): 🔴 `{summary['p99_latency']}ms`\n"
        f"• میانگین TTFB: ⚡ `{summary['avg_ttfb']}ms`\n\n"
        "🛡️ **شاخص‌های پایداری و ضد تایم‌اوت:**\n"
        f"• نرخ موفقیت اتصال: 🟢 `{summary['success_rate']}%`\n"
        f"• نرخ تایم‌اوت: 🛡️ `{summary['timeout_rate']}%` (هدف: صفر)\n"
        f"• نرخ برخورد کش (Cache Hit Rate): ⚡ `{summary['cache_hit_rate']}%`\n"
        f"• کل درخواست‌های پایش‌شده: `{summary['total_requests']}` عدد\n\n"
        "🌐 **تفکیک عملکرد اپراتورها (Carrier Performance):**\n"
        f"{carrier_str}\n\n"
        "📊 **وضعیت سلامت استخر نودها (Node Health States):**\n"
        f"• 🟢 کاملاً سالم (Healthy): `{healthy_count}`\n"
        f"• 🟡 نیازمند بهینه‌سازی (Degraded): `{degraded_count}`\n"
        f"• 🟠 دارای نوسان (Unstable): `{unstable_count}`\n"
        f"• 🔵 در حال بازگشت (Recovering): `{recovering_count}`\n"
        f"• 🔴 قطع / قرنطینه (Offline): `{offline_count}`\n\n"
        "✨ *موتور هوشمند هر ۳۰ ثانیه سلامت سرورها و هر ۳۰ دقیقه مخازن را رفرش می‌کند.*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 بروزرسانی شاخص‌ها", callback_data="btn_metrics_dashboard"),
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
        ]
    ]
    
    await query.edit_message_text(
        text=dash_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# ----------------- بخش دریافت هوشمند کانفیگ و پروکسی کاربران بر اساس اپراتور -----------------

async def cb_user_menu_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی کاربران برای تست یا استفاده"""
    query = update.callback_query
    await query.answer()
    
    user_text = (
        "🔮 **دریافت هوشمند سرور بر اساس اپراتور:**\n\n"
        "سرورها متناسب با هر سیم‌کارت بهینه‌سازی شده‌اند و دارای پینگ پایدار می‌باشند.\n"
        "👇 لطفاً اپراتور خود را انتخاب کنید:"
    )
    await query.message.reply_text(
        text=user_text,
        reply_markup=build_user_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_deliver_operator_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال کانفیگ اختصاصی و بهینه‌سازی‌شده برای اپراتور انتخابی کاربر از طریق Engine v2"""
    query = update.callback_query
    data = query.data
    
    op_map = {
        "btn_user_mci": ("📡 همراه اول", "mci"),
        "btn_user_mtn": ("📱 ایرانسل", "mtn"),
        "btn_user_wifi": ("📶 مخابرات و رایتل", "wifi"),
    }
    
    op_title, op_key = op_map.get(data, ("🌐 تمام اپراتورها", "all"))
    await query.answer(f"در حال آماده‌سازی بهترین سرور {op_title}...")
    
    tag = await get_setting("tag", DEFAULT_TAG)
    
    from config_delivery_engine import delivery_engine
    from node_registry import NetworkContext
    from health_monitor import metrics_collector, RequestMetric
    
    ctx = NetworkContext(carrier=op_key, region="all")
    t0 = time.time()
    
    # دریافت بهترین سرور با الگوریتم امتیازدهی تطبیقی
    result = await delivery_engine.get_best_config(context=ctx, tag=tag)
    elapsed_ms = int((time.time() - t0) * 1000)
    
    # ثبت تله‌متری
    await metrics_collector.record(RequestMetric(
        timestamp=time.time(),
        carrier=op_key,
        network_type="mobile" if op_key in ("mci", "mtn") else "fixed",
        region="all",
        latency_ms=elapsed_ms,
        ttfb_ms=elapsed_ms,
        retry_count=0,
        timeout_occurred=False,
        cache_hit="L1" in result.get("cache_level", ""),
        cache_level=result.get("cache_level", "L1"),
        node_id=result.get("node_id", 0)
    ))
    
    conf_to_send = result["direct"]
    escaped_conf = html.escape(conf_to_send)
    flag = result.get("flag", "🇩🇪")
    proto = result.get("proto", "VLESS Reality")
    ping = result.get("ping", 65)
    score = int(result.get("score", 85))
    
    from proxy_manager import get_current_top_proxies, format_proxies_text
    proxies_line = format_proxies_text(get_current_top_proxies(3))
    
    msg = (
        f"👑 <b>کانفیگ اختصاصی {op_title}</b>\n\n"
        f"📍 <b>موقعیت سرور :</b> {flag} (<b>{proto.upper()}</b>)\n"
        f"⚡ <b>پینگ پایدار :</b> <code>{ping}ms</code> (شاخص کیفیت: <code>{score}/100</code>)\n"
        f"🔌 <b>وضعیت اتصال :</b> تست‌شده و بدون قطعی در سراسر ایران\n"
        f"-----------------\n\n"
        f"<pre><code class=\"language-copy\">{escaped_conf}</code></pre>\n\n"
        f"-----------------\n"
        f"{proxies_line}\n\n"
        f"👑 کانال رسمی ما: <a href=\"https://t.me/muntivpn\">@Muntivpn</a>\n\n"
        f"✅ {tag}"
    )
    
    await query.message.reply_text(
        text=msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def cb_deliver_user_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال پروکسی‌های پرسرعت تلگرام به کاربر"""
    query = update.callback_query
    await query.answer("در حال دریافت ۳ پروکسی برتر...")
    
    from proxy_manager import get_current_top_proxies
    proxies = get_current_top_proxies(3)
    
    if not proxies:
        await query.message.reply_text("⚠️ در حال حاضر پروکسی تازه‌ای ثبت نشده است. لطفاً به کانال سر بزنید:\n@muntivpn")
        return

        
    tag = await get_setting("tag", DEFAULT_TAG)
    
    lines = [
        "💎 <b>پروکسی‌های پرسرعت و پایدار تلگرام (MTProto):</b>\n",
        "💡 روی هر پروکسی کلیک کنید تا با یک لمس فعال شود:\n"
    ]
    
    for idx, p in enumerate(proxies, 1):
        ping_str = f"⚡ <code>{p['ping']}ms</code>" if p.get("ping", 0) > 0 else "⚡ <code>پرسرعت</code>"
        link = p["link"]
        lines.append(f"{idx}️⃣ <a href=\"{link}\"><b>اتصال به پروکسی شماره {idx}</b></a> ({ping_str})")
        
    lines.append("\n👑 کانال رسمی ما: <a href=\"https://t.me/muntivpn\">@Muntivpn</a>\n")
    lines.append(f"✅ {tag}")
    
    await query.message.reply_text(
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def cb_get_universal_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال لینک سابسکریپشن یکپارچه و دائمی به کاربر پس از تست و انتخاب زنده ۱۰ سرور پینگ‌سبز"""
    query = update.callback_query
    await query.answer("🔍 در حال اسکن و تست زنده ۱۰ سرور پرسرعت...")
    
    # پیام موقت جهت تفکر و اسکن سرورها
    status_msg = await query.message.reply_text(
        "⏳ <b>در حال ارزیابی اتصال و غربالگری ۱۰ سرور با پینگ سبز...</b>",
        parse_mode=ParseMode.HTML
    )
    
    tag = await get_setting("tag", DEFAULT_TAG)
    
    try:
        from codespace_vip import generate_and_publish_universal_sub
        direct_cdn_url = await generate_and_publish_universal_sub(tag=tag, target_count=10)
    except Exception:
        direct_cdn_url = "https://cdn.jsdelivr.net/gh/mahdi78013/static-web-content@main/assets/d9f3a7c2.dat"
    
    msg = (
        "🌐 <b>لینک سابسکریپشن اختصاصی (۱۰ سرور تست‌شده با پینگ سبز):</b>\n\n"
        "🔮 <b>شامل ۱۰ سرور گلچین‌شده با تضمین اتصال و پینگ پایدار:</b>\n"
        "• 📱 بهینه‌شده برای ایرانسل (Reality + Vision)\n"
        "• 📡 بهینه‌شده برای همراه اول (MCI Reality)\n"
        "• 📶 مخابرات، رایتل و وای‌فای خانگی\n"
        "• ⚡ پروتکل‌های استریم و گیمینگ\n\n"
        "👇 <b>لینک مستقیم ضد فیلتر (بدون نیاز به وی‌پی‌ان جهت اضافه کردن):</b>\n\n"
        f"<code>{direct_cdn_url}</code>\n\n"
        "-----------------\n"
        "💡 <b>آموزش اتصال سریع در ۲ مرحله:</b>\n"
        "1️⃣ روی کادر بالا بزنید تا لینک به صورت خودکار کپی شود.\n"
        "2️⃣ در برنامه <b>Hiddify</b> یا <b>v2rayNG</b>، در بخش <b>Subscription (گروه‌های اشتراک)</b> این لینک را وارد کنید.\n\n"
        "🌟 <b>ترفند اتصال پایدار:</b>\n"
        "• در هیدیفای اتصال را روی <b>lowest</b> یا <b>balance</b> بگذارید تا خودکار به سریع‌ترین سرور وصل شوید.\n"
        "• گزینه <b>Auto-Update (بروزرسانی خودکار)</b> را در برنامه روی ۱ ساعت بگذارید تا همیشه سرورهای نو داشته باشید.\n\n"
        f"✅ {tag}"
    )
    
    await status_msg.edit_text(
        text=msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )



async def cb_force_refresh_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نوسازی و غربالگری بلادرنگ تمام سرورهای سابسکریپشن توسط ادمین"""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("❌ دسترسی غیرمجاز.")
        return
        
    await query.answer("🔄 در حال تست زنده پینگ و غربالگری ۵۰ سرور سابسکریپشن...")
    try:
        from codespace_vip import generate_and_publish_universal_sub
        tag = await get_setting("tag", DEFAULT_TAG)
        url = await generate_and_publish_universal_sub(tag=tag)
        await query.message.reply_text(
            "✅ <b>سابسکریپشن سراسری با موفقیت غربالگری و نوسازی شد!</b>\n\n"
            "• تمامی سرورهای سوخته یا کند حذف شدند.\n"
            "• سرورهای جدید تست‌شده با پینگ سبز جایگزین شدند.\n"
            "• فایل سابسکریپشن روی مخزن گیت‌هاب بروزرسانی شد.\n\n"
            f"🔗 <code>{url}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as ex:
        await query.message.reply_text(f"⚠️ خطا در نوسازی سابسکریپشن: {ex}")


async def cb_get_wireguard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تولید و ارسال فایل آماده .conf کانفیگ اختصاصی WireGuard به کاربر"""
    query = update.callback_query
    await query.answer("🛡️ در حال صدور فایل کانفیگ WireGuard...")
    
    status_msg = await query.message.reply_text(
        "⏳ <b>در حال تولید کلیدهای امنیتی و ساخت فایل آماده کانفیگ WireGuard...</b>",
        parse_mode=ParseMode.HTML
    )
    
    tag = await get_setting("tag", DEFAULT_TAG)
    
    try:
        from wireguard_engine import generate_warp_wireguard_config
        res = await generate_warp_wireguard_config(tag=tag)
        wg_uri = res.get("wg_uri", "")
        conf_text = res.get("conf_text", "")
        endpoint = res.get("endpoint", "162.159.192.1:2408")
        
        # ساخت فایل مجازی .conf در حافظه
        conf_bytes = conf_text.encode("utf-8")
        file_doc = io.BytesIO(conf_bytes)
        file_doc.name = "Munti_WireGuard.conf"
        
        escaped_uri = html.escape(wg_uri)
        
        caption = (
            "🛡️ <b>فایل کانفیگ اختصاصی WireGuard (Warp VIP)</b>\n\n"
            "⚡ <b>ویژگی‌ها:</b> ضد پکت‌لاس، حجم نامحدود، سرعت موشکی در استریم و گیمینگ\n"
            f"🔌 <b>اندپوینت اتصال تمیز:</b> <code>{endpoint}</code>\n"
            "-----------------\n\n"
            "👇 <b>لینک سریع (جهت کپی در هیدیفای / v2rayNG):</b>\n\n"
            f"<pre><code class=\"language-copy\">{escaped_uri}</code></pre>\n\n"
            "-----------------\n"
            "💡 <b>روش اتصال با ۱ کلیک:</b>\n"
            "روی فایل ضمیمه‌شده کلیک کنید و آن را با برنامه <b>WireGuard</b>، <b>Hiddify</b> یا <b>v2rayNG</b> باز کنید تا خودکار نصب شود.\n\n"
            f"✅ {tag}"
        )
        
        await query.message.reply_document(
            document=file_doc,
            filename="Munti_WireGuard.conf",
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in cb_get_wireguard: {e}")
        await status_msg.edit_text("❌ متاسفانه در صدور فایل کانفیگ خطایی رخ داد. لطفاً چند لحظه بعد مجدداً تلاش فرمایید.")





# ----------------- بخش مدیریت کانال‌ها و گروه‌های مقصد (Multi-Destination) -----------------

def format_interval_text(interval_sec: int) -> str:
    """تبدیل ثانیه به متن فارسی روان و خوانا"""
    if not interval_sec or interval_sec <= 0:
        return "تنظیم‌نشده"
    if interval_sec == 28800:
        return "روزی ۳ بار (هر ۸ ساعت)"
    if interval_sec == 86400:
        return "روزی ۱ بار (هر ۲۴ ساعت)"
    if interval_sec == 43200:
        return "روزی ۲ بار (هر ۱۲ ساعت)"
    if interval_sec == 21600:
        return "روزی ۴ بار (هر ۶ ساعت)"
    if interval_sec == 14400:
        return "روزی ۶ بار (هر ۴ ساعت)"
    if interval_sec >= 3600:
        hours = interval_sec / 3600.0
        if hours.is_integer():
            return f"هر {int(hours)} ساعت"
        return f"هر {hours:g} ساعت"
    if interval_sec >= 60:
        mins = interval_sec / 60.0
        if mins.is_integer():
            return f"هر {int(mins)} دقیقه"
        return f"هر {mins:g} دقیقه"
    return f"هر {interval_sec} ثانیه"

async def build_destinations_keyboard() -> InlineKeyboardMarkup:
    """ساخت کیبورد مدیریت کانال‌ها و گروه‌ها با نمایش زمان‌بندی تفکیک‌شده"""
    destinations = await get_all_destinations()
    keyboard = []
    
    for d in destinations:
        did = d["id"]
        title = d.get("title") or d["chat_id"]
        is_active = d.get("is_active", 1) == 1
        status_icon = "🟢" if is_active else "🔴"
        chat_type_icon = "📢" if d.get("chat_type") == "channel" else "👥"
        
        interval_sec = d.get("interval_seconds") or 28800
        int_str = format_interval_text(interval_sec)
            
        btn_text = f"{status_icon} {chat_type_icon} {title} | ⏱️ {int_str}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"btn_open_dest_{did}")])
        
    keyboard.append([
        InlineKeyboardButton("➕ افزودن دستی کانال یا گروه", callback_data="btn_start_add_dest"),
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
        "📢 <b>مدیریت کانال‌ها و گروه‌های مقصد (زمان‌بندی تفکیک‌شده):</b>\n\n"
        "💡 <b>راهنمای هوشمند:</b>\n"
        "برای هر کانال می‌توانید <b>زمان‌بندی و سرعت ارسال جداگانه</b> تعیین کنید!\n"
        "<i>(تنظیم پیش‌فرض برای تمام کانال‌ها: روزی ۳ بار / هر ۸ ساعت)</i>\n\n"
        f"📌 <b>تعداد کل مقاصد:</b> <code>{len(destinations)}</code> (🟢 فعال: <code>{active_count}</code>)\n\n"
        "👇 <b>روی هر کانال/گروه کلیک کنید تا تنظیمات اختصاصی آن باز شود:</b>"
    )
    
    reply_markup = await build_destinations_keyboard()
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def cb_open_single_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پنل اختصاصی تنظیمات و زمان‌بندی یک کانال/گروه خاص"""
    query = update.callback_query
    await query.answer()
    
    dest_id_str = query.data.replace("btn_open_dest_", "")
    if not dest_id_str.isdigit():
        return
    dest_id = int(dest_id_str)
    d = await get_destination_by_id(dest_id)
    if not d:
        await query.answer("مقصد یافت نشد!", show_alert=True)
        return
        
    title = d.get("title") or d["chat_id"]
    chat_id = d["chat_id"]
    chat_type_name = "کانال" if d.get("chat_type") == "channel" else "گروه"
    is_active = d.get("is_active", 1) == 1
    status_text = "🟢 فعال و روشن (ارسال انجام می‌شود)" if is_active else "🔴 غیرفعال و خاموش"
    
    interval_sec = d.get("interval_seconds") or 28800
    int_desc = format_interval_text(interval_sec)
        
    last_sent = d.get("last_sent_at") or "هنوز ارسالی انجام نشده"
    
    text = (
        f"⚙️ <b>تنظیمات اختصاصی {chat_type_name}:</b>\n\n"
        f"📌 <b>عنوان:</b> {title}\n"
        f"🆔 <b>شناسه:</b> <code>{chat_id}</code>\n"
        f"📂 <b>نوع:</b> {chat_type_name}\n"
        f"⚡ <b>وضعیت ارسال:</b> {status_text}\n"
        f"⏱️ <b>زمان‌بندی ارسال این کانال:</b> <b>{int_desc}</b>\n"
        f"🕒 <b>آخرین ارسال:</b> <code>{last_sent}</code>\n\n"
        f"👇 از گزینه‌های زیر برای تنظیم استفاده کنید:"
    )
    
    toggle_btn_text = "🔴 خاموش کردن این مقصد" if is_active else "🟢 روشن کردن این مقصد"
    
    keyboard = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"btn_toggle_dest_{dest_id}"),
            InlineKeyboardButton("⏱️ تغییر زمان‌بندی این کانال", callback_data=f"btn_set_dest_delay_{dest_id}"),
        ],
        [
            InlineKeyboardButton("📤 ارسال تستی به این مقصد", callback_data=f"btn_test_dest_{dest_id}"),
            InlineKeyboardButton("🗑️ حذف این مقصد", callback_data=f"btn_del_dest_{dest_id}"),
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست مقاصد", callback_data="btn_manage_destinations")
        ]
    ]
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def cb_toggle_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغییر وضعیت فعال/غیرفعال یک مقصد"""
    query = update.callback_query
    dest_id_str = query.data.replace("btn_toggle_dest_", "")
    
    if dest_id_str.isdigit():
        dest_id = int(dest_id_str)
        new_status = await toggle_destination(dest_id)
        status_msg = "روشن شد 🟢" if new_status == 1 else "خاموش شد 🔴"
        await query.answer(f"وضعیت مقصد: {status_msg}")
        
        # باز کردن مجدد پنل این کانال
        query.data = f"btn_open_dest_{dest_id}"
        await cb_open_single_dest(update, context)

async def cb_test_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال تستی یک بسته سرور به کانال مورد نظر"""
    query = update.callback_query
    dest_id_str = query.data.replace("btn_test_dest_", "")
    if not dest_id_str.isdigit():
        return
    dest_id = int(dest_id_str)
    d = await get_destination_by_id(dest_id)
    if not d:
        return
    chat_id = d["chat_id"]
    await query.answer(f"در حال ارسال تستی به {chat_id}...")
    success, msg = await send_single_post(context.bot, target_chat_id=chat_id, is_test=True)
    if success:
        await query.answer("✅ پست تستی با موفقیت به کانال ارسال شد!", show_alert=True)
    else:
        await query.answer(f"❌ خطا در ارسال: {msg}", show_alert=True)

async def cb_do_delete_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف قطعی یک مقصد"""
    query = update.callback_query
    dest_id_str = query.data.replace("btn_del_dest_", "")
    
    if dest_id_str.isdigit():
        await delete_destination(int(dest_id_str))
        await query.answer("مقصد با موفقیت حذف شد! 🗑️", show_alert=True)
        
    await cb_manage_destinations(update, context)

async def cb_start_set_dest_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند تنظیم زمان‌بندی اختصاصی یک کانال"""
    query = update.callback_query
    await query.answer()
    
    dest_id_str = query.data.replace("btn_set_dest_delay_", "")
    if not dest_id_str.isdigit():
        return ConversationHandler.END
    dest_id = int(dest_id_str)
    d = await get_destination_by_id(dest_id)
    if not d:
        return ConversationHandler.END
        
    context.user_data["target_dest_id"] = dest_id
    title = d.get("title") or d["chat_id"]
    
    interval_sec = d.get("interval_seconds") or 28800
    cur_desc = format_interval_text(interval_sec)
        
    text = (
        f"⏱️ <b>تنظیم زمان‌بندی اختصاصی برای کانال «{title}»:</b>\n\n"
        f"📌 <b>سرعت فعلی این کانال:</b> <code>{cur_desc}</code>\n\n"
        "💡 <b>می‌توانید به هر یک از روش‌های زیر مقدار دلخواه را ارسال فرمایید:</b>\n\n"
        "1️⃣ <b>تعداد در روز (پیشنهادی):</b>\n"
        "• <code>روزی 3</code> 👈 ۳ بار در روز (هر ۸ ساعت)\n"
        "• <code>روزی 1</code> 👈 ۱ بار در روز (هر ۲۴ ساعت)\n"
        "• <code>روزی 2</code> 👈 ۲ بار در روز (هر ۱۲ ساعت)\n"
        "• <code>روزی 6</code> 👈 ۶ بار در روز (هر ۴ ساعت)\n\n"
        "2️⃣ <b>فاصله به ساعت یا دقیقه:</b>\n"
        "• <code>8 ساعت</code> 👈 هر ۸ ساعت\n"
        "• <code>4 ساعت</code> 👈 هر ۴ ساعت\n"
        "• <code>1 ساعت</code> 👈 هر ۱ ساعت\n"
        "• <code>30</code> 👈 هر ۳۰ دقیقه\n"
        "• <code>15</code> 👈 هر ۱۵ دقیقه\n\n"
        "3️⃣ <b>تعداد در زمان (نرخ):</b>\n"
        "• <code>2 در 1</code> 👈 ۲ بار در هر ۱ دقیقه\n\n"
        "لطفاً مقدار دلخواه برای این کانال را ارسال کنید:"
    )
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    return STATE_WAIT_DEST_DELAY

async def handle_receive_dest_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره زمان‌بندی اختصاصی مقصد"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    dest_id = context.user_data.get("target_dest_id")
    if not dest_id:
        return ConversationHandler.END
        
    text = update.message.text.strip()
    total_seconds, desc = parse_schedule_input(text)
    
    if total_seconds is None:
        await update.message.reply_text(
            f"⚠️ {desc}\nلطفاً مثلاً بفرستید <code>روزی 3</code> یا <code>8 ساعت</code> یا <code>15</code>:",
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return STATE_WAIT_DEST_DELAY
        
    await set_destination_interval(dest_id, total_seconds)
    
    d = await get_destination_by_id(dest_id)
    title = d.get("title") or d["chat_id"] if d else ""
    chat_id = d.get("chat_id") if d else None
    
    if chat_id:
        import scheduler
        loop = asyncio.get_running_loop()
        scheduler._destination_next_send_time[chat_id] = loop.time() + total_seconds
        
    await update.message.reply_text(
        f"✅ <b>زمان‌بندی اختصاصی کانال «{title}» با موفقیت تنظیم شد!</b>\n\n🕒 <b>برنامه جدید:</b> {desc}",
        reply_markup=await build_destinations_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def cb_start_add_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند افزودن دستی مقصد"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "➕ <b>افزودن دستی کانال یا گروه مقصد:</b>\n\n"
        "💡 لطفاً آیدی کانال یا گروه را بفرستید (مثلاً <code>@Internet_azad369</code> یا <code>-1001234567890</code>).\n\n"
        "<i>(مطمئن شوید قبل از ارسال، ربات را در آنجا ادمین کرده‌اید)</i>"
    )
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"خطا در ویرایش پیام افزودن مقصد: {e}")
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=build_cancel_keyboard(),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
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

def parse_schedule_input(text: str) -> Tuple[Optional[int], str]:
    """
    تحلیل هوشمند ورودی زمان‌بندی کاربر:
    1. به صورت روزانه: مثلا 'روزی 3' یا '3 بار در روز' یا '3 عدد در روز'
    2. به صورت ساعت: مثلا '8 ساعت' یا 'هر 8 ساعت'
    3. به صورت دقیقه: مثلا '15' یا '30'
    4. به صورت نرخ: مثلا '2 در 1'
    """
    import re
    text = text.strip().replace("٫", ".").replace("،", ".")
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    for i in range(10):
        text = text.replace(persian_digits[i], str(i)).replace(arabic_digits[i], str(i))
        
    # 1. بررسی الگوهای روزانه (مثلاً روزی ۳ یا ۳ بار در روز یا روزی ۳ عدد)
    match_daily = re.search(r"(?:روزی|در روز|روزانه)\s*(\d+(?:\.\d+)?)\s*(?:عدد|بار|پست|کانفیگ|تا)?", text, re.IGNORECASE)
    if not match_daily:
        match_daily = re.search(r"(\d+(?:\.\d+)?)\s*(?:عدد|بار|پست|کانفیگ|تا)?\s*(?:روزی|در روز|روزانه)", text, re.IGNORECASE)
    if match_daily:
        count = float(match_daily.group(1))
        if count <= 0:
            return None, "تعداد در روز باید بزرگتر از صفر باشد."
        total_seconds = max(10, int(86400.0 / count))
        hours = total_seconds / 3600.0
        if hours >= 1:
            desc = f"**روزی {count:g} بار** (هر {hours:g} ساعت یک ارسال)"
        else:
            desc = f"**روزی {count:g} بار** (هر {total_seconds // 60} دقیقه یک ارسال)"
        return total_seconds, desc

    # 2. بررسی الگوی ساعت (مثلاً ۸ ساعت یا هر ۸ ساعت)
    match_hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:ساعت|ساعته|hours?|hr|h)", text, re.IGNORECASE)
    if match_hours:
        hours = float(match_hours.group(1))
        if hours <= 0:
            return None, "مقدار ساعت باید بزرگتر از صفر باشد."
        total_seconds = max(10, int(hours * 3600.0))
        times_per_day = 24.0 / hours
        if times_per_day >= 1 and times_per_day.is_integer():
            desc = f"**هر {hours:g} ساعت یکبار** (روزی {int(times_per_day)} بار ارسال)"
        else:
            desc = f"**هر {hours:g} ساعت یکبار**"
        return total_seconds, desc

    # 3. بررسی الگوی نرخ (مثلاً ۲ در ۱ یا ۲ در ۲۴)
    match_rate = re.search(r"(\d+(?:\.\d+)?)\s*(?:در|/|per|in|توی)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match_rate:
        count = float(match_rate.group(1))
        unit_val = float(match_rate.group(2))
        if count <= 0 or unit_val <= 0:
            return None, "مقادیر باید بزرگتر از صفر باشند."
        if unit_val == 24:
            total_seconds = max(10, int((24 * 3600.0) / count))
            hours = total_seconds / 3600.0
            desc = f"**{count:g} بار در ۲۴ ساعت** (هر {hours:g} ساعت یک ارسال)"
            return total_seconds, desc
        total_seconds = max(10, int((unit_val * 60.0) / count))
        desc = f"**{count:g} بار در هر {unit_val:g} دقیقه** (هر `{total_seconds}` ثانیه یک ارسال)"
        return total_seconds, desc

    # 4. بررسی عدد ساده (پیش‌فرض بر حسب دقیقه)
    try:
        val = float(text)
        if val <= 0:
            return None, "مقدار زمان باید بزرگتر از صفر باشد."
        total_seconds = max(10, int(val * 60.0))
        if total_seconds < 60:
            desc = f"**هر `{total_seconds}` ثانیه یکبار**"
        elif total_seconds >= 3600:
            hours = total_seconds / 3600.0
            desc = f"**هر `{hours:g}` ساعت یکبار**"
        else:
            desc = f"**هر `{val:g}` دقیقه یکبار**"
        return total_seconds, desc
    except ValueError:
        return None, "فرمت وارد شده صحیح نیست."

async def cb_start_set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند تنظیم زمان‌بندی و سرعت ارسال"""
    query = update.callback_query
    await query.answer()
    
    settings = await get_all_settings()
    raw_min = settings.get("min_delay", str(DEFAULT_MIN_DELAY))
    try:
        min_d_sec = int(float(raw_min))
        if min_d_sec < 60:
            delay_str = f"هر {min_d_sec} ثانیه"
        else:
            delay_str = f"هر {min_d_sec // 60} دقیقه"
    except Exception:
        delay_str = "هر 1 دقیقه"
        
    text = (
        "⏱️ <b>تنظیم سرعت و زمان‌بندی ارسال خودکار:</b>\n\n"
        f"🕒 <b>سرعت فعلی ارسال:</b> <code>{delay_str}</code>\n\n"
        "💡 <b>می‌توانید به هر یک از روش‌های زیر زمان دلخواه را ارسال فرمایید:</b>\n"
        "• <code>1</code> 👈 هر ۱ دقیقه یکبار\n"
        "• <code>5</code> 👈 هر ۵ دقیقه یکبار\n"
        "• <code>15</code> 👈 هر ۱۵ دقیقه یکبار\n"
        "• <code>1 ساعت</code> 👈 هر ۱ ساعت یکبار\n"
        "• <code>4 ساعت</code> 👈 هر ۴ ساعت یکبار\n"
        "• <code>روزی 3</code> 👈 ۳ بار در روز (هر ۸ ساعت)\n"
        "• <code>روزی 1</code> 👈 ۱ بار در روز (هر ۲۴ ساعت)\n\n"
        "👇 <b>لطفاً مقدار زمان جدید را تایپ و ارسال کنید:</b>"
    )
    
    keyboard = [
        [InlineKeyboardButton("⚙️ تنظیم تفکیک‌شده برای هر کانال", callback_data="btn_manage_dest_delays")],
        [InlineKeyboardButton("❌ انصراف و بازگشت", callback_data="btn_cancel")]
    ]
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    return STATE_WAIT_DELAY

async def cb_manage_dest_delays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست کانال‌ها جهت تنظیم زمان‌بندی تفکیک‌شده"""
    query = update.callback_query
    await query.answer()
    
    destinations = await get_all_destinations()
    keyboard = []
    
    for d in destinations:
        did = d["id"]
        title = d.get("title") or d["chat_id"]
        chat_type_icon = "📢" if d.get("chat_type") == "channel" else "👥"
        interval_sec = d.get("interval_seconds") or 28800
        int_str = format_interval_text(interval_sec)
            
        btn_text = f"{chat_type_icon} {title} (فعلی: {int_str})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"btn_set_dest_delay_{did}")])
        
    keyboard.append([
        InlineKeyboardButton("🔙 بازگشت به منو", callback_data="btn_main_menu"),
    ])
    
    text = (
        "⚙️ <b>تنظیم زمان‌بندی تفکیک‌شده برای هر کانال:</b>\n\n"
        "👇 لطفاً کانال مورد نظر را برای تنظیم سرعت اختصاصی انتخاب کنید:"
    )
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def handle_receive_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره زمان‌بندی و سرعت جدید"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text.strip()
    total_seconds, desc = parse_schedule_input(text)
    
    if total_seconds is None:
        await update.message.reply_text(
            f"⚠️ {desc}\nلطفاً مثلاً بفرستید `1` یا `2 در 1` یا `3/5`:",
            reply_markup=build_cancel_keyboard()
        )
        return STATE_WAIT_DELAY
        
    await set_setting("min_delay", str(total_seconds))
    await set_setting("max_delay", str(total_seconds + 5))
    reset_destination_timers()
    
    settings = await get_all_settings()
    auto_send_on = settings.get("auto_send", "0") == "1"
    batch_size = settings.get("batch_size", "3")
    source_mode = settings.get("source_mode", "vip")
    
    await update.message.reply_text(
        f"✅ **سرعت ارسال با موفقیت تنظیم شد!**\n\n🕒 **برنامه جدید:** {desc}",
        reply_markup=build_main_keyboard(auto_send_on, batch_size, source_mode),
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
        "🏷️ <b>تغییر نام و تگ سرورها:</b>\n\n"
        f"تگ فعلی: <code>{cur_tag}</code>\n\n"
        "لطفاً آیدی جدیدی که می‌خواهید روی سرورها قرار گیرد را بفرستید (مثلاً <code>@Internet_azad369</code>):"
    )
    
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=build_cancel_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"خطا در ویرایش پیام تگ: {e}")
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=build_cancel_keyboard(),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
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
    
    try:
        await query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        try:
            await query.message.reply_text(
                text=menu_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
    return ConversationHandler.END

async def post_init(application: Application):
    """راه‌اندازی دیتابیس و تسک‌های پس‌زمینه"""
    await init_db()
    start_scheduler(application.bot)
    try:
        from codespace_vip import setup_and_start_local_node
        setup_and_start_local_node()
    except Exception as e:
        logger.error(f"Error starting local 24/7 cloud node: {e}")
        
    # دریافت و تست فوری سرورهای Reality و مخازن آنلاین در پس‌زمینه هنگام بوت
    async def initial_harvest():
        try:
            from harvester import harvest_and_store_online_configs
            from database import get_active_source_urls
            sources = await get_active_source_urls()
            await harvest_and_store_online_configs(sources=sources, instant_test_count=100)
            logger.info("✅ دریافت اولیه سرورهای Reality و مخازن آنلاین با موفقیت انجام شد.")
        except Exception as ex:
            logger.warning(f"Error in initial harvest: {ex}")
            
    asyncio.create_task(initial_harvest())
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
        per_message=False,
        allow_reentry=True,
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
        per_message=False,
        allow_reentry=True,
    )
    
    # مکالمه تنظیم زمان‌بندی کلی
    conv_set_delay = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_delay, pattern="^btn_set_delay$")],
        states={
            STATE_WAIT_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_delay),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
        per_message=False,
        allow_reentry=True,
    )
    
    # مکالمه تنظیم زمان‌بندی اختصاصی یک کانال یا گروه خاص
    conv_set_dest_delay = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_set_dest_delay, pattern="^btn_set_dest_delay_")],
        states={
            STATE_WAIT_DEST_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_dest_delay),
            ]
        },
        fallbacks=[CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$")],
        per_message=False,
        allow_reentry=True,
    )
    
    # افزودن هندلرها
    application.add_handler(CommandHandler(["start", "admin", "panel"], cmd_start))
    application.add_handler(ChatMemberHandler(cb_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(conv_add_dest)
    application.add_handler(conv_set_tag)
    application.add_handler(conv_set_delay)
    application.add_handler(conv_set_dest_delay)
    
    application.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^btn_main_menu$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_auto_send, pattern="^btn_toggle_auto$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_source_mode, pattern="^btn_toggle_source_mode$"))
    application.add_handler(CallbackQueryHandler(cb_cycle_batch_size, pattern="^btn_cycle_batch_size$"))
    application.add_handler(CallbackQueryHandler(cb_harvest_now, pattern="^btn_harvest_now$"))
    application.add_handler(CallbackQueryHandler(cb_test_send_admin, pattern="^btn_test_send_admin$"))
    application.add_handler(CallbackQueryHandler(cb_admin_codespace_vip, pattern="^btn_codespace_vip$"))
    application.add_handler(CallbackQueryHandler(cb_metrics_dashboard, pattern="^btn_metrics_dashboard$"))
    application.add_handler(CallbackQueryHandler(cb_ping_all, pattern="^btn_ping_all$"))
    application.add_handler(CallbackQueryHandler(cb_clear_dead, pattern="^btn_clear_dead$"))
    application.add_handler(CallbackQueryHandler(cb_manage_dest_delays, pattern="^btn_manage_dest_delays$"))
    application.add_handler(CallbackQueryHandler(cb_manage_destinations, pattern="^btn_manage_destinations$"))
    application.add_handler(CallbackQueryHandler(cb_open_single_dest, pattern="^btn_open_dest_"))
    application.add_handler(CallbackQueryHandler(cb_toggle_dest, pattern="^btn_toggle_dest_"))
    application.add_handler(CallbackQueryHandler(cb_test_dest, pattern="^btn_test_dest_"))
    application.add_handler(CallbackQueryHandler(cb_do_delete_dest, pattern="^btn_del_dest_"))
    
    # هندلرهای کاربری و انتخاب اپراتور
    application.add_handler(CallbackQueryHandler(cb_user_menu_view, pattern="^btn_user_menu_view$"))
    application.add_handler(CallbackQueryHandler(cb_deliver_operator_config, pattern="^btn_user_(mci|mtn|wifi)$"))
    application.add_handler(CallbackQueryHandler(cb_deliver_user_proxy, pattern="^btn_user_proxy$"))
    application.add_handler(CallbackQueryHandler(cb_get_universal_sub, pattern="^btn_universal_sub$"))
    application.add_handler(CallbackQueryHandler(cb_force_refresh_sub, pattern="^btn_force_refresh_sub$"))
    application.add_handler(CallbackQueryHandler(cb_get_wireguard, pattern="^btn_get_wireguard$"))




    
    application.add_handler(CallbackQueryHandler(cb_cancel_conversation, pattern="^btn_cancel$"))
    
    logger.info("در حال اجرای ربات...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
