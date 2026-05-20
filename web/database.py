import aiosqlite
import bcrypt
import os
from pathlib import Path

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
            CREATE TABLE IF NOT EXISTS admin_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        await db.commit()

    # Migrations
    async with aiosqlite.connect(DB_PATH) as db:
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

        try:
            await db.execute("ALTER TABLE res_requests ADD COLUMN push_channel_id TEXT")
            await db.commit()
        except Exception:
            pass

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


async def auto_setup_guild(
    guild_id: str,
    category_id: str,
    scout_channel_id: str,
    archive_channel_id: str,
    button_message_id: str,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET category_id = ?, scout_channel_id = ?, archive_channel_id = ?, button_message_id = ?
            WHERE guild_id = ?
        """, (category_id, scout_channel_id, archive_channel_id, button_message_id, guild_id))
        await db.commit()


async def get_guild_stats(guild_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT COUNT(*) as total FROM scout_channels WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            total = row["total"] if row else 0

        async with db.execute(
            "SELECT COUNT(*) as total FROM scout_channels WHERE guild_id = ? AND date(created_at) = date('now')",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            today = row["total"] if row else 0

        async with db.execute(
            "SELECT COUNT(*) as total FROM scout_channels WHERE guild_id = ? AND created_at >= datetime('now', '-7 days')",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            last7 = row["total"] if row else 0

        async with db.execute(
            "SELECT player, COUNT(*) as cnt FROM scout_channels WHERE guild_id = ? GROUP BY player ORDER BY cnt DESC LIMIT 10",
            (guild_id,),
        ) as cur:
            top_players = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT coordinates, COUNT(*) as cnt FROM scout_channels WHERE guild_id = ? GROUP BY coordinates ORDER BY cnt DESC LIMIT 10",
            (guild_id,),
        ) as cur:
            top_coords = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT * FROM scout_channels WHERE guild_id = ? ORDER BY created_at DESC LIMIT 10",
            (guild_id,),
        ) as cur:
            recent = [dict(r) for r in await cur.fetchall()]

    return {
        "total": total,
        "today": today,
        "last7": last7,
        "top_players": top_players,
        "top_coords": top_coords,
        "recent": recent,
    }


async def get_scout_channels(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_channels WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def update_res_config(
    guild_id: str,
    res_request_channel_id: str,
    res_answer_channel_id: str,
    res_push_category_id: str,
    res_manager_role_ids: str,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET res_request_channel_id = ?,
                res_answer_channel_id  = ?,
                res_push_category_id   = ?,
                res_manager_role_ids   = ?
            WHERE guild_id = ?
        """, (
            res_request_channel_id or None,
            res_answer_channel_id or None,
            res_push_category_id or None,
            res_manager_role_ids or None,
            guild_id,
        ))
        await db.commit()


async def update_res_button(guild_id: str, res_request_channel_id: str, res_button_message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET res_request_channel_id = ?, res_button_message_id = ?
            WHERE guild_id = ?
        """, (res_request_channel_id, res_button_message_id, guild_id))
        await db.commit()


async def get_res_requests(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_res_contributions_for_guild(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT rc.*, rr.player_name, rr.coordinates, rr.push_height
            FROM res_contributions rc
            JOIN res_requests rr ON rc.request_id = rr.id
            WHERE rr.guild_id = ?
            ORDER BY rc.created_at DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_res_stats(guild_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT COUNT(*) as total FROM res_requests WHERE guild_id = ?", (guild_id,)
        ) as cur:
            total = (await cur.fetchone())["total"]

        async with db.execute(
            "SELECT status, COUNT(*) as cnt FROM res_requests WHERE guild_id = ? GROUP BY status",
            (guild_id,),
        ) as cur:
            by_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}

        async with db.execute(
            "SELECT * FROM res_requests WHERE guild_id = ? ORDER BY created_at DESC LIMIT 20",
            (guild_id,),
        ) as cur:
            recent = [dict(r) for r in await cur.fetchall()]

        async with db.execute("""
            SELECT rc.user_name, COUNT(*) as cnt, SUM(CAST(rc.amount AS INTEGER)) as total_sent
            FROM res_contributions rc
            JOIN res_requests rr ON rc.request_id = rr.id
            WHERE rr.guild_id = ?
            GROUP BY rc.user_id, rc.user_name
            ORDER BY total_sent DESC
            LIMIT 10
        """, (guild_id,)) as cur:
            top_contributors = [dict(r) for r in await cur.fetchall()]

    return {
        "total": total,
        "by_status": by_status,
        "recent": recent,
        "top_contributors": top_contributors,
    }


async def reset_scout_config(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET scout_channel_id = NULL, category_id = NULL,
                archive_channel_id = NULL, button_message_id = NULL
            WHERE guild_id = ?
        """, (guild_id,))
        await db.commit()


async def reset_res_config(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET res_request_channel_id = NULL, res_answer_channel_id = NULL,
                res_push_category_id = NULL, res_button_message_id = NULL
            WHERE guild_id = ?
        """, (guild_id,))
        await db.commit()


async def set_res_request_status_by_id(request_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE res_requests SET status = ? WHERE id = ?", (status, request_id)
        )
        await db.commit()


async def delete_res_request(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM res_contributions WHERE request_id = ?", (request_id,))
        await db.execute("DELETE FROM res_requests WHERE id = ?", (request_id,))
        await db.commit()


async def get_res_request_by_id_web(request_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM res_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_scout_channel_info(channel_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scout_channels WHERE channel_id = ?", (channel_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_scout_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scout_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


_ALLOWED_ROLE_FIELDS = {"allowed_role_ids", "res_manager_role_ids"}

async def toggle_role_in_field(guild_id: str, role_id: str, field: str) -> bool:
    """Toggle role_id in field. Returns True=added, False=removed."""
    if field not in _ALLOWED_ROLE_FIELDS:
        raise ValueError(f"Invalid field: {field}")
    guild = await get_guild(guild_id)
    current = (guild or {}).get(field) or ""
    role_ids = {r.strip() for r in current.split(",") if r.strip()}
    if role_id in role_ids:
        role_ids.discard(role_id)
        added = False
    else:
        role_ids.add(role_id)
        added = True
    new_value = ",".join(sorted(role_ids)) or None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE guild_configs SET {field} = ? WHERE guild_id = ?", (new_value, guild_id))
        await db.commit()
    return added
