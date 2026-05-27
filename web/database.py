import aiosqlite
import bcrypt
import os
import uuid
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
            CREATE TABLE IF NOT EXISTS scout_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      TEXT NOT NULL,
                guild_id        TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'text',
                raw_text        TEXT,
                target_player   TEXT,
                target_village  TEXT,
                target_coords   TEXT,
                attacker_player TEXT,
                attacker_village TEXT,
                resources_json  TEXT,
                troops_json     TEXT,
                losses_json     TEXT,
                experience      INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL
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
        for col in [
            "allowed_role_ids TEXT",
            "res_request_channel_id TEXT",
            "res_answer_channel_id TEXT",
            "res_push_channel_id TEXT",
            "res_manager_role_ids TEXT",
            "res_button_message_id TEXT",
            "res_push_category_id TEXT",
            "private_channel_role_ids TEXT",
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

        for col in ["requested_by_id TEXT", "requested_by_name TEXT", "corn_scout INTEGER DEFAULT 0"]:
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

        # Personal workspace support
        for col in [
            "workspace_type TEXT DEFAULT 'discord'",
            "workspace_owner_id TEXT",
        ]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        # workspace_status (active / archived)
        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN workspace_status TEXT DEFAULT 'active'")
            await db.commit()
        except Exception:
            pass

        # Trial system
        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN trial_expires_at TEXT")
            await db.commit()
        except Exception:
            pass

        # Bot language (de / en)
        try:
            await db.execute("ALTER TABLE guild_configs ADD COLUMN bot_language TEXT DEFAULT 'de'")
            await db.commit()
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_links (
                code            TEXT PRIMARY KEY,
                created_by      TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                activated_at    TEXT,
                activated_guild_id TEXT
            )
        """)
        await db.commit()

        # Referral system
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_codes (
                discord_user_id TEXT PRIMARY KEY,
                code            TEXT UNIQUE NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        await db.commit()

        # Private channels (one per user per guild)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS private_channels (
                channel_id  TEXT PRIMARY KEY,
                guild_id    TEXT NOT NULL,
                owner_id    TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(guild_id, owner_id)
            )
        """)
        await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_events (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_discord_id   TEXT NOT NULL,
                referred_discord_id   TEXT NOT NULL UNIQUE,
                awarded_at            TEXT NOT NULL
            )
        """)
        await db.commit()

        # travops_points on user_subscriptions
        try:
            await db.execute("ALTER TABLE user_subscriptions ADD COLUMN travops_points INTEGER DEFAULT 0")
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

    # Report channels table
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS report_channels (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL UNIQUE,
                channel_id   TEXT,
                channel_name TEXT,
                created_at   TEXT NOT NULL
            )
        """)
        await db.commit()

    # Request Hub + Defend channels tables
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS request_hub (
                guild_id    TEXT PRIMARY KEY,
                channel_id  TEXT NOT NULL,
                channel_name TEXT DEFAULT 'travops-anfragen',
                message_id  TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS defend_channels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      TEXT UNIQUE NOT NULL,
                guild_id        TEXT NOT NULL,
                type            TEXT NOT NULL DEFAULT 'defend',
                attacker        TEXT DEFAULT '',
                coords          TEXT DEFAULT '',
                arrival_time    TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                requested_by_id TEXT DEFAULT '',
                requested_by_name TEXT DEFAULT '',
                status          TEXT DEFAULT 'open',
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
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
    await _init_sitter_table()
    await init_blueprint_tables()
    await init_village_layout_tables()
    await _init_settle_list_table()
    await _init_dual_links_table()
    await _init_farmlist_analyses_table()
    await _init_hospital_table()
    await _init_alliance_members_table()

    # New column migrations
    async with aiosqlite.connect(DB_PATH) as db:
        for col in ["alliance_manager_role_ids TEXT", "tw_alliance_name TEXT"]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE account_sitters ADD COLUMN is_shared INTEGER DEFAULT 0")
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
        async with db.execute("SELECT * FROM guild_configs WHERE COALESCE(workspace_status,'active') != 'archived' ORDER BY guild_name") as cursor:
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
    bot_language: str = "",
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET category_id = ?, archive_channel_id = ?, allowed_role_ids = ?,
                scout_channel_id = COALESCE(NULLIF(?, ''), scout_channel_id),
                bot_language = COALESCE(NULLIF(?, ''), COALESCE(bot_language, 'de'))
            WHERE guild_id = ?
        """, (category_id or None, archive_channel_id or None, allowed_role_ids or None,
              scout_channel_id, bot_language, guild_id))
        await db.commit()


async def create_personal_workspace(owner_discord_id: str, name: str) -> str:
    """Create a personal (Discord-less) workspace and return its guild_id (UUID)."""
    ws_id = "ws_" + uuid.uuid4().hex[:16]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO guild_configs (guild_id, guild_name, workspace_type, workspace_owner_id, owner_discord_id)
            VALUES (?, ?, 'personal', ?, ?)
        """, (ws_id, name, owner_discord_id, owner_discord_id))
        await db.commit()
    return ws_id


async def get_personal_workspaces(owner_discord_id: str) -> list[dict]:
    """Return all personal workspaces owned by a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM guild_configs
            WHERE workspace_type = 'personal' AND workspace_owner_id = ?
              AND COALESCE(workspace_status,'active') != 'archived'
            ORDER BY guild_name
        """, (owner_discord_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_or_create_default_workspace(owner_discord_id: str, username: str) -> str:
    """Return the first personal workspace ID, creating one if none exists."""
    existing = await get_personal_workspaces(owner_discord_id)
    if existing:
        return existing[0]["guild_id"]
    default_name = f"{username}'s Workspace"
    return await create_personal_workspace(owner_discord_id, default_name)


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


async def close_scout_channel_by_message(discord_message_id: str):
    """Mark a scout_channel as closed when its Discord message is deleted."""
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE scout_channels SET closed_at = ?, closed_by = 'discord_delete'
            WHERE discord_message_id = ? AND closed_at IS NULL
        """, (_dt.utcnow().isoformat(), discord_message_id))
        await db.commit()


async def save_scout_image(
    guild_id: str, channel_id: str, discord_url: str,
    discord_message_id: str = "", scout_report_id: int | None = None
) -> int:
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO scout_images (scout_report_id, guild_id, channel_id,
                                      discord_url, discord_message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scout_report_id, guild_id, channel_id, discord_url,
              discord_message_id, _dt.utcnow().isoformat()))
        await db.commit()
        return cur.lastrowid


async def get_scout_images_for_channel(channel_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM scout_images WHERE channel_id = ? ORDER BY created_at DESC
        """, (channel_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Enemy Kartei ──────────────────────────────────────────────────────────────

async def upsert_enemy(
    guild_id: str, player_name: str,
    coordinates: str = "", village: str = ""
) -> int:
    """Create or update an enemy entry. Returns the enemy id."""
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            INSERT INTO enemies (guild_id, player_name, coordinates, village,
                                 first_seen, last_seen, scout_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(guild_id, player_name) DO UPDATE SET
                last_seen   = excluded.last_seen,
                scout_count = scout_count + 1,
                coordinates = CASE WHEN excluded.coordinates != '' THEN excluded.coordinates
                                   ELSE coordinates END,
                village     = CASE WHEN excluded.village != '' THEN excluded.village
                                   ELSE village END
        """, (guild_id, player_name, coordinates or "", village or "", now, now))
        await db.commit()
        async with db.execute(
            "SELECT id FROM enemies WHERE guild_id=? AND player_name=?",
            (guild_id, player_name)
        ) as cur:
            row = await cur.fetchone()
            return row["id"] if row else 0


async def get_enemies(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT e.*,
                   COUNT(sr.id) as report_count
            FROM enemies e
            LEFT JOIN scout_reports sr
                ON sr.guild_id = e.guild_id
                AND sr.target_player = e.player_name
            WHERE e.guild_id = ?
            GROUP BY e.id
            ORDER BY e.last_seen DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_enemy(guild_id: str, player_name: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM enemies WHERE guild_id=? AND player_name=?",
            (guild_id, player_name)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_enemy_scout_history(guild_id: str, player_name: str) -> list[dict]:
    """All scout reports for an enemy, with images."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT sr.*,
                   GROUP_CONCAT(si.discord_url, '|||') as image_urls_concat
            FROM scout_reports sr
            LEFT JOIN scout_images si
                ON si.scout_report_id = sr.id
            WHERE sr.guild_id = ?
              AND (
                sr.target_player = ?
                OR sr.target_player = (
                    SELECT player FROM scout_channels
                    WHERE guild_id = sr.guild_id
                      AND LOWER(player) = LOWER(?)
                    LIMIT 1
                )
              )
            GROUP BY sr.id
            ORDER BY sr.created_at DESC
        """, (guild_id, player_name, player_name)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
            for r in rows:
                urls = r.pop("image_urls_concat", "") or ""
                r["images"] = [u for u in urls.split("|||") if u]
            return rows


async def update_enemy_notes(guild_id: str, player_name: str, notes: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE enemies SET notes=? WHERE guild_id=? AND player_name=?",
            (notes, guild_id, player_name)
        )
        await db.commit()


async def delete_enemy(guild_id: str, player_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM enemies WHERE guild_id=? AND player_name=?",
            (guild_id, player_name)
        )
        await db.commit()


async def set_report_channel(guild_id: str, channel_id: str | None, channel_name: str | None):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO report_channels (guild_id, channel_id, channel_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id   = excluded.channel_id,
                channel_name = excluded.channel_name
        """, (guild_id, channel_id, channel_name, now))
        await db.commit()


async def get_report_channel(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM report_channels WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def is_report_channel(channel_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM report_channels WHERE channel_id = ?", (channel_id,)
        ) as cur:
            return await cur.fetchone() is not None


_ALLOWED_ROLE_FIELDS = {"allowed_role_ids", "res_manager_role_ids", "private_channel_role_ids"}

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


async def clear_all_snapshots(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM map_snapshots WHERE guild_id = ?", (guild_id,))
        await db.commit()


async def get_servers_overview() -> list[dict]:
    """Return all guilds with tw_world set, plus snapshot stats per guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # All guilds with tw_world configured
        async with db.execute("""
            SELECT guild_id, guild_name, tw_world,
                   subscription_status, subscription_plan
            FROM guild_configs
            ORDER BY guild_name
        """) as cur:
            guilds = [dict(r) for r in await cur.fetchall()]

        # Snapshot stats per guild
        async with db.execute("""
            SELECT guild_id,
                   COUNT(DISTINCT fetched_at)  AS snap_count,
                   MAX(fetched_at)             AS last_fetch,
                   MIN(fetched_at)             AS first_fetch,
                   COUNT(*)                    AS total_villages,
                   COUNT(DISTINCT fetched_at || '|' || CASE WHEN fetched_at = (
                       SELECT MAX(s2.fetched_at) FROM map_snapshots s2
                       WHERE s2.guild_id = map_snapshots.guild_id
                   ) THEN '1' ELSE '0' END)    AS _unused
            FROM map_snapshots
            GROUP BY guild_id
        """) as cur:
            snap_rows = {r["guild_id"]: dict(r) for r in await cur.fetchall()}

        # Villages in latest snapshot per guild
        async with db.execute("""
            SELECT m.guild_id, COUNT(*) AS latest_village_count
            FROM map_snapshots m
            INNER JOIN (
                SELECT guild_id, MAX(fetched_at) AS max_ts
                FROM map_snapshots GROUP BY guild_id
            ) latest ON m.guild_id = latest.guild_id AND m.fetched_at = latest.max_ts
            GROUP BY m.guild_id
        """) as cur:
            latest_counts = {r["guild_id"]: r["latest_village_count"] for r in await cur.fetchall()}

        # Alliance member counts
        async with db.execute("""
            SELECT guild_id, COUNT(*) AS member_count
            FROM alliance_members GROUP BY guild_id
        """) as cur:
            member_counts = {r["guild_id"]: r["member_count"] for r in await cur.fetchall()}

        result = []
        for g in guilds:
            gid = g["guild_id"]
            snap = snap_rows.get(gid, {})
            result.append({
                **g,
                "snap_count": snap.get("snap_count", 0),
                "last_fetch": snap.get("last_fetch", None),
                "first_fetch": snap.get("first_fetch", None),
                "latest_village_count": latest_counts.get(gid, 0),
                "member_count": member_counts.get(gid, 0),
            })
        return result


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
                   MIN(population) as min_pop_val, MAX(population) as max_pop_val
            FROM map_snapshots
            WHERE guild_id = ?
            GROUP BY village_id
            HAVING snapshot_count >= 2
               AND CAST(max_pop_val AS REAL) - CAST(min_pop_val AS REAL) <= 2
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


async def search_inactive_advanced(
    guild_id: str,
    ref_x: int = 0,
    ref_y: int = 0,
    max_days_inactive: int = 2,
    min_pop: int = 0,
    max_pop: int = 9999,
    min_player_pop: int = 0,
    max_player_pop: int = 999999,
    max_distance: float = 9999,
    min_distance: float = 0,
    player_filter: str = "",
    alliance_filter: str = "",
    exclude_players: str = "",
    exclude_alliances: str = "",
    tribes: list | None = None,
    include_natars: bool = False,
    max_pop_increase: int = 0,
    limit: int = 300,
) -> dict:
    import math

    async with aiosqlite.connect(DB_PATH) as db:

        # Ensure indexes exist for fast lookups
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_mapsnap_guild_ts
            ON map_snapshots(guild_id, fetched_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_mapsnap_guild_xy
            ON map_snapshots(guild_id, x, y)
        """)

        # Last 8 distinct snapshot timestamps
        async with db.execute("""
            SELECT DISTINCT fetched_at FROM map_snapshots
            WHERE guild_id = ?
            ORDER BY fetched_at DESC LIMIT 8
        """, (guild_id,)) as cur:
            snap_dates = [r[0] for r in await cur.fetchall()]

        if len(snap_dates) < 2:
            return {"villages": [], "snap_dates": snap_dates, "total": 0}

        latest_ts = snap_dates[0]

        # Build filter lists
        excl_players   = [p.strip().lower() for p in exclude_players.split(",")  if p.strip()]
        incl_players   = [p.strip().lower() for p in player_filter.split(",")    if p.strip()]
        incl_alliances = [a.strip().lower() for a in alliance_filter.split(",")  if a.strip()]
        excl_alliances = [a.strip().lower() for a in (exclude_alliances or "").split(",") if a.strip()]

        snap_placeholders = ",".join("?" * len(snap_dates))

        # CTE: per-village stats across all tracked snapshots (no self-join)
        # Then join latest snapshot for village details
        query = f"""
            WITH stats AS (
                SELECT guild_id, x, y,
                       MAX(population) - MIN(population) AS pop_range,
                       COUNT(DISTINCT fetched_at)         AS snap_count
                FROM map_snapshots
                WHERE guild_id = ?
                  AND fetched_at IN ({snap_placeholders})
                GROUP BY guild_id, x, y
            )
            SELECT v.x, v.y, v.village_name, v.player_name,
                   v.alliance_name, v.population, v.tribe,
                   s.pop_range, s.snap_count
            FROM map_snapshots v
            JOIN stats s ON s.guild_id = v.guild_id AND s.x = v.x AND s.y = v.y
            WHERE v.guild_id = ?
              AND v.fetched_at = ?
              AND s.snap_count >= 2
              AND s.pop_range <= ?
              AND v.population >= ? AND v.population <= ?
        """
        params = [guild_id, *snap_dates, guild_id, latest_ts, max_pop_increase, min_pop, max_pop]

        # Natars filter in SQL
        if not include_natars:
            query += " AND v.tribe != 4 AND v.tribe != '4'"

        async with db.execute(query, params) as cur:
            cur.row_factory = aiosqlite.Row
            raw = [dict(r) for r in await cur.fetchall()]

        if not raw:
            return {"villages": [], "snap_dates": snap_dates, "total": 0}

        # Player total pop from latest snapshot
        async with db.execute("""
            SELECT player_name, SUM(population) as total_pop
            FROM map_snapshots WHERE guild_id=? AND fetched_at=?
            GROUP BY player_name
        """, (guild_id, latest_ts)) as cur:
            player_pop = {r[0]: r[1] for r in await cur.fetchall()}

        # Collect only the xy coords that pass text/distance filters first
        filtered = []
        for v in raw:
            pname = (v["player_name"] or "").strip()
            aname = (v["alliance_name"] or "").strip()

            if incl_players and pname.lower() not in incl_players:
                continue
            if incl_alliances and aname.lower() not in incl_alliances:
                continue
            if excl_players and pname.lower() in excl_players:
                continue
            if excl_alliances and aname.lower() in excl_alliances:
                continue
            pp = player_pop.get(pname, 0) or 0
            if pp < min_player_pop or pp > max_player_pop:
                continue
            if tribes and v["tribe"] not in tribes:
                continue

            dx = min(abs(v["x"] - ref_x), 800 - abs(v["x"] - ref_x))
            dy = min(abs(v["y"] - ref_y), 800 - abs(v["y"] - ref_y))
            dist = round(math.sqrt(dx*dx + dy*dy), 2)
            if dist < min_distance or dist > max_distance:
                continue

            filtered.append({**v, "distance": dist, "player_total_pop": pp})

        total = len(filtered)
        filtered.sort(key=lambda v: v["distance"])
        filtered = filtered[:limit]

        if not filtered:
            return {"villages": [], "snap_dates": snap_dates, "total": 0}

        # Fetch pop history only for the villages in the result set
        xy_set = {(v["x"], v["y"]) for v in filtered}
        xy_placeholders = ",".join("(?,?)" for _ in xy_set)
        xy_flat = [val for xy in xy_set for val in xy]

        pop_lookup: dict = {}
        async with db.execute(f"""
            SELECT x, y, fetched_at, population
            FROM map_snapshots
            WHERE guild_id = ?
              AND fetched_at IN ({snap_placeholders})
              AND (x, y) IN ({xy_placeholders})
        """, [guild_id, *snap_dates, *xy_flat]) as cur:
            for r in await cur.fetchall():
                pop_lookup[(r[0], r[1], r[2])] = r[3]

        villages = []
        for v in filtered:
            x, y = v["x"], v["y"]
            pop_history = []
            for i, ts in enumerate(snap_dates):
                pop_now  = pop_lookup.get((x, y, ts))
                pop_prev = pop_lookup.get((x, y, snap_dates[i+1])) if i+1 < len(snap_dates) else None
                if pop_now is None:
                    pop_history.append(None)
                elif pop_prev is None:
                    pop_history.append(0)
                else:
                    pop_history.append(pop_now - pop_prev)
            villages.append({**v, "pop_history": pop_history})

        return {"villages": villages, "snap_dates": snap_dates, "total": total}


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


async def get_player_growth(guild_id: str, limit: int = 50) -> list[dict]:
    """Return top growing / shrinking players between first and last snapshot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # We need first and last snapshot times
        cur = await db.execute(
            "SELECT MIN(fetched_at) as first_snap, MAX(fetched_at) as last_snap "
            "FROM map_snapshots WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if not row or not row["first_snap"] or row["first_snap"] == row["last_snap"]:
            return []
        first_snap = row["first_snap"]
        last_snap  = row["last_snap"]

        # Sum population per player at first snapshot
        cur = await db.execute(
            """SELECT player_id, player_name, SUM(population) as pop
               FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
                 AND player_id IS NOT NULL AND player_id != '' AND player_id != '0'
               GROUP BY player_id""",
            (guild_id, first_snap)
        )
        first = {r["player_id"]: {"name": r["player_name"], "pop": r["pop"]} for r in await cur.fetchall()}

        cur = await db.execute(
            """SELECT player_id, player_name, SUM(population) as pop, COUNT(*) as villages
               FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
                 AND player_id IS NOT NULL AND player_id != '' AND player_id != '0'
               GROUP BY player_id""",
            (guild_id, last_snap)
        )
        last_rows = await cur.fetchall()

    results = []
    for r in last_rows:
        pid = r["player_id"]
        pop_now = r["pop"] or 0
        pop_before = first.get(pid, {}).get("pop", pop_now)
        delta = pop_now - pop_before
        results.append({
            "player_id": pid,
            "player_name": r["player_name"] or "(unbekannt)",
            "pop_before": pop_before,
            "pop_now": pop_now,
            "delta": delta,
            "villages": r["villages"],
            "first_snap": first_snap[:16].replace("T", " "),
            "last_snap": last_snap[:16].replace("T", " "),
        })

    results.sort(key=lambda x: x["delta"], reverse=True)
    return results[:limit]


async def search_map_snapshot(guild_id: str, query: str) -> list[dict]:
    """Search villages/players in the latest snapshot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT MAX(fetched_at) as latest FROM map_snapshots WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if not row or not row["latest"]:
            return []
        latest = row["latest"]
        q = f"%{query}%"
        cur = await db.execute(
            """SELECT x, y, village_name, player_name, alliance_name, population, tribe
               FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
                 AND (village_name LIKE ? OR player_name LIKE ? OR alliance_name LIKE ?)
               ORDER BY population DESC LIMIT 100""",
            (guild_id, latest, q, q, q)
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_farmlist_heatmap(guild_id: str, discord_user_id: str) -> list[dict]:
    """Return all farm coords with resource totals from user's latest farmlist analysis."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT farms_json FROM farmlist_analyses
               WHERE guild_id = ? AND discord_user_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (guild_id, discord_user_id)
        )
        row = await cur.fetchone()
    if not row or not row["farms_json"]:
        return []
    import json as _json
    farms = _json.loads(row["farms_json"])
    result = []
    for f in farms:
        if not isinstance(f, dict):
            continue
        try:
            x = int(f.get("x", 0))
            y = int(f.get("y", 0))
        except (TypeError, ValueError):
            continue
        res = (f.get("resources_last") or 0) + (f.get("resources_total") or 0)
        result.append({"x": x, "y": y, "resources": res,
                        "village_name": f.get("village_name", ""),
                        "player_name": f.get("player_name", "")})
    return result


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
        # Record history snapshot — update today's entry if it already exists
        async with db.execute("""
            SELECT id FROM guild_own_villages_history
            WHERE guild_id = ? AND date(uploaded_at) = date('now')
        """, (guild_id,)) as cur:
            existing = await cur.fetchone()
        if existing:
            await db.execute("""
                UPDATE guild_own_villages_history
                SET total_off = ?, total_def = ?, total_crop = ?,
                    village_count = ?, uploaded_by = ?, uploaded_at = date('now')
                WHERE id = ?
            """, (total_off, total_def, total_crop, len(villages), uploaded_by, existing[0]))
        else:
            await db.execute("""
                INSERT INTO guild_own_villages_history
                    (guild_id, uploaded_at, total_off, total_def, total_crop, village_count, uploaded_by)
                VALUES (?, date('now'), ?, ?, ?, ?, ?)
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


async def _init_sitter_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS account_sitters (
                guild_id        TEXT NOT NULL,
                discord_user_id TEXT NOT NULL,
                sitter1_name    TEXT,
                sitter1_travian TEXT,
                sitter2_name    TEXT,
                sitter2_travian TEXT,
                sitting1_name   TEXT,
                sitting1_travian TEXT,
                sitting2_name   TEXT,
                sitting2_travian TEXT,
                updated_at      TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, discord_user_id)
            )
        """)
        await db.commit()


async def get_account_sitters(guild_id: str, user_id: str) -> dict | None:
    await _init_sitter_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM account_sitters WHERE guild_id = ? AND discord_user_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_account_sitters(guild_id: str, user_id: str, data: dict):
    await _init_sitter_table()
    from datetime import datetime as _dt
    is_shared = 1 if data.get("is_shared") else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO account_sitters
                (guild_id, discord_user_id, sitter1_name, sitter1_travian,
                 sitter2_name, sitter2_travian, sitting1_name, sitting1_travian,
                 sitting2_name, sitting2_travian, is_shared, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, discord_user_id) DO UPDATE SET
                sitter1_name    = excluded.sitter1_name,
                sitter1_travian = excluded.sitter1_travian,
                sitter2_name    = excluded.sitter2_name,
                sitter2_travian = excluded.sitter2_travian,
                sitting1_name   = excluded.sitting1_name,
                sitting1_travian = excluded.sitting1_travian,
                sitting2_name   = excluded.sitting2_name,
                sitting2_travian = excluded.sitting2_travian,
                is_shared       = excluded.is_shared,
                updated_at      = excluded.updated_at
        """, (
            guild_id, user_id,
            data.get("sitter1_name"), data.get("sitter1_travian"),
            data.get("sitter2_name"), data.get("sitter2_travian"),
            data.get("sitting1_name"), data.get("sitting1_travian"),
            data.get("sitting2_name"), data.get("sitting2_travian"),
            is_shared,
            _dt.utcnow().isoformat(),
        ))
        await db.commit()


async def get_scout_reports_for_channel(channel_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_reports WHERE channel_id = ? ORDER BY created_at DESC",
            (channel_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _init_settle_list_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settle_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                discord_user_id TEXT NOT NULL,
                discord_username TEXT,
                player_name TEXT,
                coordinates TEXT NOT NULL,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()


async def get_settle_list(guild_id: str) -> list[dict]:
    await _init_settle_list_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM settle_list WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_settle_entry(
    guild_id: str, user_id: str, username: str,
    player_name: str | None, coordinates: str, note: str | None,
) -> int:
    await _init_settle_list_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO settle_list (guild_id, discord_user_id, discord_username, player_name, coordinates, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, user_id, username or None, player_name or None, coordinates, note or None))
        await db.commit()
        return cur.lastrowid


async def delete_settle_entry(entry_id: int, guild_id: str, user_id: str, is_manager: bool):
    await _init_settle_list_table()
    async with aiosqlite.connect(DB_PATH) as db:
        if is_manager:
            await db.execute(
                "DELETE FROM settle_list WHERE id = ? AND guild_id = ?",
                (entry_id, guild_id),
            )
        else:
            await db.execute(
                "DELETE FROM settle_list WHERE id = ? AND guild_id = ? AND discord_user_id = ?",
                (entry_id, guild_id, user_id),
            )
        await db.commit()


async def _init_dual_links_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dual_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                owner_discord_id TEXT NOT NULL,
                owner_username TEXT,
                invite_token TEXT UNIQUE NOT NULL,
                dual_discord_id TEXT,
                dual_username TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                accepted_at TEXT
            )
        """)
        await db.commit()


async def create_dual_invite(guild_id: str, owner_id: str, owner_username: str) -> str:
    await _init_dual_links_table()
    import secrets as _sec
    token = _sec.token_urlsafe(24)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO dual_links (guild_id, owner_discord_id, owner_username, invite_token)
            VALUES (?, ?, ?, ?)
        """, (guild_id, owner_id, owner_username or None, token))
        await db.commit()
    return token


async def get_dual_link_by_token(token: str) -> dict | None:
    await _init_dual_links_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dual_links WHERE invite_token = ?", (token,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def accept_dual_invite(token: str, dual_id: str, dual_username: str):
    await _init_dual_links_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE dual_links
            SET dual_discord_id = ?, dual_username = ?, status = 'active',
                accepted_at = datetime('now')
            WHERE invite_token = ? AND status = 'pending'
        """, (dual_id, dual_username or None, token))
        await db.commit()


async def get_dual_links_for_owner(guild_id: str, owner_id: str) -> list[dict]:
    await _init_dual_links_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dual_links WHERE guild_id = ? AND owner_discord_id = ? ORDER BY created_at DESC",
            (guild_id, owner_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def revoke_dual_link(token: str, owner_id: str):
    await _init_dual_links_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dual_links SET status = 'revoked' WHERE invite_token = ? AND owner_discord_id = ?",
            (token, owner_id),
        )
        await db.commit()


async def get_all_shared_sitters(guild_id: str) -> list[dict]:
    await _init_sitter_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM account_sitters WHERE guild_id = ? AND is_shared = 1",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _init_farmlist_analyses_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS farmlist_analyses (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id         TEXT NOT NULL,
                discord_user_id  TEXT NOT NULL,
                discord_username TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                total_farms      INTEGER DEFAULT 0,
                own_villages     INTEGER DEFAULT 0,
                avg_res          REAL DEFAULT 0,
                gut              INTEGER DEFAULT 0,
                ok               INTEGER DEFAULT 0,
                leer             INTEGER DEFAULT 0,
                total_res_last   INTEGER DEFAULT 0,
                total_res_total  INTEGER DEFAULT 0,
                groups_json      TEXT DEFAULT '[]',
                fazit            TEXT DEFAULT 'mittel',
                farms_json       TEXT DEFAULT '[]',
                group_stats_json TEXT DEFAULT '[]'
            )
        """)
        await db.commit()
        for col, default in [("farms_json", "'[]'"), ("group_stats_json", "'[]'")]:
            try:
                await db.execute(f"ALTER TABLE farmlist_analyses ADD COLUMN {col} TEXT DEFAULT {default}")
                await db.commit()
            except Exception:
                pass  # column already exists


async def save_farmlist_analysis(
    guild_id: str,
    discord_user_id: str,
    discord_username: str,
    stats: dict,
    group_stats: list,
    farms: list = None,
) -> int:
    await _init_farmlist_analyses_table()
    import json as _json

    groups = [g["name"] for g in group_stats]
    own_villages = len(group_stats)

    # Fazit based on % of gut farms (excluding natars)
    total = stats.get("total", 0)
    gut   = stats.get("gut",  0)
    ok    = stats.get("ok",   0)
    pct   = (gut / total * 100) if total > 0 else 0
    if pct >= 60:
        fazit = "sehr gut"
    elif pct >= 40:
        fazit = "gut"
    elif pct >= 20:
        fazit = "mittel"
    elif pct >= 8:
        fazit = "schlecht"
    else:
        fazit = "sehr schlecht"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO farmlist_analyses
                (guild_id, discord_user_id, discord_username,
                 total_farms, own_villages, avg_res,
                 gut, ok, leer,
                 total_res_last, total_res_total,
                 groups_json, fazit,
                 farms_json, group_stats_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            guild_id, discord_user_id, discord_username,
            total, own_villages, stats.get("avg_res", 0),
            gut, ok, stats.get("leer", 0),
            stats.get("total_res_last", 0), stats.get("total_res_total", 0),
            _json.dumps(groups, ensure_ascii=False), fazit,
            _json.dumps(farms or [], ensure_ascii=False),
            _json.dumps(group_stats, ensure_ascii=False),
        ))
        await db.commit()
        return cur.lastrowid


async def get_farmlist_analyses(guild_id: str, discord_user_id: str, limit: int = 20) -> list[dict]:
    await _init_farmlist_analyses_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM farmlist_analyses
            WHERE guild_id = ? AND discord_user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (guild_id, discord_user_id, limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_farmlist_analysis(analysis_id: int, discord_user_id: str) -> dict | None:
    await _init_farmlist_analyses_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM farmlist_analyses WHERE id = ? AND discord_user_id = ?",
            (analysis_id, discord_user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_farmlist_analysis(analysis_id: int, discord_user_id: str):
    await _init_farmlist_analyses_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM farmlist_analyses WHERE id = ? AND discord_user_id = ?",
            (analysis_id, discord_user_id),
        )
        await db.commit()


async def get_scout_stats(guild_id: str) -> list[dict]:
    """Aggregate scout reports per target player for statistics view."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                sr.target_player,
                sr.target_coords,
                COUNT(*) AS scout_count,
                MAX(sr.created_at) AS last_scouted,
                GROUP_CONCAT(sr.troops_json, '|||') AS all_troops,
                GROUP_CONCAT(sr.resources_json, '|||') AS all_resources
            FROM scout_reports sr
            WHERE sr.guild_id = ? AND sr.target_player IS NOT NULL
            GROUP BY sr.target_player
            ORDER BY scout_count DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Hospital (Lazarett) tables
# ---------------------------------------------------------------------------

async def _init_hospital_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hospital_entries (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id         TEXT NOT NULL,
                discord_user_id  TEXT NOT NULL,
                discord_username TEXT,
                uploaded_at      TEXT DEFAULT (datetime('now')),
                village_name     TEXT NOT NULL,
                troop_name       TEXT NOT NULL,
                count            INTEGER NOT NULL,
                heal_finish      TEXT
            )
        """)
        await db.commit()


async def save_hospital_data(
    guild_id: str,
    discord_user_id: str,
    discord_username: str | None,
    entries: list[dict],
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM hospital_entries WHERE guild_id = ? AND discord_user_id = ?",
            (guild_id, discord_user_id),
        )
        for e in entries:
            await db.execute(
                """INSERT INTO hospital_entries
                   (guild_id, discord_user_id, discord_username, village_name, troop_name, count, heal_finish)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, discord_user_id, discord_username,
                 e["village_name"], e["troop_name"], e["count"], e.get("heal_finish")),
            )
        await db.commit()


async def get_hospital_data(guild_id: str, discord_user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM hospital_entries
               WHERE guild_id = ? AND discord_user_id = ?
               ORDER BY heal_finish ASC""",
            (guild_id, discord_user_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_hospital_data(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM hospital_entries
               WHERE guild_id = ?
               ORDER BY discord_username ASC, heal_finish ASC""",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_hospital_data(guild_id: str, discord_user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM hospital_entries WHERE guild_id = ? AND discord_user_id = ?",
            (guild_id, discord_user_id),
        )
        await db.commit()


# ── Allianz-Mitglieder ────────────────────────────────────────────────────────

async def _init_alliance_members_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alliance_members (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                player_name   TEXT NOT NULL,
                villages      INTEGER DEFAULT 0,
                population    INTEGER DEFAULT 0,
                points        INTEGER DEFAULT 0,
                rank          INTEGER DEFAULT 0,
                tribe         TEXT DEFAULT '',
                imported_at   TEXT NOT NULL,
                imported_by   TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_am_guild ON alliance_members(guild_id)")

        # Migrate: add notes column if missing
        try:
            await db.execute("ALTER TABLE alliance_members ADD COLUMN notes TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # column already exists

        # Scout images table — stores Discord CDN URL + optional cached bytes
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scout_images (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scout_report_id INTEGER,
                guild_id        TEXT NOT NULL,
                channel_id      TEXT NOT NULL,
                discord_url     TEXT NOT NULL,
                discord_message_id TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_si_report ON scout_images(scout_report_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_si_guild  ON scout_images(guild_id)")

        # Enemies table — one row per unique target player
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                player_name TEXT NOT NULL,
                coordinates TEXT,
                village     TEXT,
                notes       TEXT DEFAULT '',
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                scout_count INTEGER DEFAULT 0,
                UNIQUE(guild_id, player_name)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_enemies_guild ON enemies(guild_id)")

        # Migrations: add discord_message_id + image_url to scout_reports
        for col in ["discord_message_id TEXT", "image_urls TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_reports ADD COLUMN {col}")
            except Exception:
                pass
        # Migrations: add requested_by_id / requested_by_name to scout_channels
        for col in ["closed_at TEXT", "closed_by TEXT", "discord_message_id TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS travian_servers (
                url             TEXT PRIMARY KEY,
                players_count   INTEGER DEFAULT 0,
                speed           INTEGER DEFAULT 1,
                region          TEXT DEFAULT '',
                last_checked    TEXT,
                last_snapshot   TEXT,
                village_count   INTEGER DEFAULT 0,
                is_active       INTEGER DEFAULT 1,
                discovered_at   TEXT
            )
        """)
        await db.commit()


async def upsert_travian_server(url: str, players: int, speed: int, region: str):
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO travian_servers (url, players_count, speed, region, last_checked, is_active, discovered_at)
            VALUES (?, ?, ?, ?, ?, 1, COALESCE((SELECT discovered_at FROM travian_servers WHERE url=?), ?))
            ON CONFLICT(url) DO UPDATE SET
                players_count = excluded.players_count,
                speed = excluded.speed,
                region = excluded.region,
                last_checked = excluded.last_checked,
                is_active = 1
        """, (url, players, speed, region, now, url, now))
        await db.commit()


async def mark_travian_server_snapshot(url: str, village_count: int):
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE travian_servers SET last_snapshot=?, village_count=? WHERE url=?
        """, (now, village_count, url))
        await db.commit()


async def get_travian_servers() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ts.*,
                   COUNT(DISTINCT gc.guild_id) AS guilds_using
            FROM travian_servers ts
            LEFT JOIN guild_configs gc ON gc.tw_world = ts.url
            GROUP BY ts.url
            ORDER BY ts.players_count DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_travian_server(url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM travian_servers WHERE url=?", (url,))
        await db.commit()


async def save_alliance_members(guild_id: str, members: list[dict], imported_by: str):
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM alliance_members WHERE guild_id = ?", (guild_id,))
        await db.executemany("""
            INSERT INTO alliance_members
                (guild_id, player_name, villages, population, points, rank, tribe, imported_at, imported_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (guild_id, m["player_name"], m.get("villages", 0), m.get("population", 0),
             m.get("points", 0), m.get("rank", 0), m.get("tribe", ""), now, imported_by)
            for m in members
        ])
        await db.commit()


async def get_alliance_members(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM alliance_members WHERE guild_id = ? ORDER BY rank ASC",
            (guild_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_strike_info_for_players(guild_id: str, player_names: list[str]) -> dict[str, dict]:
    """Return most recent Strike report per player (attacker_player).
    Result: { player_name: { "created_at": "...", "days_ago": N } }
    """
    if not player_names:
        return {}
    placeholders = ",".join("?" * len(player_names))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"""
            SELECT attacker_player, MAX(created_at) as last_strike
            FROM scout_reports
            WHERE guild_id = ?
              AND attacker_player IN ({placeholders})
              AND (
                stats_json LIKE '%"text_strike": true%'
                OR stats_json LIKE '%"text_strike":true%'
              )
            GROUP BY attacker_player
        """, [guild_id, *player_names]) as cur:
            rows = await cur.fetchall()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    result = {}
    for row in rows:
        pname, ts = row[0], row[1]
        if not pname or not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (now - dt).days
        except Exception:
            days = 0
        result[pname] = {"created_at": ts, "days_ago": days}
    return result


async def get_alliance_members_meta(guild_id: str) -> dict | None:
    """Return import metadata (count, when, by whom)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT COUNT(*) as cnt, MAX(imported_at) as last_import, imported_by
               FROM alliance_members WHERE guild_id = ?""",
            (guild_id,)
        )
        row = await cur.fetchone()
        if not row or not row["cnt"]:
            return None
        return dict(row)


async def get_tw_alliance_name(guild_id: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tw_alliance_name FROM guild_configs WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        return (row["tw_alliance_name"] or "") if row else ""


async def set_tw_alliance_name(guild_id: str, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET tw_alliance_name = ? WHERE guild_id = ?",
            (name.strip() or None, guild_id)
        )
        await db.commit()


async def sync_alliance_members_from_snapshot(guild_id: str) -> int:
    """Rebuild alliance_members from the latest map snapshot for this guild.
    Requires tw_alliance_name to be set. Returns number of members synced."""
    alliance_name = await get_tw_alliance_name(guild_id)
    if not alliance_name:
        return 0

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Latest snapshot time
        cur = await db.execute(
            "SELECT MAX(fetched_at) as latest FROM map_snapshots WHERE guild_id = ?",
            (guild_id,)
        )
        row = await cur.fetchone()
        if not row or not row["latest"]:
            return 0
        latest = row["latest"]

        # Aggregate per player in that alliance
        cur = await db.execute(
            """SELECT player_name, player_id,
                      COUNT(*) as villages,
                      SUM(population) as population
               FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
                 AND alliance_name = ?
                 AND player_name IS NOT NULL AND player_name != ''
               GROUP BY player_id
               ORDER BY population DESC""",
            (guild_id, latest, alliance_name)
        )
        rows = await cur.fetchall()

    if not rows:
        return 0

    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    members = []
    for i, r in enumerate(rows):
        members.append((
            guild_id,
            r["player_name"],
            r["villages"] or 0,
            r["population"] or 0,
            0,   # points — not in map.sql
            i + 1,  # rank by population
            "",   # tribe
            now,
            "auto-sync"
        ))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM alliance_members WHERE guild_id = ?", (guild_id,))
        await db.executemany(
            """INSERT INTO alliance_members
               (guild_id, player_name, villages, population, points, rank, tribe, imported_at, imported_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            members
        )
        await db.commit()

    return len(members)


async def get_alliance_names_from_snapshot(guild_id: str) -> list[dict]:
    """Return all distinct alliances from the latest snapshot, sorted by village count."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT MAX(fetched_at) as latest FROM map_snapshots WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if not row or not row["latest"]:
            return []
        latest = row["latest"]
        cur = await db.execute(
            """SELECT alliance_name,
                      COUNT(DISTINCT player_id) as member_count,
                      COUNT(*) as village_count,
                      SUM(population) as total_pop
               FROM map_snapshots
               WHERE guild_id = ? AND fetched_at = ?
                 AND alliance_name IS NOT NULL AND alliance_name != ''
               GROUP BY alliance_name
               ORDER BY member_count DESC
               LIMIT 200""",
            (guild_id, latest)
        )
        return [dict(r) for r in await cur.fetchall()]


async def set_alliance_member_note(guild_id: str, player_name: str, notes: str) -> bool:
    """Update the notes for a single alliance member. Returns True if row updated."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE alliance_members SET notes=? WHERE guild_id=? AND player_name=?",
            (notes[:500], guild_id, player_name)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_top_alliances_from_snapshot(guild_id: str, limit: int = 10) -> list[dict]:
    """Top alliances by total population from the latest snapshot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT alliance_name,
                   COUNT(DISTINCT player_id) as member_count,
                   SUM(population) as total_pop
            FROM map_snapshots
            WHERE guild_id = ?
              AND alliance_name != ''
              AND alliance_name IS NOT NULL
              AND fetched_at = (SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id = ?)
            GROUP BY alliance_name
            ORDER BY total_pop DESC
            LIMIT ?
        """, (guild_id, guild_id, limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def init_alliance_meta_table():
    """Create alliance_meta_groups and alliance_meta_members tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alliance_meta_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                meta_name   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, meta_name)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alliance_meta_members (
                group_id        INTEGER NOT NULL REFERENCES alliance_meta_groups(id) ON DELETE CASCADE,
                alliance_name   TEXT NOT NULL,
                PRIMARY KEY (group_id, alliance_name)
            )
        """)
        await db.commit()


async def get_meta_groups(guild_id: str) -> list[dict]:
    """Return all meta groups for a guild, each with their alliances list."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, meta_name, created_at FROM alliance_meta_groups WHERE guild_id = ? ORDER BY created_at",
            (guild_id,)
        ) as cur:
            groups = [dict(r) for r in await cur.fetchall()]
        for g in groups:
            async with db.execute(
                "SELECT alliance_name FROM alliance_meta_members WHERE group_id = ?",
                (g["id"],)
            ) as cur:
                g["alliances"] = [r[0] for r in await cur.fetchall()]
        return groups


async def create_meta_group(guild_id: str, meta_name: str) -> int | None:
    """Create a new named meta group. Returns group_id or None if limit/duplicate."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM alliance_meta_groups WHERE guild_id = ?", (guild_id,)
        ) as cur:
            count = (await cur.fetchone())[0]
        if count >= 10:
            return None
        try:
            await db.execute(
                "INSERT INTO alliance_meta_groups (guild_id, meta_name) VALUES (?, ?)",
                (guild_id, meta_name)
            )
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as cur:
                return (await cur.fetchone())[0]
        except Exception:
            return None


async def delete_meta_group(guild_id: str, group_id: int):
    """Delete a meta group (cascades to members)."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "DELETE FROM alliance_meta_groups WHERE id = ? AND guild_id = ?",
            (group_id, guild_id)
        )
        await db.commit()


async def add_alliance_to_meta(guild_id: str, group_id: int, alliance_name: str) -> bool:
    """Add alliance to a meta group. Returns False if 3-alliance limit reached."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify group belongs to guild
        async with db.execute(
            "SELECT id FROM alliance_meta_groups WHERE id = ? AND guild_id = ?",
            (group_id, guild_id)
        ) as cur:
            if not await cur.fetchone():
                return False
        async with db.execute(
            "SELECT COUNT(*) FROM alliance_meta_members WHERE group_id = ?", (group_id,)
        ) as cur:
            count = (await cur.fetchone())[0]
        if count >= 3:
            return False
        try:
            await db.execute(
                "INSERT OR IGNORE INTO alliance_meta_members (group_id, alliance_name) VALUES (?, ?)",
                (group_id, alliance_name)
            )
            await db.commit()
        except Exception:
            pass
        return True


async def remove_alliance_from_meta(guild_id: str, group_id: int, alliance_name: str):
    """Remove an alliance from a meta group."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM alliance_meta_members
            WHERE group_id = (
                SELECT id FROM alliance_meta_groups WHERE id = ? AND guild_id = ?
            ) AND alliance_name = ?
        """, (group_id, guild_id, alliance_name))
        await db.commit()


async def get_meta_group_stats(guild_id: str, group_id: int) -> list[dict]:
    """Return stats per alliance in a meta group from the latest snapshot."""
    await init_alliance_meta_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT alliance_name FROM alliance_meta_members WHERE group_id = "
            "(SELECT id FROM alliance_meta_groups WHERE id = ? AND guild_id = ?)",
            (group_id, guild_id)
        ) as cur:
            alliances = [r[0] for r in await cur.fetchall()]
    if not alliances:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(alliances))
        async with db.execute(f"""
            SELECT alliance_name,
                   COUNT(DISTINCT player_id) AS member_count,
                   COUNT(*)                    AS village_count,
                   COALESCE(SUM(population),0) AS total_pop,
                   COALESCE(AVG(population),0) AS avg_pop
            FROM map_snapshots
            WHERE guild_id = ?
              AND alliance_name IN ({placeholders})
              AND fetched_at = (SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id = ?)
            GROUP BY alliance_name
        """, [guild_id] + alliances + [guild_id]) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Backwards-compat stubs (kept so any remaining references don't crash) ──────

async def get_meta_alliances(guild_id: str) -> list[str]:
    return []


async def add_meta_alliance(guild_id: str, alliance_name: str) -> bool:
    return False


async def remove_meta_alliance(guild_id: str, alliance_name: str):
    pass


async def get_meta_stats(guild_id: str) -> list[dict]:
    return []


async def set_request_hub(guild_id: str, channel_id: str, channel_name: str, message_id: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO request_hub (guild_id, channel_id, channel_name, message_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                channel_name=excluded.channel_name,
                message_id=COALESCE(excluded.message_id, message_id)
        """, (guild_id, channel_id, channel_name, message_id))
        await db.commit()


async def get_request_hub(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM request_hub WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def clear_request_hub(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM request_hub WHERE guild_id=?", (guild_id,))
        await db.commit()


async def clear_stale_channel_refs(guild_id: str, stale_ids: set):
    """Remove DB references to Discord channels that no longer exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        # report_channels table
        await db.execute(
            f"DELETE FROM report_channels WHERE guild_id=? AND channel_id IN ({','.join('?'*len(stale_ids))})",
            (guild_id, *stale_ids),
        )
        # request_hub table
        await db.execute(
            f"DELETE FROM request_hub WHERE guild_id=? AND channel_id IN ({','.join('?'*len(stale_ids))})",
            (guild_id, *stale_ids),
        )
        # guild_configs: null out matching channel columns
        for col in ("scout_channel_id", "res_request_channel_id", "attack_channel_id",
                    "archive_channel_id", "res_push_channel_id", "poll_channel_id", "hero_scout_channel_id"):
            for stale_id in stale_ids:
                await db.execute(
                    f"UPDATE guild_configs SET {col}=NULL WHERE guild_id=? AND {col}=?",
                    (guild_id, stale_id),
                )
        await db.commit()


async def get_defend_channels(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM defend_channels WHERE guild_id=? ORDER BY created_at DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def close_defend_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE defend_channels SET status='closed' WHERE channel_id=?", (channel_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------

async def init_blueprint_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS blueprint_templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                tribe       TEXT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS blueprint_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL REFERENCES blueprint_templates(id) ON DELETE CASCADE,
                order_num   INTEGER NOT NULL DEFAULT 0,
                step_type   TEXT NOT NULL DEFAULT 'building',
                title       TEXT NOT NULL,
                description TEXT DEFAULT '',
                target      TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS player_blueprints (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                player_name   TEXT NOT NULL,
                village_name  TEXT NOT NULL,
                village_coords TEXT DEFAULT '',
                template_id   INTEGER REFERENCES blueprint_templates(id) ON DELETE SET NULL,
                assigned_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS blueprint_progress (
                player_blueprint_id INTEGER NOT NULL,
                step_id             INTEGER NOT NULL REFERENCES blueprint_steps(id) ON DELETE CASCADE,
                completed           INTEGER DEFAULT 0,
                completed_at        TEXT,
                PRIMARY KEY (player_blueprint_id, step_id)
            )
        """)
        await db.commit()


async def get_blueprint_templates(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT bt.*, COUNT(bs.id) as step_count
            FROM blueprint_templates bt
            LEFT JOIN blueprint_steps bs ON bs.template_id = bt.id
            WHERE bt.guild_id = ?
            GROUP BY bt.id
            ORDER BY bt.tribe, bt.name
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_blueprint_template(template_id: int, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM blueprint_templates WHERE id=? AND guild_id=?",
            (template_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            tmpl = dict(row)
        async with db.execute(
            "SELECT * FROM blueprint_steps WHERE template_id=? ORDER BY order_num",
            (template_id,)
        ) as cur:
            tmpl["steps"] = [dict(r) for r in await cur.fetchall()]
        return tmpl


async def create_blueprint_template(guild_id: str, tribe: str, name: str, description: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO blueprint_templates (guild_id, tribe, name, description) VALUES (?,?,?,?)",
            (guild_id, tribe, name, description)
        )
        await db.commit()
        return cur.lastrowid


async def delete_blueprint_template(guild_id: str, template_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM blueprint_templates WHERE id=? AND guild_id=?",
            (template_id, guild_id)
        )
        await db.commit()


async def add_blueprint_step(
    template_id: int, guild_id: str, step_type: str,
    title: str, description: str, target: str, order_num: int
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify template belongs to guild
        async with db.execute(
            "SELECT id FROM blueprint_templates WHERE id=? AND guild_id=?",
            (template_id, guild_id)
        ) as cur:
            if not await cur.fetchone():
                return 0
        cur = await db.execute(
            """INSERT INTO blueprint_steps (template_id, order_num, step_type, title, description, target)
               VALUES (?,?,?,?,?,?)""",
            (template_id, order_num, step_type, title, description, target)
        )
        await db.commit()
        return cur.lastrowid


async def delete_blueprint_step(guild_id: str, step_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify template belongs to guild
        async with db.execute(
            """SELECT bs.id FROM blueprint_steps bs
               JOIN blueprint_templates bt ON bt.id = bs.template_id
               WHERE bs.id=? AND bt.guild_id=?""",
            (step_id, guild_id)
        ) as cur:
            if not await cur.fetchone():
                return
        await db.execute("DELETE FROM blueprint_steps WHERE id=?", (step_id,))
        await db.commit()


async def reorder_blueprint_steps(template_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM blueprint_steps WHERE template_id=? ORDER BY order_num, id",
            (template_id,)
        ) as cur:
            rows = await cur.fetchall()
        for i, row in enumerate(rows, 1):
            await db.execute(
                "UPDATE blueprint_steps SET order_num=? WHERE id=?", (i, row[0])
            )
        await db.commit()


async def get_player_blueprints(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT pb.id, pb.player_name, pb.village_name, pb.village_coords,
                   pb.template_id, pb.assigned_at,
                   bt.name as template_name, bt.tribe as tribe,
                   COUNT(bs.id) as total_steps,
                   SUM(CASE WHEN bp.completed=1 THEN 1 ELSE 0 END) as done_steps
            FROM player_blueprints pb
            LEFT JOIN blueprint_templates bt ON bt.id = pb.template_id
            LEFT JOIN blueprint_steps bs ON bs.template_id = pb.template_id
            LEFT JOIN blueprint_progress bp ON bp.player_blueprint_id = pb.id AND bp.step_id = bs.id
            WHERE pb.guild_id = ?
            GROUP BY pb.id
            ORDER BY pb.player_name, pb.village_name
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_player_blueprint(blueprint_id: int, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT pb.*, bt.name as template_name, bt.tribe as tribe,
                      bt.description as template_description
               FROM player_blueprints pb
               LEFT JOIN blueprint_templates bt ON bt.id = pb.template_id
               WHERE pb.id=? AND pb.guild_id=?""",
            (blueprint_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            bp = dict(row)
        # Get steps with completion status
        async with db.execute(
            """SELECT bs.*, COALESCE(bpr.completed, 0) as completed, bpr.completed_at
               FROM blueprint_steps bs
               LEFT JOIN blueprint_progress bpr
                 ON bpr.step_id = bs.id AND bpr.player_blueprint_id = ?
               WHERE bs.template_id = ?
               ORDER BY bs.order_num""",
            (blueprint_id, bp["template_id"])
        ) as cur:
            bp["steps"] = [dict(r) for r in await cur.fetchall()]
        return bp


async def create_player_blueprint(
    guild_id: str, player_name: str, village_name: str,
    village_coords: str, template_id: int
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO player_blueprints
               (guild_id, player_name, village_name, village_coords, template_id)
               VALUES (?,?,?,?,?)""",
            (guild_id, player_name, village_name, village_coords, template_id)
        )
        await db.commit()
        return cur.lastrowid


async def delete_player_blueprint(guild_id: str, blueprint_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM player_blueprints WHERE id=? AND guild_id=?",
            (blueprint_id, guild_id)
        )
        await db.commit()


async def toggle_blueprint_step(blueprint_id: int, step_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT completed FROM blueprint_progress WHERE player_blueprint_id=? AND step_id=?",
            (blueprint_id, step_id)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            new_state = 1
            await db.execute(
                """INSERT INTO blueprint_progress (player_blueprint_id, step_id, completed, completed_at)
                   VALUES (?, ?, 1, datetime('now'))""",
                (blueprint_id, step_id)
            )
        else:
            new_state = 0 if row[0] else 1
            if new_state:
                await db.execute(
                    """UPDATE blueprint_progress SET completed=1, completed_at=datetime('now')
                       WHERE player_blueprint_id=? AND step_id=?""",
                    (blueprint_id, step_id)
                )
            else:
                await db.execute(
                    """UPDATE blueprint_progress SET completed=0, completed_at=NULL
                       WHERE player_blueprint_id=? AND step_id=?""",
                    (blueprint_id, step_id)
                )
        await db.commit()
        return new_state


# ---------------------------------------------------------------------------
# Village Layouts
# ---------------------------------------------------------------------------

async def init_village_layout_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS village_layouts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        TEXT NOT NULL,
                name            TEXT NOT NULL,
                tribe           TEXT NOT NULL DEFAULT '',
                created_by      TEXT DEFAULT 'admin',
                is_template     INTEGER DEFAULT 1,
                description     TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS village_slots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                layout_id       INTEGER NOT NULL REFERENCES village_layouts(id) ON DELETE CASCADE,
                slot_num        INTEGER NOT NULL,
                slot_zone       TEXT NOT NULL,
                building_type   TEXT DEFAULT '',
                target_level    INTEGER DEFAULT 0,
                notes           TEXT DEFAULT '',
                UNIQUE(layout_id, slot_num)
            )
        """)
        await db.commit()


async def get_village_layouts(guild_id: str, is_template=None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if is_template is None:
            async with db.execute(
                """SELECT vl.*, COUNT(vs.id) as slot_count
                   FROM village_layouts vl
                   LEFT JOIN village_slots vs ON vs.layout_id = vl.id AND vs.building_type != ''
                   WHERE vl.guild_id = ?
                   GROUP BY vl.id ORDER BY vl.created_at DESC""",
                (guild_id,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                """SELECT vl.*, COUNT(vs.id) as slot_count
                   FROM village_layouts vl
                   LEFT JOIN village_slots vs ON vs.layout_id = vl.id AND vs.building_type != ''
                   WHERE vl.guild_id = ? AND vl.is_template = ?
                   GROUP BY vl.id ORDER BY vl.created_at DESC""",
                (guild_id, int(is_template))
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]


async def get_village_layout(layout_id: int, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM village_layouts WHERE id = ? AND guild_id = ?",
            (layout_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        layout = dict(row)
        async with db.execute(
            "SELECT * FROM village_slots WHERE layout_id = ? ORDER BY slot_num",
            (layout_id,)
        ) as cur:
            layout["slots"] = [dict(r) for r in await cur.fetchall()]
        return layout


async def create_village_layout(guild_id: str, name: str, tribe: str, created_by: str, is_template: int, description: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO village_layouts (guild_id, name, tribe, created_by, is_template, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, name, tribe, created_by, is_template, description)
        )
        await db.commit()
        return cur.lastrowid


async def delete_village_layout(guild_id: str, layout_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM village_layouts WHERE id = ? AND guild_id = ?",
            (layout_id, guild_id)
        )
        await db.commit()


async def set_village_slot(layout_id: int, guild_id: str, slot_num: int, zone: str, building_type: str, target_level: int, notes: str):
    # Verify layout belongs to guild
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM village_layouts WHERE id=? AND guild_id=?", (layout_id, guild_id)) as cur:
            if not await cur.fetchone():
                return
        await db.execute(
            """INSERT INTO village_slots (layout_id, slot_num, slot_zone, building_type, target_level, notes)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(layout_id, slot_num) DO UPDATE SET
                 building_type=excluded.building_type,
                 target_level=excluded.target_level,
                 notes=excluded.notes""",
            (layout_id, slot_num, zone, building_type, target_level, notes)
        )
        await db.commit()


async def clear_village_slot(layout_id: int, guild_id: str, slot_num: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM village_layouts WHERE id=? AND guild_id=?", (layout_id, guild_id)) as cur:
            if not await cur.fetchone():
                return
        await db.execute(
            "DELETE FROM village_slots WHERE layout_id=? AND slot_num=?",
            (layout_id, slot_num)
        )
        await db.commit()


# ─────────────────────────────────────────────
#  TRIAL LINKS
# ─────────────────────────────────────────────

async def create_trial_link(code: str, created_by: str) -> str:
    """Insert a new one-time trial link. Returns the code."""
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trial_links (code, created_by, created_at) VALUES (?, ?, ?)",
            (code, created_by, _dt.utcnow().isoformat()),
        )
        await db.commit()
    return code


async def get_trial_link(code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trial_links WHERE code=?", (code,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def activate_trial_link(code: str, guild_id: str, days: int = 14) -> bool:
    """Mark the link used and set trial_expires_at (+days) on the guild. Returns False if already used."""
    from datetime import datetime as _dt, timedelta as _td
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trial_links WHERE code=?", (code,)) as cur:
            link = await cur.fetchone()
        if not link or link["activated_guild_id"]:
            return False
        expires = (_dt.utcnow() + _td(days=days)).isoformat()
        now = _dt.utcnow().isoformat()
        await db.execute(
            "UPDATE trial_links SET activated_at=?, activated_guild_id=? WHERE code=?",
            (now, guild_id, code),
        )
        await db.execute(
            """UPDATE guild_configs SET
                 trial_expires_at=?,
                 subscription_status='trialing',
                 subscription_plan='trial'
               WHERE guild_id=?""",
            (expires, guild_id),
        )
        await db.commit()
    return True


async def get_all_trial_links() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trial_links ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def expire_overdue_trials():
    """Downgrade guilds whose trial has expired. Returns list of expired guild_ids."""
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT guild_id FROM guild_configs
               WHERE trial_expires_at IS NOT NULL
                 AND trial_expires_at <= ?
                 AND subscription_status = 'trialing'""",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
        expired = [r["guild_id"] for r in rows]
        if expired:
            placeholders = ",".join("?" * len(expired))
            await db.execute(
                f"""UPDATE guild_configs SET subscription_status='free', subscription_plan=NULL
                    WHERE guild_id IN ({placeholders})""",
                expired,
            )
            await db.commit()
    return expired


async def get_expiring_trials(days: int = 3) -> list[dict]:
    """Return guilds whose trial expires within `days` days."""
    from datetime import datetime as _dt, timedelta as _td
    now = _dt.utcnow().isoformat()
    soon = (_dt.utcnow() + _td(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT guild_id, trial_expires_at FROM guild_configs
               WHERE trial_expires_at IS NOT NULL
                 AND trial_expires_at > ?
                 AND trial_expires_at <= ?
                 AND subscription_status = 'trialing'""",
            (now, soon),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─────────────────────────────────────────────
#  REFERRAL SYSTEM
# ─────────────────────────────────────────────

async def get_or_create_referral_code(discord_user_id: str) -> str:
    """Return existing ref code or create a new one (8-char hex)."""
    import secrets
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT code FROM referral_codes WHERE discord_user_id=?", (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["code"]
        from datetime import datetime as _dt
        code = secrets.token_hex(5)  # 10 hex chars
        # ensure uniqueness
        while True:
            async with db.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)) as cur:
                if not await cur.fetchone():
                    break
            code = secrets.token_hex(5)
        await db.execute(
            "INSERT INTO referral_codes (discord_user_id, code, created_at) VALUES (?,?,?)",
            (discord_user_id, code, _dt.utcnow().isoformat()),
        )
        await db.commit()
        return code


async def get_referral_code_owner(code: str) -> str | None:
    """Return discord_user_id for a ref code, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT discord_user_id FROM referral_codes WHERE code=?", (code,)
        ) as cur:
            row = await cur.fetchone()
            return row["discord_user_id"] if row else None


async def award_referral_point(referrer_discord_id: str, referred_discord_id: str) -> bool:
    """Award 1 TravOps-Point to referrer. Returns False if already awarded for this referred user."""
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if already awarded
        async with db.execute(
            "SELECT 1 FROM referral_events WHERE referred_discord_id=?", (referred_discord_id,)
        ) as cur:
            if await cur.fetchone():
                return False
        await db.execute(
            "INSERT INTO referral_events (referrer_discord_id, referred_discord_id, awarded_at) VALUES (?,?,?)",
            (referrer_discord_id, referred_discord_id, _dt.utcnow().isoformat()),
        )
        # Ensure user_subscriptions row exists
        await db.execute(
            """INSERT INTO user_subscriptions (discord_user_id, travops_points)
               VALUES (?, 1)
               ON CONFLICT(discord_user_id) DO UPDATE SET
                 travops_points = COALESCE(travops_points, 0) + 1""",
            (referrer_discord_id,),
        )
        await db.commit()
    return True


async def get_referral_stats(discord_user_id: str) -> dict:
    """Return {code, points, referred_count} for a user."""
    code = await get_or_create_referral_code(discord_user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COALESCE(travops_points,0) AS pts FROM user_subscriptions WHERE discord_user_id=?",
            (discord_user_id,),
        ) as cur:
            row = await cur.fetchone()
            points = row["pts"] if row else 0
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM referral_events WHERE referrer_discord_id=?",
            (discord_user_id,),
        ) as cur:
            cnt_row = await cur.fetchone()
            referred_count = cnt_row["cnt"] if cnt_row else 0
    return {"code": code, "points": points, "referred_count": referred_count}


async def redeem_travops_points(discord_user_id: str) -> bool:
    """Deduct 10 points and extend user Pro by 1 month. Returns False if not enough points."""
    from datetime import datetime as _dt, timedelta as _td
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COALESCE(travops_points,0) AS pts, subscription_status, expires_at FROM user_subscriptions WHERE discord_user_id=?",
            (discord_user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row["pts"] < 10:
            return False
        # Calculate new expiry: extend from now or from current expiry
        base = _dt.utcnow()
        if row["expires_at"]:
            try:
                existing = _dt.fromisoformat(row["expires_at"])
                if existing > base:
                    base = existing
            except Exception:
                pass
        new_expiry = (base + _td(days=30)).isoformat()
        await db.execute(
            """UPDATE user_subscriptions SET
                 travops_points = travops_points - 10,
                 subscription_status = 'active',
                 plan = COALESCE(NULLIF(plan,''), 'player_pro'),
                 expires_at = ?
               WHERE discord_user_id = ?""",
            (new_expiry, discord_user_id),
        )
        await db.commit()
    return True


async def get_scout_report_by_id(report_id: int, guild_id: str) -> dict | None:
    """Return a single scout report row, verified to belong to guild_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_reports WHERE id=? AND guild_id=?",
            (report_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────────────────────────
#  WORKSPACE ARCHIVE
# ─────────────────────────────────────────────

async def archive_workspace(guild_id: str) -> bool:
    """Mark a workspace as archived (data preserved, bot kicked)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET workspace_status='archived', bot_status='kicked' WHERE guild_id=?",
            (guild_id,),
        )
        await db.commit()
    return True


async def restore_workspace(guild_id: str) -> bool:
    """Restore an archived workspace to active."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET workspace_status='active' WHERE guild_id=?",
            (guild_id,),
        )
        await db.commit()
    return True


async def get_archived_workspaces(owner_discord_id: str) -> list[dict]:
    """Return archived workspaces for a user (as owner or discord member)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM guild_configs
               WHERE workspace_status = 'archived'
                 AND (owner_discord_id = ? OR workspace_owner_id = ?)
               ORDER BY guild_name""",
            (owner_discord_id, owner_discord_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def upsert_guild_name(guild_id: str, guild_name: str, owner_discord_id: str | None = None):
    """Register a guild without overwriting existing config.
    Only sets owner_discord_id when the column is currently NULL."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO guild_configs (guild_id, guild_name, owner_discord_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name       = excluded.guild_name,
                owner_discord_id = COALESCE(guild_configs.owner_discord_id, excluded.owner_discord_id)
        """, (guild_id, guild_name, owner_discord_id))
        await db.commit()


# ── Private Channels ─────────────────────────────────────────────────────────

async def get_private_channel(guild_id: str, owner_id: str) -> dict | None:
    """Return the private channel record for a user, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM private_channels WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_private_channel(guild_id: str, owner_id: str, channel_id: str):
    """Insert or replace a private channel record."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO private_channels (channel_id, guild_id, owner_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, owner_id) DO UPDATE SET channel_id = excluded.channel_id
        """, (channel_id, guild_id, owner_id))
        await db.commit()


async def delete_private_channel_by_id(channel_id: str):
    """Remove a private channel record when the channel is deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM private_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def get_private_channel_by_channel_id(channel_id: str) -> dict | None:
    """Return the private channel record by channel_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM private_channels WHERE channel_id = ?",
            (channel_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
