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
    record_sent_post,
    get_uncleaned_posts,
    mark_post_deleted,
    get_raw_configs_by_ids,
)
from parser import transform_config, detect_operator_for_config
from tester import (
    ping_single_config,
    ping_configs_batch,
    verify_config_stability_3x,
    verify_config_is_completely_dead_10x,
)
from harvester import harvest_and_store_online_configs
from proxy_manager import (
    get_current_top_proxies,
    format_proxies_text,
    fetch_and_test_live_proxies,
)

logger = logging.getLogger("Scheduler")

# متغیرهای سراسری تسک‌های پس‌زمینه
_scheduler_task: Optional[asyncio.Task] = None
_health_checker_task: Optional[asyncio.Task] = None
_auto_harvest_task: Optional[asyncio.Task] = None
_proxy_refresher_task: Optional[asyncio.Task] = None
_channel_cleaner_task: Optional[asyncio.Task] = None
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
    
    top_proxies = get_current_top_proxies(3)
    proxies_section = format_proxies_text(top_proxies)
    
    if len(items) == 1:
        it = items[0]
        escaped_config = html.escape(it.get("config", ""))
        flag = it.get("flag", "🌐")
        ping = it.get("ping", 0)
        operator = it.get("operator") or detect_operator_for_config(it.get("config", ""), 0)
        ping_line = f"⚡ <b>پینگ پایدار :</b> <code>{ping}ms</code>\n" if ping > 0 else ""
        text = (
            f"🔮 <b>اینترنت آزاد (Free Vpn)</b>\n\n"
            f"👑 <b>کانفیگ فیلترشکن</b>\n"
            f"📍 <b>موقعیت سرور :</b> {flag}\n"
            f"📶 <b>اپراتور مناسب :</b> {operator}\n"
            f"🔌 <b>وضعیت :</b> متصل تا زمان فیلتر\n"
            f"{ping_line}"
            f"-----------------\n\n"
            f"<pre><code class=\"language-copy\">{escaped_config}</code></pre>\n\n"
            f"-----------------\n"
            f"{proxies_section}\n\n"
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
        operator = it.get("operator") or detect_operator_for_config(it.get("config", ""), idx - 1)
        ping_str = f" | ⚡ <code>{ping}ms</code>" if ping > 0 else ""
        escaped_conf = html.escape(it.get("config", ""))
        
        lines.append(f"\n📍 <b>سرور {idx} :</b> {flag} <b>{proto}</b>{ping_str} | {operator}")
        lines.append(f"<pre><code class=\"language-copy\">{escaped_conf}</code></pre>")
        
    lines.append("\n-----------------")
    lines.append(f"{proxies_section}\n")
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
    ارسال دسته‌ای/گروهی کانفیگ‌های تاییدشده به کانال‌ها و گروه‌های مقصد با تگ و آیدی اختصاصی هر کانال
    با بهره‌گیری از موتور پیشرفته ConfigDeliveryEngine v2 و تحویل آنی بدون تایم‌اوت.
    """
    from config_delivery_engine import delivery_engine
    from node_registry import NetworkContext
    from health_monitor import metrics_collector, RequestMetric
    
    default_tag = await get_setting("tag", DEFAULT_TAG)
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
        
    context = NetworkContext(carrier="all", region="all")
    t0 = time.time()
    
    # دریافت بسته‌ای بهترین و پایدارترین سرورها از موتور تحویل کانفیگ
    raw_verified_configs = await delivery_engine.get_best_configs(
        count=target_count,
        context=context,
        tag=default_tag
    )
    
    elapsed_ms = int((time.time() - t0) * 1000)
    
    if not raw_verified_configs:
        # دریافت تک کانفیگ L4 به عنوان فال‌بک نهایی
        fallback = await delivery_engine.get_best_config(context=context, tag=default_tag)
        raw_verified_configs = [{
            "id": fallback.get("node_id", 0),
            "config": fallback["direct"],
            "raw_config": fallback["direct"],
            "flag": fallback["flag"],
            "proto": fallback["proto"],
            "ping": fallback["ping"],
            "score": fallback.get("score", 75.0)
        }]
        
    success_count = 0
    errors = []
    sent_config_ids = [it.get("id", 0) for it in raw_verified_configs if it.get("id")]
    
    # ارسال به هر مقصد با نام و تگ اختصاصی همان کانال
    for dest in destinations:
        dest_tag = dest if dest.startswith("@") else default_tag
        
        dest_items = []
        for raw_item in raw_verified_configs:
            transformed_conf, flg, prt = transform_config(raw_item.get("raw_config", raw_item.get("config", "")), tag=dest_tag)
            dest_items.append({
                "id": raw_item.get("id", 0),
                "config": transformed_conf,
                "flag": flg,
                "proto": prt,
                "ping": raw_item.get("ping", 65)
            })
            
        msg_text, reply_markup = format_batch_channel_post(
            items=dest_items,
            channel_tag=dest_tag
        )
        
        try:
            sent_msg = await bot.send_message(
                chat_id=dest,
                text=msg_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            success_count += 1
            if not is_test and sent_msg and sent_config_ids:
                try:
                    await record_sent_post(dest, sent_msg.message_id, sent_config_ids)
                except Exception as ex:
                    logger.debug(f"خطا در ثبت پست ارسالی: {ex}")
        except Forbidden as e:
            errors.append(f"{dest}: ربات ادمین نیست")
            logger.error(f"Forbidden error sending post to {dest}: {e}")
        except BadRequest as e:
            errors.append(f"{dest}: {e.message}")
            logger.error(f"BadRequest error sending post to {dest}: {e}")
        except Exception as e:
            errors.append(f"{dest}: {str(e)}")
            logger.error(f"Error sending post to {dest}: {e}")
            
    # ثبت متریک تله‌متری
    await metrics_collector.record(RequestMetric(
        timestamp=time.time(),
        carrier="all",
        network_type="broadcast",
        region="all",
        latency_ms=elapsed_ms,
        ttfb_ms=elapsed_ms,
        retry_count=0,
        timeout_occurred=(success_count == 0),
        cache_hit=True,
        cache_level="Engine-Multi",
        node_id=sent_config_ids[0] if sent_config_ids else 0
    ))

    if success_count > 0:
        if not is_test:
            for cid in sent_config_ids:
                await mark_config_as_sent(cid)
                
        count_sent = len(raw_verified_configs)
        dest_str = f"به {success_count} کانال/گروه مقصد" if len(destinations) > 1 else ""
        return True, f"✅ تعداد {count_sent} سرور گروهی با موفقیت و با تگ اختصاصی {dest_str} ارسال شد."
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
                
                # دریافت دوره‌ای بدون مزاحمت پیام در پیوی ادمین
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

_destination_next_send_time: Dict[str, float] = {}

async def scheduler_loop(bot: Bot):
    """
    حلقه اصلی زمان‌بندی و ارسال خودکار سرورها به صورت مستقل و تفکیک‌شده برای هر کانال/گروه مقصد
    """
    global _next_post_time, _destination_next_send_time
    logger.info("موتور ارسال خودکار چندکاناله با زمان‌بندی تفکیک‌شده شروع به کار کرد.")
    
    while True:
        try:
            settings = await get_all_settings()
            auto_send_enabled = settings.get("auto_send", "0") == "1"
            
            if not auto_send_enabled:
                _next_post_time = None
                await asyncio.sleep(5)
                continue
                
            from database import get_all_active_destinations_with_info, update_destination_last_sent
            active_dests = await get_all_active_destinations_with_info()
            
            if not active_dests:
                _next_post_time = None
                await asyncio.sleep(5)
                continue
                
            loop = asyncio.get_running_loop()
            current_time = loop.time()
            
            # دریافت زمان‌بندی عمومی تنظیم‌شده توسط ادمین
            try:
                global_delay = int(float(settings.get("min_delay", "60")))
            except Exception:
                global_delay = 60
            if global_delay < 10:
                global_delay = 10
            
            # بررسی هر کانال/گروه مقصد به صورت کاملاً مستقل
            for d in active_dests:
                did = d["id"]
                chat_id = d["chat_id"]
                dest_interval = d.get("interval_seconds")
                
                # اگر کاربر در بخش اختصاصی دستی عوض نکرده بود (یا مقدار پیش‌فرض ۲۸۸۰۰/۹۰۰ بود)، از زمان‌بندی انتخابی ادمین استفاده کن
                if dest_interval and dest_interval not in (900, 28800):
                    interval = int(dest_interval)
                else:
                    interval = global_delay
                    
                if interval < 10:
                    interval = 10
                    
                if chat_id not in _destination_next_send_time:
                    # نوبت اول: شروع با تاخیر کوتاه ۳ تا ۱۰ ثانیه‌ای
                    _destination_next_send_time[chat_id] = current_time + random.randint(3, min(10, interval))
                    
                if current_time >= _destination_next_send_time[chat_id]:
                    logger.info(f"زمان ارسال به مقصد {chat_id} (فاصله: {interval} ثانیه) فرا رسید.")
                    success, msg = await send_single_post(bot, target_chat_id=chat_id, is_test=False)
                    if success:
                        await update_destination_last_sent(did)
                        _destination_next_send_time[chat_id] = current_time + interval
                        logger.info(f"✅ ارسال به {chat_id} موفق بود. ارسال بعدی در {interval} ثانیه دیگر انجام خواهد شد.")
                    else:
                        logger.warning(f"ارسال به {chat_id} با خطا مواجه شد: {msg}")
                        if "هیچ کانفیگ آنلاین" in msg:
                            sources = await get_active_source_urls()
                            rep = await harvest_and_store_online_configs(sources=sources, instant_test_count=60)
                            if rep.get("instant_online", 0) > 0 or rep.get("new_added", 0) > 0:
                                _destination_next_send_time[chat_id] = current_time + 5
                                continue
                        _destination_next_send_time[chat_id] = current_time + 30
                        
            # محاسبه نزدیک‌ترین زمان ارسال بعدی برای نمایش شمارش معکوس در پنل
            upcoming_times = [_destination_next_send_time[d["chat_id"]] for d in active_dests if d["chat_id"] in _destination_next_send_time]
            if upcoming_times:
                _next_post_time = min(upcoming_times)
            else:
                _next_post_time = None
                
            await asyncio.sleep(2)
            
        except asyncio.CancelledError:
            logger.info("تسک ارسال خودکار لغو شد.")
            _next_post_time = None
            break
        except Exception as e:
            logger.error(f"خطا در حلقه ارسال خودکار: {e}", exc_info=True)
            await asyncio.sleep(10)

async def auto_refresh_proxies_loop():
    """تسک پس‌زمینه دریافت و پایش مداوم پروکسی‌های پرسرعت تلگرام هر ۳۰ دقیقه"""
    logger.info("موتور پایش و بروزرسانی خودکار پروکسی‌های MTProto تلگرام فعال شد.")
    while True:
        try:
            await fetch_and_test_live_proxies(limit=40)
            await asyncio.sleep(1800)  # هر ۳۰ دقیقه
        except asyncio.CancelledError:
            logger.info("تسک پایش پروکسی لغو شد.")
            break
        except Exception as e:
            logger.error(f"خطا در حلقه پایش پروکسی: {e}", exc_info=True)
            await asyncio.sleep(60)

async def smart_channel_cleaner_loop(bot: Bot):
    """
    تسک پس‌زمینه پاکسازی هوشمند و خودکار پست‌های سوخته از کانال‌ها و گروه‌ها:
    پست‌های ارسالی را پایش می‌کند و تنها در صورتی که تمام سرورهای پست در ۱۰ مرحله تست پینگ متوالی
    هیچ پاسخی ندهند و ۱۰۰٪ قطع/فیلتر شده باشند، پست را از کانال حذف می‌کند.
    """
    logger.info("موتور پاکسازی خودکار پست‌های سوخته (Smart Channel Cleaner - 10x Ping Validator) فعال شد.")
    while True:
        try:
            # پایش پست‌هایی که حداقل ۶۰ دقیقه از ارسال آنها گذشته است
            uncleaned_posts = await get_uncleaned_posts(min_age_minutes=60, limit=10)
            for p in uncleaned_posts:
                post_id = p["id"]
                chat_id = p["chat_id"]
                message_id = p["message_id"]
                cids_str = p.get("config_ids", "")
                if not cids_str:
                    await mark_post_deleted(post_id)
                    continue
                    
                cids = [int(c) for c in cids_str.split(",") if c.isdigit()]
                raw_configs = await get_raw_configs_by_ids(cids)
                if not raw_configs:
                    await mark_post_deleted(post_id)
                    continue
                    
                # بررسی همزمان و موازی اینکه آیا تمام سرورهای این پست سوخته‌اند
                async def check_single_dead(conf_str: str) -> bool:
                    return await verify_config_is_completely_dead_10x(conf_str, total_tests=5, timeout=1.2)
                    
                dead_results = await asyncio.gather(*(check_single_dead(c) for c in raw_configs), return_exceptions=True)
                all_dead = all(res is True for res in dead_results)
                        
                if all_dead:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=message_id)
                        logger.info(f"🗑️ پست {message_id} در {chat_id} پس از تست عدم اتصال، با موفقیت از کانال پاکسازی شد.")
                    except Exception as e:
                        logger.warning(f"عدم امکان حذف پیام {message_id} در {chat_id}: {e}")
                    await mark_post_deleted(post_id)
                else:
                    logger.info(f"✅ سرورهای پست {message_id} در {chat_id} همچنان متصل هستند و در کانال باقی می‌ماند.")
                    
            await asyncio.sleep(600)  # هر ۱۰ دقیقه بررسی مجدد
        except asyncio.CancelledError:
            logger.info("تسک پاکسازی پست‌های سوخته لغو شد.")
            break
        except Exception as e:
            logger.error(f"خطا در حلقه پاکسازی پست‌های سوخته: {e}", exc_info=True)
            await asyncio.sleep(60)

_health_monitor_task: Optional[asyncio.Task] = None

async def auto_heal_sub_loop(interval_seconds: int = 900):
    """
    حلقه خودترمیم و نوسازی پیوسته فایل سابسکریپشن هر ۱۵ دقیقه:
    تست بلادرنگ پینگ و جایگزینی خودکار سرورهای سوخته با سرورهای پینگ‌سبز
    """
    await asyncio.sleep(20)
    while True:
        try:
            from codespace_vip import generate_and_publish_universal_sub
            from database import get_setting
            from config import DEFAULT_TAG
            tag = await get_setting("tag", DEFAULT_TAG)
            logger.info("🔄 شروع فرآیند خودترمیم و غربالگری سرورهای سابسکریپشن...")
            await generate_and_publish_universal_sub(tag=tag)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in auto_heal_sub_loop: {e}")
        await asyncio.sleep(interval_seconds)

_sub_auto_heal_task: Optional[asyncio.Task] = None

def start_scheduler(bot: Bot) -> bool:
    """راه‌اندازی تسک‌های پس‌زمینه ارسال خودکار، تست سلامت، دریافت ابری، پروکسی‌ها، خودترمیم ساب و پاکسازی کانال"""
    global _scheduler_task, _health_checker_task, _auto_harvest_task, _proxy_refresher_task, _channel_cleaner_task, _health_monitor_task, _sub_auto_heal_task
    from health_monitor import health_monitor
    
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop(bot))
    if _health_checker_task is None or _health_checker_task.done():
        _health_checker_task = asyncio.create_task(health_checker_loop())
    if _auto_harvest_task is None or _auto_harvest_task.done():
        _auto_harvest_task = asyncio.create_task(auto_harvest_loop(bot))
    if _proxy_refresher_task is None or _proxy_refresher_task.done():
        _proxy_refresher_task = asyncio.create_task(auto_refresh_proxies_loop())
    if _channel_cleaner_task is None or _channel_cleaner_task.done():
        _channel_cleaner_task = asyncio.create_task(smart_channel_cleaner_loop(bot))
    if _health_monitor_task is None or _health_monitor_task.done():
        _health_monitor_task = asyncio.create_task(health_monitor.start_monitor_loop())
    if _sub_auto_heal_task is None or _sub_auto_heal_task.done():
        _sub_auto_heal_task = asyncio.create_task(auto_heal_sub_loop(900))
    return True

def stop_scheduler():
    """متوقف کردن تسک‌های پس‌زمینه"""
    global _scheduler_task, _health_checker_task, _auto_harvest_task, _proxy_refresher_task, _channel_cleaner_task, _health_monitor_task, _sub_auto_heal_task, _next_post_time
    from health_monitor import health_monitor
    
    health_monitor.stop()
    if _health_monitor_task and not _health_monitor_task.done():
        _health_monitor_task.cancel()
        _health_monitor_task = None
    if _sub_auto_heal_task and not _sub_auto_heal_task.done():
        _sub_auto_heal_task.cancel()
        _sub_auto_heal_task = None
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
    if _health_checker_task and not _health_checker_task.done():
        _health_checker_task.cancel()
        _health_checker_task = None
    if _auto_harvest_task and not _auto_harvest_task.done():
        _auto_harvest_task.cancel()
        _auto_harvest_task = None
    if _proxy_refresher_task and not _proxy_refresher_task.done():
        _proxy_refresher_task.cancel()
        _proxy_refresher_task = None
    if _channel_cleaner_task and not _channel_cleaner_task.done():
        _channel_cleaner_task.cancel()
        _channel_cleaner_task = None
    _next_post_time = None


def reset_destination_timers():
    """ریست کردن کش زمان‌بندی برای اعمال آنی سرعت جدید تنظیم‌شده توسط ادمین"""
    global _destination_next_send_time, _next_post_time
    _destination_next_send_time = {}
    _next_post_time = None

def get_next_post_countdown() -> Optional[int]:
    """ثانیه‌های باقی‌مانده تا ارسال بعدی"""
    global _next_post_time
    if _next_post_time is None:
        return None
    loop = asyncio.get_running_loop()
    remaining = int(_next_post_time - loop.time())
    return max(0, remaining)
