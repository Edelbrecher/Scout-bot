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

        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN tw_world TEXT")
            await db.commit()
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN poll_channel_id TEXT")
            await db.commit()
        except Exception:
            pass

        for col in ["requested_by_id TEXT", "requested_by_name TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        for col in [
            "stripe_customer_id TEXT",
            "stripe_subscription_id TEXT",
            "subscription_status TEXT DEFAULT 'free'",
            "subscription_plan TEXT",
            "subscription_expires_at TEXT",
            "attack_channel_id TEXT",
            "attack_button_message_id TEXT",
            "owner_discord_id TEXT",
            "bot_status TEXT DEFAULT 'active'",
            "bot_kicked_at TEXT",
        ]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS attack_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        TEXT NOT NULL,
                reporter_id     TEXT NOT NULL,
                reporter_name   TEXT NOT NULL,
                raw_text        TEXT NOT NULL,
                attacks_json    TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        await db.commit()

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS auth_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT DEFAULT (datetime('now')),
                discord_id      TEXT,
                username        TEXT,
                ip              TEXT,
                status          TEXT NOT NULL,
                detail          TEXT,
                guild_count     INTEGER DEFAULT 0,
                accessible_guilds INTEGER DEFAULT 0,
                has_active_sub  INTEGER DEFAULT 0,
                is_returning    INTEGER DEFAULT 0
            )
        """)
        await db.commit()

    await _init_farming_tables()
    await _init_einsatz_tables()
    await _init_admin_tables()
    await _init_consent_tables()
    await _init_user_sub_tables()
    await _init_own_villages_table()
    await _init_own_villages_history_table()

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


async def update_tw_world(guild_id: str, tw_world: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE guild_configs SET tw_world = ? WHERE guild_id = ?", (tw_world or None, guild_id))
        await db.commit()


async def get_scouted_coordinates(guild_id: str) -> list[dict]:
    """Return list of {coordinates, player, village} from scout_channels."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT coordinates, player, village FROM scout_channels WHERE guild_id = ? AND coordinates IS NOT NULL",
            (guild_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


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


async def get_poll_participation_stats(guild_id: str) -> list[dict]:
    """Per-user participation rate across all polls in the guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                pr.user_name,
                COUNT(DISTINCT pr.poll_id)                                        AS responded,
                SUM(CASE WHEN pr.response = 'available'   THEN 1 ELSE 0 END)     AS available,
                SUM(CASE WHEN pr.response = 'maybe'       THEN 1 ELSE 0 END)     AS maybe,
                SUM(CASE WHEN pr.response = 'unavailable' THEN 1 ELSE 0 END)     AS unavailable,
                (SELECT COUNT(*) FROM availability_polls WHERE guild_id = ?)      AS total_polls
            FROM poll_responses pr
            JOIN availability_polls ap ON ap.id = pr.poll_id
            WHERE ap.guild_id = ?
            GROUP BY pr.user_id, pr.user_name
            ORDER BY responded DESC, available DESC
        """, (guild_id, guild_id)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_res_contribution_leaderboard(guild_id: str) -> list[dict]:
    """Per-user contribution totals across all res-push requests."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                rc.user_name,
                COUNT(*)                                      AS contributions,
                SUM(CAST(REPLACE(rc.amount, ',', '') AS INTEGER)) AS total_amount,
                MAX(rc.created_at)                            AS last_active
            FROM res_contributions rc
            JOIN res_requests rr ON rr.id = rc.request_id
            WHERE rr.guild_id = ?
            GROUP BY rc.user_id, rc.user_name
            ORDER BY total_amount DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_res_contribution_details(guild_id: str) -> list[dict]:
    """Recent individual contributions with request context."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                rc.user_name, rc.amount, rc.created_at,
                rr.player_name, rr.coordinates, rr.push_height, rr.status
            FROM res_contributions rc
            JOIN res_requests rr ON rr.id = rc.request_id
            WHERE rr.guild_id = ?
            ORDER BY rc.created_at DESC
            LIMIT 50
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_scout_requester_stats(guild_id: str) -> list[dict]:
    """Who submitted the most scout requests (needs requested_by_name column)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Only works if the column exists (added by migration below)
        try:
            async with db.execute("""
                SELECT
                    requested_by_name AS name,
                    COUNT(*) AS cnt
                FROM scout_channels
                WHERE guild_id = ? AND requested_by_name IS NOT NULL AND requested_by_name != ''
                GROUP BY requested_by_name
                ORDER BY cnt DESC
                LIMIT 20
            """, (guild_id,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []


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


async def upsert_poll_response_admin(poll_id: int, user_id: str, user_name: str, response: str):
    """Admin override — works on open and closed polls."""
    from datetime import datetime
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO poll_responses (poll_id, user_id, user_name, response, responded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(poll_id, user_id) DO UPDATE SET
                response=excluded.response, user_name=excluded.user_name, responded_at=excluded.responded_at
        """, (poll_id, user_id, user_name, response, datetime.utcnow().isoformat()))
        await db.commit()


async def delete_poll_response(poll_id: int, user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM poll_responses WHERE poll_id = ? AND user_id = ?",
            (poll_id, user_id),
        )
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


# ---------------------------------------------------------------------------
# Poll system
# ---------------------------------------------------------------------------

async def update_poll_channel(guild_id: str, poll_channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE guild_configs SET poll_channel_id = ? WHERE guild_id = ?", (poll_channel_id or None, guild_id))
        await db.commit()


async def create_poll(guild_id: str, title: str, description: str, event_datetime: str) -> int:
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO availability_polls (guild_id, title, description, event_datetime, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (guild_id, title, description, event_datetime, _dt.utcnow().isoformat()))
        await db.commit()
        return cur.lastrowid


async def set_poll_message_id(poll_id: int, message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE availability_polls SET discord_message_id = ? WHERE id = ?", (message_id, poll_id))
        await db.commit()


async def get_polls(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM availability_polls WHERE guild_id = ? ORDER BY created_at DESC", (guild_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_poll(poll_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM availability_polls WHERE id = ?", (poll_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_poll_responses(poll_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM poll_responses WHERE poll_id = ? ORDER BY responded_at ASC", (poll_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def close_poll(poll_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE availability_polls SET status = 'closed' WHERE id = ?", (poll_id,))
        await db.commit()


async def delete_poll(poll_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM poll_responses WHERE poll_id = ?", (poll_id,))
        await db.execute("DELETE FROM availability_polls WHERE id = ?", (poll_id,))
        await db.commit()


async def get_guild_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE stripe_customer_id = ?", (stripe_customer_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_subscription(
    guild_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    plan: str,
    expires_at: str | None,
    owner_discord_id: str | None = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs SET
                stripe_customer_id      = ?,
                stripe_subscription_id  = ?,
                subscription_status     = ?,
                subscription_plan       = ?,
                subscription_expires_at = ?,
                owner_discord_id        = COALESCE(?, owner_discord_id)
            WHERE guild_id = ?
        """, (stripe_customer_id, stripe_subscription_id, status, plan, expires_at, owner_discord_id, guild_id))
        await db.commit()


async def get_owner_active_guilds(owner_discord_id: str) -> list[dict]:
    """All guilds where this Discord user is the subscription owner and sub is active/trialing."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT guild_id, guild_name, subscription_status, subscription_plan
            FROM guild_configs
            WHERE owner_discord_id = ?
              AND subscription_status IN ('active', 'trialing')
              AND (bot_status IS NULL OR bot_status != 'kicked')
        """, (owner_discord_id,)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_owner_tier_limit(owner_discord_id: str) -> int:
    """Returns the max number of servers this owner's highest active tier allows.
    Checks user_subscriptions first, then falls back to guild-level plans."""
    _tier_limits = {"starter": 1, "clan": 2, "alliance": 3, "imperium": 5}

    # Check user-level subscription first
    user_sub = await get_user_subscription(owner_discord_id)
    if user_sub and user_sub.get("subscription_status") in ("active", "trialing"):
        plan = (user_sub.get("plan") or "starter").split("_")[0]
        return _tier_limits.get(plan, 1)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT subscription_plan FROM guild_configs
            WHERE owner_discord_id = ?
              AND subscription_status IN ('active', 'trialing')
            ORDER BY
                CASE WHEN subscription_plan LIKE 'imperium%' THEN 4
                     WHEN subscription_plan LIKE 'alliance%' THEN 3
                     WHEN subscription_plan LIKE 'clan%'     THEN 2
                     ELSE 1 END DESC
            LIMIT 1
        """, (owner_discord_id,)) as cursor:
            row = await cursor.fetchone()
    if not row or not row["subscription_plan"]:
        return 0
    tier = row["subscription_plan"].split("_")[0]
    return _tier_limits.get(tier, 1)


async def set_bot_kicked(guild_id: str):
    """Mark guild as kicked — bot was removed from the server."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET bot_status = 'kicked', bot_kicked_at = datetime('now') WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def set_bot_active(guild_id: str):
    """Mark guild as active — bot rejoined or is present."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET bot_status = 'active', bot_kicked_at = NULL WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def set_subscription_status(guild_id: str, status: str, expires_at: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET subscription_status = ?, subscription_expires_at = ? WHERE guild_id = ?",
            (status, expires_at, guild_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Attack reports
# ---------------------------------------------------------------------------

async def get_attack_reports(guild_id: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM attack_reports WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_attack_stats(guild_id: str) -> dict:
    import json as _json

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT COUNT(*) as total FROM attack_reports WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            total_reports = row["total"] if row else 0

        async with db.execute(
            "SELECT * FROM attack_reports WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cur:
            all_reports = [dict(r) for r in await cur.fetchall()]

    # Aggregate from JSON
    attacker_counts: dict[str, int] = {}
    hours = [0] * 24
    for report in all_reports:
        try:
            attacks = _json.loads(report["attacks_json"])
        except Exception:
            attacks = []
        for atk in attacks:
            name = (atk.get("attacker") or "").strip()
            if name:
                attacker_counts[name] = attacker_counts.get(name, 0) + 1
        # Hour from created_at ISO string "2026-05-21T14:32:15.123456"
        try:
            hour = int(report["created_at"][11:13])
            hours[hour] += 1
        except Exception:
            pass

    top_attackers = sorted(
        [{"name": k, "count": v} for k, v in attacker_counts.items()],
        key=lambda x: -x["count"],
    )[:10]

    recent_attacks = all_reports[:10]

    return {
        "total_reports": total_reports,
        "top_attackers": top_attackers,
        "attacks_by_hour": hours,
        "recent_attacks": recent_attacks,
    }


async def set_attack_channel_web(guild_id: str, attack_channel_id: str, attack_button_message_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET attack_channel_id = ?, attack_button_message_id = ? WHERE guild_id = ?",
            (attack_channel_id or None, attack_button_message_id or None, guild_id),
        )
        await db.commit()


async def get_attack_report(guild_id: str, report_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM attack_reports WHERE id = ? AND guild_id = ?",
            (report_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_from_snapshot(guild_id: str, player_name: str) -> dict | None:
    """Look up a player by name in the latest map snapshot."""
    if not player_name:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Get the latest snapshot time for this guild
        async with db.execute(
            "SELECT MAX(fetched_at) as latest FROM map_snapshots WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            latest = row["latest"] if row else None
        if not latest:
            return None
        # Fetch all villages of this player from the latest snapshot
        async with db.execute(
            """SELECT * FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
               AND lower(player_name) = lower(?)""",
            (guild_id, latest, player_name),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return None
        first = rows[0]
        return {
            "player_name": first.get("player_name"),
            "alliance_name": first.get("alliance_name"),
            "alliance_id": first.get("alliance_id"),
            "tribe": first.get("tribe"),
            "villages": [
                {
                    "x": r["x"], "y": r["y"],
                    "village_name": r.get("village_name"),
                    "population": r.get("population"),
                    "is_capital": False,
                }
                for r in rows
            ],
            "total_pop": sum(r.get("population", 0) or 0 for r in rows),
            "village_count": len(rows),
        }


async def get_village_from_snapshot(guild_id: str, x: int, y: int) -> dict | None:
    """Look up a village by exact coords in the latest map snapshot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT MAX(fetched_at) as latest FROM map_snapshots WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            latest = row["latest"] if row else None
        if not latest:
            return None
        async with db.execute(
            "SELECT * FROM map_snapshots WHERE guild_id = ? AND fetched_at = ? AND x = ? AND y = ?",
            (guild_id, latest, x, y),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        r = dict(row)
        return {
            "player_name": r.get("player_name"),
            "village_name": r.get("village_name"),
            "alliance_name": r.get("alliance_name"),
            "tribe": r.get("tribe"),
            "population": r.get("population"),
            "x": r["x"], "y": r["y"],
        }


async def get_reports_by_attackers(guild_id: str, attacker_names: list[str]) -> list[dict]:
    """Return all reports where any of the given attacker names appear in attacks_json."""
    if not attacker_names:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM attack_reports WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cur:
            all_rows = [dict(r) for r in await cur.fetchall()]
    import json as _json
    results = []
    name_set = {n.lower() for n in attacker_names}
    for row in all_rows:
        try:
            attacks = _json.loads(row["attacks_json"])
        except Exception:
            continue
        if any((a.get("attacker") or "").lower() in name_set for a in attacks):
            results.append(row)
    return results


async def delete_attack_report(report_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM attack_reports WHERE id = ?", (report_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Farming / Map Snapshots
# ---------------------------------------------------------------------------

async def _init_farming_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS map_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                fetched_at    TEXT NOT NULL,
                village_id    TEXT NOT NULL,
                x             INTEGER NOT NULL,
                y             INTEGER NOT NULL,
                village_name  TEXT,
                player_id     TEXT,
                player_name   TEXT,
                alliance_id   TEXT,
                alliance_name TEXT,
                population    INTEGER NOT NULL,
                tribe         INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS farm_list_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                added_by_id  TEXT NOT NULL,
                added_by_name TEXT NOT NULL,
                x            INTEGER NOT NULL,
                y            INTEGER NOT NULL,
                village_name TEXT,
                player_name  TEXT,
                population   INTEGER,
                notes        TEXT,
                added_at     TEXT NOT NULL
            )
        """)
        await db.commit()
        # Index for performance
        try:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_map_snap_guild ON map_snapshots(guild_id, fetched_at)")
            await db.commit()
        except Exception:
            pass


async def save_map_snapshot(guild_id: str, villages: list[dict]):
    from datetime import datetime as _dt
    fetched_at = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("""
            INSERT INTO map_snapshots
                (guild_id, fetched_at, village_id, x, y, village_name, player_id, player_name,
                 alliance_id, alliance_name, population, tribe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                guild_id, fetched_at,
                str(v.get("village_id", "")),
                int(v.get("x", 0)), int(v.get("y", 0)),
                v.get("village_name"), v.get("player_id"), v.get("player_name"),
                v.get("alliance_id"), v.get("alliance_name"),
                int(v.get("population", 0)), v.get("tribe"),
            )
            for v in villages
        ])
        await db.commit()


async def get_latest_snapshot_time(guild_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT MAX(fetched_at) as t FROM map_snapshots WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def prune_old_snapshots(guild_id: str, keep_days: int = 30):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM map_snapshots
            WHERE guild_id = ?
              AND fetched_at < datetime('now', ? || ' days')
        """, (guild_id, f"-{keep_days}"))
        await db.commit()


async def get_snapshot_count(guild_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT fetched_at) as cnt FROM map_snapshots WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_inactive_farms(
    guild_id: str,
    min_days: int = 3,
    min_pop: int = 0,
    max_pop: int = 9999,
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT village_id, x, y, village_name, player_name, population, tribe,
                   MIN(fetched_at) as first_seen, MAX(fetched_at) as last_seen,
                   COUNT(DISTINCT fetched_at) as snapshot_count,
                   MIN(population) as min_pop, MAX(population) as max_pop
            FROM map_snapshots
            WHERE guild_id = ?
            GROUP BY village_id
            HAVING snapshot_count >= 2
               AND min_pop = max_pop
               AND julianday(last_seen) - julianday(first_seen) >= ?
               AND population >= ? AND population <= ?
            ORDER BY population DESC
        """, (guild_id, min_days, min_pop, max_pop)) as cur:
            rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                from datetime import datetime as _dt
                fs = _dt.fromisoformat(d["first_seen"])
                ls = _dt.fromisoformat(d["last_seen"])
                d["days_tracked"] = (ls - fs).days
            except Exception:
                d["days_tracked"] = 0
            result.append(d)
        return result


async def add_farm_list_entry(
    guild_id: str,
    added_by_id: str,
    added_by_name: str,
    x: int,
    y: int,
    village_name: str | None,
    player_name: str | None,
    population: int | None,
    notes: str | None,
) -> int:
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO farm_list_entries
                (guild_id, added_by_id, added_by_name, x, y, village_name, player_name, population, notes, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, added_by_id, added_by_name, x, y,
              village_name or None, player_name or None,
              population if population is not None else None,
              notes or None, _dt.utcnow().isoformat()))
        await db.commit()
        return cur.lastrowid


async def get_farm_list(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM farm_list_entries WHERE guild_id = ? ORDER BY added_at DESC", (guild_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_farm_list_entry(guild_id: str, entry_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM farm_list_entries WHERE id = ? AND guild_id = ?", (entry_id, guild_id)
        )
        await db.commit()


async def get_farm_stats(guild_id: str) -> dict:
    snapshot_count = await get_snapshot_count(guild_id)
    latest_snapshot = await get_latest_snapshot_time(guild_id)
    inactive = await get_inactive_farms(guild_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM farm_list_entries WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            farm_list_count = row[0] if row else 0
    return {
        "snapshot_count": snapshot_count,
        "latest_snapshot": latest_snapshot,
        "inactive_count": len(inactive),
        "farm_list_count": farm_list_count,
    }


async def get_farming_cross_reference(guild_id: str, min_days: int = 3) -> list[dict]:
    """Farm list entries that are also inactive."""
    inactive = await get_inactive_farms(guild_id, min_days=min_days)
    inactive_coords = {(r["x"], r["y"]): r for r in inactive}
    farm_list = await get_farm_list(guild_id)
    result = []
    for entry in farm_list:
        key = (entry["x"], entry["y"])
        if key in inactive_coords:
            inactive_data = inactive_coords[key]
            result.append({
                "x": entry["x"],
                "y": entry["y"],
                "village_name": entry.get("village_name") or inactive_data.get("village_name"),
                "player_name": entry.get("player_name") or inactive_data.get("player_name"),
                "population": inactive_data.get("population"),
                "days_tracked": inactive_data.get("days_tracked", 0),
                "notes": entry.get("notes"),
                "entry_id": entry["id"],
            })
    return result


# ── Einsatzplanung ────────────────────────────────────────────────────────────

async def _init_einsatz_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attack_plans (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                created_by   TEXT NOT NULL,
                created_name TEXT NOT NULL,
                plan_name    TEXT NOT NULL,
                target_x     INTEGER NOT NULL,
                target_y     INTEGER NOT NULL,
                target_name  TEXT,
                player_name  TEXT,
                arrival_time TEXT NOT NULL,
                wave_type    TEXT NOT NULL DEFAULT 'attack',
                troop_speed  REAL NOT NULL DEFAULT 6.0,
                notes        TEXT,
                created_at   TEXT NOT NULL
            )
        """)
        await db.commit()


async def get_attack_plans(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM attack_plans WHERE guild_id = ? ORDER BY arrival_time ASC",
            (guild_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def create_attack_plan(
    guild_id: str, created_by: str, created_name: str,
    plan_name: str, target_x: int, target_y: int,
    target_name: str | None, player_name: str | None,
    arrival_time: str, wave_type: str, troop_speed: float,
    notes: str | None,
) -> int:
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO attack_plans
                (guild_id, created_by, created_name, plan_name, target_x, target_y,
                 target_name, player_name, arrival_time, wave_type, troop_speed, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id, created_by, created_name, plan_name,
            target_x, target_y, target_name or None, player_name or None,
            arrival_time, wave_type, troop_speed, notes or None,
            _dt.utcnow().isoformat(),
        ))
        await db.commit()
        return cur.lastrowid


async def delete_attack_plan(guild_id: str, plan_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM attack_plans WHERE id = ? AND guild_id = ?",
            (plan_id, guild_id)
        )
        await db.commit()


# ── Admin settings ────────────────────────────────────────────────────────────

async def _init_admin_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()


async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM admin_settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO admin_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def update_subscription_plan(guild_id: str, status: str, plan: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET subscription_status = ?, subscription_plan = ? WHERE guild_id = ?",
            (status, plan, guild_id),
        )
        await db.commit()


async def get_recent_guilds(limit: int = 10) -> list[dict]:
    """Return the most recently added guilds (no created_at column, use rowid)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs ORDER BY rowid DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Cookie consent audit log
# ---------------------------------------------------------------------------

async def _init_consent_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cookie_consents (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT,
                username   TEXT,
                action     TEXT NOT NULL,
                ip         TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS page_visits (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT,
                username   TEXT,
                path       TEXT NOT NULL,
                ip         TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def log_cookie_consent(
    user_id: str | None,
    username: str | None,
    action: str,
    ip: str | None,
    user_agent: str | None,
):
    from datetime import datetime as _dt2
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO cookie_consents (user_id, username, action, ip, user_agent, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, action, ip, user_agent, _dt2.utcnow().isoformat()),
        )
        await db.commit()


async def get_cookie_consents(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM cookie_consents ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Page visits (funnel)
# ---------------------------------------------------------------------------

async def log_page_visit(
    user_id: str | None,
    username: str | None,
    path: str,
    ip: str | None,
):
    from datetime import datetime as _dt3
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO page_visits (user_id, username, path, ip, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, path, ip, _dt3.utcnow().isoformat()),
        )
        await db.commit()


async def get_funnel_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM page_visits WHERE path LIKE '%/billing' OR path LIKE '%/billing/' AND created_at >= datetime('now', '-30 days')"
        ) as cur:
            billing_visits = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM page_visits WHERE path LIKE '%/billing/checkout%' AND created_at >= datetime('now', '-30 days')"
        ) as cur:
            checkout_starts = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM guild_configs WHERE subscription_status = 'active' AND subscription_expires_at >= datetime('now', '-30 days')"
        ) as cur:
            completed = (await cur.fetchone())[0]

    return {
        "billing_visits": billing_visits,
        "checkout_starts": checkout_starts,
        "completed": completed,
    }


# ---------------------------------------------------------------------------
# User-level subscriptions (subscribe without owning a server first)
# ---------------------------------------------------------------------------

async def _init_user_sub_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                discord_user_id         TEXT PRIMARY KEY,
                stripe_customer_id      TEXT,
                stripe_subscription_id  TEXT,
                subscription_status     TEXT DEFAULT 'free',
                plan                    TEXT DEFAULT '',
                expires_at              TEXT,
                discord_username        TEXT,
                updated_at              TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()
        # Migrations for existing tables
        for col in [
            "discord_username TEXT",
        ]:
            try:
                await db.execute(f"ALTER TABLE user_subscriptions ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass


async def get_user_subscription(discord_user_id: str) -> dict | None:
    """Return the user_subscriptions row for this Discord user, or None."""
    if not discord_user_id:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_subscriptions WHERE discord_user_id = ?", (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_user_subscription(
    discord_user_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    plan: str,
    expires_at: str | None = None,
):
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_subscriptions
                (discord_user_id, stripe_customer_id, stripe_subscription_id,
                 subscription_status, plan, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                stripe_customer_id      = excluded.stripe_customer_id,
                stripe_subscription_id  = excluded.stripe_subscription_id,
                subscription_status     = excluded.subscription_status,
                plan                    = excluded.plan,
                expires_at              = excluded.expires_at,
                updated_at              = excluded.updated_at
        """, (
            discord_user_id, stripe_customer_id, stripe_subscription_id,
            status, plan, expires_at, _dt.utcnow().isoformat(),
        ))
        await db.commit()


async def get_user_available_slots(discord_user_id: str) -> tuple[int, int]:
    """Returns (slots_used, slots_max) for this Discord user.

    slots_max is derived from user_subscriptions.plan if active/trialing,
    otherwise falls back to the highest guild-level plan held by the owner.
    slots_used counts guild_configs rows where owner_discord_id = user and
    subscription_status is active or trialing.
    """
    _tier_limits = {"starter": 1, "clan": 2, "alliance": 3, "imperium": 5}

    # Count used slots
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM guild_configs
               WHERE owner_discord_id = ?
                 AND subscription_status IN ('active', 'trialing')""",
            (discord_user_id,),
        ) as cur:
            row = await cur.fetchone()
            slots_used = row[0] if row else 0

    # Determine max slots
    user_sub = await get_user_subscription(discord_user_id)
    if user_sub and user_sub.get("subscription_status") in ("active", "trialing"):
        tier = (user_sub.get("plan") or "starter").split("_")[0]
        slots_max = _tier_limits.get(tier, 1)
        return slots_used, slots_max

    # Fall back to guild-level plans
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT subscription_plan FROM guild_configs
               WHERE owner_discord_id = ?
                 AND subscription_status IN ('active', 'trialing')
               ORDER BY
                   CASE WHEN subscription_plan LIKE 'imperium%' THEN 4
                        WHEN subscription_plan LIKE 'alliance%' THEN 3
                        WHEN subscription_plan LIKE 'clan%'     THEN 2
                        ELSE 1 END DESC
               LIMIT 1""",
            (discord_user_id,),
        ) as cur:
            row = await cur.fetchone()
    if row and row["subscription_plan"]:
        tier = row["subscription_plan"].split("_")[0]
        slots_max = _tier_limits.get(tier, 1)
    else:
        slots_max = 0

    return slots_used, slots_max


async def get_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    """Return the user_subscriptions row for a given Stripe customer ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_subscriptions WHERE stripe_customer_id = ?", (stripe_customer_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_billing_visitors_without_sub() -> list[dict]:
    """Users who visited billing pages but don't have active subscription."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT pv.user_id, pv.username, pv.path, MAX(pv.created_at) as last_visit
            FROM page_visits pv
            WHERE pv.path LIKE '%/billing%'
              AND pv.user_id IS NOT NULL
              AND pv.user_id NOT IN (
                  SELECT owner_discord_id FROM guild_configs
                  WHERE subscription_status IN ('active', 'trialing')
                  AND owner_discord_id IS NOT NULL
              )
            GROUP BY pv.user_id
            ORDER BY last_visit DESC
            LIMIT 50
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def cache_discord_username(discord_user_id: str, username: str):
    """Store/update discord username in user_subscriptions for admin visibility."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_subscriptions (discord_user_id, discord_username, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(discord_user_id) DO UPDATE SET
                discord_username = excluded.discord_username,
                updated_at = datetime('now')
        """, (discord_user_id, username))
        await db.commit()


_TIER_LIMITS = {"starter": 1, "clan": 2, "alliance": 3, "imperium": 5}


async def get_customers_overview() -> list[dict]:
    """
    Returns all customers grouped by owner (discord_user_id).
    Each customer has: discord_user_id, discord_username, user_sub (plan/status),
    guilds (list of their servers), slots_used, slots_max.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # All guilds that have an owner set
        async with db.execute("""
            SELECT guild_id, guild_name, subscription_status, subscription_plan,
                   subscription_expires_at, stripe_customer_id, stripe_subscription_id,
                   owner_discord_id, category_id, archive_channel_id
            FROM guild_configs
            ORDER BY guild_name
        """) as cur:
            all_guilds = [dict(r) for r in await cur.fetchall()]

        # All user-level subscriptions
        async with db.execute("SELECT * FROM user_subscriptions") as cur:
            user_subs = {r["discord_user_id"]: dict(r) for r in await cur.fetchall()}

    # Group guilds by owner
    by_owner: dict[str, dict] = {}

    for g in all_guilds:
        owner_id = g.get("owner_discord_id") or "__unowned__"
        if owner_id not in by_owner:
            usub = user_subs.get(owner_id, {})
            by_owner[owner_id] = {
                "discord_user_id": owner_id,
                "discord_username": usub.get("discord_username") or owner_id,
                "user_sub": usub,
                "guilds": [],
            }
        by_owner[owner_id]["guilds"].append(g)

    # Also include users with user_sub but no guilds yet
    for uid, usub in user_subs.items():
        if uid not in by_owner:
            by_owner[uid] = {
                "discord_user_id": uid,
                "discord_username": usub.get("discord_username") or uid,
                "user_sub": usub,
                "guilds": [],
            }

    # Compute slots for each customer
    customers = []
    for owner_id, data in by_owner.items():
        if owner_id == "__unowned__":
            data["slots_used"] = 0
            data["slots_max"] = 0
            customers.append(data)
            continue

        # Slots from user_sub
        usub = data["user_sub"]
        usub_status = usub.get("subscription_status", "free")
        usub_plan = (usub.get("plan") or "").split("_")[0]
        if usub_status in ("active", "trialing") and usub_plan in _TIER_LIMITS:
            slots_max = _TIER_LIMITS[usub_plan]
        else:
            # Fall back to guild-level plans
            guild_plans = [
                g.get("subscription_plan", "") for g in data["guilds"]
                if g.get("subscription_status") in ("active", "trialing")
            ]
            best = max(
                (_TIER_LIMITS.get((p or "").split("_")[0], 0) for p in guild_plans),
                default=0
            )
            slots_max = best

        slots_used = sum(
            1 for g in data["guilds"]
            if g.get("subscription_status") in ("active", "trialing")
        )
        data["slots_used"] = slots_used
        data["slots_max"] = slots_max
        customers.append(data)

    # Sort: active customers first, then by username
    def _sort_key(c):
        has_active = any(
            g.get("subscription_status") in ("active", "trialing") for g in c["guilds"]
        ) or c["user_sub"].get("subscription_status") in ("active", "trialing")
        return (0 if has_active else 1, c["discord_username"].lower())

    customers.sort(key=_sort_key)
    return customers


async def update_user_subscription_admin(
    discord_user_id: str, status: str, plan: str,
    stripe_customer_id: str = "", stripe_subscription_id: str = "",
):
    """Admin override for user-level subscription."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_subscriptions
                (discord_user_id, subscription_status, plan, stripe_customer_id,
                 stripe_subscription_id, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(discord_user_id) DO UPDATE SET
                subscription_status = excluded.subscription_status,
                plan = excluded.plan,
                stripe_customer_id = COALESCE(NULLIF(excluded.stripe_customer_id,''), stripe_customer_id),
                stripe_subscription_id = COALESCE(NULLIF(excluded.stripe_subscription_id,''), stripe_subscription_id),
                updated_at = datetime('now')
        """, (discord_user_id, status, plan, stripe_customer_id, stripe_subscription_id))
        await db.commit()


async def has_logged_in_before(discord_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_username FROM user_subscriptions WHERE discord_user_id = ? AND discord_username IS NOT NULL",
            (discord_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def log_auth(
    status: str,
    discord_id: str = "",
    username: str = "",
    ip: str = "",
    detail: str = "",
    guild_count: int = 0,
    accessible_guilds: int = 0,
    has_active_sub: bool = False,
    is_returning: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO auth_logs
                (status, discord_id, username, ip, detail, guild_count, accessible_guilds, has_active_sub, is_returning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (status, discord_id, username, ip, detail, guild_count, accessible_guilds,
              1 if has_active_sub else 0, 1 if is_returning else 0))
        await db.commit()


async def get_auth_logs(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM auth_logs ORDER BY created_at DESC LIMIT ?
        """, (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _init_own_villages_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_own_villages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                village_name TEXT,
                x INTEGER,
                y INTEGER,
                population INTEGER,
                troops_json TEXT,
                village_type TEXT,
                def_score INTEGER DEFAULT 0,
                off_score INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0,
                uploaded_by TEXT,
                uploaded_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, x, y)
            )
        """)
        await db.commit()


async def save_own_villages(guild_id: str, villages: list[dict], uploaded_by: str):
    """Replace all own villages for a guild and record a history snapshot."""
    import json as _json
    CROP_MAP = {
        "Legionär": 1, "Prätorianer": 1, "Imperianer": 1,
        "Equites Legati": 2, "Equites Imperatoris": 3, "Equites Caesaris": 4,
        "Rammbock": 5, "Feuerkatapult": 6, "Senator": 5,
        "Keulenschwinger": 1, "Speerkämpfer": 1, "Axtkämpfer": 1,
        "Späher": 1, "Kundschafter": 1, "Paladin": 2, "Teut. Ritter": 3,
        "Häuptling": 4, "Stammesführer": 4, "Teutonen-Rammbock": 5, "Kriegsmaschine": 6,
        "Phalanx": 1, "Schwertkämpfer": 1, "Pathfinder": 2,
        "Theutates-Blitz": 2, "Druidentreiter": 2, "Haeduer": 3,
        "Stammesältester": 5, "Gallier-Rammbock": 5, "Gallier-Kata": 6,
        "Siedler": 1, "Held": 0,
    }
    total_off = sum(v.get("off_score", 0) for v in villages)
    total_def = sum(v.get("def_score", 0) for v in villages)
    total_crop = sum(
        sum(CROP_MAP.get(t, 1) * c for t, c in v.get("troops", {}).items())
        for v in villages
    )
    async with aiosqlite.connect(DB_PATH) as db:
        # Full replace: delete all current villages, then insert fresh
        await db.execute("DELETE FROM guild_own_villages WHERE guild_id = ?", (guild_id,))
        for v in villages:
            await db.execute("""
                INSERT INTO guild_own_villages
                    (guild_id, village_name, x, y, population, troops_json,
                     village_type, def_score, off_score, priority, uploaded_by, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                guild_id,
                v.get("village_name"),
                v.get("x"),
                v.get("y"),
                v.get("population", 0),
                _json.dumps(v.get("troops", {})),
                v.get("village_type", "mixed"),
                v.get("def_score", 0),
                v.get("off_score", 0),
                v.get("priority", 0),
                uploaded_by,
            ))
        # Record history snapshot
        await db.execute("""
            INSERT INTO guild_own_villages_history
                (guild_id, uploaded_at, total_off, total_def, total_crop, village_count, uploaded_by)
            VALUES (?, datetime('now'), ?, ?, ?, ?, ?)
        """, (guild_id, total_off, total_def, total_crop, len(villages), uploaded_by))
        await db.commit()


async def get_own_villages(guild_id: str) -> list[dict]:
    """Return all own villages for a guild, sorted by priority desc."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_own_villages WHERE guild_id = ? ORDER BY priority DESC, village_name ASC",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_own_villages(guild_id: str):
    """Delete all own villages for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guild_own_villages WHERE guild_id = ?", (guild_id,))
        await db.commit()


async def _init_own_villages_history_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_own_villages_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                uploaded_at TEXT DEFAULT (datetime('now')),
                total_off INTEGER DEFAULT 0,
                total_def INTEGER DEFAULT 0,
                total_crop INTEGER DEFAULT 0,
                village_count INTEGER DEFAULT 0,
                uploaded_by TEXT
            )
        """)
        await db.commit()


async def get_own_villages_history(guild_id: str) -> list[dict]:
    """Return historical snapshots for own villages, oldest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_own_villages_history WHERE guild_id = ? ORDER BY uploaded_at ASC",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_auth_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT status, COUNT(*) as count
            FROM auth_logs
            WHERE created_at >= datetime('now', '-30 days')
            GROUP BY status
        """) as cur:
            by_status = {r["status"]: r["count"] for r in await cur.fetchall()}
        async with db.execute("""
            SELECT is_returning, COUNT(*) as count
            FROM auth_logs
            WHERE status='success' AND created_at >= datetime('now', '-30 days')
            GROUP BY is_returning
        """) as cur:
            returning = {r["is_returning"]: r["count"] for r in await cur.fetchall()}
        async with db.execute("""
            SELECT COUNT(*) as count FROM auth_logs
            WHERE status='success' AND accessible_guilds=0 AND created_at >= datetime('now', '-30 days')
        """) as cur:
            no_server = (await cur.fetchone())["count"]
        async with db.execute("""
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM auth_logs
            WHERE status='success' AND created_at >= datetime('now', '-14 days')
            GROUP BY day ORDER BY day
        """) as cur:
            daily = [dict(r) for r in await cur.fetchall()]
        return {
            "by_status": by_status,
            "new_users": returning.get(0, 0),
            "returning_users": returning.get(1, 0),
            "no_server_logins": no_server,
            "daily": daily,
        }
