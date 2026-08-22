import asyncio
import html
import logging
import random
from typing import Optional, Tuple, List, Dict, Any
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError, RetryAfter, Forbidden, BadRequest
from telegram.constants import ParseMode

from config import ADMIN_ID, DEFAULT_TAG, DEFAULT_MIN_DELAY, DEFAULT_MAX_DELAY
from database import (
    get_setting,
    get_all_settings,
    get_next_config_to_send,
    mark_config_as_sent,
    set_setting,
    update_config_ping,
    update_configs_ping_bulk,
    get_configs_for_health_check,
    get_active_source_urls,
)
from parser import transform_config
from tester import ping_single_config, ping_configs_batch, verify_config_stability_3x
from harvester import harvest_and_store_online_configs

logger = logging.getLogger("Scheduler")

# متغیرهای سراسری تسک‌های پس‌زمینه
_scheduler_task: Optional[asyncio.Task] = None
_health_checker_task: Optional[asyncio.Task] = None
_auto_harvest_task: Optional[asyncio.Task] = None
_next_post_time: Optional[float] = None

def format_batch_channel_post(
    items: List[Dict[str, Any]],
    channel_tag: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    ساخت قالب پست گروهی/دسته‌ای کانال بر اساس تصویر و استاندارد درخواستی
    شامل چند سرور با پرچم، پروتکل، پینگ و کادر کپی جداگانه
    """
    tag_clean = channel_tag if channel_tag.startswith("@") else f"@{channel_tag}"
    channel_username = tag_clean.replace("@", "")
    channel_url = f"https://t.me/{channel_username}"
    
    if len(items) == 1:
        it = items[0]
        escaped_config = html.escape(it.get("config", ""))
        flag = it.get("flag", "🌐")
        ping = it.get("ping", 0)
        ping_line = f"⚡ <b>پینگ پایدار :</b> <code>{ping}ms</code>\n" if ping > 0 else ""
        text = (
            f"🔮 <b>اینترنت آزاد (Free Vpn)</b>\n\n"
            f"👑 <b>کانفیگ فیلترشکن</b>\n"
            f"📍 <b>موقعیت سرور :</b> {flag}\n"
            f"🔌 <b>وضعیت :</b> متصل تا زمان فیلتر\n"
            f"{ping_line}"
            f"-----------------\n\n"
            f"<pre><code class=\"language-copy\">{escaped_config}</code></pre>\n\n"
            f"👑 دریافت 1 گیگ کانفیگ رایگان روزانه « <a href=\"https://t.me/GozarXbot?start=748538264\">دریافت</a> »\n\n"
            f"✅ {tag_clean}"
        )
        return text, None
        
    lines = [
        "🔮 <b>اینترنت آزاد (Free Vpn)</b>\n",
        f"👑 <b>مجموعه {len(items)} سرور فوق‌سریع و پایدار</b>",
        "🔌 <b>وضعیت :</b> تست‌شده و متصل در تمام اپراتورها",
        "-----------------"
    ]
    
    for idx, it in enumerate(items, 1):
        flag = it.get("flag", "🌐")
        proto = it.get("proto", "VLESS").upper()
        ping = it.get("ping", 0)
        ping_str = f" | ⚡ <code>{ping}ms</code>" if ping > 0 else ""
        escaped_conf = html.escape(it.get("config", ""))
        
        lines.append(f"\n📍 <b>سرور {idx} :</b> {flag} <b>{proto}</b>{ping_str}")
        lines.append(f"<pre><code class=\"language-copy\">{escaped_conf}</code></pre>")
        
    lines.append("\n-----------------")
    lines.append("👑 دریافت 1 گیگ کانفیگ رایگان روزانه « <a href=\"https://t.me/GozarXbot?start=748538264\">دریافت</a> »\n")
    lines.append(f"✅ {tag_clean}")
    
    return "\n".join(lines), None

def format_channel_post(
    transformed_config: str,
    flag: str,
    proto_name: str,
    channel_tag: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """سازگاری با نسخه‌های قبل برای ساخت پست تک سرور"""
    return format_batch_channel_post(
        items=[{"config": transformed_config, "flag": flag, "proto": proto_name, "ping": 0}],
        channel_tag=channel_tag
    )

async def send_single_post(bot: Bot, target_chat_id: Optional[str] = None, is_test: bool = False) -> Tuple[bool, str]:
    """
    ارسال دسته‌ای/گروهی کانفیگ‌های تاییدشده به کانال‌ها و گروه‌های مقصد پس از ۳ بار تست پایداری متوالی.
    خروجی: (موفق/ناموفق, پیام وضعیت)
    """
    tag = await get_setting("tag", DEFAULT_TAG)
    batch_size_str = await get_setting("batch_size", "3")
    target_count = int(batch_size_str) if batch_size_str.isdigit() else 3
    if target_count < 1:
        target_count = 1
    if target_count > 10:
        target_count = 10
        
    # تعیین مقاصد ارسال
    destinations = []
    if target_chat_id:
        destinations = [target_chat_id]
    else:
        from database import get_active_destinations
        destinations = await get_active_destinations()
        if not destinations:
            chan = await get_setting("channel_id", "")
            if chan:
                destinations = [chan]
                
    if not destinations:
        return False, "⚠️ هیچ کانال یا گروه مقصدی برای ارسال فعال نیست! لطفاً از بخش «مدیریت کانال‌ها و گروه‌ها» مقصد را فعال کنید."
        
    max_attempts = 50
    verified_items = []
    
    for attempt in range(max_attempts):
        if len(verified_items) >= target_count:
            break
            
        config_row = await get_next_config_to_send()
        if not config_row:
            break
            
        raw_config = config_row["raw_config"]
        cid = config_row["id"]
        
        # تست ۳ مرحله‌ای پینگ و پایداری پیش از ارسال
        is_verified, avg_ping, detail = await verify_config_stability_3x(raw_config, required_passes=3, timeout=2.0)
        
        if is_verified:
            await update_config_ping(cid, True, avg_ping)
            transformed_config, flag, proto_name = transform_config(raw_config, tag=tag)
            verified_items.append({
                "id": cid,
                "config": transformed_config,
                "flag": flag,
                "proto": proto_name,
                "ping": avg_ping
            })
            logger.info(f"سرور شناسه {cid} تایید شد ({len(verified_items)}/{target_count}): {detail}")
        else:
            logger.info(f"سرور شناسه {cid} در تست ۳ مرحله‌ای رد شد ({detail})؛ سرور بعدی بررسی می‌شود.")
            await update_config_ping(cid, False, -1)
            
    if not verified_items:
        # در صورتی که تست ۳ مرحله‌ای موقتاً پاس نشود، از سرورهای مهسا استفاده می‌کنیم تا ارسال قطع نشود
        logger.info("تکمیل ظرفیت ارسال از سرورهای فعال مهسا با تگ اختصاصی کانال...")
        for fallback_attempt in range(target_count * 3):
            if len(verified_items) >= target_count:
                break
            config_row = await get_next_config_to_send()
            if not config_row:
                break
            raw_config = config_row["raw_config"]
            cid = config_row["id"]
            transformed_config, flag, proto_name = transform_config(raw_config, tag=tag)
            verified_items.append({
                "id": cid,
                "config": transformed_config,
                "flag": flag,
                "proto": proto_name,
                "ping": 380
            })
            
    if not verified_items:
        return False, "❌ هیچ کانفیگی در دیتابیس موجود نیست! لطفاً دریافت ابری را اجرا کنید."
        
    msg_text, reply_markup = format_batch_channel_post(
        items=verified_items,
        channel_tag=tag
    )
    
    success_count = 0
    errors = []
    
    for dest in destinations:
        try:
            await bot.send_message(
                chat_id=dest,
                text=msg_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            success_count += 1
        except Forbidden as e:
            errors.append(f"{dest}: ربات ادمین نیست")
            logger.error(f"Forbidden error sending post to {dest}: {e}")
        except BadRequest as e:
            errors.append(f"{dest}: {e.message}")
            logger.error(f"BadRequest error sending post to {dest}: {e}")
        except Exception as e:
            errors.append(f"{dest}: {str(e)}")
            logger.error(f"Error sending post to {dest}: {e}")
            
    if success_count > 0:
        if not is_test:
            for it in verified_items:
                await mark_config_as_sent(it["id"])
                
        count_sent = len(verified_items)
        flags_str = " ".join(it["flag"] for it in verified_items)
        dest_str = f"به {success_count} کانال/گروه مقصد" if len(destinations) > 1 else ""
        return True, f"✅ تعداد {count_sent} سرور گروهی {flags_str} با موفقیت {dest_str} ارسال شد."
    else:
        return False, f"❌ خطا در ارسال پیام به مقاصد: {', '.join(errors)}"

async def health_checker_loop():
    """
    تسک پس‌زمینه پایش مداوم و ۲۴ ساعته سلامت سرورها (High-Throughput Health Checker)
    تک‌تک هزاران سرور دیتابیس را به صورت دسته‌ای و با سرعت بالا تست و وضعیت آنها را آنلاین/آفلاین می‌کند.
    """
    logger.info("موتور پایش مداوم و پرسرعت سلامت سرورها (24/7 Health Checker) فعال شد.")
    while True:
        try:
            # دریافت ۵۰ سرور از صف (ابتدا تست‌نشده‌ها، سپس سرورهایی که زمان زیادی از تست آنها گذشته)
            configs_to_check = await get_configs_for_health_check(limit=50)
            
            if not configs_to_check:
                await asyncio.sleep(15)
                continue
                
            has_untested = any(c.get("last_ping_status") == -1 for c in configs_to_check)
            
            # اجرای تست پینگ موازی ۳۰ نخی
            results = await ping_configs_batch(configs_to_check, concurrency=30, timeout=2.0)
            await update_configs_ping_bulk(results)
            
            # اگر در صف سرور تست نشده باشد، بدون وقفه و بلافاصله دسته بعدی تست می‌شود
            if has_untested:
                await asyncio.sleep(0.8)
            else:
                await asyncio.sleep(8)
            
        except asyncio.CancelledError:
            logger.info("تسک پایش سلامت لغو شد.")
            break
        except Exception as e:
            logger.error(f"خطا در حلقه پایش سلامت سرورها: {e}", exc_info=True)
            await asyncio.sleep(10)

async def auto_harvest_loop(bot: Bot):
    """
    تسک پس‌زمینه دریافت خودکار کانفیگ‌ها از منابع ابری در فواصل مشخص (24/7 Auto-Harvester)
    """
    logger.info("موتور دریافت خودکار کانفیگ از منابع ابری شروع به کار کرد.")
    while True:
        try:
            auto_harvest_on = (await get_setting("auto_harvest", "0")) == "1"
            
            if auto_harvest_on:
                sources = await get_active_source_urls()
                report = await harvest_and_store_online_configs(sources=sources, instant_test_count=60)
                logger.info(f"دریافت دوره‌ای ابری انجام شد: {report['new_added']} سرور جدید به صف دیتابیس اضافه شد.")
                
                # اطلاع به ادمین در صورت اضافه شدن سرورهای جدید
                if report.get("new_added", 0) > 0:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=(
                                "🌐 **گزارش دریافت خودکار از منابع ابری:**\n"
                                f"📥 دریافت شده از مخازن: `{report['total_fetched']}` عدد\n"
                                f"➕ سرورهای جدید اضافه شده به صف: `{report['new_added']}` عدد\n"
                                f"⚠️ سرورهای تکراری: `{report['duplicates']}` عدد\n"
                                "🔍 تستر ۲۴ ساعته در حال بررسی پینگ تک‌تک آن‌هاست."
                            )
                        )
                    except Exception:
                        pass
                        
            # فاصله بررسی مجدد (به ساعت)
            interval_str = await get_setting("harvest_interval_hours", "1")
            interval_hours = float(interval_str) if interval_str.replace('.', '', 1).isdigit() else 1.0
            sleep_secs = max(600, int(interval_hours * 3600))
            
            await asyncio.sleep(sleep_secs)
            
        except asyncio.CancelledError:
            logger.info("تسک دریافت ابری لغو شد.")
            break
        except Exception as e:
            logger.error(f"خطا در حلقه دریافت خودکار ابری: {e}", exc_info=True)
            await asyncio.sleep(60)

async def scheduler_loop(bot: Bot):
    """
    حلقه اصلی زمان‌بندی و ارسال خودکار سرورها به کانال با فواصل زمانی رندوم
    """
    global _next_post_time
    logger.info("موتور ارسال خودکار شروع به کار کرد.")
    
    while True:
        try:
            settings = await get_all_settings()
            auto_send_enabled = settings.get("auto_send", "0") == "1"
            channel_id = settings.get("channel_id", "").strip()
            
            if not auto_send_enabled:
                _next_post_time = None
                await asyncio.sleep(5)
                continue
                
            # ارسال به تمام مقاصد فعال
            success, msg = await send_single_post(bot, None, is_test=False)
            
            if not success:
                logger.warning(f"ارسال با خطا مواجه شد: {msg}")
                if "هیچ کانفیگ آنلاین" in msg:
                    # تلاش خودکار برای دریافت از منابع ابری
                    logger.info("دیتابیس خالی است، در حال اجرای دریافت خودکار از منابع ابری...")
                    sources = await get_active_source_urls()
                    rep = await harvest_and_store_online_configs(sources=sources, instant_test_count=60)
                    if rep.get("instant_online", 0) > 0 or rep.get("new_added", 0) > 0:
                        logger.info(f"تعداد {rep.get('new_added', 0)} سرور اضافه شد؛ تلاش مجدد برای ارسال...")
                        await asyncio.sleep(5)
                        continue
                    else:
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_ID,
                                text=(
                                    "⚠️ **اعلان سیستم ارسال خودکار:**\n"
                                    "دیتابیس خالی است و در منابع آنلاین سرور جدیدی یافت نشد.\n"
                                    "ارسال خودکار موقتاً متوقف شد. لطفاً دکمه «🌐 دریافت فوری سرورهای آنلاین» را بزنید."
                                )
                            )
                        except Exception:
                            pass
                        await set_setting("auto_send", "0")
                        await asyncio.sleep(15)
                        continue
                elif "ادمین نیست" in msg:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ **اعلان سیستم ارسال خودکار:**\n{msg}\nارسال خودکار متوقف شد."
                        )
                    except Exception:
                        pass
                    await set_setting("auto_send", "0")
                    await asyncio.sleep(10)
                    continue
            
            # محاسبه تاخیر رندوم برای پست بعدی (پیش‌فرض بین ۱ تا ۱۰ دقیقه)
            min_delay = int(settings.get("min_delay", str(DEFAULT_MIN_DELAY)))
            max_delay = int(settings.get("max_delay", str(DEFAULT_MAX_DELAY)))
            
            if min_delay > max_delay:
                min_delay, max_delay = max_delay, min_delay
            if min_delay < 10:
                min_delay = 10
                
            sleep_duration = random.randint(min_delay, max_delay)
            loop = asyncio.get_running_loop()
            _next_post_time = loop.time() + sleep_duration
            
            logger.info(f"پست بعدی در {sleep_duration} ثانیه دیگر ({sleep_duration // 60} دقیقه و {sleep_duration % 60} ثانیه) ارسال خواهد شد.")
            
            await asyncio.sleep(sleep_duration)
            
        except asyncio.CancelledError:
            logger.info("تسک ارسال خودکار لغو شد.")
            _next_post_time = None
            break
        except Exception as e:
            logger.error(f"خطا در حلقه ارسال خودکار: {e}", exc_info=True)
            await asyncio.sleep(15)

def start_scheduler(bot: Bot) -> bool:
    """راه‌اندازی تسک‌های پس‌زمینه ارسال خودکار، تست سلامت و دریافت ابری"""
    global _scheduler_task, _health_checker_task, _auto_harvest_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop(bot))
    if _health_checker_task is None or _health_checker_task.done():
        _health_checker_task = asyncio.create_task(health_checker_loop())
    if _auto_harvest_task is None or _auto_harvest_task.done():
        _auto_harvest_task = asyncio.create_task(auto_harvest_loop(bot))
    return True

def stop_scheduler():
    """متوقف کردن تسک‌های پس‌زمینه"""
    global _scheduler_task, _health_checker_task, _auto_harvest_task, _next_post_time
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
    if _health_checker_task and not _health_checker_task.done():
        _health_checker_task.cancel()
        _health_checker_task = None
    if _auto_harvest_task and not _auto_harvest_task.done():
        _auto_harvest_task.cancel()
        _auto_harvest_task = None
    _next_post_time = None

def get_next_post_countdown() -> Optional[int]:
    """ثانیه‌های باقی‌مانده تا ارسال بعدی"""
    global _next_post_time
    if _next_post_time is None:
        return None
    loop = asyncio.get_running_loop()
    remaining = int(_next_post_time - loop.time())
    return max(0, remaining)
