import asyncio
import os

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
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        # Sync all current guilds to DB
        for guild in self.guilds:
            await database.upsert_guild_name(str(guild.id), guild.name)
        print(f"Synced {len(self.guilds)} guild(s) to database.")

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
                    await database.upsert_guild_name(str(guild.id), guild.name)
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

        await database.upsert_guild_name(str(guild.id), guild.name)
        print(f"Joined guild: {guild.name} ({guild.id})")


bot = ScouterBot()


async def main():
    async with bot:
        await bot.start(os.environ["DISCORD_TOKEN"])


asyncio.run(main())
