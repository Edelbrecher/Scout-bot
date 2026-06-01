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
            "defend_role_ids TEXT",
            "archive_role_ids TEXT",
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

        # Bot last seen / activity tracking & inactive flag
        for col in [
            "bot_last_seen TEXT",
            "is_active INTEGER DEFAULT 1",
        ]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
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

        # Dual-player links
        await db.execute("""
            CREATE TABLE IF NOT EXISTS account_duals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                anchor_discord_id TEXT NOT NULL,
                dual_discord_id   TEXT NOT NULL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(anchor_discord_id, dual_discord_id)
            )
        """)
        await db.commit()

        # Dual invite codes (one per user, reusable by up to 10 duals)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dual_codes (
                discord_user_id TEXT PRIMARY KEY,
                code            TEXT UNIQUE NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        await db.commit()

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
    await _init_op_tables()
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
    await _init_scout_incidents_table()

    # New column migrations
    async with aiosqlite.connect(DB_PATH) as db:
        for col in ["alliance_manager_role_ids TEXT", "tw_alliance_name TEXT",
                    "server_utc_offset INTEGER DEFAULT 60"]:
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


async def update_guild_config_fields(guild_id: str, **fields):
    """Update arbitrary guild_config columns by keyword argument."""
    if not fields:
        return
    allowed = {"server_utc_offset", "tw_alliance_name", "alliance_manager_role_ids",
               "bot_language", "poll_channel_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [guild_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE guild_configs SET {set_clause} WHERE guild_id = ?", vals)
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


async def get_res_contributions_per_request(guild_id: str) -> dict[str, list]:
    """Return {request_id: [contributions]} for all requests in a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT rc.request_id, rc.user_name, rc.amount, rc.created_at
            FROM res_contributions rc
            JOIN res_requests rr ON rr.id = rc.request_id
            WHERE rr.guild_id = ?
            ORDER BY rc.request_id, rc.created_at ASC
        """, (guild_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    result: dict[str, list] = {}
    for r in rows:
        rid = str(r.pop("request_id"))
        result.setdefault(rid, []).append(r)
    return result


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


async def update_enemy_meta(
    guild_id: str, player_name: str,
    danger_level: str = "", tags: str = "", alliance_name: str | None = None
):
    """Update danger level, tags (comma-sep), optional alliance_name."""
    async with aiosqlite.connect(DB_PATH) as db:
        if alliance_name is not None:
            await db.execute(
                "UPDATE enemies SET danger_level=?, tags=?, alliance_name=? WHERE guild_id=? AND player_name=?",
                (danger_level or "", tags or "", alliance_name, guild_id, player_name),
            )
        else:
            await db.execute(
                "UPDATE enemies SET danger_level=?, tags=? WHERE guild_id=? AND player_name=?",
                (danger_level or "", tags or "", guild_id, player_name),
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


_ALLOWED_ROLE_FIELDS = {"allowed_role_ids", "res_manager_role_ids", "private_channel_role_ids", "defend_role_ids", "archive_role_ids"}

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


async def update_bot_last_seen(guild_id: str):
    """Update the timestamp when bot last had activity in this guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET bot_last_seen = datetime('now') WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def set_guild_active_flag(guild_id: str, active: bool):
    """Set is_active flag (1=active, 0=inactive) for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET is_active = ? WHERE guild_id = ?",
            (1 if active else 0, guild_id),
        )
        await db.commit()


async def archive_guild(guild_id: str):
    """Archive a guild (hide from dashboard, mark bot as kicked)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET workspace_status='archived', bot_status='kicked' WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def unarchive_guild(guild_id: str):
    """Restore an archived guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET workspace_status='active' WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def get_archived_guilds() -> list[dict]:
    """Return all guilds with workspace_status='archived'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT guild_id, guild_name, owner_discord_id, subscription_status, subscription_plan,
                   bot_status, bot_last_seen, is_active, workspace_status, bot_kicked_at
            FROM guild_configs WHERE workspace_status = 'archived'
            ORDER BY guild_name
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_customer(discord_user_id: str):
    """Remove a customer (user subscription + unlink owned guilds)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Remove user subscription
        await db.execute("DELETE FROM user_subscriptions WHERE discord_user_id = ?", (discord_user_id,))
        # Unlink guilds owned by this user (don't delete the guilds themselves)
        await db.execute(
            "UPDATE guild_configs SET owner_discord_id = NULL WHERE owner_discord_id = ?",
            (discord_user_id,),
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
                tribe         INTEGER,
                is_capital    INTEGER DEFAULT 0,
                village_type  INTEGER DEFAULT 0
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
        # Migrations for new columns
        try:
            await db.execute("ALTER TABLE map_snapshots ADD COLUMN is_capital INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE map_snapshots ADD COLUMN village_type INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
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
                 alliance_id, alliance_name, population, tribe, is_capital, village_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                guild_id, fetched_at,
                str(v.get("village_id", "")),
                int(v.get("x", 0)), int(v.get("y", 0)),
                v.get("village_name"), v.get("player_id"), v.get("player_name"),
                v.get("alliance_id"), v.get("alliance_name"),
                int(v.get("population", 0)), v.get("tribe"),
                int(v.get("is_capital", 0)), int(v.get("village_type", 0)),
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


async def get_village_pop_history(guild_id: str, x: int, y: int, days: int = 7) -> list[dict]:
    """Return daily population snapshots for a village, newest first, up to `days` distinct dates."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT DATE(fetched_at) as snap_date, MAX(population) as population
            FROM map_snapshots
            WHERE guild_id=? AND x=? AND y=?
              AND fetched_at >= datetime('now', '-' || ? || ' days')
            GROUP BY DATE(fetched_at)
            ORDER BY snap_date DESC
            LIMIT ?
        """, (guild_id, x, y, days, days)) as cur:
            rows = await cur.fetchall()
        return [{"date": r[0], "population": r[1]} for r in rows]


async def get_player_pop_growth(guild_id: str, player_name: str, days: int = 7) -> dict:
    """Return player's total pop at latest snapshot vs N days ago."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT SUM(population) FROM map_snapshots
            WHERE guild_id=? AND player_name=?
              AND fetched_at=(SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id=?)
        """, (guild_id, player_name, guild_id)) as cur:
            latest = (await cur.fetchone() or [None])[0] or 0
        async with db.execute("""
            SELECT SUM(population) FROM map_snapshots
            WHERE guild_id=? AND player_name=?
              AND fetched_at=(
                SELECT fetched_at FROM map_snapshots WHERE guild_id=?
                  AND fetched_at <= datetime('now', '-' || ? || ' days')
                ORDER BY fetched_at DESC LIMIT 1
              )
        """, (guild_id, player_name, guild_id, days)) as cur:
            old = (await cur.fetchone() or [None])[0] or 0
        return {"latest": latest, "old": old, "delta": latest - old}


async def get_bulk_village_pop_history(guild_id: str, coords: list, days: int = 7) -> dict:
    """Return {(x,y): [{"date":..., "pop":...}, ...]} for multiple villages."""
    if not coords:
        return {}
    result: dict = {}
    async with aiosqlite.connect(DB_PATH) as db:
        or_clauses = " OR ".join("(x=? AND y=?)" for _ in coords)
        flat = [val for (x, y) in coords for val in (x, y)]
        async with db.execute(f"""
            SELECT x, y, DATE(fetched_at) as snap_date, MAX(population) as pop
            FROM map_snapshots
            WHERE guild_id=? AND fetched_at >= datetime('now', '-' || ? || ' days')
              AND ({or_clauses})
            GROUP BY x, y, DATE(fetched_at)
            ORDER BY x, y, snap_date DESC
        """, (guild_id, days, *flat)) as cur:
            for r in await cur.fetchall():
                key = (r[0], r[1])
                result.setdefault(key, []).append({"date": r[2], "pop": r[3]})
    return result


async def get_bulk_player_pop_growth(guild_id: str, player_names: list, days: int = 7) -> dict:
    """Return {player_name: {"delta": N, "latest": M}} for multiple players."""
    if not player_names:
        return {}
    async with aiosqlite.connect(DB_PATH) as db:
        # Latest snapshot time
        async with db.execute(
            "SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id=?", (guild_id,)
        ) as cur:
            latest_ts = (await cur.fetchone() or [None])[0]
        if not latest_ts:
            return {}

        # Old snapshot time (closest to N days ago)
        async with db.execute("""
            SELECT fetched_at FROM map_snapshots WHERE guild_id=?
              AND fetched_at <= datetime('now', '-' || ? || ' days')
            ORDER BY fetched_at DESC LIMIT 1
        """, (guild_id, days)) as cur:
            old_ts_row = await cur.fetchone()
        old_ts = old_ts_row[0] if old_ts_row else None

        ph = ",".join("?" for _ in player_names)

        # Latest totals
        async with db.execute(f"""
            SELECT player_name, SUM(population) FROM map_snapshots
            WHERE guild_id=? AND fetched_at=? AND player_name IN ({ph})
            GROUP BY player_name
        """, (guild_id, latest_ts, *player_names)) as cur:
            latest_map = {r[0]: r[1] for r in await cur.fetchall()}

        old_map: dict = {}
        if old_ts:
            async with db.execute(f"""
                SELECT player_name, SUM(population) FROM map_snapshots
                WHERE guild_id=? AND fetched_at=? AND player_name IN ({ph})
                GROUP BY player_name
            """, (guild_id, old_ts, *player_names)) as cur:
                old_map = {r[0]: r[1] for r in await cur.fetchall()}

        result = {}
        for pname in player_names:
            lat = latest_map.get(pname) or 0
            old = old_map.get(pname) or 0
            result[pname] = {"latest": lat, "old": old, "delta": lat - old}
        return result


async def get_servers_overview() -> list[dict]:
    """Return all guilds with tw_world set, plus snapshot stats per guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # All guilds (not archived)
        async with db.execute("""
            SELECT guild_id, guild_name, tw_world,
                   subscription_status, subscription_plan,
                   bot_last_seen, is_active, workspace_status, bot_status
            FROM guild_configs
            WHERE COALESCE(workspace_status,'active') != 'archived'
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
            SELECT village_id, x, y, village_name, player_name, tribe,
                   MIN(fetched_at) as first_seen, MAX(fetched_at) as last_seen,
                   COUNT(DISTINCT fetched_at) as snapshot_count,
                   MIN(population) as min_pop_val, MAX(population) as max_pop_val
            FROM map_snapshots
            WHERE guild_id = ?
            GROUP BY village_id
            HAVING snapshot_count >= 2
               AND CAST(max_pop_val AS REAL) - CAST(min_pop_val AS REAL) <= 5
               AND julianday(last_seen) - julianday(first_seen) >= ?
               AND max_pop_val >= ? AND min_pop_val <= ?
            ORDER BY max_pop_val DESC
        """, (guild_id, min_days, min_pop, max_pop)) as cur:
            rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["population"] = d.get("max_pop_val", 0)  # use latest/max as display value
            try:
                from datetime import datetime as _dt
                fs = _dt.fromisoformat(d["first_seen"])
                ls = _dt.fromisoformat(d["last_seen"])
                d["days_tracked"] = (ls - fs).days
            except Exception:
                d["days_tracked"] = 0
            result.append(d)
        return result


async def get_village_id_by_xy(guild_id: str, x: int, y: int) -> str | None:
    """Return Travian village_id for a given coordinate from the latest snapshot."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT village_id FROM map_snapshots
            WHERE guild_id=? AND x=? AND y=?
            ORDER BY fetched_at DESC LIMIT 1
        """, (guild_id, x, y)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_own_village_ids(guild_id: str, discord_id: str, tw_player_name: str = "") -> list[dict]:
    """Return own villages with Travian village_ids looked up from map_snapshots."""
    await _init_own_villages_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT village_name, x, y FROM guild_own_villages WHERE guild_id=? AND discord_id=? AND x IS NOT NULL",
            (guild_id, discord_id)
        ) as cur:
            own = [dict(r) for r in await cur.fetchall()]
        if not own:
            return []
        # Lookup village_ids from latest snapshot
        latest_ts_row = await db.execute(
            "SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id=?", (guild_id,))
        row = await (await latest_ts_row).fetchone()
        latest_ts = row[0] if row else None
        if not latest_ts:
            return [{**v, "village_id": None} for v in own]
        result = []
        for v in own:
            async with db.execute(
                "SELECT village_id FROM map_snapshots WHERE guild_id=? AND x=? AND y=? AND fetched_at=? LIMIT 1",
                (guild_id, v["x"], v["y"], latest_ts)
            ) as cur2:
                vid_row = await cur2.fetchone()
            result.append({**v, "village_id": vid_row[0] if vid_row else None})
        return result


async def get_snapshot_pop_range(guild_id: str) -> dict:
    """Return min/max population seen in snapshots for this guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT MIN(population), MAX(population) FROM map_snapshots WHERE guild_id=? AND population > 0",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return {"min_pop": row[0] or 0, "max_pop": row[1] or 9999} if row else {"min_pop": 0, "max_pop": 9999}


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
                       COUNT(DISTINCT fetched_at)         AS snap_count,
                       MIN(fetched_at)                    AS first_seen,
                       MAX(fetched_at)                    AS last_seen
                FROM map_snapshots
                WHERE guild_id = ?
                  AND fetched_at IN ({snap_placeholders})
                GROUP BY guild_id, x, y
            )
            SELECT v.x, v.y, v.village_name, v.player_name,
                   v.alliance_name, v.population, v.tribe,
                   v.is_capital, v.village_type,
                   s.pop_range, s.snap_count, s.first_seen, s.last_seen
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
            pname = (v["player_name"] or "").strip().lower()
            aname = (v["alliance_name"] or "").strip().lower()

            # Inclusion filters: substring match (any of the comma-separated terms)
            if incl_players and not any(f in pname for f in incl_players):
                continue
            if incl_alliances and not any(f in aname for f in incl_alliances):
                continue
            # Exclusion filters: substring match
            if excl_players and any(f in pname for f in excl_players):
                continue
            if excl_alliances and any(f in aname for f in excl_alliances):
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

            from datetime import datetime as _dt
            try:
                fs = _dt.fromisoformat(v["first_seen"])
                ls = _dt.fromisoformat(v["last_seen"])
                days_tracked = max(0, (ls - fs).days)
            except Exception:
                days_tracked = 0
            filtered.append({**v, "distance": dist, "player_total_pop": pp, "days_tracked": days_tracked})

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
                discord_id TEXT NOT NULL DEFAULT '',
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
                UNIQUE(guild_id, discord_id, x, y)
            )
        """)
        # Migration: add discord_id column if not present
        try:
            await db.execute("ALTER TABLE guild_own_villages ADD COLUMN discord_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        # Migration: scout village flag
        try:
            await db.execute("ALTER TABLE guild_own_villages ADD COLUMN is_scout_village INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.commit()


async def set_scout_village(guild_id: str, discord_id: str, x: int, y: int) -> None:
    """Toggle a village as scout village — unsets all others first."""
    await _init_own_villages_table()
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if this village is already the scout village
        async with db.execute(
            "SELECT is_scout_village FROM guild_own_villages WHERE guild_id=? AND discord_id=? AND x=? AND y=?",
            (guild_id, discord_id, x, y)
        ) as cur:
            row = await cur.fetchone()
        already = row and row[0]
        # Clear all scout flags for this user
        await db.execute(
            "UPDATE guild_own_villages SET is_scout_village=0 WHERE guild_id=? AND discord_id=?",
            (guild_id, discord_id)
        )
        # Set this one (unless it was already set — toggle off)
        if not already:
            await db.execute(
                "UPDATE guild_own_villages SET is_scout_village=1 WHERE guild_id=? AND discord_id=? AND x=? AND y=?",
                (guild_id, discord_id, x, y)
            )
        await db.commit()


async def get_scout_village(guild_id: str, discord_id: str) -> dict | None:
    """Return the marked scout village for a user, or None."""
    await _init_own_villages_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_own_villages WHERE guild_id=? AND discord_id=? AND is_scout_village=1 LIMIT 1",
            (guild_id, discord_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_own_villages(guild_id: str, villages: list[dict], uploaded_by: str, discord_id: str = ""):
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
        # Replace only this user's villages
        if discord_id:
            await db.execute(
                "DELETE FROM guild_own_villages WHERE guild_id = ? AND discord_id = ?",
                (guild_id, discord_id)
            )
        else:
            await db.execute("DELETE FROM guild_own_villages WHERE guild_id = ?", (guild_id,))
        for v in villages:
            await db.execute("""
                INSERT INTO guild_own_villages
                    (guild_id, discord_id, village_name, x, y, population, troops_json,
                     village_type, def_score, off_score, priority, uploaded_by, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                guild_id,
                discord_id,
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
        # Record history snapshot — update today's entry for this user if it already exists
        hist_filter = "guild_id = ? AND discord_id = ? AND date(uploaded_at) = date('now')"
        hist_params = (guild_id, discord_id)
        async with db.execute(
            f"SELECT id FROM guild_own_villages_history WHERE {hist_filter}", hist_params
        ) as cur:
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
                    (guild_id, discord_id, uploaded_at, total_off, total_def, total_crop, village_count, uploaded_by)
                VALUES (?, ?, date('now'), ?, ?, ?, ?, ?)
            """, (guild_id, discord_id, total_off, total_def, total_crop, len(villages), uploaded_by))
        await db.commit()


async def get_own_villages(guild_id: str, discord_id: str = "") -> list[dict]:
    """Return own villages for a guild (filtered by discord_id if provided)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if discord_id:
            async with db.execute(
                "SELECT * FROM guild_own_villages WHERE guild_id = ? AND discord_id = ? ORDER BY priority DESC, village_name ASC",
                (guild_id, discord_id),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT * FROM guild_own_villages WHERE guild_id = ? ORDER BY priority DESC, village_name ASC",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_own_villages(guild_id: str, discord_id: str = ""):
    """Delete own villages for a guild (filtered by discord_id if provided)."""
    async with aiosqlite.connect(DB_PATH) as db:
        if discord_id:
            await db.execute(
                "DELETE FROM guild_own_villages WHERE guild_id = ? AND discord_id = ?",
                (guild_id, discord_id)
            )
        else:
            await db.execute("DELETE FROM guild_own_villages WHERE guild_id = ?", (guild_id,))
        await db.commit()
        await db.commit()


async def _init_own_villages_history_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_own_villages_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                discord_id TEXT NOT NULL DEFAULT '',
                uploaded_at TEXT DEFAULT (datetime('now')),
                total_off INTEGER DEFAULT 0,
                total_def INTEGER DEFAULT 0,
                total_crop INTEGER DEFAULT 0,
                village_count INTEGER DEFAULT 0,
                uploaded_by TEXT
            )
        """)
        # Migration: add discord_id column if not present
        try:
            await db.execute("ALTER TABLE guild_own_villages_history ADD COLUMN discord_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        await db.commit()


async def get_own_villages_history(guild_id: str, discord_id: str = "") -> list[dict]:
    """Return historical snapshots for own villages, oldest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if discord_id:
            async with db.execute(
                "SELECT * FROM guild_own_villages_history WHERE guild_id = ? AND discord_id = ? ORDER BY uploaded_at ASC",
                (guild_id, discord_id),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
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
        async with db.execute("""
            SELECT sr.*,
                   GROUP_CONCAT(si.local_path, '|||') AS local_paths,
                   GROUP_CONCAT(si.discord_url, '|||') AS discord_urls
            FROM scout_reports sr
            LEFT JOIN scout_images si ON si.scout_report_id = sr.id
            WHERE sr.channel_id = ?
            GROUP BY sr.id
            ORDER BY sr.created_at DESC
        """, (channel_id,)) as cur:
            rows = []
            for r in await cur.fetchall():
                d = dict(r)
                local_raw   = d.pop("local_paths", None) or ""
                discord_raw = d.pop("discord_urls", None) or ""
                locals_ = [p for p in local_raw.split("|||") if p]
                discords = [u for u in discord_raw.split("|||") if u]
                # Prefer local paths (served via /scout-images/), fallback to Discord CDN
                d["image_urls"] = [f"/scout-images/{p}" for p in locals_] or discords
                rows.append(d)
            return rows


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


# ── My Ally ──────────────────────────────────────────────────────────────────

async def _init_ally_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meta_alliances (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                alliance_name TEXT NOT NULL,
                color        TEXT DEFAULT '#94a3b8',
                sort_order   INTEGER DEFAULT 0,
                UNIQUE(guild_id, alliance_name)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ally_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL UNIQUE,
                owner_discord_id TEXT NOT NULL,
                owner_username TEXT,
                ally_name TEXT NOT NULL,
                invite_token TEXT UNIQUE NOT NULL,
                wing1_name TEXT DEFAULT '',
                wing2_name TEXT DEFAULT '',
                wing1_token TEXT UNIQUE,
                wing2_token TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ally_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ally_group_id INTEGER NOT NULL REFERENCES ally_groups(id) ON DELETE CASCADE,
                role_name TEXT NOT NULL,
                color TEXT DEFAULT '#94a3b8',
                UNIQUE(ally_group_id, role_name)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ally_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ally_group_id INTEGER NOT NULL REFERENCES ally_groups(id) ON DELETE CASCADE,
                discord_id TEXT NOT NULL,
                discord_username TEXT,
                travian_name TEXT,
                role_id INTEGER REFERENCES ally_roles(id) ON DELETE SET NULL,
                wing INTEGER DEFAULT 0,
                note TEXT,
                joined_at TEXT DEFAULT (datetime('now')),
                UNIQUE(ally_group_id, discord_id)
            )
        """)
        # Migrations for existing tables
        for col, definition in [
            ("wing1_name", "TEXT DEFAULT ''"),
            ("wing2_name", "TEXT DEFAULT ''"),
            ("wing1_token", "TEXT"),
            ("wing2_token", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ally_groups ADD COLUMN {col} {definition}")
            except Exception:
                pass
        for col, definition in [
            ("role_id", "INTEGER"),
            ("wing", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'approved'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ally_members ADD COLUMN {col} {definition}")
            except Exception:
                pass
        # ally_roles: add permissions column (comma-separated flags e.g. "ep_notify")
        try:
            await db.execute("ALTER TABLE ally_roles ADD COLUMN permissions TEXT DEFAULT ''")
        except Exception:
            pass
        # ally_groups: tq_min (Truppenquote Mindestanforderung in %)
        try:
            await db.execute("ALTER TABLE ally_groups ADD COLUMN tq_min INTEGER DEFAULT 0")
        except Exception:
            pass
        # lock_travian_name: 1 = only editors (lead/HC) may change the travian name
        try:
            await db.execute("ALTER TABLE ally_groups ADD COLUMN lock_travian_name INTEGER DEFAULT 0")
        except Exception:
            pass
        # alliance_bonuses: JSON blob storing current bonus levels
        try:
            await db.execute("ALTER TABLE ally_groups ADD COLUMN alliance_bonuses TEXT DEFAULT '{}'")
        except Exception:
            pass
        await db.commit()


async def get_ally_group_for_guild(guild_id: str) -> dict | None:
    """Return the one ally group for this guild (if any)."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ally_groups WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_ally_group(guild_id: str, owner_id: str, owner_username: str, ally_name: str) -> dict:
    await _init_ally_tables()
    import secrets as _sec
    token = _sec.token_urlsafe(24)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute("""
                INSERT INTO ally_groups (guild_id, owner_discord_id, owner_username, ally_name, invite_token)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, owner_id, owner_username, ally_name, token))
            group_id = cur.lastrowid
            # Auto-add owner as approved member so they receive EP notifications
            await db.execute("""
                INSERT OR IGNORE INTO ally_members
                    (ally_group_id, discord_id, discord_username, travian_name, status)
                VALUES (?, ?, ?, '', 'approved')
            """, (group_id, owner_id, owner_username))
            await db.commit()
            return {"id": group_id, "invite_token": token}
        except Exception:
            return {}


async def delete_ally_group(ally_group_id: int, owner_id: str):
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM ally_members WHERE ally_group_id=?", (ally_group_id,))
        await db.execute(
            "DELETE FROM ally_roles WHERE ally_group_id=?", (ally_group_id,))
        await db.execute(
            "DELETE FROM ally_groups WHERE id=? AND owner_discord_id=?", (ally_group_id, owner_id))
        await db.commit()


async def get_alliance_bonuses(guild_id: str) -> dict:
    """Return current alliance bonus levels as dict, e.g. {'recruitment': 2, 'philosophy': 1}."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT alliance_bonuses FROM ally_groups WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                import json as _json
                try:
                    return _json.loads(row[0])
                except Exception:
                    pass
    return {}


async def save_alliance_bonuses(guild_id: str, bonuses: dict) -> None:
    """Persist alliance bonus levels for a guild."""
    import json as _json
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ally_groups SET alliance_bonuses=? WHERE guild_id=?",
            (_json.dumps(bonuses), guild_id)
        )
        await db.commit()


async def update_ally_group(ally_group_id: int, owner_id: str, **kwargs):
    """Update ally_group fields."""
    allowed = {"ally_name", "wing1_name", "wing2_name", "wing1_token", "wing2_token", "tq_min", "lock_travian_name"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [ally_group_id, owner_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE ally_groups SET {set_clause} WHERE id=? AND owner_discord_id=?", vals)
        await db.commit()


async def get_ally_roles(ally_group_id: int) -> list[dict]:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        # migrate: add sort_order if missing
        try:
            await db.execute("ALTER TABLE ally_roles ADD COLUMN sort_order INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ally_roles WHERE ally_group_id=? ORDER BY sort_order ASC, id ASC", (ally_group_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def reorder_ally_roles(ally_group_id: int, ordered_ids: list[int]):
    """Set sort_order for each role id in the given order."""
    async with aiosqlite.connect(DB_PATH) as db:
        for i, rid in enumerate(ordered_ids):
            await db.execute(
                "UPDATE ally_roles SET sort_order=? WHERE id=? AND ally_group_id=?",
                (i, rid, ally_group_id),
            )
        await db.commit()


async def create_ally_role(ally_group_id: int, role_name: str, color: str, permissions: str = "") -> int:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        # new role goes to the end
        async with db.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 FROM ally_roles WHERE ally_group_id=?", (ally_group_id,)
        ) as cur:
            next_order = (await cur.fetchone())[0]
        cur = await db.execute(
            "INSERT OR IGNORE INTO ally_roles (ally_group_id, role_name, color, permissions, sort_order) VALUES (?,?,?,?,?)",
            (ally_group_id, role_name[:40], color or "#94a3b8", permissions or "", next_order),
        )
        await db.commit()
        return cur.lastrowid


async def update_ally_role(role_id: int, ally_group_id: int, color: str | None = None, permissions: str | None = None):
    await _init_ally_tables()
    sets, vals = [], []
    if color is not None:
        sets.append("color=?"); vals.append(color or "#94a3b8")
    if permissions is not None:
        sets.append("permissions=?"); vals.append(permissions or "")
    if not sets:
        return
    vals += [role_id, ally_group_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE ally_roles SET {', '.join(sets)} WHERE id=? AND ally_group_id=?", vals)
        await db.commit()


async def delete_ally_role(ally_group_id: int, role_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM ally_roles WHERE id=? AND ally_group_id=?", (role_id, ally_group_id))
        await db.execute("UPDATE ally_members SET role_id=NULL WHERE role_id=?", (role_id,))
        await db.commit()


async def set_member_role_and_wing(ally_group_id: int, discord_id: str, role_id: int | None, wing: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ally_members SET role_id=?, wing=? WHERE ally_group_id=? AND discord_id=?",
            (role_id, wing, ally_group_id, discord_id),
        )
        await db.commit()


async def get_ally_group_by_token(token: str) -> dict | None:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ally_groups WHERE invite_token = ?", (token,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_ally_group_by_wing_token(token: str) -> dict | None:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ally_groups WHERE wing1_token=? OR wing2_token=?", (token, token)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_ally_group_for_owner(guild_id: str, owner_id: str) -> dict | None:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ally_groups WHERE guild_id=? AND owner_discord_id=?",
            (guild_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_ally_members(ally_group_id: int) -> list[dict]:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT am.*, ar.role_name, ar.color AS role_color
            FROM ally_members am
            LEFT JOIN ally_roles ar ON ar.id = am.role_id
            WHERE am.ally_group_id=?
            ORDER BY am.wing ASC, ar.role_name ASC, am.travian_name ASC
        """, (ally_group_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def join_ally_group(ally_group_id: int, discord_id: str, discord_username: str,
                          travian_name: str = "", wing: int = 0, status: str = "approved") -> bool:
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
                INSERT INTO ally_members (ally_group_id, discord_id, discord_username, travian_name, wing, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ally_group_id, discord_id) DO UPDATE
                SET discord_username=excluded.discord_username,
                    travian_name=excluded.travian_name,
                    wing=excluded.wing,
                    status=excluded.status
            """, (ally_group_id, discord_id, discord_username, travian_name, wing, status))
            await db.commit()
            return True
        except Exception:
            return False


async def set_ally_member_status(ally_group_id: int, discord_id: str, status: str):
    """Approve or reject a pending member (status: 'approved' | 'rejected')."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ally_members SET status=? WHERE ally_group_id=? AND discord_id=?",
            (status, ally_group_id, discord_id)
        )
        await db.commit()


async def update_ally_member(ally_group_id: int, discord_id: str, travian_name, note,
                             role_id=None, wing=None):
    """Update member fields — pass None to leave a field unchanged."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        sets, vals = [], []
        if travian_name is not None:
            sets.append("travian_name=?"); vals.append(travian_name)
        if note is not None:
            sets.append("note=?"); vals.append(note)
        if role_id is not False:   # None means clear, False means skip
            sets.append("role_id=?"); vals.append(role_id)
        if wing is not None:
            sets.append("wing=?"); vals.append(wing)
        if not sets:
            return
        vals += [ally_group_id, discord_id]
        await db.execute(
            f"UPDATE ally_members SET {','.join(sets)} WHERE ally_group_id=? AND discord_id=?",
            vals,
        )
        await db.commit()


async def remove_ally_member(ally_group_id: int, discord_id: str):
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM ally_members WHERE ally_group_id=? AND discord_id=?",
            (ally_group_id, discord_id),
        )
        await db.commit()


async def get_ally_membership_guild_id(discord_id: str) -> str | None:
    """Return the guild_id of any approved ally membership for this user (latest joined)."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ag.guild_id FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE am.discord_id = ? AND am.status = 'approved'
            ORDER BY am.joined_at DESC LIMIT 1
        """, (discord_id,)) as cur:
            row = await cur.fetchone()
            return row["guild_id"] if row else None


async def get_ally_membership(guild_id: str, discord_id: str) -> dict | None:
    """Return the ally_group this user has joined (not owner) in this guild."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ag.*, am.travian_name, am.note, am.joined_at AS member_since
            FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE ag.guild_id = ? AND am.discord_id = ?
            ORDER BY am.joined_at DESC LIMIT 1
        """, (guild_id, discord_id)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_member_permissions(guild_id: str, discord_id: str) -> set[str]:
    """Return the set of permission flags for a user in a guild.
    Guild owners and users with the ally_manage flag get ALL permissions."""
    await _init_ally_tables()
    ALL_PERMS = {
        "ally_manage", "ep_manage", "ep_view", "ep_notify",
        "attack_manage", "attack_view", "scout_manage", "scout_view",
        "map_manage", "map_view", "sector_view", "hospital_view",
    }
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Check if they are the ally group owner
        async with db.execute(
            "SELECT id FROM ally_groups WHERE guild_id=? AND owner_discord_id=?",
            (guild_id, discord_id)
        ) as cur:
            if await cur.fetchone():
                return ALL_PERMS
        # Check membership role permissions
        async with db.execute("""
            SELECT ar.permissions
            FROM ally_members am
            JOIN ally_roles ar ON ar.id = am.role_id
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE ag.guild_id = ? AND am.discord_id = ?
            LIMIT 1
        """, (guild_id, discord_id)) as cur:
            row = await cur.fetchone()
            if not row:
                return set()
            raw = row["permissions"] or ""
            flags = {f.strip() for f in raw.split(",") if f.strip()}
            if "ally_manage" in flags:
                return ALL_PERMS
            return flags


async def regenerate_ally_token(ally_group_id: int, owner_id: str) -> str:
    import secrets as _sec
    token = _sec.token_urlsafe(24)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ally_groups SET invite_token=? WHERE id=? AND owner_discord_id=?",
            (token, ally_group_id, owner_id),
        )
        await db.commit()
    return token


async def get_all_shared_sitters(guild_id: str) -> list[dict]:
    """Return sitter entries for ALL approved alliance members in this guild.
    No is_shared gate — being in the same alliance is enough.
    Looks up by discord_id across all guild_ids (covers workspace saves)."""
    await _init_sitter_table()
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Collect all approved member discord_ids + owner
        async with db.execute("""
            SELECT DISTINCT am.discord_id
            FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE ag.guild_id = ? AND am.status = 'approved'
        """, (guild_id,)) as cur:
            member_ids = [r[0] for r in await cur.fetchall()]
        async with db.execute(
            "SELECT owner_discord_id FROM ally_groups WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] and row[0] not in member_ids:
                member_ids.append(row[0])

        if not member_ids:
            return []

        # Get their sitter entries (any guild_id, no is_shared filter)
        placeholders = ",".join("?" * len(member_ids))
        async with db.execute(
            f"SELECT * FROM account_sitters WHERE discord_user_id IN ({placeholders})",
            member_ids,
        ) as cur:
            # Deduplicate: keep most recent per discord_user_id
            seen: dict[str, dict] = {}
            for r in await cur.fetchall():
                d = dict(r)
                uid = d["discord_user_id"]
                if uid not in seen or (d.get("updated_at") or "") > (seen[uid].get("updated_at") or ""):
                    seen[uid] = d
            return list(seen.values())


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


async def get_farmlist_xy_lookup(guild_id: str, discord_user_id: str) -> dict:
    """Returns {(x,y): [list_name, ...]} from user's most recent farmlist analysis,
    cross-referenced with map_snapshots to get coordinates."""
    await _init_farmlist_analyses_table()
    import json as _json
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT farms_json FROM farmlist_analyses
               WHERE guild_id = ? AND discord_user_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (guild_id, discord_user_id)
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["farms_json"]:
            return {}
        farms = _json.loads(row["farms_json"])
        # Build village_name -> list of list_names
        name_to_lists: dict = {}
        for f in farms:
            if not isinstance(f, dict):
                continue
            vname = (f.get("village_name") or "").strip().lower()
            if not vname:
                continue
            lname = f.get("list_name") or f.get("group") or ""
            name_to_lists.setdefault(vname, [])
            if lname and lname not in name_to_lists[vname]:
                name_to_lists[vname].append(lname)
        if not name_to_lists:
            return {}
        # Get latest snapshot coords, match by village_name
        async with db.execute("""
            SELECT DISTINCT village_name, x, y FROM map_snapshots
            WHERE guild_id = ? AND fetched_at = (
                SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id = ?
            )
        """, (guild_id, guild_id)) as cur:
            snapshot_rows = await cur.fetchall()
    result: dict = {}
    for r in snapshot_rows:
        vname_lower = (r["village_name"] or "").strip().lower()
        if vname_lower in name_to_lists:
            key = (r["x"], r["y"])
            result[key] = name_to_lists[vname_lower]
    return result


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

        # Migrations: danger_level + tags + alliance_name on enemies
        for col in ["danger_level TEXT DEFAULT ''",
                    "tags TEXT DEFAULT ''",
                    "alliance_name TEXT DEFAULT ''"]:
            try:
                await db.execute(f"ALTER TABLE enemies ADD COLUMN {col}")
            except Exception:
                pass

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

async def get_meta_alliances(guild_id: str) -> list[dict]:
    """Return extra tracked meta-alliances for this guild (beyond ally_group wings)."""
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM meta_alliances WHERE guild_id=? ORDER BY sort_order, id",
            (guild_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_meta_alliance(guild_id: str, alliance_name: str, color: str = '#94a3b8') -> bool:
    """Add an extra meta-alliance. Returns True if inserted, False if duplicate."""
    await _init_ally_tables()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO meta_alliances (guild_id, alliance_name, color) VALUES (?,?,?)",
                (guild_id, alliance_name.strip(), color)
            )
            await db.commit()
        return True
    except Exception:
        return False


async def remove_meta_alliance(guild_id: str, alliance_name: str):
    await _init_ally_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM meta_alliances WHERE guild_id=? AND alliance_name=?",
            (guild_id, alliance_name)
        )
        await db.commit()


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


async def get_defend_contributions_for_guild(guild_id: str) -> dict[str, list]:
    """Return {channel_id: [contribution_dicts]} for all channels in a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT channel_id, user_name, user_id,
                   SUM(amount_parsed) as total_troops,
                   SUM(amount_parsed * grain_per_unit) as total_grain,
                   GROUP_CONCAT(troop_type, ', ') as troop_types,
                   MIN(sent_at) as first_sent
            FROM defend_sent
            WHERE guild_id=?
            GROUP BY channel_id, user_name
            ORDER BY channel_id, total_troops DESC
        """, (guild_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    result: dict[str, list] = {}
    for r in rows:
        cid = r.pop("channel_id")
        result.setdefault(cid, []).append(r)
    return result


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
                pos_x           REAL DEFAULT 50,
                pos_y           REAL DEFAULT 50,
                UNIQUE(layout_id, slot_num)
            )
        """)
        for col in ["pos_x REAL DEFAULT 50", "pos_y REAL DEFAULT 50"]:
            try:
                await db.execute(f"ALTER TABLE village_slots ADD COLUMN {col}")
            except Exception:
                pass
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


async def set_village_slot(
    layout_id: int, guild_id: str, slot_num: int, zone: str,
    building_type: str, target_level: int, notes: str,
    pos_x: float = 50.0, pos_y: float = 50.0,
):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM village_layouts WHERE id=? AND guild_id=?", (layout_id, guild_id)) as cur:
            if not await cur.fetchone():
                return
        await db.execute(
            """INSERT INTO village_slots
                   (layout_id, slot_num, slot_zone, building_type, target_level, notes, pos_x, pos_y)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(layout_id, slot_num) DO UPDATE SET
                 building_type=excluded.building_type,
                 target_level=excluded.target_level,
                 notes=excluded.notes,
                 pos_x=excluded.pos_x,
                 pos_y=excluded.pos_y""",
            (layout_id, slot_num, zone, building_type, target_level, notes, pos_x, pos_y)
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


async def delete_village_slot_by_id(slot_id: int, layout_id: int, guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM village_layouts WHERE id=? AND guild_id=?", (layout_id, guild_id)) as cur:
            if not await cur.fetchone():
                return
        await db.execute(
            "DELETE FROM village_slots WHERE id=? AND layout_id=?", (slot_id, layout_id)
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


async def get_guild_ids_for_discord_user(discord_user_id: str) -> list[str]:
    """Return all guild_ids where this user is subscription owner or an ally member."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        # As subscription/guild owner
        async with db.execute(
            "SELECT guild_id FROM guild_configs WHERE owner_discord_id=?",
            (discord_user_id,)
        ) as cur:
            owned = [r[0] for r in await cur.fetchall()]
        # As ally member
        async with db.execute("""
            SELECT DISTINCT ag.guild_id
            FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE am.discord_id = ?
        """, (discord_user_id,)) as cur:
            member_guilds = [r[0] for r in await cur.fetchall()]
    return list(set(owned + member_guilds))


async def get_or_create_dual_code(discord_user_id: str) -> str:
    """Return (or create) the dual invite code for a user."""
    import secrets as _sec
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT code FROM dual_codes WHERE discord_user_id=?", (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["code"]
        # Generate unique code
        while True:
            code = "D-" + _sec.token_urlsafe(8).upper()[:10]
            async with db.execute("SELECT 1 FROM dual_codes WHERE code=?", (code,)) as cur:
                if not await cur.fetchone():
                    break
        await db.execute(
            "INSERT INTO dual_codes (discord_user_id, code, created_at) VALUES (?,?,datetime('now'))",
            (discord_user_id, code),
        )
        await db.commit()
        return code


async def get_dual_info(discord_user_id: str) -> dict:
    """Return dual status for a user:
      {code, is_anchor, anchor_id, anchor_username (if dual), duals: [{discord_id, username}]}
    """
    await init_db()
    code = await get_or_create_dual_code(discord_user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Duals linked TO this user (they are the anchor)
        async with db.execute(
            "SELECT dual_discord_id, created_at FROM account_duals WHERE anchor_discord_id=? ORDER BY created_at ASC",
            (discord_user_id,)
        ) as cur:
            my_duals = [dict(r) for r in await cur.fetchall()]
        # Is this user a dual of someone else?
        async with db.execute(
            "SELECT anchor_discord_id FROM account_duals WHERE dual_discord_id=?",
            (discord_user_id,)
        ) as cur:
            anchor_row = await cur.fetchone()
    return {
        "code": code,
        "is_anchor": len(my_duals) > 0,
        "is_dual": anchor_row is not None,
        "anchor_id": anchor_row["anchor_discord_id"] if anchor_row else None,
        "duals": my_duals,
        "dual_count": len(my_duals),
    }


async def link_dual(code: str, requester_discord_id: str) -> dict:
    """Link requester as a dual of the code owner. Returns {ok, error}."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Find anchor
        async with db.execute(
            "SELECT discord_user_id FROM dual_codes WHERE code=?", (code,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {"ok": False, "error": "invalid_code"}
        anchor_id = row["discord_user_id"]
        if anchor_id == requester_discord_id:
            return {"ok": False, "error": "self_link"}
        # Check if requester is already someone's anchor (can't be dual if you have duals)
        async with db.execute(
            "SELECT 1 FROM account_duals WHERE anchor_discord_id=?", (requester_discord_id,)
        ) as cur:
            if await cur.fetchone():
                return {"ok": False, "error": "already_anchor"}
        # Check if requester is already a dual of someone else
        async with db.execute(
            "SELECT 1 FROM account_duals WHERE dual_discord_id=?", (requester_discord_id,)
        ) as cur:
            if await cur.fetchone():
                return {"ok": False, "error": "already_dual"}
        # Check anchor's dual count
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM account_duals WHERE anchor_discord_id=?", (anchor_id,)
        ) as cur:
            cnt_row = await cur.fetchone()
        if cnt_row and cnt_row["cnt"] >= 10:
            return {"ok": False, "error": "max_duals"}
        # Create link
        try:
            await db.execute(
                "INSERT INTO account_duals (anchor_discord_id, dual_discord_id) VALUES (?,?)",
                (anchor_id, requester_discord_id),
            )
            await db.commit()
        except Exception:
            return {"ok": False, "error": "already_linked"}
        return {"ok": True, "anchor_id": anchor_id}


async def unlink_dual(discord_user_id: str, target_discord_id: str) -> bool:
    """Remove a dual link. Works whether called by anchor or dual."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """DELETE FROM account_duals WHERE
               (anchor_discord_id=? AND dual_discord_id=?)
               OR (anchor_discord_id=? AND dual_discord_id=?)""",
            (discord_user_id, target_discord_id, target_discord_id, discord_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_dual_anchor(discord_user_id: str) -> str | None:
    """If this user is a dual, return the anchor's discord_id. Else None."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT anchor_discord_id FROM account_duals WHERE dual_discord_id=?",
            (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def get_dual_partners(discord_user_id: str) -> list[str]:
    """Return all discord_ids that share the same Travian account (excluding self)."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        # Is this user an anchor?
        async with db.execute(
            "SELECT dual_discord_id FROM account_duals WHERE anchor_discord_id=?",
            (discord_user_id,)
        ) as cur:
            duals = [r[0] for r in await cur.fetchall()]
        if duals:
            return duals
        # Is this user a dual?
        async with db.execute(
            "SELECT anchor_discord_id FROM account_duals WHERE dual_discord_id=?",
            (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        anchor = row[0]
        # Get all other duals of same anchor
        async with db.execute(
            "SELECT dual_discord_id FROM account_duals WHERE anchor_discord_id=? AND dual_discord_id!=?",
            (anchor, discord_user_id)
        ) as cur:
            others = [r[0] for r in await cur.fetchall()]
        return [anchor] + others


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


async def get_all_private_channels_for_guild(guild_id: str) -> list[dict]:
    """Return all private channel records for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM private_channels WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_defend_stats(guild_id: str) -> dict:
    """Return aggregated defense statistics for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Most annoying attackers
        async with db.execute("""
            SELECT attacker, COUNT(*) as attacks
            FROM defend_channels
            WHERE guild_id=? AND attacker != '' AND attacker IS NOT NULL
            GROUP BY attacker ORDER BY attacks DESC LIMIT 20
        """, (guild_id,)) as cur:
            attackers = [dict(r) for r in await cur.fetchall()]

        # Most targeted defenders (our players)
        async with db.execute("""
            SELECT requested_by_name as player, COUNT(*) as defends
            FROM defend_channels
            WHERE guild_id=? AND requested_by_name != '' AND requested_by_name IS NOT NULL
            GROUP BY requested_by_name ORDER BY defends DESC LIMIT 20
        """, (guild_id,)) as cur:
            targeted = [dict(r) for r in await cur.fetchall()]

        # Troop senders — who sends the most deff, with avg response time
        async with db.execute("""
            SELECT
                ds.user_name,
                ds.user_id,
                SUM(ds.amount_parsed) as total_troops,
                COUNT(DISTINCT ds.channel_id) as participations,
                SUM(ds.amount_parsed * ds.grain_per_unit) as total_grain,
                AVG(
                    (julianday(ds.sent_at) - julianday(dc.created_at)) * 24 * 60
                ) as avg_response_minutes,
                MIN(
                    (julianday(ds.sent_at) - julianday(dc.created_at)) * 24 * 60
                ) as best_response_minutes
            FROM defend_sent ds
            JOIN defend_channels dc ON dc.channel_id = ds.channel_id
            WHERE ds.guild_id=?
            GROUP BY ds.user_name
            ORDER BY total_troops DESC
            LIMIT 50
        """, (guild_id,)) as cur:
            senders = [dict(r) for r in await cur.fetchall()]

        # Per-player breakdown: troop types sent
        async with db.execute("""
            SELECT user_name, troop_type,
                   SUM(amount_parsed) as troops,
                   SUM(amount_parsed * grain_per_unit) as grain
            FROM defend_sent
            WHERE guild_id=? AND troop_type != ''
            GROUP BY user_name, troop_type
            ORDER BY user_name, troops DESC
        """, (guild_id,)) as cur:
            troop_breakdown_rows = [dict(r) for r in await cur.fetchall()]

        # Build per-player troop breakdown dict
        from collections import defaultdict
        troop_breakdown: dict = defaultdict(list)
        for r in troop_breakdown_rows:
            troop_breakdown[r["user_name"]].append(r)

        # Most defended targets (channel = one defend request)
        async with db.execute("""
            SELECT dc.channel_id, dc.attacker, dc.coords, dc.requested_by_name,
                   dc.created_at, dc.goal, dc.type,
                   COALESCE(SUM(ds.amount_parsed),0) as total_troops,
                   COUNT(ds.id) as contributor_count
            FROM defend_channels dc
            LEFT JOIN defend_sent ds ON dc.channel_id = ds.channel_id
            WHERE dc.guild_id=?
            GROUP BY dc.channel_id
            ORDER BY total_troops DESC LIMIT 15
        """, (guild_id,)) as cur:
            top_defends = [dict(r) for r in await cur.fetchall()]

        # Total counts
        async with db.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                   SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed_count
            FROM defend_channels WHERE guild_id=?
        """, (guild_id,)) as cur:
            totals = dict(await cur.fetchone())

        async with db.execute("""
            SELECT COALESCE(SUM(amount_parsed),0) as total_troops_sent,
                   COUNT(DISTINCT user_id) as unique_senders
            FROM defend_sent WHERE guild_id=?
        """, (guild_id,)) as cur:
            sent_totals = dict(await cur.fetchone())

    return {
        "attackers": attackers,
        "targeted": targeted,
        "senders": senders,
        "troop_breakdown": dict(troop_breakdown),
        "top_defends": top_defends,
        "totals": {**totals, **sent_totals},
    }


# =============================================================================
# ── OPERATION PLANNING ────────────────────────────────────────────────────────
# =============================================================================

import json as _json_op

TRAVIAN_TROOPS: dict = {
    "romans": [
        {"id": "legionnaire",    "name": "Legionär",               "speed": 6,  "carry": 50,   "attack": 40,  "scout": False},
        {"id": "praetorian",     "name": "Prätorianer",             "speed": 5,  "carry": 20,   "attack": 30,  "scout": False},
        {"id": "imperian",       "name": "Imperianer",              "speed": 7,  "carry": 70,   "attack": 70,  "scout": False},
        {"id": "eq_legati",      "name": "Equites Legati",          "speed": 16, "carry": 0,    "attack": 0,   "scout": True},
        {"id": "eq_imperatoris", "name": "Equites Imperatoris",     "speed": 14, "carry": 100,  "attack": 120, "scout": False},
        {"id": "eq_caesaris",    "name": "Equites Caesaris",        "speed": 10, "carry": 70,   "attack": 180, "scout": False},
        {"id": "bat_ram",        "name": "Rammbock",                "speed": 4,  "carry": 0,    "attack": 60,  "scout": False},
        {"id": "fire_cat",       "name": "Feuerkatapult",           "speed": 3,  "carry": 0,    "attack": 75,  "scout": False},
        {"id": "senator",        "name": "Senator",                 "speed": 4,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "settler",        "name": "Siedler",                 "speed": 5,  "carry": 3000, "attack": 10,  "scout": False},
    ],
    "gauls": [
        {"id": "phalanx",    "name": "Phalanx",              "speed": 7,  "carry": 35,   "attack": 15,  "scout": False},
        {"id": "swordsman",  "name": "Schwertkämpfer",       "speed": 6,  "carry": 45,   "attack": 65,  "scout": False},
        {"id": "pathfinder", "name": "Pathfinder",           "speed": 17, "carry": 0,    "attack": 0,   "scout": True},
        {"id": "thunder",    "name": "Theutates-Blitz",      "speed": 19, "carry": 75,   "attack": 100, "scout": False},
        {"id": "druid",      "name": "Druidentreiter",       "speed": 16, "carry": 35,   "attack": 45,  "scout": False},
        {"id": "haeduan",    "name": "Haeduer",              "speed": 13, "carry": 65,   "attack": 140, "scout": False},
        {"id": "gal_ram",    "name": "Rammbock",             "speed": 4,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "trebuchet",  "name": "Trebuchet",            "speed": 3,  "carry": 0,    "attack": 70,  "scout": False},
        {"id": "chieftain",  "name": "Häuptling",            "speed": 5,  "carry": 0,    "attack": 40,  "scout": False},
        {"id": "settler",    "name": "Siedler",              "speed": 5,  "carry": 3000, "attack": 10,  "scout": False},
    ],
    "teutons": [
        {"id": "clubswinger", "name": "Keulenschwinger",     "speed": 7,  "carry": 60,   "attack": 40,  "scout": False},
        {"id": "spearman",    "name": "Speerkämpfer",        "speed": 7,  "carry": 40,   "attack": 10,  "scout": False},
        {"id": "axeman",      "name": "Axtkämpfer",          "speed": 7,  "carry": 50,   "attack": 60,  "scout": False},
        {"id": "scout",       "name": "Kundschafter",        "speed": 9,  "carry": 0,    "attack": 0,   "scout": True},
        {"id": "paladin",     "name": "Paladin",             "speed": 10, "carry": 55,   "attack": 55,  "scout": False},
        {"id": "tk",          "name": "Teutonenknight",      "speed": 9,  "carry": 80,   "attack": 150, "scout": False},
        {"id": "teu_ram",     "name": "Rammbock",            "speed": 4,  "carry": 0,    "attack": 65,  "scout": False},
        {"id": "catapult",    "name": "Katapult",            "speed": 3,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "chief",       "name": "Stammesführer",       "speed": 4,  "carry": 0,    "attack": 40,  "scout": False},
        {"id": "settler",     "name": "Siedler",             "speed": 5,  "carry": 3000, "attack": 10,  "scout": False},
    ],
    "huns": [
        {"id": "mercenary",     "name": "Söldner",           "speed": 10, "carry": 40,   "attack": 45,  "scout": False},
        {"id": "bowman",        "name": "Bogenschütze",      "speed": 9,  "carry": 30,   "attack": 30,  "scout": False},
        {"id": "spotter",       "name": "Späher",            "speed": 13, "carry": 0,    "attack": 0,   "scout": True},
        {"id": "steppe_rider",  "name": "Steppenreiter",     "speed": 14, "carry": 55,   "attack": 100, "scout": False},
        {"id": "marksman",      "name": "Scharfschütze",     "speed": 13, "carry": 50,   "attack": 70,  "scout": False},
        {"id": "marauder",      "name": "Plünderer",         "speed": 11, "carry": 80,   "attack": 120, "scout": False},
        {"id": "hun_ram",       "name": "Rammbock",          "speed": 4,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "hun_cat",       "name": "Katapult",          "speed": 3,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "logades",       "name": "Logades",           "speed": 5,  "carry": 0,    "attack": 40,  "scout": False},
        {"id": "settler",       "name": "Siedler",           "speed": 5,  "carry": 3000, "attack": 10,  "scout": False},
    ],
    "egyptians": [
        {"id": "slave_militia", "name": "Sklavenmiliz",      "speed": 8,  "carry": 40,   "attack": 10,  "scout": False},
        {"id": "ash_warden",    "name": "Aschenwächter",     "speed": 6,  "carry": 35,   "attack": 45,  "scout": False},
        {"id": "khopesh",       "name": "Khopeschkämpfer",   "speed": 7,  "carry": 50,   "attack": 60,  "scout": False},
        {"id": "sopdu",         "name": "Sopdu-Entdecker",   "speed": 13, "carry": 0,    "attack": 0,   "scout": True},
        {"id": "anhur",         "name": "Anhur-Wächter",     "speed": 12, "carry": 55,   "attack": 100, "scout": False},
        {"id": "resheph",       "name": "Resheph-Streitwagen","speed":10, "carry": 70,   "attack": 140, "scout": False},
        {"id": "egy_ram",       "name": "Rammbock",          "speed": 4,  "carry": 0,    "attack": 50,  "scout": False},
        {"id": "stone_cat",     "name": "Steinkatapult",     "speed": 3,  "carry": 0,    "attack": 65,  "scout": False},
        {"id": "nomarch",       "name": "Nomarch",           "speed": 5,  "carry": 0,    "attack": 40,  "scout": False},
        {"id": "settler",       "name": "Siedler",           "speed": 5,  "carry": 3000, "attack": 10,  "scout": False},
    ],
}

# Flat lookup: id → speed
_TROOP_SPEED: dict[str, float] = {
    t["id"]: float(t["speed"])
    for troops in TRAVIAN_TROOPS.values()
    for t in troops
}

def _calc_travel_seconds(x1: int, y1: int, x2: int, y2: int,
                          speed_tiles_per_hour: float, server_speed: float = 1.0,
                          map_size: int = 801) -> int:
    """Return travel time in seconds (Travian torus distance)."""
    dx = min(abs(x1 - x2), map_size - abs(x1 - x2))
    dy = min(abs(y1 - y2), map_size - abs(y1 - y2))
    dist = (dx*dx + dy*dy) ** 0.5
    if dist == 0:
        return 0
    hours = dist / (speed_tiles_per_hour * server_speed)
    return int(hours * 3600)


async def _init_op_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        # Operation plan header
        await db.execute("""
            CREATE TABLE IF NOT EXISTS op_plans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                name          TEXT NOT NULL DEFAULT 'Einsatz',
                status        TEXT NOT NULL DEFAULT 'draft',
                landing_time  TEXT,
                server_speed  REAL NOT NULL DEFAULT 1.0,
                target_ally   TEXT DEFAULT '',
                notes         TEXT DEFAULT '',
                created_by    TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_op_plans_guild ON op_plans(guild_id)")

        # Targets within a plan
        await db.execute("""
            CREATE TABLE IF NOT EXISTS op_targets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id       INTEGER NOT NULL,
                guild_id      TEXT NOT NULL,
                player_name   TEXT NOT NULL DEFAULT '',
                village_name  TEXT NOT NULL DEFAULT '',
                x             INTEGER NOT NULL,
                y             INTEGER NOT NULL,
                population    INTEGER DEFAULT 0,
                order_idx     INTEGER DEFAULT 0,
                notes         TEXT DEFAULT '',
                FOREIGN KEY (plan_id) REFERENCES op_plans(id) ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_op_targets_plan ON op_targets(plan_id)")

        # Waves per target
        await db.execute("""
            CREATE TABLE IF NOT EXISTS op_waves (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id           INTEGER NOT NULL,
                plan_id             INTEGER NOT NULL,
                guild_id            TEXT NOT NULL,
                attacker_discord_id TEXT DEFAULT '',
                attacker_name       TEXT NOT NULL DEFAULT '',
                origin_village      TEXT DEFAULT '',
                origin_x            INTEGER,
                origin_y            INTEGER,
                wave_type           TEXT NOT NULL DEFAULT 'real',
                tribe               TEXT NOT NULL DEFAULT 'romans',
                troop_json          TEXT NOT NULL DEFAULT '{}',
                slowest_unit        TEXT DEFAULT '',
                slowest_speed       REAL DEFAULT 0,
                travel_seconds      INTEGER DEFAULT 0,
                send_time           TEXT DEFAULT '',
                arrival_time        TEXT DEFAULT '',
                notes               TEXT DEFAULT '',
                order_idx           INTEGER DEFAULT 0,
                confirmed           INTEGER DEFAULT 0,
                FOREIGN KEY (target_id) REFERENCES op_targets(id) ON DELETE CASCADE,
                FOREIGN KEY (plan_id)   REFERENCES op_plans(id)   ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_op_waves_target ON op_waves(target_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_op_waves_attacker ON op_waves(attacker_discord_id)")
        # Migration: add tournament_square if not present
        try:
            await db.execute("ALTER TABLE op_waves ADD COLUMN tournament_square INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        # Migration: wave confirmation status
        try:
            await db.execute("ALTER TABLE op_waves ADD COLUMN confirm_status TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE op_waves ADD COLUMN confirm_delta_seconds INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        # availability_polls: link to EP plan
        try:
            await db.execute("ALTER TABLE availability_polls ADD COLUMN plan_id INTEGER")
            await db.commit()
        except Exception:
            pass
        # Notifications table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        TEXT NOT NULL,
                ally_group_id   INTEGER,
                recipient_id    TEXT NOT NULL,
                type            TEXT NOT NULL DEFAULT 'info',
                title           TEXT NOT NULL DEFAULT '',
                message         TEXT NOT NULL DEFAULT '',
                plan_id         INTEGER,
                read            INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(guild_id, recipient_id, read)")
        await db.commit()

        # Favourites per user per guild (saved targets)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS op_favorites (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                discord_id    TEXT NOT NULL,
                player_name   TEXT DEFAULT '',
                village_name  TEXT DEFAULT '',
                x             INTEGER NOT NULL,
                y             INTEGER NOT NULL,
                label         TEXT DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(guild_id, discord_id, x, y)
            )
        """)

        # Per-member troop snapshot (latest upload per user)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_troops (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                discord_id    TEXT NOT NULL,
                discord_name  TEXT DEFAULT '',
                travian_name  TEXT DEFAULT '',
                villages_json TEXT NOT NULL DEFAULT '[]',
                tribe         TEXT DEFAULT '',
                total_off     INTEGER DEFAULT 0,
                total_def     INTEGER DEFAULT 0,
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(guild_id, discord_id)
            )
        """)
        await db.commit()


# ── op_plans CRUD ──────────────────────────────────────────────────────────────

async def create_op_plan(guild_id: str, name: str, landing_time: str,
                          server_speed: float, target_ally: str,
                          notes: str, created_by: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO op_plans (guild_id, name, status, landing_time, server_speed,
                                   target_ally, notes, created_by)
            VALUES (?,?,?,?,?,?,?,?)
        """, (guild_id, name[:120], "draft", landing_time, server_speed,
              target_ally[:100], notes[:500], created_by))
        await db.commit()
        return cur.lastrowid


async def get_op_plans(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT p.*, COUNT(DISTINCT t.id) as target_count,
                   COUNT(DISTINCT w.id) as wave_count
            FROM op_plans p
            LEFT JOIN op_targets t ON t.plan_id = p.id
            LEFT JOIN op_waves w   ON w.plan_id = p.id
            WHERE p.guild_id = ?
            GROUP BY p.id ORDER BY p.created_at DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_op_plan(plan_id: int, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM op_plans WHERE id=? AND guild_id=?", (plan_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_op_plan(plan_id: int, guild_id: str, **kwargs):
    allowed = {"name", "status", "landing_time", "server_speed", "target_ally", "notes"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE op_plans SET {cols} WHERE id=? AND guild_id=?",
            (*sets.values(), plan_id, guild_id)
        )
        await db.commit()


async def delete_op_plan(plan_id: int, guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM op_plans WHERE id=? AND guild_id=?", (plan_id, guild_id))
        await db.commit()


# ── op_targets CRUD ────────────────────────────────────────────────────────────

async def add_op_target(plan_id: int, guild_id: str, player_name: str,
                         village_name: str, x: int, y: int,
                         population: int = 0, notes: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(MAX(order_idx)+1,0) FROM op_targets WHERE plan_id=?", (plan_id,)
        ) as cur:
            idx = (await cur.fetchone())[0]
        cur = await db.execute("""
            INSERT INTO op_targets (plan_id, guild_id, player_name, village_name, x, y, population, order_idx, notes)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (plan_id, guild_id, player_name[:80], village_name[:80], x, y, population, idx, notes[:300]))
        await db.commit()
        return cur.lastrowid


async def delete_op_target(target_id: int, guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM op_targets WHERE id=? AND guild_id=?", (target_id, guild_id))
        await db.commit()


# ── op_waves CRUD ──────────────────────────────────────────────────────────────

async def add_op_wave(target_id: int, plan_id: int, guild_id: str,
                       attacker_discord_id: str, attacker_name: str,
                       origin_village: str, origin_x: int | None, origin_y: int | None,
                       wave_type: str, tribe: str, troop_json: dict,
                       landing_time: str, server_speed: float,
                       notes: str = "", tournament_square: int = 0) -> dict:
    """Add a wave and compute send_time from landing_time and troop speed."""
    # Slowest troop
    troops = {k: v for k, v in troop_json.items() if v and int(v) > 0}
    slowest_id = min(troops.keys(), key=lambda t: _TROOP_SPEED.get(t, 99), default="")
    base_speed = _TROOP_SPEED.get(slowest_id, 6.0)
    # Tournament square boosts speed by 20% per level
    slowest_speed = base_speed * (1 + max(0, min(tournament_square, 20)) * 0.2)

    # Get target coords
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT x,y FROM op_targets WHERE id=?", (target_id,)) as cur:
            trow = await cur.fetchone()

    travel_sec = 0
    send_time = landing_time
    if trow and origin_x is not None and origin_y is not None and slowest_speed > 0:
        travel_sec = _calc_travel_seconds(
            origin_x, origin_y, trow["x"], trow["y"], slowest_speed, server_speed
        )
        if landing_time:
            import datetime as _dt
            try:
                lt = _dt.datetime.fromisoformat(landing_time.replace("Z",""))
                send_dt = lt - _dt.timedelta(seconds=travel_sec)
                send_time = send_dt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(MAX(order_idx)+1,0) FROM op_waves WHERE target_id=?", (target_id,)
        ) as cur:
            idx = (await cur.fetchone())[0]
        cur = await db.execute("""
            INSERT INTO op_waves
                (target_id, plan_id, guild_id, attacker_discord_id, attacker_name,
                 origin_village, origin_x, origin_y, wave_type, tribe, troop_json,
                 slowest_unit, slowest_speed, travel_seconds, send_time, arrival_time,
                 notes, order_idx, tournament_square)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (target_id, plan_id, guild_id,
              attacker_discord_id, attacker_name[:80],
              origin_village[:80], origin_x, origin_y,
              wave_type, tribe,
              _json_op.dumps(troops),
              slowest_id, slowest_speed, travel_sec,
              send_time, landing_time,
              notes[:300], idx, max(0, min(tournament_square, 20))))
        wave_id = cur.lastrowid
        await db.commit()

    return {"id": wave_id, "send_time": send_time, "travel_seconds": travel_sec,
            "slowest_unit": slowest_id, "slowest_speed": slowest_speed}


async def update_op_wave(wave_id: int, guild_id: str, **kwargs):
    allowed = {"attacker_name","origin_village","origin_x","origin_y","wave_type","tribe",
               "troop_json","send_time","arrival_time","notes","confirmed","slowest_unit",
               "slowest_speed","travel_seconds","confirm_status","confirm_delta_seconds"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return
    # Serialise troop_json if dict
    if "troop_json" in sets and isinstance(sets["troop_json"], dict):
        sets["troop_json"] = _json_op.dumps(sets["troop_json"])
    cols = ", ".join(f"{k}=?" for k in sets)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE op_waves SET {cols} WHERE id=? AND guild_id=?",
            (*sets.values(), wave_id, guild_id)
        )
        await db.commit()


async def delete_op_wave(wave_id: int, guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM op_waves WHERE id=? AND guild_id=?", (wave_id, guild_id))
        await db.commit()


async def get_all_op_waves(plan_id: int) -> list[dict]:
    """Return all waves for a plan (attacker_discord_id + send_time)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, attacker_discord_id, send_time, arrival_time, wave_type, order_idx "
            "FROM op_waves WHERE plan_id=? ORDER BY attacker_discord_id, send_time",
            (plan_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_my_op_waves(guild_id: str, discord_id: str) -> list[dict]:
    """Return all waves assigned to a user across all active/draft plans, with plan + target info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT w.id, w.plan_id, w.target_id, w.attacker_name, w.origin_village,
                   w.origin_x, w.origin_y, w.wave_type, w.tribe, w.troop_json,
                   w.send_time, w.arrival_time, w.travel_seconds, w.notes,
                   w.confirm_status, w.confirm_delta_seconds, w.tournament_square,
                   p.name AS plan_name, p.landing_time, p.status AS plan_status,
                   t.player_name AS target_player, t.village_name AS target_village,
                   t.x AS target_x, t.y AS target_y
            FROM op_waves w
            JOIN op_plans p  ON p.id = w.plan_id
            JOIN op_targets t ON t.id = w.target_id
            WHERE w.guild_id = ?
              AND w.attacker_discord_id = ?
              AND p.status IN ('draft','active')
            ORDER BY p.landing_time ASC, w.send_time ASC
        """, (guild_id, discord_id)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        try:
            import json as _j
            r["troop_json"] = _j.loads(r["troop_json"] or "{}")
        except Exception:
            r["troop_json"] = {}
    return rows


async def get_op_plan_full(plan_id: int, guild_id: str) -> dict | None:
    """Return plan with nested targets and waves."""
    plan = await get_op_plan(plan_id, guild_id)
    if not plan:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM op_targets WHERE plan_id=? ORDER BY order_idx", (plan_id,)
        ) as cur:
            targets = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT * FROM op_waves WHERE plan_id=? ORDER BY target_id, order_idx", (plan_id,)
        ) as cur:
            waves = [dict(r) for r in await cur.fetchall()]

    waves_by_target: dict[int, list] = {}
    for w in waves:
        w["troop_json"] = _json_op.loads(w["troop_json"]) if w["troop_json"] else {}
        waves_by_target.setdefault(w["target_id"], []).append(w)

    for t in targets:
        t["waves"] = waves_by_target.get(t["id"], [])

    plan["targets"] = targets
    return plan


# ── op_favorites ───────────────────────────────────────────────────────────────

async def get_op_favorites(guild_id: str, discord_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM op_favorites WHERE guild_id=? AND discord_id=?
            ORDER BY created_at DESC
        """, (guild_id, discord_id)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_op_favorite(guild_id: str, discord_id: str, player_name: str,
                           village_name: str, x: int, y: int, label: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT OR REPLACE INTO op_favorites
                (guild_id, discord_id, player_name, village_name, x, y, label)
            VALUES (?,?,?,?,?,?,?)
        """, (guild_id, discord_id, player_name[:80], village_name[:80], x, y, label[:80]))
        await db.commit()
        return cur.lastrowid


async def delete_op_favorite(fav_id: int, discord_id: str, guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM op_favorites WHERE id=? AND discord_id=? AND guild_id=?",
            (fav_id, discord_id, guild_id)
        )
        await db.commit()


# ── member_troops ──────────────────────────────────────────────────────────────

async def _migrate_member_troops():
    async with aiosqlite.connect(DB_PATH) as db:
        for col, definition in [
            ("total_crop", "INTEGER DEFAULT 0"),
            ("total_units", "INTEGER DEFAULT 0"),
            ("total_scouts", "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE member_troops ADD COLUMN {col} {definition}")
            except Exception:
                pass
        await db.commit()


async def upsert_member_troops(guild_id: str, discord_id: str, discord_name: str,
                                travian_name: str, villages: list[dict],
                                tribe: str = "", total_off: int = 0, total_def: int = 0,
                                total_crop: int = 0, total_units: int = 0, total_scouts: int = 0):
    await _migrate_member_troops()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO member_troops
                (guild_id, discord_id, discord_name, travian_name, villages_json,
                 tribe, total_off, total_def, total_crop, total_units, total_scouts, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                discord_name  = excluded.discord_name,
                travian_name  = excluded.travian_name,
                villages_json = excluded.villages_json,
                tribe         = excluded.tribe,
                total_off     = excluded.total_off,
                total_def     = excluded.total_def,
                total_crop    = excluded.total_crop,
                total_units   = excluded.total_units,
                total_scouts  = excluded.total_scouts,
                updated_at    = excluded.updated_at
        """, (guild_id, discord_id, discord_name, travian_name,
              _json_op.dumps(villages), tribe, total_off, total_def,
              total_crop, total_units, total_scouts))
        await db.commit()


async def get_member_troops(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM member_troops WHERE guild_id=? ORDER BY discord_name
        """, (guild_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["villages"] = _json_op.loads(r["villages_json"]) if r["villages_json"] else []
    return rows


async def get_member_troops_single(guild_id: str, discord_id: str) -> dict | None:
    """Return member troop data — prefers current guild, falls back to any guild (latest entry)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Try current guild first
        async with db.execute(
            "SELECT * FROM member_troops WHERE guild_id=? AND discord_id=?",
            (guild_id, discord_id)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            # Fallback: most recent entry across any guild
            async with db.execute(
                "SELECT * FROM member_troops WHERE discord_id=? ORDER BY updated_at DESC LIMIT 1",
                (discord_id,)
            ) as cur:
                row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    r["villages"] = _json_op.loads(r["villages_json"]) if r["villages_json"] else []
    return r


async def get_member_troops_for_discord_ids(discord_ids: list[str]) -> dict[str, dict]:
    """Return most recent troop entry per discord_id, across all guilds.
    Returns dict: discord_id -> row dict (with 'villages' list parsed)."""
    if not discord_ids:
        return {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(discord_ids))
        # Pick the row with the latest updated_at per discord_id
        rows = await db.execute_fetchall(f"""
            SELECT mt.*
            FROM member_troops mt
            INNER JOIN (
                SELECT discord_id, MAX(updated_at) AS max_updated
                FROM member_troops
                WHERE discord_id IN ({placeholders})
                GROUP BY discord_id
            ) latest ON mt.discord_id = latest.discord_id
                     AND mt.updated_at = latest.max_updated
        """, discord_ids)
    result = {}
    for r in rows:
        d = dict(r)
        d["villages"] = _json_op.loads(d["villages_json"]) if d.get("villages_json") else []
        # Only keep first match per discord_id (in case of tie)
        if d["discord_id"] not in result:
            result[d["discord_id"]] = d
    return result


async def get_member_leaderboard(guild_id: str) -> list[dict]:
    """Return member_troops enriched with population from latest map snapshot.
    Each row: discord_id, discord_name, travian_name, total_off, total_def,
              total_crop, total_units, total_scouts, population, village_count,
              avg_population, tq (%)"""
    await _migrate_member_troops()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Get population + village count per player_name from latest snapshot
        pop_rows = await db.execute_fetchall("""
            SELECT player_name, SUM(population) as pop, COUNT(*) as vcount
            FROM map_snapshots
            WHERE guild_id = ?
              AND fetched_at = (SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id = ?)
              AND player_name IS NOT NULL AND player_name != ''
            GROUP BY player_name
        """, (guild_id, guild_id))
        pop_map = {r["player_name"]: {"pop": r["pop"], "vcount": r["vcount"]} for r in pop_rows}

        rows = await db.execute_fetchall(
            "SELECT * FROM member_troops WHERE guild_id=? ORDER BY total_off DESC",
            (guild_id,)
        )
    result = []
    for r in rows:
        d = dict(r)
        pdata = pop_map.get(d.get("travian_name") or "", {})
        pop = pdata.get("pop", 0) or 0
        vcount = pdata.get("vcount", 0) or 0
        crop = d.get("total_crop") or 0
        tq = round(crop / pop * 100) if pop > 0 else None
        d["population"] = pop
        d["village_count"] = vcount
        d["avg_population"] = round(pop / vcount) if vcount > 0 else 0
        d["total_troops"] = (d.get("total_off") or 0) + (d.get("total_def") or 0)
        d["tq"] = tq
        result.append(d)
    return result


async def get_active_ep_members(guild_id: str) -> set:
    """Return set of discord_ids that have waves in active plans."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("""
            SELECT DISTINCT w.attacker_discord_id
            FROM op_waves w
            JOIN op_plans p ON p.id = w.plan_id
            WHERE w.guild_id = ? AND p.status = 'active'
              AND w.attacker_discord_id != ''
        """, (guild_id,))
        return {r["attacker_discord_id"] for r in rows}


async def get_member_growth(guild_id: str, discord_ids: list) -> dict:
    """Return village history per discord_id for growth charts.
    Returns dict: discord_id → list of {uploaded_at, village_count, total_off, total_def, total_crop}
    Only last 14 entries per member."""
    if not discord_ids:
        return {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(discord_ids))
        rows = await db.execute_fetchall(f"""
            SELECT discord_id, uploaded_at, village_count, total_off, total_def, total_crop
            FROM guild_own_villages_history
            WHERE guild_id = ? AND discord_id IN ({placeholders})
            ORDER BY discord_id, uploaded_at ASC
        """, [guild_id] + discord_ids)
    result: dict = {}
    for r in rows:
        did = r["discord_id"]
        if did not in result:
            result[did] = []
        result[did].append({
            "uploaded_at": r["uploaded_at"][:10],
            "village_count": r["village_count"] or 0,
            "total_off": r["total_off"] or 0,
            "total_def": r["total_def"] or 0,
            "total_crop": r["total_crop"] or 0,
        })
    # Keep last 14 snapshots per member
    for did in result:
        result[did] = result[did][-14:]
    return result


# ── Personal missions (per member) ─────────────────────────────────────────────

async def get_personal_missions(guild_id: str, discord_id: str) -> list[dict]:
    """Return all waves assigned to a specific member across active/completed plans."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT w.*,
                   t.player_name as target_player, t.village_name as target_village,
                   t.x as target_x, t.y as target_y,
                   p.name as plan_name, p.status as plan_status,
                   p.landing_time, p.server_speed
            FROM op_waves w
            JOIN op_targets t ON t.id = w.target_id
            JOIN op_plans   p ON p.id = w.plan_id
            WHERE w.guild_id=? AND w.attacker_discord_id=?
              AND p.status IN ('active','completed')
            ORDER BY w.send_time ASC
        """, (guild_id, discord_id)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["troop_json"] = _json_op.loads(r["troop_json"]) if r["troop_json"] else {}
    return rows


# ── Plausibility check ─────────────────────────────────────────────────────────

async def check_op_plausibility(plan_id: int, guild_id: str) -> dict:
    """Run plausibility checks. Returns {ok:[], warnings:[], errors:[], suggestions:[], summary:{}}."""
    plan = await get_op_plan_full(plan_id, guild_id)
    if not plan:
        return {"ok": [], "warnings": [], "errors": [{"msg": "Plan nicht gefunden."}], "suggestions": [], "summary": {}}

    ok_items: list[str] = []
    warnings: list[str] = []
    errors:   list[str] = []
    suggestions: list[str] = []

    import datetime as _dt
    server_speed = float(plan.get("server_speed") or 1.0)
    landing_time_str = plan.get("landing_time") or ""
    now = _dt.datetime.utcnow()

    try:
        landing_dt = _dt.datetime.fromisoformat(landing_time_str.replace("Z","")) if landing_time_str else None
    except Exception:
        landing_dt = None

    # ── Plan-level basics ────────────────────────────────────────────────────
    if landing_dt:
        mins_until = int((landing_dt - now).total_seconds() / 60)
        if mins_until > 0:
            ok_items.append(f"Einschlagszeit gesetzt: {landing_time_str[:16].replace('T',' ')} (in {mins_until} min)")
        else:
            errors.append(f"Einschlagszeit liegt in der Vergangenheit ({abs(mins_until)} min)!")
    else:
        errors.append("Keine Einschlagszeit gesetzt — Sendezeiten können nicht berechnet werden.")
        suggestions.append("Einschlagszeit im Plan-Einstellungen (✏ Bearbeiten) hinterlegen.")

    targets = plan.get("targets") or []
    if not targets:
        warnings.append("Keine Ziele im Plan.")
        return {"ok": ok_items, "warnings": warnings, "errors": errors, "suggestions": suggestions,
                "summary": _check_summary(ok_items, warnings, errors, suggestions)}
    else:
        ok_items.append(f"{len(targets)} Ziel{'e' if len(targets)!=1 else ''} angelegt.")

    # ── Per-target checks ───────────────────────────────────────────────────
    targets_no_waves = []
    targets_no_fake  = []
    targets_no_scout = []
    targets_good     = []

    for t in targets:
        waves = t.get("waves") or []
        label = f"{t['player_name']} ({t['x']}|{t['y']})"

        if not waves:
            targets_no_waves.append(label)
            suggestions.append(f"Ziel {label}: Noch keine Wellen — Angreifer zuweisen.")
            continue

        real_waves  = [w for w in waves if w["wave_type"] == "real"]
        fake_waves  = [w for w in waves if w["wave_type"] == "fake"]
        scout_waves = [w for w in waves if w["wave_type"] == "scout"]

        target_ok = True

        # Fake ratio
        if real_waves and not fake_waves:
            errors.append(f"{label}: Echter Angriff ohne Fakes — Ziel wird nicht getarnt!")
            suggestions.append(f"{label}: Mindestens {3*len(real_waves)} Fake-Wellen hinzufügen.")
            target_ok = False
        elif real_waves and len(fake_waves) < 3 * len(real_waves):
            needed = 3 * len(real_waves) - len(fake_waves)
            warnings.append(f"{label}: Nur {len(fake_waves)} Fakes für {len(real_waves)} echte Wellen (Empfehlung: 3:1).")
            suggestions.append(f"{label}: {needed} weitere Fake-Welle{'n' if needed>1 else ''} hinzufügen.")
            target_ok = False
        elif real_waves and len(fake_waves) >= 3 * len(real_waves):
            ok_items.append(f"{label}: Fake-Ratio gut ({len(fake_waves)} Fakes / {len(real_waves)} echt).")

        # Fake troop count < 20
        import json as _jf
        for fw in fake_waves:
            try:
                tj = _jf.loads(fw.get("troop_json") or "{}")
            except Exception:
                tj = {}
            total = sum(int(v) for v in tj.values() if v)
            if 0 < total < 20:
                warnings.append(f"{label}: Fake von '{fw.get('attacker_name','')}' hat nur {total} Truppen (< 20) — könnte erkannt werden!")

        # Scout
        if real_waves and not scout_waves:
            warnings.append(f"{label}: Keine Aufklärungswelle.")
            suggestions.append(f"{label}: Scout-Welle hinzufügen um Truppenstärke vor Angriff zu prüfen.")
            target_ok = False
        elif scout_waves:
            ok_items.append(f"{label}: Aufklärungswelle vorhanden.")

        # Timing
        if landing_dt:
            past_count = 0
            no_send_count = 0
            for w in waves:
                send_str = w.get("send_time", "")
                if not send_str:
                    no_send_count += 1
                    continue
                try:
                    send_dt = _dt.datetime.fromisoformat(send_str.replace("Z", ""))
                    if send_dt < now:
                        past_count += 1
                    diff = abs((send_dt + _dt.timedelta(seconds=w.get("travel_seconds", 0))) - landing_dt).total_seconds()
                    if diff > 120:
                        warnings.append(f"{label} / {w['attacker_name']}: Ankunft weicht {int(diff)}s von Einschlagszeit ab.")
                except Exception:
                    pass
            if past_count:
                errors.append(f"{label}: {past_count} Welle{'n' if past_count>1 else ''} mit Sendezeit in der Vergangenheit!")
                target_ok = False
            if no_send_count:
                warnings.append(f"{label}: {no_send_count} Welle{'n' if no_send_count>1 else ''} ohne Sendezeit (Koordinaten fehlen?).")
                suggestions.append(f"{label}: Koordinaten der Angreifer-Dörfer im Wellen-Dialog nachtragen.")
                target_ok = False

        if target_ok:
            targets_good.append(label)

    if targets_no_waves:
        warnings.append(f"{len(targets_no_waves)} Ziel{'e' if len(targets_no_waves)!=1 else ''} ohne Wellen: " + ", ".join(targets_no_waves[:3]) + ("…" if len(targets_no_waves)>3 else ""))

    if targets_good:
        ok_items.append(f"{len(targets_good)} Ziel{'e' if len(targets_good)!=1 else ''} vollständig geplant ✅")

    # ── Cross-plan: double-booking ──────────────────────────────────────────
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT w.attacker_name, w.send_time, w.wave_type, t.x, t.y
            FROM op_waves w JOIN op_targets t ON t.id = w.target_id
            WHERE w.plan_id=? AND w.guild_id=?
            ORDER BY w.attacker_name, w.send_time
        """, (plan_id, guild_id)) as cur:
            all_waves = [dict(r) for r in await cur.fetchall()]

    attacker_times: dict[str, list] = {}
    for w in all_waves:
        attacker_times.setdefault(w["attacker_name"], []).append(w)

    double_booked = []
    for attacker, awt in attacker_times.items():
        awt_s = sorted(awt, key=lambda x: x.get("send_time") or "")
        for i in range(len(awt_s) - 1):
            t1, t2 = awt_s[i].get("send_time",""), awt_s[i+1].get("send_time","")
            if t1 and t2:
                try:
                    if abs((_dt.datetime.fromisoformat(t2) - _dt.datetime.fromisoformat(t1)).total_seconds()) < 30:
                        double_booked.append(attacker)
                        break
                except Exception:
                    pass

    if double_booked:
        warnings.append(f"Möglicherweise doppelt verplant (< 30s Abstand): {', '.join(double_booked[:4])}")
        suggestions.append("Sendezeiten dieser Angreifer prüfen — ggf. verschiedene Dörfer oder Zeiten nutzen.")
    elif attacker_times:
        ok_items.append(f"Kein Angreifer doppelt verplant ({len(attacker_times)} Angreifer).")

    # ── Zwei-Dorf-Fake-Check: mehrere Fakes vom selben Dorf mit gleicher Ankunftszeit ──
    fake_waves_all = [w for w in all_waves if w.get("wave_type") == "fake"]
    # Group by (attacker_name, target_x, target_y, send_time)
    from collections import Counter as _Counter
    fake_key_counts = _Counter(
        (w["attacker_name"], w["x"], w["y"], (w.get("send_time") or "")[:16])
        for w in fake_waves_all
        if w.get("send_time")
    )
    for (attacker, tx, ty, stime), cnt in fake_key_counts.items():
        if cnt >= 2:
            warnings.append(
                f"🔺 Zwei-Dorf-Fake: {attacker} hat {cnt}× exakt dieselbe Fake-Welle auf {tx}|{ty} (Sendezeit {stime}) — "
                f"Verteidiger erkennt gleiche Ankunftszeit!"
            )
            suggestions.append(
                f"{attacker}: Fake-Wellen auf {tx}|{ty} aus verschiedenen Dörfern schicken oder Zeiten minimal verschieben."
            )

    return {
        "ok": ok_items,
        "warnings": warnings,
        "errors": errors,
        "suggestions": suggestions,
        "summary": _check_summary(ok_items, warnings, errors, suggestions),
    }


def _check_summary(ok, warnings, errors, suggestions) -> dict:
    score = len(ok)
    total = len(ok) + len(warnings) + len(errors)
    pct = int(score / total * 100) if total else 100
    if errors:
        grade = "error"
    elif warnings:
        grade = "warning"
    else:
        grade = "ok"
    return {"ok": len(ok), "warnings": len(warnings), "errors": len(errors),
            "suggestions": len(suggestions), "score_pct": pct, "grade": grade}


# ── Notifications ─────────────────────────────────────────────────────────────

async def create_notifications(guild_id: str, ally_group_id: int | None,
                                recipient_ids: list[str], notif_type: str,
                                title: str, message: str, plan_id: int | None = None):
    """Create a notification for each recipient_id."""
    if not recipient_ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        for rid in recipient_ids:
            await db.execute("""
                INSERT INTO notifications (guild_id, ally_group_id, recipient_id, type, title, message, plan_id)
                VALUES (?,?,?,?,?,?,?)
            """, (guild_id, ally_group_id, rid, notif_type, title[:200], message[:1000], plan_id))
        await db.commit()


async def get_ep_notify_members(guild_id: str) -> list[str]:
    """Return discord_ids of approved members whose role has 'ep_notify' permission."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT am.discord_id
            FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            JOIN ally_roles ar ON ar.id = am.role_id
            WHERE ag.guild_id=? AND am.status='approved'
              AND (ar.permissions LIKE '%ep_notify%' OR ag.owner_discord_id = am.discord_id)
        """, (guild_id,)) as cur:
            rows = await cur.fetchall()
        # Also always include the group owner
        async with db.execute(
            "SELECT owner_discord_id FROM ally_groups WHERE guild_id=?", (guild_id,)
        ) as cur:
            owner = await cur.fetchone()
    ids = list({r["discord_id"] for r in rows})
    if owner and owner[0] and owner[0] not in ids:
        ids.append(owner[0])
    # Expand to include dual partners of each notified member
    expanded = set(ids)
    for discord_id in list(ids):
        partners = await get_dual_partners(discord_id)
        expanded.update(partners)
    return list(expanded)


async def get_notifications(guild_id: str, discord_id: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM notifications WHERE guild_id=? AND recipient_id=?"
        params: list = [guild_id, discord_id]
        if unread_only:
            q += " AND read=0"
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with db.execute(q, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def count_unread_notifications(guild_id: str, discord_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM notifications WHERE guild_id=? AND recipient_id=? AND read=0",
            (guild_id, discord_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def mark_notifications_read(guild_id: str, discord_id: str, notif_ids: list[int] | None = None):
    """Mark specific notifications (or all) as read for this user."""
    async with aiosqlite.connect(DB_PATH) as db:
        if notif_ids:
            placeholders = ",".join("?" * len(notif_ids))
            await db.execute(
                f"UPDATE notifications SET read=1 WHERE guild_id=? AND recipient_id=? AND id IN ({placeholders})",
                [guild_id, discord_id] + notif_ids
            )
        else:
            await db.execute(
                "UPDATE notifications SET read=1 WHERE guild_id=? AND recipient_id=?",
                (guild_id, discord_id)
            )
        await db.commit()


# ── EP Poll integration ───────────────────────────────────────────────────────

async def create_ep_poll(guild_id: str, plan_id: int, title: str, description: str, event_datetime: str) -> int:
    """Create a poll linked to an EP plan."""
    from datetime import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO availability_polls (guild_id, plan_id, title, description, event_datetime, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, plan_id, title, description, event_datetime, _dt.utcnow().isoformat()))
        await db.commit()
        return cur.lastrowid


async def get_ep_poll(guild_id: str, plan_id: int) -> dict | None:
    """Return the most recent active poll for this EP plan."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM availability_polls WHERE guild_id=? AND plan_id=?
            ORDER BY created_at DESC LIMIT 1
        """, (guild_id, plan_id)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_ep_poll_availability(guild_id: str, plan_id: int) -> dict[str, str]:
    """Return {discord_user_id: response} for the EP's linked poll."""
    poll = await get_ep_poll(guild_id, plan_id)
    if not poll:
        return {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, response FROM poll_responses WHERE poll_id=?", (poll["id"],)
        ) as cur:
            rows = await cur.fetchall()
    return {r["user_id"]: r["response"] for r in rows}


# ── EP wave time recalculation ────────────────────────────────────────────────

async def recalc_op_wave_times(plan_id: int, guild_id: str) -> int:
    """Recompute send_time for all waves in a plan from its current landing_time. Returns updated count."""
    import datetime as _dt
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT landing_time, server_speed FROM op_plans WHERE id=? AND guild_id=?",
                              (plan_id, guild_id)) as cur:
            plan_row = await cur.fetchone()
        if not plan_row or not plan_row["landing_time"]:
            return 0
        landing_time = plan_row["landing_time"]
        server_speed = float(plan_row["server_speed"] or 1.0)
        try:
            lt = _dt.datetime.fromisoformat(landing_time.replace("Z", ""))
        except Exception:
            return 0
        async with db.execute("""
            SELECT w.id, w.origin_x, w.origin_y, w.slowest_speed, t.x AS tx, t.y AS ty
            FROM op_waves w JOIN op_targets t ON t.id = w.target_id
            WHERE w.plan_id=? AND w.guild_id=?
        """, (plan_id, guild_id)) as cur:
            waves = [dict(r) for r in await cur.fetchall()]
        updated = 0
        for w in waves:
            if w["origin_x"] is None or w["origin_y"] is None or not w["slowest_speed"]:
                continue
            travel_sec = _calc_travel_seconds(
                w["origin_x"], w["origin_y"], w["tx"], w["ty"],
                float(w["slowest_speed"]), server_speed
            )
            send_dt = lt - _dt.timedelta(seconds=travel_sec)
            send_time = send_dt.strftime("%Y-%m-%dT%H:%M:%S")
            await db.execute(
                "UPDATE op_waves SET send_time=?, travel_seconds=?, arrival_time=? WHERE id=?",
                (send_time, travel_sec, landing_time, w["id"])
            )
            updated += 1
        await db.commit()
    return updated


# ── Admin Ideas ───────────────────────────────────────────────────────────────

async def init_ideas_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_ideas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT DEFAULT '',
                category    TEXT DEFAULT 'general',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.commit()
        # Seed default ideas once
        async with db.execute("SELECT COUNT(*) FROM admin_ideas") as cur:
            count = (await cur.fetchone())[0]
        if count == 0:
            defaults = [
                ("EP-Vorlagen (Templates)", "Plan-Struktur ohne Wellen als Vorlage speichern und für zukünftige Ops wiederverwenden.", "ep"),
                ("Wellen Bulk-Import per Text", "Textbox zum Einfügen einer Angreifer-Liste (Name, Dorf, Typ, Sendezeit) aus Discord — alles auf einmal importieren.", "ep"),
                ("Mobile App / PWA", "Native App oder Progressive Web App für TravOps. Aktuell zu kostenintensiv, aber für die Zukunft sinnvoll.", "general"),
                ("Angreifer-Zuverlässigkeits-Score", "Über alle EPs hinweg tracken: wer hat immer pünktlich bestätigt, wer fällt oft aus? Score in der Allianz-Ansicht anzeigen.", "ep"),
                ("Minimap der EP-Ziele", "Einfache Canvas-Visualisierung der Zielkoordinaten — sieht man sofort ob alle Ziele im gleichen Bereich liegen.", "ep"),
                ("Sendezeit-Alarm via Discord-DM", "Bot schickt eine DM X Minuten vor der Sendezeit: 'Denk daran: Deine Welle geht um 14:32 ab!'", "integration"),
                ("Karte: Heatmap-Modus", "Allianzen oder Spieler eingeben → Heatmap der Aktivität auf der Travian-Karte erstellen.", "ux"),
            ]
            for t, d, c in defaults:
                await db.execute("INSERT INTO admin_ideas (title, description, category) VALUES (?,?,?)", (t, d, c))
            await db.commit()


async def create_idea(title: str, description: str, category: str) -> int:
    await init_ideas_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO admin_ideas (title, description, category) VALUES (?,?,?)",
            (title[:120], description[:600], category or "general")
        )
        await db.commit()
        return cur.lastrowid


async def get_ideas() -> list[dict]:
    await init_ideas_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM admin_ideas ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_idea(idea_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_ideas WHERE id=?", (idea_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Attack Detection — incoming_attacks + enemy_artifacts
# ---------------------------------------------------------------------------

async def _init_attack_detection_tables():
    """Create incoming_attacks and enemy_artifacts tables if not yet present."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS incoming_attacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                imported_by_discord_id TEXT DEFAULT '',
                imported_by_name TEXT DEFAULT '',
                import_time TEXT NOT NULL,
                server_time_at_import TEXT DEFAULT '',
                own_village_name TEXT NOT NULL,
                own_village_x INTEGER,
                own_village_y INTEGER,
                attacker_player TEXT NOT NULL,
                attacker_village_name TEXT DEFAULT '',
                attacker_x INTEGER,
                attacker_y INTEGER,
                attack_type TEXT DEFAULT 'attack',
                troops_hidden INTEGER DEFAULT 0,
                troop_count INTEGER DEFAULT 0,
                troop_details TEXT DEFAULT '{}',
                arrival_time TEXT NOT NULL,
                fake_score INTEGER DEFAULT 50,
                fake_reasons TEXT DEFAULT '[]',
                is_dismissed INTEGER DEFAULT 0,
                label TEXT DEFAULT '',
                labeled_by TEXT DEFAULT '',
                labeled_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_incoming_attacks_guild
            ON incoming_attacks(guild_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_incoming_attacks_arrival
            ON incoming_attacks(arrival_time)
        """)
        # Migration: add label columns if missing
        for col, default in [("label","''"), ("labeled_by","''"), ("labeled_at","''")]:
            try:
                await db.execute(f"ALTER TABLE incoming_attacks ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass
        # Unique index so duplicate imports are silently skipped (INSERT OR IGNORE)
        try:
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_incoming_attacks_unique
                ON incoming_attacks(guild_id, own_village_x, own_village_y,
                                    attacker_x, attacker_y, arrival_time)
            """)
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                village_x INTEGER,
                village_y INTEGER,
                artifact_type TEXT NOT NULL,
                artifact_size TEXT NOT NULL,
                confirmed INTEGER DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, player_name, village_x, village_y,
                       artifact_type, artifact_size)
            )
        """)
        await db.commit()


async def save_incoming_attacks(guild_id: str, attacks: list[dict],
                                 imported_by_discord_id: str = "",
                                 imported_by_name: str = "") -> int:
    import json as _json
    await _init_attack_detection_tables()
    now = __import__('datetime').datetime.utcnow().isoformat()
    saved = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for atk in attacks:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO incoming_attacks (
                        guild_id, imported_by_discord_id, imported_by_name, import_time,
                        server_time_at_import,
                        own_village_name, own_village_x, own_village_y,
                        attacker_player, attacker_village_name, attacker_x, attacker_y,
                        attack_type, troops_hidden, troop_count, troop_details,
                        arrival_time, fake_score, fake_reasons
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    guild_id,
                    imported_by_discord_id,
                    imported_by_name,
                    now,
                    atk.get("server_time_at_import", ""),
                    atk.get("own_village_name", ""),
                    atk.get("own_x"),
                    atk.get("own_y"),
                    atk.get("attacker_player", ""),
                    atk.get("attacker_village_name", ""),
                    atk.get("attacker_x"),
                    atk.get("attacker_y"),
                    atk.get("attack_type", "attack"),
                    1 if atk.get("troops_hidden") else 0,
                    atk.get("troop_count", 0),
                    _json.dumps(atk.get("troop_details", {})),
                    atk.get("arrival_time", ""),
                    atk.get("fake_score", 50),
                    _json.dumps(atk.get("fake_reasons", [])),
                ))
                saved += 1
            except Exception:
                pass
        await db.commit()
    return saved


async def get_incoming_attacks(guild_id: str, own_x: int = None, own_y: int = None,
                                limit: int = 200) -> list[dict]:
    await _init_attack_detection_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if own_x is not None and own_y is not None:
            async with db.execute(
                """SELECT * FROM incoming_attacks
                   WHERE guild_id=? AND own_village_x=? AND own_village_y=? AND is_dismissed=0
                   ORDER BY arrival_time ASC LIMIT ?""",
                (guild_id, own_x, own_y, limit)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                """SELECT * FROM incoming_attacks
                   WHERE guild_id=? AND is_dismissed=0
                   ORDER BY arrival_time ASC LIMIT ?""",
                (guild_id, limit)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]


async def get_incoming_attacks_alliance(guild_id: str, limit: int = 500) -> list[dict]:
    await _init_attack_detection_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM incoming_attacks WHERE guild_id=? AND is_dismissed=0
               ORDER BY arrival_time ASC LIMIT ?""",
            (guild_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def label_attack(attack_id: int, guild_id: str, label: str, labeled_by: str) -> bool:
    """Set label on an attack: 'fake' | 'hard' | 'low' | '' (clear).
    Returns True if row was found and updated."""
    import datetime as _dt
    await _init_attack_detection_tables()
    valid = {"fake", "hard", "low", ""}
    if label not in valid:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """UPDATE incoming_attacks
               SET label=?, labeled_by=?, labeled_at=?
               WHERE id=? AND guild_id=?""",
            (label, labeled_by, _dt.datetime.utcnow().isoformat(), attack_id, guild_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def dismiss_attack(attack_id: int, guild_id: str):
    await _init_attack_detection_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE incoming_attacks SET is_dismissed=1 WHERE id=? AND guild_id=?",
            (attack_id, guild_id)
        )
        await db.commit()


async def get_enemy_artifacts(guild_id: str, player_name: str) -> list[dict]:
    await _init_attack_detection_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM enemy_artifacts WHERE guild_id=? AND lower(player_name)=lower(?)
               ORDER BY artifact_size, artifact_type""",
            (guild_id, player_name)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def toggle_enemy_artifact(guild_id: str, player_name: str,
                                 village_x, village_y,
                                 artifact_type: str, artifact_size: str) -> bool:
    """Toggle artifact: insert if missing, delete if present. Returns True if now active."""
    await _init_attack_detection_tables()

# ── Sektor-Überwachung ────────────────────────────────────────────────────────

async def _init_sector_monitor_tables():
    """Create sector_monitors and sector_alerts tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sector_monitors (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL UNIQUE,
                enabled     INTEGER DEFAULT 1,
                x1 INTEGER DEFAULT -50, y1 INTEGER DEFAULT -50,
                x2 INTEGER DEFAULT  50, y2 INTEGER DEFAULT  50,
                watch_new_village  INTEGER DEFAULT 1,
                watch_nobling      INTEGER DEFAULT 1,
                watch_fast_growth  INTEGER DEFAULT 1,
                growth_threshold   INTEGER DEFAULT 200,
                nobling_threshold  INTEGER DEFAULT 500,
                sectors     TEXT DEFAULT '',
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sector_alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                alert_type   TEXT NOT NULL,
                x            INTEGER NOT NULL,
                y            INTEGER NOT NULL,
                village_name TEXT DEFAULT '',
                player_name  TEXT DEFAULT '',
                alliance_name TEXT DEFAULT '',
                population   INTEGER DEFAULT 0,
                pop_change   INTEGER DEFAULT 0,
                detected_at  TEXT DEFAULT (datetime('now')),
                dismissed    INTEGER DEFAULT 0
            )
        """)
        await db.commit()
        try:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sector_alerts_guild ON sector_alerts(guild_id, dismissed)")
            await db.commit()
        except Exception:
            pass
        # Migration: add sectors column if missing
        try:
            await db.execute("ALTER TABLE sector_monitors ADD COLUMN sectors TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass


async def get_sector_monitor(guild_id: str) -> dict | None:
    """Return sector monitor config for a guild, or None."""
    await _init_sector_monitor_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sector_monitors WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_sector_monitor(guild_id: str, **fields) -> None:
    """Insert or replace sector monitor config, always updating updated_at."""
    await _init_sector_monitor_tables()
    # Get existing or defaults
    existing = await get_sector_monitor(guild_id) or {
        "enabled": 1,
        "x1": -50, "y1": -50, "x2": 50, "y2": 50,
        "watch_new_village": 1, "watch_nobling": 1, "watch_fast_growth": 1,
        "growth_threshold": 200, "nobling_threshold": 500,
    }
    existing.update(fields)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO sector_monitors
                (guild_id, enabled, x1, y1, x2, y2,
                 watch_new_village, watch_nobling, watch_fast_growth,
                 growth_threshold, nobling_threshold, sectors, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (
            guild_id,
            int(existing.get("enabled", 1)),
            int(existing.get("x1", -50)), int(existing.get("y1", -50)),
            int(existing.get("x2", 50)),  int(existing.get("y2", 50)),
            int(existing.get("watch_new_village", 1)),
            int(existing.get("watch_nobling", 1)),
            int(existing.get("watch_fast_growth", 1)),
            int(existing.get("growth_threshold", 200)),
            int(existing.get("nobling_threshold", 500)),
            str(existing.get("sectors", "")),
        ))
        await db.commit()


async def get_sector_alerts(guild_id: str, include_dismissed: bool = False, limit: int = 100) -> list[dict]:
    """Return sector alerts for a guild."""
    await _init_sector_monitor_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if include_dismissed:
            q = "SELECT * FROM sector_alerts WHERE guild_id=? ORDER BY detected_at DESC LIMIT ?"
            params = (guild_id, limit)
        else:
            q = "SELECT * FROM sector_alerts WHERE guild_id=? AND dismissed=0 ORDER BY detected_at DESC LIMIT ?"
            params = (guild_id, limit)
        async with db.execute(q, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def dismiss_sector_alert(guild_id: str, alert_id: int) -> None:
    """Dismiss a single sector alert."""
    await _init_sector_monitor_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sector_alerts SET dismissed=1 WHERE id=? AND guild_id=?",
            (alert_id, guild_id)
        )
        await db.commit()


async def dismiss_all_sector_alerts(guild_id: str) -> None:
    """Dismiss all active sector alerts for a guild."""
    await _init_sector_monitor_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sector_alerts SET dismissed=1 WHERE guild_id=? AND dismissed=0",
            (guild_id,)
        )
        await db.commit()


async def run_sector_scan(guild_id: str) -> list[dict]:
    """Compare two most recent map snapshots, detect changes in bounding box, create alerts.

    Returns list of newly created alert dicts.
    """
    await _init_sector_monitor_tables()
    monitor = await get_sector_monitor(guild_id)
    if not monitor or not monitor.get("enabled"):
        return []

    x1, y1, x2, y2 = monitor["x1"], monitor["y1"], monitor["x2"], monitor["y2"]
    nobling_thr = monitor.get("nobling_threshold", 500)
    growth_thr  = monitor.get("growth_threshold", 200)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get two most recent distinct fetched_at timestamps
        async with db.execute(
            """SELECT DISTINCT fetched_at FROM map_snapshots
               WHERE guild_id=? ORDER BY fetched_at DESC LIMIT 2""",
            (guild_id,)
        ) as cur:
            ts_rows = await cur.fetchall()

    if len(ts_rows) < 2:
        return []

    ts_new = ts_rows[0]["fetched_at"]
    ts_old = ts_rows[1]["fetched_at"]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Load villages in bounding box for both snapshots
        async with db.execute(
            """SELECT village_id, x, y, village_name, player_name, alliance_name, population
               FROM map_snapshots
               WHERE guild_id=? AND fetched_at=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?""",
            (guild_id, ts_new, x1, x2, y1, y2)
        ) as cur:
            new_rows = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            """SELECT village_id, x, y, village_name, player_name, alliance_name, population
               FROM map_snapshots
               WHERE guild_id=? AND fetched_at=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?""",
            (guild_id, ts_old, x1, x2, y1, y2)
        ) as cur:
            old_rows = [dict(r) for r in await cur.fetchall()]

        # Get all meta-alliance names for this guild
        async with db.execute(
            """SELECT LOWER(amm.alliance_name) as aname
               FROM alliance_meta_members amm
               JOIN alliance_meta_groups amg ON amg.id = amm.group_id
               WHERE amg.guild_id=?""",
            (guild_id,)
        ) as cur:
            meta_rows = await cur.fetchall()

    meta_alliance_names = {r["aname"] for r in meta_rows}

    # Filter by meta-alliance membership
    def in_meta(row):
        return (row.get("alliance_name") or "").lower() in meta_alliance_names

    new_filtered = [r for r in new_rows if in_meta(r)]
    old_filtered = [r for r in old_rows if in_meta(r)]

    old_by_id = {r["village_id"]: r for r in old_filtered}
    new_by_id = {r["village_id"]: r for r in new_filtered}

    new_alerts: list[dict] = []

    for vid, nv in new_by_id.items():
        alert_type = None
        pop_change = 0

        if monitor.get("watch_new_village") and vid not in old_by_id:
            alert_type = "new_village"
        elif vid in old_by_id:
            diff = nv["population"] - old_by_id[vid]["population"]
            if diff >= nobling_thr and monitor.get("watch_nobling"):
                alert_type = "nobling"
                pop_change = diff
            elif diff >= growth_thr and monitor.get("watch_fast_growth"):
                alert_type = "fast_growth"
                pop_change = diff

        if not alert_type:
            continue

        # Skip duplicate: same guild+x+y+alert_type within 1 hour
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT id FROM sector_alerts
                   WHERE guild_id=? AND x=? AND y=? AND alert_type=?
                   AND detected_at > datetime('now', '-1 hour')""",
                (guild_id, nv["x"], nv["y"], alert_type)
            ) as cur:
                dup = await cur.fetchone()
            if dup:
                continue

            await db.execute(
                """INSERT INTO sector_alerts
                   (guild_id, alert_type, x, y, village_name, player_name, alliance_name, population, pop_change)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    guild_id, alert_type, nv["x"], nv["y"],
                    nv.get("village_name", ""), nv.get("player_name", ""),
                    nv.get("alliance_name", ""), nv.get("population", 0), pop_change,
                )
            )
            await db.commit()

            async with db.execute(
                "SELECT * FROM sector_alerts WHERE rowid=last_insert_rowid()"
            ) as cur:
                db.row_factory = aiosqlite.Row
                pass
            async with db.execute(
                "SELECT * FROM sector_alerts WHERE guild_id=? ORDER BY id DESC LIMIT 1",
                (guild_id,)
            ) as cur:
                db.row_factory = aiosqlite.Row
                inserted = await cur.fetchone()
                if inserted:
                    new_alerts.append(dict(inserted))

    # Send dashboard notifications to guild leaders
    if new_alerts:
        try:
            lead_ids = await get_ep_notify_members(guild_id)
            if lead_ids:
                type_labels = {"new_village": "Neues Dorf", "nobling": "Adelung", "fast_growth": "Wachstum"}
                types_found = list({a["alert_type"] for a in new_alerts})
                label = " & ".join(type_labels.get(t, t) for t in types_found)
                await create_notifications(
                    guild_id=guild_id,
                    ally_group_id=None,
                    recipient_ids=lead_ids,
                    notif_type="sector_alert",
                    title=f"🗺️ Sektor-Alert: {label}",
                    message=f"{len(new_alerts)} neue Aktivität(en) in deinem überwachten Sektor erkannt.",
                    plan_id=None,
                )
        except Exception:
            pass

    return new_alerts
    async with aiosqlite.connect(DB_PATH) as db:
        vx = village_x if village_x is not None else None
        vy = village_y if village_y is not None else None
        async with db.execute(
            """SELECT id FROM enemy_artifacts
               WHERE guild_id=? AND lower(player_name)=lower(?)
               AND COALESCE(village_x,-999)=COALESCE(?,-999)
               AND COALESCE(village_y,-999)=COALESCE(?,-999)
               AND artifact_type=? AND artifact_size=?""",
            (guild_id, player_name, vx, vy, artifact_type, artifact_size)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            await db.execute("DELETE FROM enemy_artifacts WHERE id=?", (existing[0],))
            await db.commit()
            return False
        else:
            await db.execute(
                """INSERT INTO enemy_artifacts
                   (guild_id, player_name, village_x, village_y, artifact_type, artifact_size)
                   VALUES (?,?,?,?,?,?)""",
                (guild_id, player_name, vx, vy, artifact_type, artifact_size)
            )
            await db.commit()
            return True


# ---------------------------------------------------------------------------
# Scout Incidents (enemy scouted our member)
# ---------------------------------------------------------------------------

async def _init_scout_incidents_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scout_incidents (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          TEXT NOT NULL,
                reported_by_id    TEXT NOT NULL,
                reported_by_name  TEXT NOT NULL,
                victim_player     TEXT NOT NULL,
                victim_village    TEXT DEFAULT '',
                victim_coords     TEXT DEFAULT '',
                enemy_player      TEXT NOT NULL,
                enemy_village     TEXT DEFAULT '',
                scout_time        TEXT DEFAULT '',
                notes             TEXT DEFAULT '',
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.commit()


async def add_scout_incident(
    guild_id: str, reported_by_id: str, reported_by_name: str,
    victim_player: str, victim_village: str, victim_coords: str,
    enemy_player: str, enemy_village: str, scout_time: str, notes: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO scout_incidents
                (guild_id, reported_by_id, reported_by_name,
                 victim_player, victim_village, victim_coords,
                 enemy_player, enemy_village, scout_time, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (guild_id, reported_by_id, reported_by_name,
              victim_player, victim_village, victim_coords,
              enemy_player, enemy_village, scout_time, notes))
        await db.commit()
        return cur.lastrowid


async def get_scout_incidents(guild_id: str, enemy_filter: str = "", limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if enemy_filter:
            cur = await db.execute("""
                SELECT * FROM scout_incidents
                WHERE guild_id=? AND LOWER(enemy_player) LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """, (guild_id, f"%{enemy_filter.lower()}%", limit))
        else:
            cur = await db.execute("""
                SELECT * FROM scout_incidents WHERE guild_id=?
                ORDER BY created_at DESC LIMIT ?
            """, (guild_id, limit))
        return [dict(r) for r in await cur.fetchall()]


async def get_scout_incident_stats(guild_id: str) -> list[dict]:
    """Return enemy players ranked by scout count (last 30 days)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT enemy_player, COUNT(*) as cnt,
                   MAX(created_at) as last_seen
            FROM scout_incidents
            WHERE guild_id=? AND created_at >= datetime('now', '-30 days')
            GROUP BY LOWER(enemy_player)
            ORDER BY cnt DESC
            LIMIT 20
        """, (guild_id,))
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Ally Bonus Order
# ---------------------------------------------------------------------------

async def _init_ally_bonus_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ally_bonuses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ally_group_id INTEGER NOT NULL,
                position      INTEGER DEFAULT 0,
                name          TEXT NOT NULL,
                max_level     INTEGER DEFAULT 20,
                current_level INTEGER DEFAULT 0,
                description   TEXT DEFAULT '',
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()


async def get_ally_bonuses(ally_group_id: int) -> list[dict]:
    await _init_ally_bonus_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ally_bonuses WHERE ally_group_id=? ORDER BY position, id",
            (ally_group_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def add_ally_bonus(ally_group_id: int, name: str, max_level: int, description: str) -> int:
    await _init_ally_bonus_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM ally_bonuses WHERE ally_group_id=?",
            (ally_group_id,)
        )
        pos = (await cur.fetchone())[0]
        cur = await db.execute(
            "INSERT INTO ally_bonuses (ally_group_id, position, name, max_level, description) VALUES (?,?,?,?,?)",
            (ally_group_id, pos, name[:60], max_level, description[:200])
        )
        await db.commit()
        return cur.lastrowid


async def update_ally_bonus(bonus_id: int, ally_group_id: int,
                             name: str, max_level: int, current_level: int, description: str):
    await _init_ally_bonus_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE ally_bonuses SET name=?, max_level=?, current_level=?, description=?
            WHERE id=? AND ally_group_id=?
        """, (name[:60], max_level, current_level, description[:200], bonus_id, ally_group_id))
        await db.commit()


async def delete_ally_bonus(bonus_id: int, ally_group_id: int):
    await _init_ally_bonus_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM ally_bonuses WHERE id=? AND ally_group_id=?",
                         (bonus_id, ally_group_id))
        await db.commit()


async def reorder_ally_bonuses(ally_group_id: int, ordered_ids: list[int]):
    """Set position = index for each bonus id in the given order."""
    await _init_ally_bonus_table()
    async with aiosqlite.connect(DB_PATH) as db:
        for pos, bid in enumerate(ordered_ids):
            await db.execute(
                "UPDATE ally_bonuses SET position=? WHERE id=? AND ally_group_id=?",
                (pos, bid, ally_group_id)
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Enemy Troop Entries (manual troop records with history)
# ---------------------------------------------------------------------------

async def _init_enemy_troops_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_troop_entries (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id       TEXT NOT NULL,
                player_name    TEXT NOT NULL,
                off_troops     INTEGER DEFAULT 0,
                def_troops     INTEGER DEFAULT 0,
                total_troops   INTEGER DEFAULT 0,
                notes          TEXT DEFAULT '',
                reported_by    TEXT DEFAULT '',
                entry_time     TEXT DEFAULT '',
                troop_details  TEXT DEFAULT '',
                village_name   TEXT DEFAULT '',
                created_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        # migrations
        for col in ["troop_details TEXT DEFAULT ''", "village_name TEXT DEFAULT ''"]:
            try:
                await db.execute(f"ALTER TABLE enemy_troop_entries ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()


async def add_enemy_troop_entry(
    guild_id: str, player_name: str,
    off_troops: int, def_troops: int, total_troops: int,
    notes: str, reported_by: str, entry_time: str,
    troop_details: str = "", village_name: str = "",
) -> int:
    await _init_enemy_troops_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO enemy_troop_entries
                (guild_id, player_name, off_troops, def_troops, total_troops,
                 notes, reported_by, entry_time, troop_details, village_name)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (guild_id, player_name, off_troops, def_troops, total_troops,
              notes, reported_by, entry_time, troop_details or "", village_name or ""))
        await db.commit()
        return cur.lastrowid


async def get_enemy_troop_entries(guild_id: str, player_name: str) -> list[dict]:
    await _init_enemy_troops_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM enemy_troop_entries
            WHERE guild_id=? AND player_name=?
            ORDER BY created_at DESC
        """, (guild_id, player_name))
        return [dict(r) for r in await cur.fetchall()]


async def delete_enemy_troop_entry(entry_id: int, guild_id: str):
    await _init_enemy_troops_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM enemy_troop_entries WHERE id=? AND guild_id=?",
                         (entry_id, guild_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Travian Statistics Snapshots
# ---------------------------------------------------------------------------

async def _init_travian_stats_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS travian_stats_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                imported_by  TEXT DEFAULT '',
                snapshot_at  TEXT NOT NULL,
                raw_text     TEXT DEFAULT '',
                row_count    INTEGER DEFAULT 0,
                stats_type   TEXT DEFAULT 'player',
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS travian_stats_entries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id   INTEGER NOT NULL REFERENCES travian_stats_snapshots(id) ON DELETE CASCADE,
                guild_id      TEXT NOT NULL,
                snapshot_at   TEXT NOT NULL,
                player_name   TEXT NOT NULL,
                alliance_name TEXT DEFAULT '',
                population    INTEGER DEFAULT 0,
                off_points    INTEGER DEFAULT 0,
                def_points    INTEGER DEFAULT 0,
                raid_points   INTEGER DEFAULT 0,
                off_rank      INTEGER DEFAULT 0,
                def_rank      INTEGER DEFAULT 0,
                raid_rank     INTEGER DEFAULT 0,
                pop_rank      INTEGER DEFAULT 0,
                pve_points    INTEGER DEFAULT 0,
                pve_rank      INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_stats_entries_guild_player
            ON travian_stats_entries(guild_id, player_name, snapshot_at)
        """)
        # Migrations for existing installs
        for col in ["pve_points INTEGER DEFAULT 0", "pve_rank INTEGER DEFAULT 0"]:
            try:
                await db.execute(f"ALTER TABLE travian_stats_entries ADD COLUMN {col}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE travian_stats_snapshots ADD COLUMN stats_type TEXT DEFAULT 'player'")
        except Exception:
            pass
        await db.commit()


async def save_stats_snapshot(guild_id: str, imported_by: str,
                               snapshot_at: str, raw_text: str,
                               entries: list[dict], stats_type: str = 'player') -> int:
    await _init_travian_stats_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO travian_stats_snapshots
                (guild_id, imported_by, snapshot_at, raw_text, row_count, stats_type)
            VALUES (?,?,?,?,?,?)
        """, (guild_id, imported_by, snapshot_at, raw_text, len(entries), stats_type))
        snap_id = cur.lastrowid
        for e in entries:
            await db.execute("""
                INSERT INTO travian_stats_entries
                    (snapshot_id, guild_id, snapshot_at, player_name, alliance_name,
                     population, off_points, def_points, raid_points,
                     off_rank, def_rank, raid_rank, pop_rank,
                     pve_points, pve_rank)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (snap_id, guild_id, snapshot_at,
                  e.get("player_name",""), e.get("alliance_name",""),
                  e.get("population",0), e.get("off_points",0),
                  e.get("def_points",0), e.get("raid_points",0),
                  e.get("off_rank",0), e.get("def_rank",0),
                  e.get("raid_rank",0), e.get("pop_rank",0),
                  e.get("pve_points",0), e.get("pve_rank",0)))
        await db.commit()
        return snap_id


async def get_stats_snapshots(guild_id: str) -> list[dict]:
    await _init_travian_stats_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM travian_stats_snapshots
            WHERE guild_id=? ORDER BY created_at DESC
        """, (guild_id,))
        return [dict(r) for r in await cur.fetchall()]


async def delete_stats_snapshot(snapshot_id: int, guild_id: str):
    await _init_travian_stats_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM travian_stats_snapshots WHERE id=? AND guild_id=?",
            (snapshot_id, guild_id)
        )
        await db.commit()


async def get_stats_trend_data(guild_id: str, limit_snapshots: int = 20,
                               stats_type: str = 'player') -> dict:
    """
    Returns per-player timeline data for trend analysis.
    Each player gets a list of {snapshot_at, off_points, def_points, raid_points,
                                 pve_points, population, off_rank, raid_rank,
                                 off_rate, raid_rate, def_rate, pve_rate}
    Rates are points-per-snapshot between this and the previous snapshot.
    """
    await _init_travian_stats_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT id, snapshot_at FROM travian_stats_snapshots
            WHERE guild_id=? AND stats_type=? ORDER BY snapshot_at DESC LIMIT ?
        """, (guild_id, stats_type, limit_snapshots))
        snaps = [dict(r) for r in await cur.fetchall()]
        if not snaps:
            return {}

        snap_ids = [s["id"] for s in snaps]
        placeholders = ",".join("?" * len(snap_ids))
        cur = await db.execute(f"""
            SELECT * FROM travian_stats_entries
            WHERE guild_id=? AND snapshot_id IN ({placeholders})
            ORDER BY player_name, snapshot_at ASC
        """, (guild_id, *snap_ids))
        rows = [dict(r) for r in await cur.fetchall()]

    # Group by player
    from collections import defaultdict
    from datetime import datetime as _dt
    players: dict[str, list] = defaultdict(list)
    for r in rows:
        players[r["player_name"]].append(r)

    # Calculate rates (pts/hour) between consecutive snapshots
    result = {}
    for pname, entries in players.items():
        entries.sort(key=lambda x: x["snapshot_at"])
        timeline = []
        for i, e in enumerate(entries):
            point = {
                "snapshot_at": e["snapshot_at"],
                "off_points":  e.get("off_points", 0),
                "def_points":  e.get("def_points", 0),
                "raid_points": e.get("raid_points", 0),
                "pve_points":  e.get("pve_points", 0),
                "population":  e.get("population", 0),
                "off_rank":    e.get("off_rank", 0),
                "raid_rank":   e.get("raid_rank", 0),
                "def_rank":    e.get("def_rank", 0),
                "pve_rank":    e.get("pve_rank", 0),
                # Deltas vs previous snapshot (week-over-week change)
                "off_delta": None, "def_delta": None,
                "raid_delta": None, "pve_delta": None,
                "hours_since_prev": None,
            }
            if i > 0:
                prev = entries[i - 1]
                try:
                    t1 = _dt.fromisoformat(prev["snapshot_at"])
                    t2 = _dt.fromisoformat(e["snapshot_at"])
                    hours = max((t2 - t1).total_seconds() / 3600, 0.1)
                    point["off_delta"]  = e.get("off_points", 0)  - prev.get("off_points", 0)
                    point["def_delta"]  = e.get("def_points", 0)  - prev.get("def_points", 0)
                    point["raid_delta"] = e.get("raid_points", 0) - prev.get("raid_points", 0)
                    point["pve_delta"]  = e.get("pve_points", 0)  - prev.get("pve_points", 0)
                    point["hours_since_prev"] = round(hours, 1)
                except Exception:
                    pass
            timeline.append(point)
        result[pname] = timeline
    return result


# ---------------------------------------------------------------------------
# Enemy Village Tracking
# ---------------------------------------------------------------------------

async def _init_enemy_villages_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_village_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                player_name TEXT NOT NULL,
                snapshot_at TEXT NOT NULL,
                imported_by TEXT DEFAULT '',
                raw_text    TEXT DEFAULT '',
                village_count INTEGER DEFAULT 0,
                population  INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_villages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id   INTEGER NOT NULL REFERENCES enemy_village_snapshots(id) ON DELETE CASCADE,
                guild_id      TEXT NOT NULL,
                player_name   TEXT NOT NULL,
                village_name  TEXT NOT NULL,
                coords_x      INTEGER,
                coords_y      INTEGER,
                population    INTEGER DEFAULT 0,
                is_capital    INTEGER DEFAULT 0,
                label         TEXT DEFAULT '',
                snapshot_at   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_village_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                player_name  TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                village_name TEXT NOT NULL,
                coords_x     INTEGER,
                coords_y     INTEGER,
                label        TEXT DEFAULT '',
                detected_at  TEXT NOT NULL,
                snapshot_at  TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ev_snapshots_player
            ON enemy_village_snapshots(guild_id, player_name)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ev_events_player
            ON enemy_village_events(guild_id, player_name)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enemy_village_details (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                player_name  TEXT NOT NULL,
                coords_key   TEXT NOT NULL,
                detail_json  TEXT DEFAULT '',
                updated_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, player_name, coords_key)
            )
        """)
        await db.commit()


async def get_enemy_village_details(guild_id: str, player_name: str) -> dict:
    """Return {coords_key: detail_dict} for all villages of a player."""
    await _init_enemy_villages_tables()
    import json as _json
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT coords_key, detail_json FROM enemy_village_details WHERE guild_id=? AND player_name=?",
            (guild_id, player_name),
        ) as cur:
            result = {}
            for row in await cur.fetchall():
                try:
                    result[row["coords_key"]] = _json.loads(row["detail_json"] or "{}")
                except Exception:
                    result[row["coords_key"]] = {}
            return result


async def save_enemy_village_detail(
    guild_id: str, player_name: str, coords_key: str, detail: dict
):
    """Upsert building/field detail for a specific village."""
    import json as _json
    from datetime import datetime as _dt
    await _init_enemy_villages_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO enemy_village_details (guild_id, player_name, coords_key, detail_json, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id, player_name, coords_key) DO UPDATE SET
                detail_json = excluded.detail_json,
                updated_at  = excluded.updated_at
        """, (guild_id, player_name, coords_key, _json.dumps(detail), _dt.utcnow().isoformat()))
        await db.commit()


async def save_enemy_village_snapshot(
    guild_id: str, player_name: str, snapshot_at: str,
    imported_by: str, raw_text: str, villages: list[dict]
) -> tuple[int, list[dict]]:
    """
    Save a village snapshot and auto-detect events by comparing to the previous snapshot.
    Returns (snapshot_id, new_events).
    """
    from datetime import datetime as _dt
    await _init_enemy_villages_tables()

    total_pop = sum(v.get("population", 0) for v in villages)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get previous snapshot villages for diff
        cur = await db.execute("""
            SELECT ev.village_name, ev.coords_x, ev.coords_y, ev.is_capital, ev.label
            FROM enemy_villages ev
            JOIN enemy_village_snapshots s ON s.id = ev.snapshot_id
            WHERE ev.guild_id=? AND ev.player_name=?
            ORDER BY s.snapshot_at DESC
            LIMIT 200
        """, (guild_id, player_name))
        prev_rows = await cur.fetchall()

        # Build previous village set (by coords key if available, else name)
        prev = {}
        for r in prev_rows:
            key = (r["coords_x"], r["coords_y"]) if r["coords_x"] is not None else r["village_name"]
            prev[key] = dict(r)

        # Insert snapshot
        cur = await db.execute("""
            INSERT INTO enemy_village_snapshots
                (guild_id, player_name, snapshot_at, imported_by, raw_text, village_count, population)
            VALUES (?,?,?,?,?,?,?)
        """, (guild_id, player_name, snapshot_at, imported_by, raw_text, len(villages), total_pop))
        snap_id = cur.lastrowid

        # Insert villages
        for v in villages:
            await db.execute("""
                INSERT INTO enemy_villages
                    (snapshot_id, guild_id, player_name, village_name, coords_x, coords_y,
                     population, is_capital, label, snapshot_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (snap_id, guild_id, player_name,
                  v.get("village_name",""), v.get("coords_x"), v.get("coords_y"),
                  v.get("population",0), 1 if v.get("is_capital") else 0,
                  v.get("label",""), snapshot_at))

        # Detect events
        now_set = {}
        for v in villages:
            key = (v.get("coords_x"), v.get("coords_y")) if v.get("coords_x") is not None else v.get("village_name","")
            now_set[key] = v

        events = []
        if prev:  # only diff if we have a previous snapshot
            # New villages
            for key, v in now_set.items():
                if key not in prev:
                    etype = "capital_settled" if v.get("is_capital") else "village_settled"
                    if v.get("label"):
                        etype = "village_conquered"
                    events.append({
                        "event_type": etype,
                        "village_name": v.get("village_name",""),
                        "coords_x": v.get("coords_x"),
                        "coords_y": v.get("coords_y"),
                        "label": v.get("label",""),
                    })
            # Lost villages
            for key, v in prev.items():
                if key not in now_set:
                    events.append({
                        "event_type": "village_lost",
                        "village_name": v["village_name"],
                        "coords_x": v["coords_x"],
                        "coords_y": v["coords_y"],
                        "label": v.get("label",""),
                    })

        for e in events:
            await db.execute("""
                INSERT INTO enemy_village_events
                    (guild_id, player_name, event_type, village_name, coords_x, coords_y,
                     label, detected_at, snapshot_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (guild_id, player_name, e["event_type"], e["village_name"],
                  e.get("coords_x"), e.get("coords_y"), e.get("label",""),
                  _dt.utcnow().isoformat(), snapshot_at))

        await db.commit()
        return snap_id, events


async def get_enemy_village_history(guild_id: str, player_name: str) -> dict:
    """Return snapshots, latest villages, and events for a player."""
    await _init_enemy_villages_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("""
            SELECT * FROM enemy_village_snapshots
            WHERE guild_id=? AND player_name=?
            ORDER BY snapshot_at DESC LIMIT 50
        """, (guild_id, player_name))
        snapshots = [dict(r) for r in await cur.fetchall()]

        latest_snap_id = snapshots[0]["id"] if snapshots else None
        villages = []
        if latest_snap_id:
            cur = await db.execute("""
                SELECT * FROM enemy_villages WHERE snapshot_id=?
                ORDER BY is_capital DESC, population DESC
            """, (latest_snap_id,))
            villages = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute("""
            SELECT * FROM enemy_village_events
            WHERE guild_id=? AND player_name=?
            ORDER BY snapshot_at DESC
        """, (guild_id, player_name))
        events = [dict(r) for r in await cur.fetchall()]

        return {"snapshots": snapshots, "villages": villages, "events": events}


async def get_enemy_village_all_snapshots(guild_id: str, player_name: str) -> list[dict]:
    """Return all village lists per snapshot for population trend."""
    await _init_enemy_villages_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT s.snapshot_at, s.village_count, s.population,
                   ev.village_name, ev.coords_x, ev.coords_y, ev.population as vpop,
                   ev.is_capital, ev.label
            FROM enemy_village_snapshots s
            JOIN enemy_villages ev ON ev.snapshot_id = s.id
            WHERE s.guild_id=? AND s.player_name=?
            ORDER BY s.snapshot_at ASC
        """, (guild_id, player_name))
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Map Share Links
# ---------------------------------------------------------------------------

async def _init_map_share_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS map_share_links (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                short_id   TEXT UNIQUE NOT NULL,
                state_json TEXT NOT NULL,
                created_by TEXT DEFAULT '',
                is_public  INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # migration: add is_public if missing
        try:
            await db.execute("ALTER TABLE map_share_links ADD COLUMN is_public INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.commit()


async def create_map_share(guild_id: str, state_json: str, created_by: str = "", is_public: bool = False) -> str:
    import secrets
    await _init_map_share_table()
    short_id = secrets.token_urlsafe(6)   # ~8 chars, URL-safe
    async with aiosqlite.connect(DB_PATH) as db:
        for _ in range(5):
            try:
                await db.execute("""
                    INSERT INTO map_share_links (guild_id, short_id, state_json, created_by, is_public)
                    VALUES (?,?,?,?,?)
                """, (guild_id, short_id, state_json, created_by, 1 if is_public else 0))
                await db.commit()
                return short_id
            except Exception:
                short_id = secrets.token_urlsafe(6)
    return short_id


async def get_map_share(short_id: str) -> dict | None:
    await _init_map_share_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM map_share_links WHERE short_id=?", (short_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None
