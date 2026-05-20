import aiosqlite
from pathlib import Path
from datetime import datetime

DB_PATH = Path("/app/data/scouter.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_configs (
                guild_id                TEXT PRIMARY KEY,
                guild_name              TEXT NOT NULL,
                scout_channel_id        TEXT,
                category_id             TEXT,
                archive_channel_id      TEXT,
                button_message_id       TEXT,
                allowed_role_ids        TEXT,
                res_request_channel_id  TEXT,
                res_answer_channel_id   TEXT,
                res_push_channel_id     TEXT,
                res_manager_role_ids    TEXT,
                res_button_message_id   TEXT
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
            CREATE TABLE IF NOT EXISTS res_requests (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          TEXT NOT NULL,
                answer_message_id TEXT,
                push_message_id   TEXT,
                user_id           TEXT NOT NULL,
                user_name         TEXT NOT NULL,
                player_name       TEXT NOT NULL,
                coordinates       TEXT NOT NULL,
                push_height       TEXT NOT NULL,
                reason            TEXT,
                status            TEXT DEFAULT 'pending',
                created_at        TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS res_contributions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id  INTEGER NOT NULL,
                user_id     TEXT NOT NULL,
                user_name   TEXT NOT NULL,
                amount      TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        await db.commit()

    # Migrations
    async with aiosqlite.connect(DB_PATH) as db:
        for col in [
            "allowed_role_ids TEXT",
            "res_request_channel_id TEXT",
            "res_answer_channel_id TEXT",
            "res_push_channel_id TEXT",
            "res_manager_role_ids TEXT",
            "res_button_message_id TEXT",
            "res_push_category_id TEXT",
        ]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        # Migrate res_requests: add push_channel_id column
        try:
            await db.execute("ALTER TABLE res_requests ADD COLUMN push_channel_id TEXT")
            await db.commit()
        except Exception:
            pass

        # Poll system migrations
        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN poll_channel_id TEXT")
            await db.commit()
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS availability_polls (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id            TEXT NOT NULL,
                title               TEXT NOT NULL,
                description         TEXT,
                event_datetime      TEXT NOT NULL,
                status              TEXT DEFAULT 'active',
                discord_message_id  TEXT,
                created_at          TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS poll_responses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id      INTEGER NOT NULL,
                user_id      TEXT NOT NULL,
                user_name    TEXT NOT NULL,
                response     TEXT NOT NULL,
                responded_at TEXT NOT NULL,
                UNIQUE(poll_id, user_id)
            )
        """)
        await db.commit()


async def get_guild_config(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def upsert_guild_name(guild_id: str, guild_name: str):
    """Register a guild without overwriting existing config."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO guild_configs (guild_id, guild_name)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET guild_name = excluded.guild_name
        """, (guild_id, guild_name))
        await db.commit()


async def update_button_message_id(guild_id: str, message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET button_message_id = ?, scout_channel_id = COALESCE(scout_channel_id, scout_channel_id)
            WHERE guild_id = ?
        """, (message_id, guild_id))
        await db.commit()


async def update_scout_channel_and_button(guild_id: str, scout_channel_id: str, button_message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET scout_channel_id = ?, button_message_id = ?
            WHERE guild_id = ?
        """, (scout_channel_id, button_message_id, guild_id))
        await db.commit()


async def add_scout_channel(
    channel_id: str,
    guild_id: str,
    player: str,
    coordinates: str,
    village: str,
    scout_time: str,
    additional_info: str,
    requested_by_id: str = "",
    requested_by_name: str = "",
):
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure columns exist (migration guard)
        for col in ["requested_by_id TEXT", "requested_by_name TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass
        await db.execute("""
            INSERT OR IGNORE INTO scout_channels
                (channel_id, guild_id, created_at, player, coordinates, village,
                 scout_time, additional_info, requested_by_id, requested_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            channel_id, guild_id, datetime.utcnow().isoformat(),
            player, coordinates, village, scout_time, additional_info,
            requested_by_id, requested_by_name,
        ))
        await db.commit()


async def is_scout_channel(channel_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM scout_channels WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def add_res_request(
    guild_id: str,
    answer_message_id: str,
    user_id: str,
    user_name: str,
    player_name: str,
    coordinates: str,
    push_height: str,
    reason: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO res_requests
                (guild_id, answer_message_id, user_id, user_name, player_name, coordinates, push_height, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (guild_id, answer_message_id, user_id, user_name, player_name, coordinates, push_height, reason, datetime.utcnow().isoformat()))
        await db.commit()
        return cursor.lastrowid


async def get_res_request_by_answer_msg(answer_message_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE answer_message_id = ?", (answer_message_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_res_request_by_push_msg(push_message_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE push_message_id = ?", (push_message_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_res_request_by_id(request_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_res_request_status(answer_message_id: str, status: str, push_channel_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        if push_channel_id:
            await db.execute(
                "UPDATE res_requests SET status = ?, push_channel_id = ? WHERE answer_message_id = ?",
                (status, push_channel_id, answer_message_id),
            )
        else:
            await db.execute(
                "UPDATE res_requests SET status = ? WHERE answer_message_id = ?",
                (status, answer_message_id),
            )
        await db.commit()


async def get_res_request_by_push_channel(push_channel_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE push_channel_id = ?", (push_channel_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_res_contribution(request_id: int, user_id: str, user_name: str, amount: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO res_contributions (request_id, user_id, user_name, amount, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (request_id, user_id, user_name, amount, datetime.utcnow().isoformat()))
        await db.commit()


async def get_res_contributions(request_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_contributions WHERE request_id = ? ORDER BY created_at ASC",
            (request_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_res_button(guild_id: str, res_request_channel_id: str, res_button_message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET res_request_channel_id = ?, res_button_message_id = ?
            WHERE guild_id = ?
        """, (res_request_channel_id, res_button_message_id, guild_id))
        await db.commit()


async def get_scout_channel_info(channel_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_channels WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_poll(poll_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM availability_polls WHERE id = ?", (poll_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_poll_response(poll_id: int, user_id: str, user_name: str, response: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO poll_responses (poll_id, user_id, user_name, response, responded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(poll_id, user_id) DO UPDATE SET
                response=excluded.response, user_name=excluded.user_name, responded_at=excluded.responded_at
        """, (poll_id, user_id, user_name, response, datetime.utcnow().isoformat()))
        await db.commit()
