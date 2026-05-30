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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scout_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      TEXT NOT NULL,
                guild_id        TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'text',  -- 'text' or 'ocr'
                raw_text        TEXT,
                target_player   TEXT,
                target_village  TEXT,
                target_coords   TEXT,
                attacker_player TEXT,
                attacker_village TEXT,
                resources_json  TEXT,   -- {"wood":..,"clay":..,"iron":..,"crop":..,"total":..}
                troops_json     TEXT,   -- {"Legionnaire":5, ...}
                losses_json     TEXT,   -- {"Legionnaire":1, ...}
                experience      INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL
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

        # Attack system migrations
        for col in ["attack_channel_id TEXT", "attack_button_message_id TEXT"]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        # Scout corn_scout migration
        for col in ["requested_by_id TEXT", "requested_by_name TEXT", "corn_scout INTEGER DEFAULT 0"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        # Scout images + enemies tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scout_images (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                scout_report_id     INTEGER,
                guild_id            TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                discord_url         TEXT NOT NULL,
                discord_message_id  TEXT,
                created_at          TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_si_report ON scout_images(scout_report_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_si_guild  ON scout_images(guild_id)")
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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS report_channels (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL UNIQUE,
                channel_id   TEXT,
                channel_name TEXT,
                created_at   TEXT NOT NULL
            )
        """)

        # Migrations: discord_message_id on scout_channels
        for col in ["closed_at TEXT", "closed_by TEXT", "discord_message_id TEXT",
                    "discord_message_id TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
            except Exception:
                pass
        # Migrations: discord_message_id + image_urls on scout_reports
        for col in ["discord_message_id TEXT", "image_urls TEXT"]:
            try:
                await db.execute(f"ALTER TABLE scout_reports ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()

    # Migrations: guild_configs extra columns
    async with aiosqlite.connect(DB_PATH) as db:
        for col in [
            "subscription_status TEXT DEFAULT 'free'",
            "subscription_plan TEXT",
            "subscription_expires_at TEXT",
            "stripe_customer_id TEXT",
            "stripe_subscription_id TEXT",
            "owner_discord_id TEXT",
            "bot_status TEXT DEFAULT 'active'",
            "bot_kicked_at TEXT",
            "workspace_type TEXT DEFAULT 'discord'",
            "workspace_owner_id TEXT",
            "workspace_status TEXT DEFAULT 'active'",
            "trial_expires_at TEXT",
            "tw_world TEXT",
            "poll_channel_id TEXT",
            "hero_scout_channel_id TEXT",
            "private_channel_role_ids TEXT",
            "defend_role_ids TEXT",
        ]:
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass

        # trial_links table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_links (
                code                TEXT PRIMARY KEY,
                created_by          TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                activated_at        TEXT,
                activated_guild_id  TEXT
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS defend_sent (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      TEXT NOT NULL,
                guild_id        TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                user_name       TEXT NOT NULL,
                amount_raw      TEXT NOT NULL,
                amount_parsed   INTEGER NOT NULL DEFAULT 0,
                troop_type      TEXT DEFAULT '',
                grain_per_unit  INTEGER NOT NULL DEFAULT 1,
                sent_at         TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Add new columns to defend_channels if not present
        for col in ("tracking_msg_id TEXT", "goal TEXT DEFAULT ''", "ratio TEXT DEFAULT ''"):
            try:
                await db.execute(f"ALTER TABLE defend_channels ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()


async def get_guild_config(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def upsert_guild_name(guild_id: str, guild_name: str, owner_discord_id: str | None = None):
    """Register a guild without overwriting existing config.
    If owner_discord_id is given it is only written when the column is currently NULL."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO guild_configs (guild_id, guild_name, owner_discord_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name       = excluded.guild_name,
                owner_discord_id = COALESCE(guild_configs.owner_discord_id, excluded.owner_discord_id)
        """, (guild_id, guild_name, owner_discord_id))
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
    corn_scout: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure columns exist (migration guard)
        for col in ["requested_by_id TEXT", "requested_by_name TEXT", "corn_scout INTEGER DEFAULT 0"]:
            try:
                await db.execute(f"ALTER TABLE scout_channels ADD COLUMN {col}")
                await db.commit()
            except Exception:
                pass
        await db.execute("""
            INSERT OR IGNORE INTO scout_channels
                (channel_id, guild_id, created_at, player, coordinates, village,
                 scout_time, additional_info, requested_by_id, requested_by_name, corn_scout)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            channel_id, guild_id, datetime.utcnow().isoformat(),
            player, coordinates, village, scout_time, additional_info,
            requested_by_id, requested_by_name, 1 if corn_scout else 0,
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


async def delete_scout_channel(channel_id: str):
    """Remove a scout channel from the database (e.g. when deleted in Discord)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scout_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def save_scout_report(
    channel_id: str,
    guild_id: str,
    source: str,
    raw_text: str | None,
    target_player: str | None,
    target_village: str | None,
    target_coords: str | None,
    attacker_player: str | None,
    attacker_village: str | None,
    resources_json: str | None,
    troops_json: str | None,
    losses_json: str | None,
    experience: int = 0,
    stats_json: str | None = None,
) -> int:
    """Insert a parsed scout report and return its rowid."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "ALTER TABLE scout_reports ADD COLUMN stats_json TEXT"
        ).close() if False else None
        try:
            await db.execute("ALTER TABLE scout_reports ADD COLUMN stats_json TEXT")
        except Exception:
            pass
        cur = await db.execute("""
            INSERT INTO scout_reports
                (channel_id, guild_id, source, raw_text,
                 target_player, target_village, target_coords,
                 attacker_player, attacker_village,
                 resources_json, troops_json, losses_json,
                 experience, created_at, stats_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            channel_id, guild_id, source, raw_text,
            target_player, target_village, target_coords,
            attacker_player, attacker_village,
            resources_json, troops_json, losses_json,
            experience, datetime.utcnow().isoformat(), stats_json,
        ))
        await db.commit()
        return cur.lastrowid


async def get_scout_reports(channel_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_reports WHERE channel_id = ? ORDER BY created_at DESC",
            (channel_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_scout_reports(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scout_reports WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_poll(poll_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM availability_polls WHERE id = ?", (poll_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_attack_report(
    guild_id: str,
    reporter_id: str,
    reporter_name: str,
    raw_text: str,
    attacks_json_str: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO attack_reports
                (guild_id, reporter_id, reporter_name, raw_text, attacks_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, reporter_id, reporter_name, raw_text, attacks_json_str,
              datetime.utcnow().isoformat()))
        await db.commit()
        return cursor.lastrowid


async def get_attack_channel(guild_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT attack_channel_id FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_attack_channel(guild_id: str, channel_id: str, message_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE guild_configs
            SET attack_channel_id = ?, attack_button_message_id = ?
            WHERE guild_id = ?
        """, (channel_id, message_id, guild_id))
        await db.commit()


async def upsert_poll_response(poll_id: int, user_id: str, user_name: str, response: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO poll_responses (poll_id, user_id, user_name, response, responded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(poll_id, user_id) DO UPDATE SET
                response=excluded.response, user_name=excluded.user_name, responded_at=excluded.responded_at
        """, (poll_id, user_id, user_name, response, datetime.utcnow().isoformat()))
        await db.commit()


# ---------------------------------------------------------------------------
# User-level subscriptions
# ---------------------------------------------------------------------------

async def get_user_subscription(discord_user_id: str) -> dict | None:
    """Get user-level subscription from user_subscriptions table."""
    if not discord_user_id:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_subscriptions WHERE discord_user_id = ?", (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_owner_guild_count(discord_user_id: str) -> int:
    """Count guilds where this Discord user is owner with active/trialing sub."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM guild_configs
               WHERE owner_discord_id = ?
                 AND subscription_status IN ('active', 'trialing')""",
            (discord_user_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ---------------------------------------------------------------------------
# Subscription / server-slot enforcement
# ---------------------------------------------------------------------------

_TIER_LIMITS = {"starter": 1, "clan": 2, "alliance": 3, "imperium": 5}


async def get_subscription_status(guild_id: str) -> str:
    """Return the subscription_status for a guild ('free' if not found)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT subscription_status FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else "free"


async def check_guild_join_allowed(guild_id: str, discord_owner_id: str) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    A guild join is allowed when:
    - The guild already has an active/trialing subscription in the DB, OR
    - The Discord server owner has remaining server slots in their subscription.
    If the guild is brand-new (not in DB) and the owner is at/over their limit → not allowed.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Check if this guild already exists with an active sub
        async with db.execute(
            "SELECT subscription_status, subscription_plan FROM guild_configs WHERE guild_id = ?",
            (guild_id,)
        ) as cur:
            existing = await cur.fetchone()

        if existing and existing["subscription_status"] in ("active", "trialing"):
            return True, "existing_active"

        # 2. Check owner's slots (by owner_discord_id matching the Discord server owner)
        async with db.execute(
            """
            SELECT subscription_plan
            FROM guild_configs
            WHERE owner_discord_id = ?
              AND subscription_status IN ('active', 'trialing')
            ORDER BY
                CASE WHEN subscription_plan LIKE 'imperium%' THEN 4
                     WHEN subscription_plan LIKE 'alliance%' THEN 3
                     WHEN subscription_plan LIKE 'clan%'     THEN 2
                     ELSE 1 END DESC
            LIMIT 1
            """,
            (discord_owner_id,)
        ) as cur:
            best_plan_row = await cur.fetchone()

        if not best_plan_row:
            # No subscription at all → only allow if guild already in DB (was previously configured)
            return existing is not None, "no_subscription"

        tier = (best_plan_row["subscription_plan"] or "starter").split("_")[0]
        max_slots = _TIER_LIMITS.get(tier, 1)

        # Count currently active/trialing guilds for this owner
        async with db.execute(
            """
            SELECT COUNT(*) FROM guild_configs
            WHERE owner_discord_id = ?
              AND subscription_status IN ('active', 'trialing')
              AND guild_id != ?
            """,
            (discord_owner_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
        used_slots = row[0] if row else 0

        if used_slots >= max_slots:
            return False, f"limit_reached:{used_slots}/{max_slots}:{tier}"

        return True, "ok"


async def set_bot_kicked(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET bot_status = 'kicked', bot_kicked_at = datetime('now') WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def set_bot_active(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET bot_status = 'active', bot_kicked_at = NULL WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()


async def get_all_active_guild_ids() -> list[str]:
    """Return all guild_ids with bot_status = 'active'."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT guild_id FROM guild_configs WHERE bot_status = 'active'"
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


async def set_category(guild_id: str, category_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET category_id = ? WHERE guild_id = ?",
            (category_id, guild_id),
        )
        await db.commit()


async def set_archive_channel_id(guild_id: str, archive_channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET archive_channel_id = ? WHERE guild_id = ?",
            (archive_channel_id, guild_id),
        )
        await db.commit()


async def activate_guild_subscription(guild_id: str, status: str, plan: str):
    """Copy user subscription status/plan onto the guild so the dashboard allows access."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE guild_configs
               SET subscription_status = ?, subscription_plan = ?, bot_status = 'active', bot_kicked_at = NULL
               WHERE guild_id = ?""",
            (status, plan, guild_id),
        )
        await db.commit()


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


async def get_player_from_snapshot(guild_id: str, player_name: str) -> dict | None:
    """Look up a player by name in the latest map snapshot."""
    if not player_name:
        return None
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


async def save_scout_image(
    guild_id: str, channel_id: str, discord_url: str,
    discord_message_id: str = "", scout_report_id: int | None = None,
    local_path: str = "",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure local_path column exists (migration)
        try:
            await db.execute("ALTER TABLE scout_images ADD COLUMN local_path TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        cur = await db.execute("""
            INSERT INTO scout_images
                (scout_report_id, guild_id, channel_id, discord_url, discord_message_id, local_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (scout_report_id, guild_id, channel_id, discord_url,
              discord_message_id, local_path, datetime.utcnow().isoformat()))
        await db.commit()
        return cur.lastrowid


async def update_scout_image_local_path(image_id: int, local_path: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scout_images SET local_path=? WHERE id=?", (local_path, image_id))
        await db.commit()


async def link_scout_image_to_report(image_id: int, report_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scout_images SET scout_report_id=? WHERE id=?", (report_id, image_id))
        await db.commit()


async def upsert_enemy(
    guild_id: str, player_name: str,
    coordinates: str = "", village: str = ""
) -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
            db.row_factory = aiosqlite.Row
            row = await cur.fetchone()
            return row[0] if row else 0


async def close_scout_channel_by_message(discord_message_id: str):
    """Mark a scout_channel as closed when its Discord message is deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE scout_channels SET closed_at = ?, closed_by = 'discord_delete'
            WHERE discord_message_id = ? AND closed_at IS NULL
        """, (datetime.utcnow().isoformat(), discord_message_id))
        await db.commit()


async def set_report_channel(guild_id: str, channel_id: str | None, channel_name: str | None):
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


async def get_all_report_channels() -> list[dict]:
    """Return all registered report channels across all guilds."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT guild_id, channel_id FROM report_channels") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_active_scout_channels() -> list[dict]:
    """Return all open scout channels across all guilds."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT channel_id, guild_id, player, coordinates, village FROM scout_channels WHERE closed = 0"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_bot_last_online() -> datetime | None:
    """Return the last time the bot was online (stored in meta table)."""
    from datetime import datetime
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS bot_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        await db.commit()
        async with db.execute("SELECT value FROM bot_meta WHERE key = 'last_online'") as cur:
            row = await cur.fetchone()
            if row:
                try:
                    return datetime.fromisoformat(row[0])
                except Exception:
                    return None
    return None


async def set_bot_last_online():
    """Update last_online to now."""
    from datetime import datetime
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS bot_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO bot_meta (key, value) VALUES ('last_online', ?)",
            (datetime.utcnow().isoformat(),),
        )
        await db.commit()


async def get_player_tribe(guild_id: str, player_name: str) -> int:
    """Look up a player's tribe from map_snapshots. Returns 0 if unknown."""
    if not player_name:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT tribe FROM map_snapshots
            WHERE guild_id = ? AND player_name = ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (guild_id, player_name)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                try:
                    return int(row[0])
                except (ValueError, TypeError):
                    return 0
    return 0


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


async def is_request_hub(channel_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM request_hub WHERE channel_id=?", (channel_id,)) as cur:
            return await cur.fetchone() is not None


async def add_defend_channel(
    channel_id: str, guild_id: str, type: str,
    attacker: str, coords: str, arrival_time: str,
    notes: str, requested_by_id: str, requested_by_name: str,
    goal: str = "", ratio: str = "",
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO defend_channels
                (channel_id, guild_id, type, attacker, coords, arrival_time,
                 notes, goal, ratio, requested_by_id, requested_by_name, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'open')
        """, (channel_id, guild_id, type, attacker, coords, arrival_time,
              notes, goal or "", ratio or "", requested_by_id, requested_by_name))
        await db.commit()


async def close_defend_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE defend_channels SET status='closed' WHERE channel_id=?", (channel_id,))
        await db.commit()


async def get_defend_channel(channel_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM defend_channels WHERE channel_id = ?", (channel_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_defend_sent(
    channel_id: str, guild_id: str, user_id: str, user_name: str,
    amount_raw: str, amount_parsed: int, troop_type: str, grain_per_unit: int = 1,
):
    async with aiosqlite.connect(DB_PATH) as db:
        # Add grain_per_unit column if missing (migration)
        try:
            await db.execute("ALTER TABLE defend_sent ADD COLUMN grain_per_unit INTEGER NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            pass
        await db.execute("""
            INSERT INTO defend_sent
                (channel_id, guild_id, user_id, user_name, amount_raw, amount_parsed, troop_type, grain_per_unit)
            VALUES (?,?,?,?,?,?,?,?)
        """, (channel_id, guild_id, user_id, user_name, amount_raw, amount_parsed, troop_type, grain_per_unit))
        await db.commit()


async def get_defend_sent(channel_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM defend_sent WHERE channel_id = ? ORDER BY sent_at ASC", (channel_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def set_defend_tracking_msg(channel_id: str, msg_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE defend_channels SET tracking_msg_id = ? WHERE channel_id = ?",
            (msg_id, channel_id),
        )
        await db.commit()


async def get_guild(guild_id: str) -> dict | None:
    """Return guild_configs row for guild_id, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_trial_link(code: str, created_by: str) -> str:
    """Insert a new one-time trial link. Returns the code."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO trial_links (code, created_by, created_at) VALUES (?, ?, ?)",
            (code, created_by, datetime.utcnow().isoformat()),
        )
        await db.commit()
    return code


async def activate_trial_link(code: str, guild_id: str, days: int = 14) -> bool:
    """Mark the link used and set trial_expires_at on the guild. Returns False if already used."""
    from datetime import timedelta
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trial_links WHERE code=?", (code,)) as cur:
            link = await cur.fetchone()
        if not link or (link["activated_guild_id"] if "activated_guild_id" in link.keys() else None):
            return False
        expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
        now = datetime.utcnow().isoformat()
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


# ── Private Channels ──────────────────────────────────────────────────────────

async def get_private_channel(guild_id: str, owner_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM private_channels WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_private_channel(guild_id: str, owner_id: str, channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO private_channels (channel_id, guild_id, owner_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, owner_id) DO UPDATE SET channel_id = excluded.channel_id
        """, (channel_id, guild_id, owner_id))
        await db.commit()


async def delete_private_channel_by_id(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM private_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def get_private_channel_by_channel_id(channel_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM private_channels WHERE channel_id = ?",
            (channel_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_member_permissions(guild_id: str, discord_id: str) -> set[str]:
    """Return the permission flag set for a user (mirrors web/database.py logic)."""
    ALL_PERMS = {
        "ally_manage", "ep_manage", "ep_view", "ep_notify",
        "attack_manage", "attack_view", "scout_manage", "scout_view",
        "map_manage", "map_view", "sector_view", "hospital_view",
        "defend_manage",
    }
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM ally_groups WHERE guild_id=? AND owner_discord_id=?",
            (guild_id, discord_id),
        ) as cur:
            if await cur.fetchone():
                return ALL_PERMS
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
