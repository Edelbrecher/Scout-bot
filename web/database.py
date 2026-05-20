import aiosqlite
import bcrypt
import os
from pathlib import Path

DB_PATH = Path("/app/data/scouter.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_configs (
                guild_id            TEXT PRIMARY KEY,
                guild_name          TEXT NOT NULL,
                scout_channel_id    TEXT,
                category_id         TEXT,
                archive_channel_id  TEXT,
                button_message_id   TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scout_channels (
                channel_id      TEXT PRIMARY KEY,
                guild_id        TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                player          TEXT,
                coordinates     TEXT,
                village         TEXT,
                scout_time      TEXT,
                additional_info TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        await db.commit()

    # Migrate: add allowed_role_ids column to existing DBs
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN allowed_role_ids TEXT")
            await db.commit()
        except Exception:
            pass  # Column already exists

    # Seed admin user from env if not exists
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "changeme")
    await ensure_admin(username, password)


async def ensure_admin(username: str, password: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM admin_users WHERE username = ?", (username,)
        ) as cursor:
            if await cursor.fetchone():
                return
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await db.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username, hashed),
        )
        await db.commit()


async def verify_password(username: str, password: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT password_hash FROM admin_users WHERE username = ?", (username,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            return bcrypt.checkpw(password.encode(), row[0].encode())


async def get_all_guilds() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guild_configs ORDER BY guild_name") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_guild(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_guild_config(
    guild_id: str,
    category_id: str,
    archive_channel_id: str,
    allowed_role_ids: str,
    scout_channel_id: str = "",
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET category_id = ?, archive_channel_id = ?, allowed_role_ids = ?,
                scout_channel_id = COALESCE(NULLIF(?, ''), scout_channel_id)
            WHERE guild_id = ?
        """, (category_id or None, archive_channel_id or None, allowed_role_ids or None,
              scout_channel_id, guild_id))
        await db.commit()


async def update_button_message(guild_id: str, scout_channel_id: str, button_message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET scout_channel_id = ?, button_message_id = ?
            WHERE guild_id = ?
        """, (scout_channel_id, button_message_id, guild_id))
        await db.commit()


async def get_scout_channels(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_channels WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
