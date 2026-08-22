import aiosqlite
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from config import DB_PATH, DEFAULT_TAG, DEFAULT_MIN_DELAY, DEFAULT_MAX_DELAY
from parser import get_config_core_signature, transform_config

async def init_db():
    """ایجاد جداول پایگاه داده و اعمال ارتقاهای امن بدون از دست رفتن داده‌ها"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_config TEXT NOT NULL,
                core_signature TEXT UNIQUE NOT NULL,
                protocol TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                sent_in_cycle INTEGER DEFAULT 0,
                last_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ping_ms INTEGER DEFAULT -1,
                last_ping_status INTEGER DEFAULT -1,
                last_ping_time TIMESTAMP
            )
        """)

        # اضافه کردن ستون‌های جدید به دیتابیس موجود در صورت عدم وجود (مهاجرت امن)
        try:
            await db.execute("ALTER TABLE configs ADD COLUMN ping_ms INTEGER DEFAULT -1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE configs ADD COLUMN last_ping_status INTEGER DEFAULT -1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE configs ADD COLUMN last_ping_time TIMESTAMP")
        except Exception:
            pass

        # ساخت ایندکس‌های فوق‌سریع برای جستجو و چرخه ارسال
        await db.execute("CREATE INDEX IF NOT EXISTS idx_configs_active_ping ON configs(is_active, last_ping_status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_configs_cycle ON configs(is_active, last_ping_status, sent_in_cycle);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_configs_time ON configs(last_ping_time);")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_sent INTEGER DEFAULT 0,
                last_sent_time TIMESTAMP
            )
        """)

        # جدول کانال‌ها و گروه‌های مقصد (چندگانه با زمان‌بندی مستقل - پیش‌فرض روزی ۳ عدد = هر ۸ ساعت)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE NOT NULL,
                title TEXT,
                chat_type TEXT DEFAULT 'channel',
                interval_seconds INTEGER DEFAULT 28800,
                last_sent_at TIMESTAMP DEFAULT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        try:
            await db.execute("ALTER TABLE destinations ADD COLUMN interval_seconds INTEGER DEFAULT 28800")
        except Exception:
            pass
            
        try:
            await db.execute("ALTER TABLE destinations ADD COLUMN last_sent_at TIMESTAMP DEFAULT NULL")
        except Exception:
            pass

        # جدول ثبت پست‌های ارسالی جهت پاکسازی خودکار پس از فیلتر شدن
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sent_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                config_ids TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_deleted INTEGER DEFAULT 0
            )
        """)
        
        # مقداردهی کانال‌های پیش‌فرض در مقاصد (پیش‌فرض روزی ۳ عدد = هر ۸ ساعت = 28800 ثانیه)
        await db.execute(
            """
            INSERT INTO destinations (chat_id, title, chat_type, interval_seconds, is_active) 
            VALUES (?, ?, 'channel', 28800, 1)
            ON CONFLICT(chat_id) DO UPDATE SET interval_seconds = 28800, is_active = 1
            """,
            ("@Internet_azad369", "کانال اصلی اینترنت آزاد")
        )
        await db.execute(
            """
            INSERT INTO destinations (chat_id, title, chat_type, interval_seconds, is_active) 
            VALUES (?, ?, 'channel', 28800, 1)
            ON CONFLICT(chat_id) DO UPDATE SET interval_seconds = 28800, is_active = 1
            """,
            ("@Muntivpn", "کانال دوم مانتی وی‌پی‌ان")
        )

        # مقداردهی اولیه آمار
        await db.execute("INSERT OR IGNORE INTO stats (id, total_sent) VALUES (1, 0)")

        # مقداردهی اولیه تنظیمات
        default_settings = {
            "channel_id": "@Internet_azad369",
            "auto_send": "1",  # 0: خاموش, 1: روشن
            "auto_harvest": "1", # 0: خاموش, 1: روشن (دریافت خودکار از گیت‌هاب)
            "harvest_interval_hours": "2", # هر 2 ساعت
            "batch_size": "3", # تعداد کانفیگ ارسالی در هر پست گروهی (پیش‌فرض ۳ تایی)
            "source_mode": "mahsa", # منبع اصلی: مخازن مهسا نت و Reality
            "min_delay": str(DEFAULT_MIN_DELAY),
            "max_delay": str(DEFAULT_MAX_DELAY),
            "current_cycle": "1",
            "tag": "@Internet_azad369",
            "custom_header": "🚀 **سرور پرسرعت و رایگان**",
            "custom_footer": "🆔 @Internet_azad369\n🌐 اینترنت آزاد برای همه",
        }

        for key, val in default_settings.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

        # منابع پیش‌فرض (خالص مهسا نت)
        from harvester import DEFAULT_SUBSCRIPTION_SOURCES
        await db.execute("DELETE FROM sources")
        for s in DEFAULT_SUBSCRIPTION_SOURCES:
            await db.execute("INSERT OR REPLACE INTO sources (name, url, is_active) VALUES (?, ?, 1)", (s["name"], s["url"]))

        await db.commit()

async def get_setting(key: str, default: str = "") -> str:
    """دریافت مقدار یک تنظیم"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    """ذخیره یا بروزرسانی یک تنظیم"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def get_all_settings() -> Dict[str, str]:
    """دریافت تمام تنظیمات به صورت دیکشنری"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT key, value FROM settings") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}

async def get_active_source_urls() -> List[str]:
    """دریافت لینک‌های فعال منابع سابسکریپشن"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT url FROM sources WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_all_sources() -> List[Dict[str, Any]]:
    """دریافت تمام منابع ثبت شده"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, url, is_active FROM sources") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def add_source(name: str, url: str) -> bool:
    """افزودن لینک منبع جدید"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        try:
            await db.execute("INSERT INTO sources (name, url) VALUES (?, ?)", (name, url))
            await db.commit()
            return True
        except Exception:
            return False

async def delete_source(source_id: int) -> bool:
    """حذف یک منبع"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        await db.commit()
        return True

async def add_configs_bulk(configs: List[str]) -> Tuple[int, int]:
    """
    افزودن دسته‌ای کانفیگ‌ها به دیتابیس
    خروجی: (تعداد اضافه شده, تعداد تکراری)
    """
    added = 0
    duplicates = 0
    
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        for raw_c in configs:
            raw_c = raw_c.strip()
            if not raw_c:
                continue
                
            sig = get_config_core_signature(raw_c)
            proto = raw_c.split("://", 1)[0] if "://" in raw_c else "custom"
            
            try:
                await db.execute(
                    """
                    INSERT INTO configs (raw_config, core_signature, protocol, last_ping_status, ping_ms) 
                    VALUES (?, ?, ?, -1, -1)
                    """,
                    (raw_c, sig, proto)
                )
                added += 1
            except aiosqlite.IntegrityError:
                duplicates += 1
                
        await db.commit()
        
    return added, duplicates

async def update_config_ping(config_id: int, is_online: bool, ping_ms: int):
    """ثبت نتیجه پینگ یک کانفیگ منفرد"""
    status_code = 1 if is_online else 0
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute(
            """
            UPDATE configs 
            SET last_ping_status = ?, ping_ms = ?, last_ping_time = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            (status_code, ping_ms, config_id)
        )
        await db.commit()

async def update_configs_ping_bulk(results: List[Tuple[int, bool, int]]):
    """ثبت گروهی نتایج پینگ کانفیگ‌ها"""
    if not results:
        return
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        for cid, is_online, ping in results:
            status_code = 1 if is_online else 0
            await db.execute(
                """
                UPDATE configs 
                SET last_ping_status = ?, ping_ms = ?, last_ping_time = CURRENT_TIMESTAMP 
                WHERE id = ?
                """,
                (status_code, ping, cid)
            )
        await db.commit()

async def get_configs_for_health_check(limit: int = 100) -> List[Dict[str, Any]]:
    """دریافت سرورهای فعال جهت پایش سلامت دوره‌ای"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, raw_config, protocol, last_ping_status, ping_ms 
            FROM configs 
            WHERE is_active = 1 
            ORDER BY 
                CASE WHEN last_ping_status = -1 THEN 0 ELSE 1 END,
                last_ping_time ASC NULLS FIRST 
            LIMIT ?
            """,
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_next_config_to_send() -> Optional[Dict[str, Any]]:
    """
    انتخاب کانفیگ بعدی برای ارسال بر اساس چرخه فعلی.
    فقط سرورهایی که پینگ داشته‌اند یا هنوز در صف تست هستند انتخاب می‌شوند.
    """
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT value FROM settings WHERE key = 'current_cycle'") as cursor:
            row = await cursor.fetchone()
            cur_cycle_str = row[0] if row else "1"
            
        current_cycle = int(cur_cycle_str) if cur_cycle_str.isdigit() else 1
        
        # 1. جستجوی کانفیگ زنده در دور فعلی
        async with db.execute(
            """
            SELECT id, raw_config, protocol, sent_in_cycle, ping_ms, last_ping_status 
            FROM configs 
            WHERE is_active = 1 AND (last_ping_status != 0 OR last_ping_status IS NULL) AND sent_in_cycle < ? 
            ORDER BY RANDOM() 
            LIMIT 1
            """,
            (current_cycle,)
        ) as cursor:
            row = await cursor.fetchone()
            
        if row:
            return dict(row)
            
        # 2. بررسی وجود کانفیگ فعال
        async with db.execute("SELECT COUNT(*) FROM configs WHERE is_active = 1 AND (last_ping_status != 0 OR last_ping_status IS NULL)") as cursor:
            count_row = await cursor.fetchone()
            total_active = count_row[0] if count_row else 0
            
        if total_active == 0:
            return None
            
        # 3. شروع دور جدید (Cycle + 1)
        current_cycle += 1
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_cycle', ?)", (str(current_cycle),))
        await db.commit()
        
        async with db.execute(
            """
            SELECT id, raw_config, protocol, sent_in_cycle, ping_ms, last_ping_status 
            FROM configs 
            WHERE is_active = 1 AND (last_ping_status != 0 OR last_ping_status IS NULL) AND sent_in_cycle < ? 
            ORDER BY RANDOM() 
            LIMIT 1
            """,
            (current_cycle,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def mark_config_as_sent(config_id: int):
    """ثبت زمان و وضعیت ارسال شدن کانفیگ در دور جاری"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'current_cycle'") as cursor:
            row = await cursor.fetchone()
            cur_cycle_str = row[0] if row else "1"
        current_cycle = int(cur_cycle_str) if cur_cycle_str.isdigit() else 1
        
        await db.execute(
            """
            UPDATE configs 
            SET sent_in_cycle = ?, last_sent_at = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            (current_cycle, config_id)
        )
        
        # افزایش آمار کل
        await db.execute(
            """
            UPDATE stats 
            SET total_sent = total_sent + 1, last_sent_time = CURRENT_TIMESTAMP 
            WHERE id = 1
            """
        )
        await db.commit()

async def get_stats() -> Dict[str, Any]:
    """دریافت آمار جامع سرورها، وضعیت سلامت پینگ و ارسال‌ها"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT COUNT(*) FROM configs") as cursor:
            total_configs = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM configs WHERE is_active = 1 AND last_ping_status = 1") as cursor:
            online_configs = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM configs WHERE is_active = 1 AND last_ping_status = 0") as cursor:
            offline_configs = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM configs WHERE is_active = 1 AND (last_ping_status = -1 OR last_ping_status IS NULL)") as cursor:
            untested_configs = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT value FROM settings WHERE key = 'current_cycle'") as cursor:
            row = await cursor.fetchone()
            cur_cycle = int(row[0]) if (row and row[0].isdigit()) else 1
        
        async with db.execute("SELECT COUNT(*) FROM configs WHERE is_active = 1 AND (last_ping_status != 0) AND sent_in_cycle >= ?", (cur_cycle,)) as cursor:
            sent_in_current_cycle = (await cursor.fetchone())[0]
            
        usable_active = online_configs + untested_configs
        remaining_in_cycle = max(0, usable_active - sent_in_current_cycle)
        
        async with db.execute("SELECT total_sent, last_sent_time FROM stats WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            total_sent = row[0] if row else 0
            last_sent_time = row[1] if row else "هرگز"
            
        return {
            "total_configs": total_configs,
            "online_configs": online_configs,
            "offline_configs": offline_configs,
            "untested_configs": untested_configs,
            "usable_active": usable_active,
            "current_cycle": cur_cycle,
            "sent_in_current_cycle": sent_in_current_cycle,
            "remaining_in_cycle": remaining_in_cycle,
            "total_lifetime_sent": total_sent,
            "last_sent_time": last_sent_time
        }

async def delete_dead_configs() -> int:
    """حذف سرورهایی که در تست پینگ ناموفق و قطع بوده‌اند"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        cursor = await db.execute("DELETE FROM configs WHERE last_ping_status = 0")
        deleted_count = cursor.rowcount
        await db.commit()
        return deleted_count

async def clear_all_configs():
    """حذف تمام کانفیگ‌های ذخیره شده"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("DELETE FROM configs")
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_cycle', '1')")
        await db.commit()

async def export_all_configs() -> List[str]:
    """دریافت تمامی کانفیگ‌های موجود برای خروجی یا بکاپ"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT raw_config FROM configs") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

# ----------------- توابع مدیریت مقاصد چندگانه (کانال‌ها و گروه‌ها) -----------------

async def get_active_destinations() -> List[str]:
    """دریافت آیدی تمامی کانال‌ها و گروه‌های فعال مقصد"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT chat_id FROM destinations WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_all_destinations() -> List[Dict[str, Any]]:
    """دریافت لیست تمام مقاصد ثبت شده (کانال‌ها و گروه‌ها) همراه با زمان‌بندی مستقل آنها"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, chat_id, title, chat_type, interval_seconds, last_sent_at, is_active FROM destinations ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_all_active_destinations_with_info() -> List[Dict[str, Any]]:
    """دریافت مقاصد فعال همراه با جزئیات زمان‌بندی و آخرین ارسال"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, chat_id, title, chat_type, interval_seconds, last_sent_at, is_active FROM destinations WHERE is_active = 1 ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_destination_by_id(dest_id: int) -> Optional[Dict[str, Any]]:
    """دریافت اطلاعات یک مقصد بر اساس شناسه"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, chat_id, title, chat_type, interval_seconds, last_sent_at, is_active FROM destinations WHERE id = ?", (dest_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def set_destination_interval(dest_id: int, interval_seconds: int):
    """تنظیم فاصله زمانی اختصاصی ارسال برای یک مقصد خاص"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("UPDATE destinations SET interval_seconds = ? WHERE id = ?", (interval_seconds, dest_id))
        await db.commit()

async def update_destination_last_sent(dest_id: int):
    """بروزرسانی زمان آخرین ارسال برای یک مقصد خاص"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("UPDATE destinations SET last_sent_at = CURRENT_TIMESTAMP WHERE id = ?", (dest_id,))
        await db.commit()

async def add_destination(chat_id: str, title: str, chat_type: str = "channel") -> bool:
    """افزودن یا فعال‌سازی یک کانال یا گروه مقصد"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        try:
            await db.execute(
                """
                INSERT INTO destinations (chat_id, title, chat_type, interval_seconds, is_active) 
                VALUES (?, ?, ?, 28800, 1)
                ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, is_active = 1
                """,
                (chat_id, title, chat_type)
            )
            await db.commit()
            return True
        except Exception:
            return False

async def toggle_destination(dest_id: int) -> Optional[int]:
    """تغییر وضعیت فعال/غیرفعال بودن یک مقصد"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute("SELECT is_active FROM destinations WHERE id = ?", (dest_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            new_status = 0 if row[0] == 1 else 1
            
        await db.execute("UPDATE destinations SET is_active = ? WHERE id = ?", (new_status, dest_id))
        await db.commit()
        return new_status

async def delete_destination(dest_id: int) -> bool:
    """حذف یک مقصد از لیست"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("DELETE FROM destinations WHERE id = ?", (dest_id,))
        await db.commit()
        return True

# ----------------- بخش پاکسازی خودکار پست‌های سوخته از کانال -----------------

async def record_sent_post(chat_id: str, message_id: int, config_ids: List[int]):
    """ثبت شناسه پست و سرورهای آن جهت بررسی و پایش بعدی"""
    cids_str = ",".join(str(cid) for cid in config_ids)
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute(
            "INSERT INTO sent_posts (chat_id, message_id, config_ids) VALUES (?, ?, ?)",
            (chat_id, message_id, cids_str)
        )
        await db.commit()

async def get_uncleaned_posts(min_age_minutes: int = 60, limit: int = 15) -> List[Dict[str, Any]]:
    """دریافت پست‌های ارسالی که زمان مشخصی از آنها گذشته و هنوز پاک نشده‌اند"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, chat_id, message_id, config_ids, sent_at 
            FROM sent_posts 
            WHERE is_deleted = 0 
              AND (strftime('%s', 'now') - strftime('%s', sent_at)) >= (? * 60)
            ORDER BY sent_at ASC 
            LIMIT ?
            """,
            (min_age_minutes, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def mark_post_deleted(post_record_id: int):
    """علامت‌گذاری پست به عنوان پاک شده"""
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute("UPDATE sent_posts SET is_deleted = 1 WHERE id = ?", (post_record_id,))
        await db.commit()

async def get_raw_configs_by_ids(config_ids: List[int]) -> List[str]:
    """دریافت کانفیگ‌های خام بر اساس شناسه‌ها"""
    if not config_ids:
        return []
    placeholders = ",".join("?" for _ in config_ids)
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        async with db.execute(
            f"SELECT raw_config FROM configs WHERE id IN ({placeholders})",
            tuple(config_ids)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

