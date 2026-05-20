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
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        # Sync all current guilds to DB
        for guild in self.guilds:
            await database.upsert_guild_name(str(guild.id), guild.name)
        print(f"Synced {len(self.guilds)} guild(s) to database.")

    async def on_guild_join(self, guild: discord.Guild):
        await database.upsert_guild_name(str(guild.id), guild.name)
        print(f"Joined guild: {guild.name} ({guild.id})")


bot = ScouterBot()


async def main():
    async with bot:
        await bot.start(os.environ["DISCORD_TOKEN"])


asyncio.run(main())
