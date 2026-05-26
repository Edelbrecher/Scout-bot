import asyncio
import json
import os

import aiohttp
from aiohttp import web as aiohttp_web
import discord
from discord.ext import commands
from dotenv import load_dotenv

import database

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


class ScouterBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await database.init_db()
        await self.load_extension("cogs.scout")
        await self.load_extension("cogs.archiver")
        await self.load_extension("cogs.res_push")
        await self.load_extension("cogs.poll")
        await self.load_extension("cogs.attacks")
        await self.load_extension("cogs.hub")
        await self.load_extension("cogs.hero_scout")
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        # Sync all current guilds to DB — include owner so slots_used is accurate
        for guild in self.guilds:
            owner_id = str(guild.owner_id) if guild.owner_id else None
            await database.upsert_guild_name(str(guild.id), guild.name, owner_discord_id=owner_id)
        print(f"Synced {len(self.guilds)} guild(s) to database.")

        # Detect deleted/left guilds: any active DB entry not in current guild list → free slot
        active_ids = await database.get_all_active_guild_ids()
        current_ids = {str(g.id) for g in self.guilds}
        freed = 0
        for guild_id in active_ids:
            if guild_id not in current_ids:
                await database.set_bot_kicked(guild_id)
                freed += 1
                print(f"[on_ready] Guild {guild_id} no longer exists → slot freed.")
        if freed:
            print(f"[on_ready] Freed {freed} stale guild slot(s).")

    async def on_guild_join(self, guild: discord.Guild):
        _TIER_LIMITS = {"starter": 1, "clan": 2, "alliance": 3, "imperium": 5}
        owner_id = str(guild.owner_id) if guild.owner_id else ""
        allowed, reason = await database.check_guild_join_allowed(str(guild.id), owner_id)

        if not allowed:
            # Before leaving, check if owner has a user-level subscription with open slots
            user_sub = await database.get_user_subscription(owner_id) if owner_id else None
            if user_sub and user_sub.get("subscription_status") in ("active", "trialing"):
                tier = (user_sub.get("plan") or "starter").split("_")[0]
                max_slots = _TIER_LIMITS.get(tier, 1)
                used_slots = await database.get_owner_guild_count(owner_id)
                if used_slots < max_slots:
                    # Auto-activate: register guild and mark as active via web DB (shared SQLite)
                    await database.upsert_guild_name(str(guild.id), guild.name, owner_discord_id=owner_id)
                    sub_status = user_sub.get("subscription_status", "active")
                    sub_plan = user_sub.get("plan") or f"{tier}_monthly"
                    await database.activate_guild_subscription(str(guild.id), sub_status, sub_plan)
                    print(f"[on_guild_join] Auto-activated {guild.name} ({guild.id}) via user sub for {owner_id}")
                    try:
                        owner = guild.owner or await self.fetch_user(guild.owner_id)
                        if owner:
                            await owner.send(
                                f"✅ Dein TravOps Pro Abo wurde für diesen Server aktiviert!\n"
                                f"Richte den Bot unter https://travops.online/dashboard ein."
                            )
                    except Exception as dm_err:
                        print(f"[on_guild_join] Could not DM owner: {dm_err}")
                    return
                else:
                    # User sub exists but no slots left
                    print(f"[on_guild_join] Leaving {guild.name} ({guild.id}) — user sub slot limit {used_slots}/{max_slots}")
                    try:
                        owner = guild.owner or await self.fetch_user(guild.owner_id)
                        if owner:
                            await owner.send(
                                f"👋 Hallo! Ich musste **{guild.name}** sofort wieder verlassen.\n\n"
                                f"Dein Paket **{tier.capitalize()}** erlaubt **{max_slots} Server** "
                                f"und du nutzt bereits alle Slots.\n"
                                f"Upgrade auf ein höheres Paket:\n"
                                f"➡️ https://travops.online/plans"
                            )
                    except Exception as dm_err:
                        print(f"[on_guild_join] Could not DM owner: {dm_err}")
                    await guild.leave()
                    return

            print(f"[on_guild_join] Leaving {guild.name} ({guild.id}) — {reason}")
            # Try to DM the server owner
            try:
                owner = guild.owner or await self.fetch_user(guild.owner_id)
                if owner:
                    tier_info = ""
                    if "limit_reached" in reason:
                        parts = reason.split(":")
                        used_max = parts[1] if len(parts) > 1 else "?"
                        tier = parts[2] if len(parts) > 2 else "?"
                        tier_info = (
                            f"\n\nDein aktuelles Paket **{tier.capitalize()}** erlaubt **{used_max} Server**. "
                            f"Upgrade auf ein höheres Paket, um mehr Server hinzuzufügen:\n"
                            f"➡️ https://travops.online/plans"
                        )
                    else:
                        tier_info = (
                            "\n\nBitte erwirb ein Abonnement und lade den Bot danach erneut ein:\n"
                            "➡️ https://travops.online/plans"
                        )
                    await owner.send(
                        f"👋 Hallo! Ich wollte deinem Server **{guild.name}** beitreten, "
                        f"aber dein Server-Limit ist erreicht und ich musste den Server sofort wieder verlassen.{tier_info}"
                    )
            except Exception as dm_err:
                print(f"[on_guild_join] Could not DM owner: {dm_err}")
            await guild.leave()
            return

        await database.upsert_guild_name(str(guild.id), guild.name, owner_discord_id=owner_id)
        await database.set_bot_active(str(guild.id))
        print(f"Joined guild: {guild.name} ({guild.id})")

        # ── Auto-Trial: 7 days for new guilds without any subscription ──
        existing = await database.get_guild(str(guild.id))
        existing_status = (existing or {}).get("subscription_status", "free") if existing else "free"
        if existing_status in ("free", None, ""):
            import secrets as _sec
            trial_code = _sec.token_urlsafe(10)
            await database.create_trial_link(code=trial_code, created_by="auto-bot-invite")
            activated = await database.activate_trial_link(trial_code, str(guild.id), days=7)
            if activated:
                print(f"[on_guild_join] Auto-trial activated for {guild.name} ({guild.id})")
                try:
                    owner = guild.owner or await self.fetch_user(guild.owner_id)
                    if owner:
                        await owner.send(
                            f"🎉 **Willkommen bei TravOps, {guild.name}!**\n\n"
                            f"Du hast automatisch **7 Tage vollen Pro-Zugang** erhalten — "
                            f"kein Kreditkarte, kein Abo nötig.\n\n"
                            f"**Was jetzt?**\n"
                            f"➡️ Richte deinen Server unter https://travops.online/dashboard ein\n"
                            f"➡️ Alle Features freischalten: https://travops.online/plans\n\n"
                            f"_Dein Trial endet in 7 Tagen automatisch — danach kannst du "
                            f"auf ein Pro-Paket upgraden oder mit dem kostenlosen Plan weitermachen._"
                        )
                except Exception as dm_err:
                    print(f"[on_guild_join] Could not DM owner for trial: {dm_err}")

    async def on_guild_remove(self, guild: discord.Guild):
        """Bot was kicked or left a guild — mark as kicked in DB."""
        await database.set_bot_kicked(str(guild.id))
        print(f"[on_guild_remove] Marked {guild.name} ({guild.id}) as kicked.")


bot = ScouterBot()


# ── Internal HTTP API ──────────────────────────────────────────────────────────
async def handle_create_report_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Called by the web container to create a Discord report channel."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not guild_id:
        return aiohttp_web.json_response({"ok": False, "error": "guild_id required"}, status=400)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    config = await database.get_guild_config(guild_id)
    try:
        category = await _get_or_create_category(guild, config)
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": f"category error: {e}"}, status=500)

    # Permissions: same pattern as scout channels
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True,
        ),
    }
    for role_id_str in (config.get("allowed_role_ids") or "").split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True
            )

    try:
        channel = await guild.create_text_channel(
            name="battle-reports",
            category=category,
            topic="Kampfberichte-Eingang — Bot scannt alle Bilder automatisch.",
            overwrites=overwrites,
        )
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    # Persist as report channel
    await database.set_report_channel(guild_id, str(channel.id), channel.name)

    # Welcome message in the new channel
    try:
        await channel.send(
            "📋 **Bericht-Eingang aktiv** — Postet hier eure Kampfberichte als Screenshot. "
            "Der Bot analysiert sie automatisch und trägt sie in die Gegner-Kartei ein."
        )
    except Exception:
        pass

    return aiohttp_web.json_response({
        "ok": True,
        "channel_id": str(channel.id),
        "channel_name": channel.name,
    })


async def _get_or_create_category(guild: discord.Guild, config: dict | None) -> discord.CategoryChannel:
    """Return configured category or create a default 'TravOps' category."""
    if config and config.get("category_id"):
        cat = guild.get_channel(int(config["category_id"]))
        if cat and isinstance(cat, discord.CategoryChannel):
            return cat
    # Auto-create category
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    cat = await guild.create_category("TravOps", overwrites=overwrites)
    # Save to config so future channels land here too
    await database.set_category(guild_id=str(guild.id), category_id=str(cat.id))
    return cat


async def handle_create_request_hub(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Called by web container to create the Request Hub channel."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not guild_id:
        return aiohttp_web.json_response({"ok": False, "error": "guild_id required"}, status=400)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    config = await database.get_guild_config(guild_id)
    try:
        category = await _get_or_create_category(guild, config)
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": f"category error: {e}"}, status=500)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True,
        ),
    }
    for role_id_str in (config.get("allowed_role_ids") or "").split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

    try:
        channel = await guild.create_text_channel(
            name="travops-anfragen",
            category=category,
            topic="Alle Anfragen auf einen Blick — Scout, Defend, Res-Push und mehr.",
            overwrites=overwrites,
        )
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    from cogs.hub import RequestHubView
    embed = discord.Embed(
        title="📋 TravOps Anfragen-Hub",
        description=(
            "Klicke einen Button um einen Kanal zu erstellen:\n\n"
            "🔍 **Scout** — Gegner spähen lassen\n"
            "🌾 **Kornspäh** — Korn eines Gegners ausspähen\n"
            "📡 **Permanent-Scout** — Dauerhaft Späher im eigenen Dorf stationieren\n"
            "🪖 **Res-Push** — Ressourcen anfordern\n"
            "🛡️ **Defend** — Verteidigung koordinieren\n"
            "⏱️ **Timed-Defend** — Getimte Verteidigung koordinieren\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗡️ **Helden-Scout** — Screenshots von Gegner-Helden in einem dedizierten Channel posten. "
            "Der Bot erkennt automatisch Ausrüstungswechsel & XP-Sprünge.\n"
            "→ Setup: `/hero-scout-setup #channel` oder [travops.online](https://travops.online)"
        ),
        color=discord.Color.blurple(),
    )
    msg = await channel.send(embed=embed, view=RequestHubView())

    await database.set_request_hub(guild_id, str(channel.id), channel.name, str(msg.id))

    return aiohttp_web.json_response({
        "ok": True,
        "channel_id": str(channel.id),
        "channel_name": channel.name,
    })


async def handle_check_permissions(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Check if bot has required permissions in configured channels."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    required = discord.Permissions(
        view_channel=True, send_messages=True,
        embed_links=True, attach_files=True, manage_channels=True,
    )
    issues = []

    config = await database.get_guild_config(guild_id)
    channels_to_check = []

    if config:
        for key in ("scout_channel_id", "res_request_channel_id", "attack_channel_id"):
            ch_id = config.get(key)
            if ch_id:
                channels_to_check.append(ch_id)

    # Also check report + hub channels
    report = await database.get_report_channel(guild_id)
    if report:
        channels_to_check.append(report.get("channel_id"))

    hub = await database.get_request_hub(guild_id)
    if hub:
        channels_to_check.append(hub.get("channel_id"))

    for ch_id in channels_to_check:
        if not ch_id:
            continue
        ch = guild.get_channel(int(ch_id))
        if not ch:
            issues.append({"channel_id": ch_id, "issue": "channel not found"})
            continue
        perms = ch.permissions_for(guild.me)
        missing = []
        if not perms.view_channel:   missing.append("view_channel")
        if not perms.send_messages:  missing.append("send_messages")
        if not perms.embed_links:    missing.append("embed_links")
        if not perms.attach_files:   missing.append("attach_files")
        if missing:
            issues.append({"channel_id": ch_id, "channel_name": ch.name, "missing": missing})

    return aiohttp_web.json_response({"ok": True, "issues": issues})


async def handle_build_hero_library(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Startet den Item-Bibliothek-Download im Hintergrund."""
    try:
        data = await request.json()
        world_url = str(data.get("world_url", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not world_url:
        return aiohttp_web.json_response({"ok": False, "error": "world_url required"}, status=400)

    try:
        import hero_item_matcher
        asyncio.create_task(hero_item_matcher.build_library(world_url))
        return aiohttp_web.json_response({"ok": True, "message": "Build gestartet"})
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_hero_library_status(request: aiohttp_web.Request) -> aiohttp_web.Response:
    try:
        import hero_item_matcher
        return aiohttp_web.json_response({"ok": True, **hero_item_matcher.get_library_status()})
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)})


async def handle_list_channels(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Return text channels for a guild so the web dashboard can offer a dropdown."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "channels": []})

    channels = [
        {"id": str(ch.id), "name": ch.name}
        for ch in guild.text_channels
        if ch.permissions_for(guild.me).view_channel
    ]
    return aiohttp_web.json_response({"ok": True, "channels": channels})


async def handle_set_hero_scout_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Called by web container to configure the hero-scout channel."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
        channel_id = str(data.get("channel_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not guild_id or not channel_id:
        return aiohttp_web.json_response({"ok": False, "error": "guild_id and channel_id required"}, status=400)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    channel = guild.get_channel(int(channel_id))
    channel_name = channel.name if channel else channel_id

    from cogs.hero_scout import set_hero_scout_channel
    await set_hero_scout_channel(guild_id, channel_id, channel_name, "web")

    return aiohttp_web.json_response({"ok": True, "channel_id": channel_id, "channel_name": channel_name})


async def start_api_server():
    app = aiohttp_web.Application()
async def handle_guild_info(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Return basic info (name, icon) for a guild."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False}, status=400)
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "name": guild_id})
    return aiohttp_web.json_response({"ok": True, "name": guild.name, "icon": str(guild.icon) if guild.icon else ""})


async def handle_leave_guild(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Leave a Discord guild on request from the web dashboard."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not guild_id:
        return aiohttp_web.json_response({"ok": False, "error": "guild_id required"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        # Already not in the guild — still mark as kicked
        await database.set_bot_kicked(guild_id)
        return aiohttp_web.json_response({"ok": True, "note": "not_in_guild"})

    try:
        await guild.leave()
        await database.set_bot_kicked(guild_id)
        print(f"[leave_guild] Left {guild.name} ({guild_id}) via dashboard request")
        return aiohttp_web.json_response({"ok": True})
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)


    app.router.add_post("/api/create-report-channel", handle_create_report_channel)
    app.router.add_post("/api/create-request-hub", handle_create_request_hub)
    app.router.add_post("/api/check-permissions", handle_check_permissions)
    app.router.add_post("/api/set-hero-scout-channel", handle_set_hero_scout_channel)
    app.router.add_post("/api/list-channels", handle_list_channels)
    app.router.add_post("/api/leave-guild", handle_leave_guild)
    app.router.add_post("/api/guild-info", handle_guild_info)
    app.router.add_post("/api/hero-scout-build-library", handle_build_hero_library)
    app.router.add_get("/api/hero-scout-library-status", handle_hero_library_status)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, "0.0.0.0", 7777)
    await site.start()
    print("Internal API listening on :7777")


async def main():
    async with bot:
        await asyncio.gather(
            bot.start(os.environ["DISCORD_TOKEN"]),
            start_api_server(),
        )


asyncio.run(main())
