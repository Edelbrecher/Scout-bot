import asyncio
import json
import os

import aiohttp
from aiohttp import web as aiohttp_web
import discord
from discord.ext import commands
from dotenv import load_dotenv

import database
from i18n import t, get_guild_lang

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True   # needed for guild.members lookup (grant/revoke access)


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
        await self.load_extension("cogs.crop_tracker")
        await self.load_extension("cogs.digest")
        await self.tree.sync()
        print("Slash commands synced.")
        self.loop.create_task(self.heartbeat_loop())

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        import traceback
        print(f"[interaction error] /{interaction.command.name if interaction.command else '?'} by {interaction.user}: {error}")
        traceback.print_exc()
        try:
            msg = f"❌ Fehler: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

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

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Detect when a member gains an allowed_role → auto-create as ally member."""
        guild_id = str(after.guild.id)
        config = await database.get_guild_config(guild_id)
        if not config:
            return
        allowed_ids = {r.strip() for r in (config.get("allowed_role_ids") or "").split(",") if r.strip()}
        if not allowed_ids:
            return
        before_role_ids = {str(r.id) for r in before.roles}
        after_role_ids  = {str(r.id) for r in after.roles}
        newly_gained = after_role_ids - before_role_ids
        if newly_gained & allowed_ids:
            added = await database.join_ally_member(
                guild_id, str(after.id), after.display_name or after.name
            )
            if added:
                print(f"[on_member_update] Auto-joined {after} in guild {guild_id}")

    async def heartbeat_loop(self):
        """Update bot_last_seen for all current guilds every 8 hours."""
        await self.wait_until_ready()
        import asyncio as _asyncio
        while not self.is_closed():
            for guild in self.guilds:
                try:
                    await database.update_bot_last_seen(str(guild.id))
                except Exception:
                    pass
            print(f"[heartbeat] Updated bot_last_seen for {len(self.guilds)} guild(s).")
            await _asyncio.sleep(8 * 3600)  # 8 hours



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

    lang = await get_guild_lang(guild_id)
    try:
        channel = await guild.create_text_channel(
            name=t(lang, "report_channel.name"),
            category=category,
            topic=t(lang, "report_channel.topic"),
            overwrites=overwrites,
        )
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    # Persist as report channel
    await database.set_report_channel(guild_id, str(channel.id), channel.name)

    # Welcome message in the new channel
    try:
        await channel.send(t(lang, "report_channel.welcome"))
    except Exception:
        pass

    return aiohttp_web.json_response({
        "ok": True,
        "channel_id": str(channel.id),
        "channel_name": channel.name,
    })


async def _get_or_create_category(guild: discord.Guild, config: dict | None) -> discord.CategoryChannel:
    """Return configured category (by ID or name), or create a default 'TravOps' category."""
    if config and config.get("category_id"):
        cat = guild.get_channel(int(config["category_id"]))
        if cat and isinstance(cat, discord.CategoryChannel):
            return cat
        # Try API fetch for stale cache
        try:
            cat = await guild.fetch_channel(int(config["category_id"]))
            if cat and isinstance(cat, discord.CategoryChannel):
                return cat
        except Exception:
            pass
    # Search existing categories by name before creating
    for ch in guild.categories:
        if ch.name.lower() == "travops":
            await database.set_category(guild_id=str(guild.id), category_id=str(ch.id))
            return ch
    # Auto-create category as last resort
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

    lang = await get_guild_lang(guild_id)
    try:
        channel = await guild.create_text_channel(
            name=t(lang, "hub.channel_name"),
            category=category,
            topic=t(lang, "hub.channel_topic"),
            overwrites=overwrites,
        )
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    from cogs.hub import RequestHubView
    embed = discord.Embed(
        title=t(lang, "hub.title"),
        description=t(lang, "hub.description"),
        color=discord.Color.blurple(),
    )
    msg = await channel.send(embed=embed, view=RequestHubView())

    await database.set_request_hub(guild_id, str(channel.id), channel.name, str(msg.id))

    # Ensure archive channel exists in the correct category
    archive_channel_id = (config or {}).get("archive_channel_id")
    archive_ok = False
    if archive_channel_id:
        existing = guild.get_channel(int(archive_channel_id))
        if not existing:
            try:
                existing = await guild.fetch_channel(int(archive_channel_id))
            except Exception:
                existing = None
        if existing and isinstance(existing, discord.TextChannel):
            if existing.category_id != category.id:
                # Move to correct category
                try:
                    await existing.edit(category=category)
                    print(f"[hub] Moved scout-archive {existing.id} → category {category.id}")
                except Exception as e:
                    print(f"[hub] Could not move archive channel: {e}")
            archive_ok = True
    if not archive_ok:
        try:
            archive_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    embed_links=True, attach_files=True, manage_channels=True,
                ),
            }
            archive_ch = await guild.create_text_channel(
                name="scout-archive",
                category=category,
                topic="Scout-Archiv — automatisch befüllt",
                overwrites=archive_overwrites,
            )
            await database.set_archive_channel_id(guild_id, str(archive_ch.id))
            print(f"[hub] Created scout-archive channel {archive_ch.id} in category {category.id}")
        except Exception as e:
            print(f"[hub] Could not create archive channel: {e}")

    return aiohttp_web.json_response({
        "ok": True,
        "channel_id": str(channel.id),
        "channel_name": channel.name,
    })


async def handle_refresh_request_hub(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Refresh the hub message embed + view without recreating the channel."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id", ""))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    hub = await database.get_request_hub(guild_id)
    if not hub:
        return aiohttp_web.json_response({"ok": False, "error": "no hub configured"}, status=404)

    channel = guild.get_channel(int(hub["channel_id"]))
    if not channel:
        try:
            channel = await guild.fetch_channel(int(hub["channel_id"]))
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

    lang = await get_guild_lang(guild_id)
    from cogs.hub import RequestHubView
    embed = discord.Embed(
        title=t(lang, "hub.title"),
        description=t(lang, "hub.description"),
        color=discord.Color.blurple(),
    )

    # Try to edit existing message; fall back to deleting + sending new
    old_msg_id = hub.get("message_id")
    new_msg = None
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
        except Exception:
            pass

    try:
        new_msg = await channel.send(embed=embed, view=RequestHubView())
        await database.set_request_hub(guild_id, str(channel.id), channel.name, str(new_msg.id))
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    # Also ensure archive channel exists in the correct category (same logic as create)
    config = await database.get_guild_config(guild_id)
    try:
        category = await _get_or_create_category(guild, config)
        archive_channel_id = (config or {}).get("archive_channel_id")
        archive_ok = False
        if archive_channel_id:
            existing = guild.get_channel(int(archive_channel_id))
            if not existing:
                try:
                    existing = await guild.fetch_channel(int(archive_channel_id))
                except Exception:
                    existing = None
            if existing and isinstance(existing, discord.TextChannel):
                if existing.category_id != category.id:
                    await existing.edit(category=category)
                archive_ok = True
        if not archive_ok:
            allowed_ids = (config or {}).get("allowed_role_ids") or ""
            archive_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    embed_links=True, attach_files=True, manage_channels=True,
                ),
            }
            for rid in [r.strip() for r in allowed_ids.split(",") if r.strip()]:
                role = guild.get_role(int(rid))
                if role:
                    archive_overwrites[role] = discord.PermissionOverwrite(view_channel=True)
            archive_ch = await guild.create_text_channel(
                name="scout-archive",
                category=category,
                topic="Scout-Archiv — automatisch befüllt",
                overwrites=archive_overwrites,
            )
            await database.set_archive_channel_id(guild_id, str(archive_ch.id))
            print(f"[hub_refresh] Created scout-archive {archive_ch.id} in category {category.id}")
    except Exception as e:
        print(f"[hub_refresh] archive error: {e}")

    return aiohttp_web.json_response({"ok": True})


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


async def handle_refresh_res_push(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Rebuild and edit the push embed after a status change from the dashboard."""
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    request_id = data.get("request_id")
    if not request_id:
        return aiohttp_web.json_response({"ok": False, "error": "request_id required"}, status=400)

    try:
        from cogs.res_push import _build_push_embed, ResPushChannelView
        req = await database.get_res_request_by_id(int(request_id))
        if not req:
            return aiohttp_web.json_response({"ok": False, "error": "not found"}, status=404)

        push_channel_id = req.get("push_channel_id")
        push_message_id = req.get("push_message_id")
        status = req.get("status", "accepted")

        if not push_channel_id or not push_message_id:
            return aiohttp_web.json_response({"ok": False, "error": "no push message tracked"}, status=404)

        guild = bot_instance.get_guild(int(req["guild_id"]))
        if not guild:
            return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

        channel = guild.get_channel(int(push_channel_id))
        if not channel:
            return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

        contribs = await database.get_res_contributions(int(request_id))
        embed = _build_push_embed(req, contribs, status=status)
        msg = await channel.fetch_message(int(push_message_id))
        await msg.edit(embed=embed, view=ResPushChannelView())
        return aiohttp_web.json_response({"ok": True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_op_notify(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Send Discord DMs to each attacker with their waves from an op plan."""
    try:
        data = await request.json()
        guild_id = str(data.get("guild_id",""))
        plan = data.get("plan",{})
    except Exception:
        return aiohttp_web.json_response({"ok":False,"error":"invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok":False,"error":"guild not found"}, status=404)

    plan_name = plan.get("name","Einsatz")
    landing   = (plan.get("landing_time") or "")[:16].replace("T"," ")
    ally      = plan.get("target_ally") or ""

    # Build per-attacker message
    attacker_waves: dict[str, list] = {}
    for t in plan.get("targets",[]):
        coords = f"({t.get('x')}|{t.get('y')})"
        player = t.get("player_name","?")
        for w in t.get("waves",[]):
            aid = w.get("attacker_discord_id","")
            aname = w.get("attacker_name","")
            key = aid or aname
            attacker_waves.setdefault(key, []).append({
                "type": w.get("wave_type","?"),
                "send_time": (w.get("send_time") or "—")[:16].replace("T"," "),
                "target": f"{player} {coords}",
                "origin": w.get("origin_village","?"),
                "travel": w.get("travel_seconds",0),
            })

    results = []
    for key, waves in attacker_waves.items():
        # Try to resolve attacker name from waves
        aname = waves[0].get("attacker_name", "") if waves else key
        discord_id = key if (key and key.isdigit()) else ""

        if not key or not key.isdigit():
            results.append({"discord_id": "", "name": aname or key, "status": "no_discord_id"})
            continue

        member = guild.get_member(int(key))
        if not member:
            results.append({"discord_id": key, "name": aname, "status": "not_in_server"})
            continue

        try:
            _icons = {'real':'⚔','fake':'👻','def':'🛡','scout':'🔍','cleaner1':'🧹','cleaner2':'🧹'}
            _labels = {'real':'REAL','fake':'FAKE','def':'DEF','scout':'SCOUT','cleaner1':'1st-CLEANER','cleaner2':'2nd-CLEANER'}
            wave_lines = "\n".join([
                f"  {_icons.get(w['type'],'⚔')} **{_labels.get(w['type'],w['type'].upper())}** → {w['target']}"
                + ("\n    ⚠️ **Cleaner — arrive BEFORE main attacks!**" if w['type'] in ('cleaner1','cleaner2') else "")
                + f"\n    📤 Send: **{w['send_time']}** | 🕐 March: {w['travel']//3600}h{(w['travel']%3600)//60}m | From: {w['origin']}"
                for w in waves
            ])
            msg = (f"⚔️ **Einsatz: {plan_name}**\n"
                   f"{'🎯 Ziel-Allianz: ' + ally + chr(10) if ally else ''}"
                   f"🕐 Ankunftszeit: **{landing}**\n\n"
                   f"**Deine Wellen:**\n{wave_lines}\n\n"
                   f"➡️ Details: TravOps → Einsatzplanung")
            await member.send(msg)
            results.append({"discord_id": key, "name": member.display_name or aname, "status": "sent"})
        except Exception as e:
            err_msg = str(e)[:120]
            results.append({"discord_id": key, "name": member.display_name or aname, "status": "dm_blocked", "error": err_msg})

    sent = sum(1 for r in results if r["status"] == "sent")
    return aiohttp_web.json_response({"ok": True, "sent": sent, "results": results})


async def handle_op_wave_assigned(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Send a Discord DM to one player when a wave is newly assigned to them."""
    try:
        data = await request.json()
        guild_id     = str(data.get("guild_id", ""))
        discord_id   = str(data.get("discord_id", ""))
        attacker_name= data.get("attacker_name", "")
        wave_type    = data.get("wave_type", "real")
        target_x     = data.get("target_x")
        target_y     = data.get("target_y")
        send_time    = (data.get("send_time") or "—")[:16].replace("T", " ")
        plan         = data.get("plan", {})
        plan_name    = plan.get("name", "Operation")
        landing      = (plan.get("landing_time") or "")[:16].replace("T", " ")
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not discord_id or not discord_id.isdigit():
        return aiohttp_web.json_response({"ok": False, "status": "no_discord_id"})

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    member = guild.get_member(int(discord_id))
    if not member:
        return aiohttp_web.json_response({"ok": False, "status": "not_in_server"})

    type_icon = {"real": "⚔️", "fake": "👻", "def": "🛡️", "scout": "🔍", "chief": "👑", "cleaner1": "🧹", "cleaner2": "🧹"}.get(wave_type, "⚔️")
    type_label = {"cleaner1": "1st-Cleaner", "cleaner2": "2nd-Cleaner"}.get(wave_type, wave_type.upper())
    target_str = f"({target_x}|{target_y})" if target_x is not None else "?"
    try:
        msg = (
            f"⚔️ **You've been assigned a wave — {plan_name}**\n"
            f"🕐 Landing: **{landing}**\n\n"
            f"{type_icon} **{type_label}** → {target_str}\n"
            + (f"⚠️ **You are a Cleaner — your wave must arrive BEFORE the main attacks!**\n" if wave_type in ('cleaner1','cleaner2') else "")
            + f"📤 Send time: **{send_time}**\n\n"
            + f"➡️ See your full plan: TravOps → My Op Plan"
        )
        await member.send(msg)
        return aiohttp_web.json_response({"ok": True, "status": "sent"})
    except Exception as e:
        return aiohttp_web.json_response({"ok": True, "status": "dm_blocked", "error": str(e)[:120]})


async def handle_op_hero_action(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Notify a player they have a hero gear switch / no-hero task."""
    try:
        data = await request.json()
        guild_id    = str(data.get("guild_id", ""))
        discord_id  = str(data.get("discord_id", ""))
        action_type = data.get("action_type", "gear_switch")
        item_slot   = data.get("item_slot", "")
        item_name   = data.get("item_name", "")
        notes       = data.get("notes", "")
        plan_name   = data.get("plan_name", "Operation")
        landing     = str(data.get("landing_time", "")).replace("T", " ")[:16]
        player_name = data.get("player_name", "")
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not discord_id or not discord_id.isdigit():
        return aiohttp_web.json_response({"ok": False, "status": "no_discord_id"})

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"})

    member = guild.get_member(int(discord_id))
    if not member:
        return aiohttp_web.json_response({"ok": False, "status": "not_in_server"})

    try:
        if action_type == "no_hero":
            msg = (
                f"⚔️ **Operation: {plan_name}**\n"
                f"🕐 Landing: **{landing}**\n\n"
                f"🦸 **Your real attack must run WITHOUT your hero!**\n"
                f"{('📝 ' + notes) if notes else ''}\n\n"
                f"➡️ Check your waves in TravOps → My Op Plan"
            )
        else:
            slot_label = {"weapon":"⚔️ Weapon","helmet":"🪖 Helmet","body":"🛡 Body","shoes":"👟 Boots","horse":"🐴 Horse"}.get(item_slot, f"🎒 {item_slot}")
            msg = (
                f"⚔️ **Operation: {plan_name}**\n"
                f"🕐 Landing: **{landing}**\n\n"
                f"🔄 **Hero Gear Switch assigned to you:**\n"
                f"{slot_label}: **{item_name or 'as instructed'}**\n"
                f"{'📝 ' + notes if notes else ''}\n\n"
                f"⚠️ Switch your hero gear **before** the operation launches to confuse the enemy!\n"
                f"➡️ TravOps → My Op Plan for full details"
            )
        await member.send(msg)
        return aiohttp_web.json_response({"ok": True, "status": "sent"})
    except Exception as e:
        return aiohttp_web.json_response({"ok": True, "status": "dm_blocked", "error": str(e)[:120]})


async def handle_announce_ep(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Announce a newly activated EP plan to the poll channel + DM all approved ally members."""
    try:
        data = await request.json()
        guild_id   = str(data.get("guild_id", ""))
        plan_name  = str(data.get("plan_name", "Einsatz"))
        landing    = str(data.get("landing_time", "")).replace("T", " ")[:16]
        plan_url   = str(data.get("plan_url", ""))
        poll_channel_id = str(data.get("poll_channel_id", ""))
        member_ids = data.get("member_discord_ids", [])   # list of discord id strings
        member_wave_times = data.get("member_wave_times", {})  # {discord_id: send_time_iso}
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    channel_sent = False
    # Post to poll channel if configured
    if poll_channel_id and poll_channel_id.isdigit():
        ch = guild.get_channel(int(poll_channel_id))
        if not ch:
            try:
                ch = await bot.fetch_channel(int(poll_channel_id))
            except Exception as e:
                print(f"[announce-ep] fetch_channel error: {e}")
                ch = None
        if ch:
            try:
                import discord as _discord
                embed = _discord.Embed(
                    title=f"⚔️ Neuer aktiver Einsatzplan: {plan_name}",
                    description=f"Ein neuer Einsatz wurde aktiviert.\nBitte öffne den Plan, prüfe deine Wellen und bestätige deine Teilnahme.",
                    color=0xED4245,
                )
                if landing:
                    embed.add_field(name="🕐 Einschlagszeit", value=landing, inline=True)
                embed.set_footer(text="TravOps Einsatzplanung")
                view = _discord.ui.View()
                if plan_url:
                    view.add_item(_discord.ui.Button(label="Zum Einsatzplan", url=plan_url, style=_discord.ButtonStyle.link))
                await ch.send(embed=embed, view=view)
                channel_sent = True
            except Exception as e:
                print(f"[announce-ep] channel send error: {e}")

    # DM all approved members
    import calendar as _calendar
    import datetime as _datetime_mod

    def _iso_to_unix(iso: str) -> int | None:
        try:
            dt = _datetime_mod.datetime.fromisoformat(iso.replace("Z", ""))
            return int(_calendar.timegm(dt.timetuple()))
        except Exception:
            return None

    dm_results = []
    for uid in member_ids:
        if not uid or not str(uid).isdigit():
            dm_results.append({"discord_id": str(uid), "name": str(uid), "status": "no_discord_id"})
            continue
        member = guild.get_member(int(uid))
        if not member:
            try:
                member = await guild.fetch_member(int(uid))
            except Exception as e:
                dm_results.append({"discord_id": str(uid), "name": str(uid), "status": "not_in_server", "error": str(e)[:80]})
                continue
        try:
            wave_iso = member_wave_times.get(str(uid)) or member_wave_times.get(uid)
            if wave_iso:
                unix_ts = _iso_to_unix(wave_iso)
                countdown_line = (
                    f"\n⏱️ **Dein Angriff:** <t:{unix_ts}:F> (<t:{unix_ts}:R>)"
                    if unix_ts else ""
                )
            else:
                unix_ts = _iso_to_unix(landing.replace(" ", "T")) if landing else None
                countdown_line = (
                    f"\n⏱️ **Einschlag:** <t:{unix_ts}:F> (<t:{unix_ts}:R>)"
                    if unix_ts else ""
                )
            dm_text = (
                f"⚔️ **Neuer Einsatz: {plan_name}**\n"
                f"{('🕐 Einschlag: **' + landing + '**') if landing else ''}"
                f"{countdown_line}\n\n"
                f"{'Du bist für diesen Einsatz eingeplant.' if wave_iso else 'Du könntest für diesen Einsatz eingeplant sein.'}\n"
                f"{'➡️ ' + plan_url if plan_url else 'Bitte prüfe TravOps → Einsatzplanung.'}"
            )
            await member.send(dm_text)
            dm_results.append({"discord_id": str(uid), "name": member.display_name or member.name, "status": "sent"})
            print(f"[announce-ep] DM sent to {uid} ({member.name})")
        except Exception as e:
            dm_results.append({"discord_id": str(uid), "name": member.display_name or member.name, "status": "dm_blocked", "error": str(e)[:80]})
            print(f"[announce-ep] DM to {uid} ({getattr(member,'name',uid)}) failed: {e}")

    dm_sent = sum(1 for r in dm_results if r["status"] == "sent")
    return aiohttp_web.json_response({"ok": True, "channel": channel_sent, "dms": dm_sent, "results": dm_results})


async def handle_announce_ep_cancelled(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Notify Discord members that an EP has been cancelled."""
    try:
        data = await request.json()
        guild_id   = str(data.get("guild_id", ""))
        plan_name  = str(data.get("plan_name", "Einsatz"))
        plan_url   = str(data.get("plan_url", ""))
        member_ids = data.get("member_discord_ids", [])
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    dm_sent = 0
    dm_text = (
        f"❌ **Einsatz abgebrochen: {plan_name}**\n\n"
        f"Dieser Einsatz wurde abgebrochen und findet nicht statt.\n"
        f"{'➡️ ' + plan_url if plan_url else ''}"
    )
    for uid in member_ids:
        if not uid or not str(uid).isdigit():
            continue
        member = guild.get_member(int(uid))
        if not member:
            try:
                member = await guild.fetch_member(int(uid))
            except Exception as e:
                print(f"[announce-ep-cancelled] fetch_member {uid} failed: {e}")
                continue
        try:
            await member.send(dm_text)
            dm_sent += 1
            print(f"[announce-ep-cancelled] DM sent to {uid} ({member.name})")
        except Exception as e:
            print(f"[announce-ep-cancelled] DM to {uid} ({member.name}) failed: {e}")

    return aiohttp_web.json_response({"ok": True, "dms": dm_sent})


async def _get_or_create_archive_category(guild: discord.Guild) -> discord.CategoryChannel:
    """Return the '📦 Archiv' category, creating it if needed."""
    ARCHIVE_NAME = "📦 Archiv"
    for cat in guild.categories:
        if cat.name == ARCHIVE_NAME:
            return cat
    # Create with restricted permissions — only bot + admins see it
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
        ),
    }
    # Grant view to anyone who already had it (admins etc.) by inheriting nothing
    return await guild.create_category(
        ARCHIVE_NAME,
        overwrites=overwrites,
        reason="Defend-Archiv-Kategorie automatisch erstellt",
    )


async def _get_defend_channel(guild: discord.Guild, channel_id: str):
    channel = guild.get_channel(int(channel_id))
    if not channel:
        channel = await guild.fetch_channel(int(channel_id))
    return channel


async def handle_archive_defend_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Move defend channel into the 📦 Archiv category and make it read-only."""
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild_id   = str(data.get("guild_id", ""))
    channel_id = str(data.get("channel_id", ""))

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    try:
        channel = await _get_defend_channel(guild, channel_id)
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

    try:
        archive_cat = await _get_or_create_archive_category(guild)

        # Load archive role config: only these roles may view archived channels
        config = await database.get_guild_config(guild_id) or {}
        archive_role_ids_str = config.get("archive_role_ids") or config.get("allowed_role_ids") or ""
        allowed_role_ids = {r.strip() for r in archive_role_ids_str.split(",") if r.strip()}

        # Build fresh overwrites — do NOT copy old defend-channel overwrites,
        # those grant view to all defend participants which must not carry over.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        # Grant view (read-only) only to explicitly configured archive roles
        for role_id_str in allowed_role_ids:
            role = guild.get_role(int(role_id_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, add_reactions=False
                )

        await channel.edit(
            category=archive_cat,
            overwrites=overwrites,
            reason="Defend-Channel archiviert",
        )
        await channel.send("📦 **Dieser Channel wurde archiviert** und ins Archiv verschoben.")
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    return aiohttp_web.json_response({"ok": True})


async def handle_unarchive_defend_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Move defend channel back from archive to original category and restore permissions."""
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild_id   = str(data.get("guild_id", ""))
    channel_id = str(data.get("channel_id", ""))
    # Optionally the caller can pass the original category id
    orig_category_id = data.get("category_id")

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    try:
        channel = await _get_defend_channel(guild, channel_id)
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

    try:
        # Find original category from config or fall back to first non-archive category
        config = await database.get_guild_config(guild_id)
        cat_id = orig_category_id or (config or {}).get("category_id")
        target_cat = None
        if cat_id:
            target_cat = guild.get_channel(int(cat_id))

        # Re-enable send_messages for all existing overwrites
        overwrites = {}
        for target, ow in channel.overwrites.items():
            allow, deny = ow.pair()
            new_ow = discord.PermissionOverwrite.from_pair(allow, deny)
            new_ow.update(send_messages=None, add_reactions=None)
            overwrites[target] = new_ow

        edit_kwargs = {"overwrites": overwrites, "reason": "Defend-Channel wieder geöffnet"}
        if target_cat:
            edit_kwargs["category"] = target_cat

        await channel.edit(**edit_kwargs)
        await channel.send(
            "🔓 **Dieser Channel wurde wieder geöffnet!** "
            "Beiträge sind erneut möglich."
        )
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    return aiohttp_web.json_response({"ok": True})


ARCHIVE_RES_NAME = "📦 Archive-Pushes"


async def _get_or_create_res_archive_category(guild: discord.Guild) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name == ARCHIVE_RES_NAME:
            return cat
    return await guild.create_category(
        ARCHIVE_RES_NAME,
        overwrites={
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        },
        reason="Res-Push archive category auto-created",
    )


async def handle_archive_res_push_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Move a res-push channel to 📦 Archive-Pushes and make it read-only."""
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild_id   = str(data.get("guild_id", ""))
    channel_id = str(data.get("channel_id", ""))

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    try:
        channel = guild.get_channel(int(channel_id))
        if not channel:
            channel = await guild.fetch_channel(int(channel_id))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

    try:
        archive_cat = await _get_or_create_res_archive_category(guild)

        # Build read-only overwrites — copy existing, strip send rights
        overwrites = {}
        for target, ow in channel.overwrites.items():
            allow, deny = ow.pair()
            new_ow = discord.PermissionOverwrite.from_pair(allow, deny)
            new_ow.update(send_messages=False, add_reactions=False)
            overwrites[target] = new_ow
        # Fully hide from regular members, keep bot access
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)

        await channel.edit(category=archive_cat, overwrites=overwrites, reason="Res-Push archived")
        await channel.send("📦 **This push channel has been archived.** No further contributions possible.")
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    return aiohttp_web.json_response({"ok": True})


async def handle_unarchive_res_push_channel(request: aiohttp_web.Request) -> aiohttp_web.Response:
    """Move a res-push channel back from archive and fully restore permissions from config."""
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)

    guild_id   = str(data.get("guild_id", ""))
    channel_id = str(data.get("channel_id", ""))
    requester_id = str(data.get("requester_id", ""))

    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if not guild:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

    try:
        channel = guild.get_channel(int(channel_id))
        if not channel:
            channel = await guild.fetch_channel(int(channel_id))
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "channel not found"}, status=404)

    try:
        config = await database.get_guild_config(guild_id)

        # Find "🪖 Active Pushes" category (same one used on accept)
        PUSH_CAT_NAME = "🪖 Active Pushes"
        push_cat = None
        for cat in guild.categories:
            if cat.name == PUSH_CAT_NAME:
                push_cat = cat
                break

        # Rebuild permissions from config (same logic as accept)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_channels=True, manage_messages=True,
            ),
        }
        # Manager roles
        for role_id_str in ((config or {}).get("res_manager_role_ids") or "").split(","):
            role_id_str = role_id_str.strip()
            if not role_id_str:
                continue
            role = guild.get_role(int(role_id_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_messages=True,
                )
        # Member roles
        for role_id_str in ((config or {}).get("allowed_role_ids") or "").split(","):
            role_id_str = role_id_str.strip()
            if not role_id_str:
                continue
            role = guild.get_role(int(role_id_str))
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        # Original requester
        if requester_id:
            try:
                member = guild.get_member(int(requester_id))
                if member:
                    overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            except Exception:
                pass

        edit_kwargs = {"overwrites": overwrites, "reason": "Res-Push channel reactivated"}
        if push_cat:
            edit_kwargs["category"] = push_cat

        await channel.edit(**edit_kwargs)
        await channel.send("🔓 **This push channel has been reactivated!** Contributions are open again.")
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    return aiohttp_web.json_response({"ok": True})


async def handle_refresh_defend_tracking(request: aiohttp_web.Request):
    """Refresh the Discord tracking embed for a defend channel after dashboard edits."""
    data = await request.json()
    channel_id = str(data.get("channel_id", ""))
    if not channel_id:
        return aiohttp_web.json_response({"ok": False, "error": "missing channel_id"}, status=400)
    try:
        from cogs.hub import _build_defend_tracking_embed
        contrib_rows = await database.get_defend_sent(channel_id)
        defend_rec   = await database.get_defend_channel(channel_id)
        if not defend_rec:
            return aiohttp_web.json_response({"ok": False, "error": "channel not found"})
        guild_id   = defend_rec.get("guild_id", "")
        config     = await database.get_guild_config(guild_id)
        tw_world   = (config or {}).get("tw_world") or ""
        coords     = defend_rec.get("coords") or ""
        goal_raw   = defend_rec.get("goal") or ""
        ratio      = defend_rec.get("ratio") or ""
        lang       = "de"
        embed = _build_defend_tracking_embed(contrib_rows, lang, coords, tw_world, goal_raw, ratio)
        tracking_msg_id = defend_rec.get("tracking_msg_id")
        if not tracking_msg_id:
            return aiohttp_web.json_response({"ok": False, "error": "no tracking message"})
        # Find the Discord channel
        disc_channel = None
        for guild in bot.guilds:
            ch = guild.get_channel(int(channel_id))
            if ch:
                disc_channel = ch
                break
        if not disc_channel:
            return aiohttp_web.json_response({"ok": False, "error": "discord channel not found"})
        msg = await disc_channel.fetch_message(int(tracking_msg_id))
        await msg.edit(embed=embed)
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)
    return aiohttp_web.json_response({"ok": True})


async def handle_create_defend_channel(request: aiohttp_web.Request):
    """Create a defend channel from the web dashboard (attack detection page)."""
    data = await request.json()
    guild_id      = str(data.get("guild_id", ""))
    defender      = str(data.get("defender", ""))        # own village name + coords
    attacker      = str(data.get("attacker", ""))        # attacker player + village
    coords        = str(data.get("coords", ""))          # target coords "X|Y"
    arrival_time  = str(data.get("arrival_time", ""))    # ISO or "HH:MM" string
    troop_goal    = str(data.get("troop_goal", ""))      # crop/h goal
    ratio         = str(data.get("ratio", ""))           # e.g. "70/30"
    notes         = str(data.get("notes", ""))
    requested_by_id   = str(data.get("requested_by_id", ""))
    requested_by_name = str(data.get("requested_by_name", "Webdashboard"))

    if not guild_id or not defender or not coords:
        return aiohttp_web.json_response({"ok": False, "error": "missing fields"}, status=400)

    try:
        from cogs.hub import _create_defend_channel_api, DefendCloseView
        disc_guild = bot.get_guild(int(guild_id))
        if not disc_guild:
            return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)

        channel_id, channel_mention = await _create_defend_channel_api(
            guild=disc_guild,
            defender=defender,
            attacker=attacker,
            coords=coords,
            arrival_time=arrival_time,
            troop_goal=troop_goal,
            ratio=ratio,
            notes=notes,
            requested_by_id=requested_by_id,
            requested_by_name=requested_by_name,
        )
        return aiohttp_web.json_response({"ok": True, "channel_id": channel_id, "channel_mention": channel_mention})
    except Exception as e:
        import traceback; traceback.print_exc()
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)


async def start_api_server():
    app = aiohttp_web.Application()
    app.router.add_post("/api/create-report-channel", handle_create_report_channel)
    app.router.add_post("/api/create-request-hub", handle_create_request_hub)
    app.router.add_post("/api/check-permissions", handle_check_permissions)
    app.router.add_post("/api/set-hero-scout-channel", handle_set_hero_scout_channel)
    app.router.add_post("/api/list-channels", handle_list_channels)
    app.router.add_post("/api/leave-guild", handle_leave_guild)
    app.router.add_post("/api/guild-info", handle_guild_info)
    app.router.add_post("/api/hero-scout-build-library", handle_build_hero_library)
    app.router.add_get("/api/hero-scout-library-status", handle_hero_library_status)
    app.router.add_post("/api/refresh-res-push", handle_refresh_res_push)
    app.router.add_post("/api/refresh-request-hub", handle_refresh_request_hub)
    app.router.add_post("/api/op-notify", handle_op_notify)
    app.router.add_post("/api/op-wave-assigned", handle_op_wave_assigned)
    app.router.add_post("/api/op-hero-action", handle_op_hero_action)
    app.router.add_post("/api/announce-ep", handle_announce_ep)
    app.router.add_post("/api/announce-ep-cancelled", handle_announce_ep_cancelled)
    app.router.add_post("/api/create-defend-channel", handle_create_defend_channel)
    app.router.add_post("/api/archive-defend-channel", handle_archive_defend_channel)
    app.router.add_post("/api/unarchive-defend-channel", handle_unarchive_defend_channel)
    app.router.add_post("/api/refresh-defend-tracking", handle_refresh_defend_tracking)
    app.router.add_post("/api/archive-res-push-channel", handle_archive_res_push_channel)
    app.router.add_post("/api/unarchive-res-push-channel", handle_unarchive_res_push_channel)
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
