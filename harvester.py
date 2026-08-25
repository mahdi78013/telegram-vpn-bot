import asyncio
import logging
import time
import aiohttp
import aiosqlite
from typing import List, Dict, Any, Optional

from config import DB_PATH, DEFAULT_SUBSCRIPTION_INTERVAL
from parser import extract_configs_from_text, decode_base64_safe
from tester import ping_configs_batch
from node_registry import CandidateNode, NodeHealth, registry

logger = logging.getLogger("CloudHarvester")

DEFAULT_SUBSCRIPTION_SOURCES = [
    {
        "name": "MahsaNet MTN Dedicated VLESS Reality",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt"
    },
    {
        "name": "MahsaNet MCI Dedicated VLESS Reality",
        "url": "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mci/sub_1.txt"
    },
    {
        "name": "Epodonios VLESS Reality Stream (2200+ Reality)",
        "url": "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt"
    },
    {
        "name": "Epodonios Global All Configs",
        "url": "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt"
    },
    {
        "name": "ALIILAPRO Live V2Ray Stream",
        "url": "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt"
    },
    {
        "name": "FreeFQ Live Public Pool",
        "url": "https://raw.githubusercontent.com/freefq/free/master/v2"
    }
]

async def init_sources_table():
    """ایجاد جدول منابع در دیتابیس در صورت عدم وجود"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscription_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                is_active INTEGER DEFAULT 1,
                last_fetch_at TIMESTAMP,
                configs_found INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0
            )
        """)
        
        for src in DEFAULT_SUBSCRIPTION_SOURCES:
            await db.execute("""
                INSERT OR IGNORE INTO subscription_sources (name, url, is_active)
                VALUES (?, ?, 1)
            """, (src["name"], src["url"]))
            
        await db.commit()

async def fetch_source_content(url: str, timeout: float = 6.0) -> Optional[str]:
    """دریافت محتوای یک لینک سابسکریپشن با پشتیبانی از هدرهای ضد انسداد"""
    headers = {
        "User-Agent": "v2rayNG/1.8.19 (Android; Mobile; fa-IR)",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status == 200:
                    text = await response.text()
                    return text
                else:
                    logger.debug(f"HTTP error {response.status} for {url}")
                    return None
    except Exception as e:
        logger.debug(f"Fetch failed for {url}: {e}")
        return None

async def harvest_single_source(src_id: int, name: str, url: str) -> int:
    """دریافت و اعتبارسنجی کانفیگ‌های یک منبع مشخص و ورود مستقیم به پایگاه داده و استخر L2"""
    raw_content = await fetch_source_content(url)
    if not raw_content:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE subscription_sources 
                SET fail_count = fail_count + 1, last_fetch_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (src_id,))
            await db.commit()
        return 0

    extracted_configs = extract_configs_from_text(raw_content)
    if not extracted_configs:
        return 0

    # بررسی و سنجش پینگ کانفیگ‌ها در دسته‌های سریع
    tested_batch = await ping_configs_batch(extracted_configs[:100], concurrency=35)
    
    saved_count = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for conf, ping_res in tested_batch:
            proto = conf.split("://", 1)[0].lower() if "://" in conf else "custom"
            is_active = 1 if ping_res.is_online else 0
            ping_ms = ping_res.ping_ms if ping_res.is_online else -1
            status_code = 1 if ping_res.is_online else 0
            
            # ذخیره در دیتابیس
            cursor = await db.execute("""
                INSERT INTO configs (raw_config, protocol, is_active, ping_ms, last_ping_status, last_checked)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(raw_config) DO UPDATE SET
                    is_active = excluded.is_active,
                    ping_ms = excluded.ping_ms,
                    last_ping_status = excluded.last_ping_status,
                    last_checked = CURRENT_TIMESTAMP
            """, (conf, proto, is_active, ping_ms, status_code))
            
            # ثبت در کش و استخر نودهای زنده
            if ping_res.is_online:
                saved_count += 1
                node_id = cursor.lastrowid or int(time.time() * 1000) % 10000000
                score = 90.0 if "security=reality" in conf.lower() else (80.0 if ping_ms < 150 else 60.0)
                node = CandidateNode(
                    id=node_id,
                    raw_config=conf,
                    protocol=proto,
                    ping_ms=ping_ms,
                    ttfb_ms=ping_res.ttfb_ms,
                    score=score,
                    health_state=NodeHealth.HEALTHY
                )
                registry.put_l2_pool(node)

        await db.execute("""
            UPDATE subscription_sources 
            SET configs_found = ?, success_count = success_count + 1, last_fetch_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (len(extracted_configs), src_id))
        await db.commit()

    logger.info(f"✅ منبع '{name}': {len(extracted_configs)} سرور دریافت شد ({saved_count} سرور با پینگ سبز فعال شد).")
    return saved_count

async def run_harvester_cycle():
    """یک دور دریافت کامل از تمام منابع فعال در دیتابیس"""
    await init_sources_table()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, url FROM subscription_sources WHERE is_active = 1") as cursor:
            sources = await cursor.fetchall()

    logger.info(f"🚀 شروع دریافت خودکار کانفیگ‌ها از {len(sources)} منبع فعال ابری...")
    total_saved = 0
    
    # پردازش سریع منابع
    for src in sources:
        try:
            saved = await harvest_single_source(src["id"], src["name"], src["url"])
            total_saved += saved
        except Exception as e:
            logger.error(f"Error harvesting source {src['name']}: {e}")
            
    logger.info(f"🎉 پایان سیکل جمع‌آوری: {total_saved} کانفیگ فوق‌سریع و سالم وارد استخر شدند.")

async def start_harvester_background_task(interval_seconds: int = 1800):
    """حلقه پس‌زمینه جمع‌آوری و بروزرسانی خودکار دیتابیس کانفیگ‌ها"""
    while True:
        try:
            await run_harvester_cycle()
        except Exception as e:
            logger.error(f"Unexpected error in harvester background task: {e}")
        await asyncio.sleep(interval_seconds)

async def harvest_and_store_online_configs(sources: Optional[List[str]] = None, instant_test_count: int = 100) -> Dict[str, Any]:
    """دریافت فوری و همزمان کانفیگ‌ها از منابع جهت پاسخگویی به درخواست‌های منوی بات"""
    await init_sources_table()
    total_fetched = 0
    total_online = 0
    
    source_list = sources if sources else [s["url"] for s in DEFAULT_SUBSCRIPTION_SOURCES]
    
    for url in source_list:
        content = await fetch_source_content(url)
        if content:
            confs = extract_configs_from_text(content)
            total_fetched += len(confs)
            if confs:
                tested = await ping_configs_batch(confs[:instant_test_count], concurrency=35)
                async with aiosqlite.connect(DB_PATH) as db:
                    for c, ping_res in tested:
                        proto = c.split("://", 1)[0].lower() if "://" in c else "custom"
                        is_act = 1 if ping_res.is_online else 0
                        p_ms = ping_res.ping_ms if ping_res.is_online else -1
                        status = 1 if ping_res.is_online else 0
                        cursor = await db.execute("""
                            INSERT INTO configs (raw_config, protocol, is_active, ping_ms, last_ping_status, last_checked)
                            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(raw_config) DO UPDATE SET
                                is_active = excluded.is_active,
                                ping_ms = excluded.ping_ms,
                                last_ping_status = excluded.last_ping_status,
                                last_checked = CURRENT_TIMESTAMP
                        """, (c, proto, is_act, p_ms, status))
                        if ping_res.is_online:
                            total_online += 1
                            nid = cursor.lastrowid or int(time.time() * 1000) % 10000000
                            score = 90.0 if "security=reality" in c.lower() else 75.0
                            node = CandidateNode(
                                id=nid,
                                raw_config=c,
                                protocol=proto,
                                ping_ms=p_ms,
                                ttfb_ms=ping_res.ttfb_ms,
                                score=score,
                                health_state=NodeHealth.HEALTHY
                            )
                            registry.put_l2_pool(node)
                    await db.commit()
                    
    return {
        "total_fetched": total_fetched,
        "new_added": total_online,
        "duplicates": max(0, total_fetched - total_online),
        "instant_online": total_online
    }

