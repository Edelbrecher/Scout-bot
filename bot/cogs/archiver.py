import discord
from discord.ext import commands

import database
from utils import PREMIUM_STATUSES, travops_footer

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class Archiver(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not message.attachments:
            return

        # Subscription gate: silently skip non-premium guilds
        status = await database.get_subscription_status(str(message.guild.id))
        if status not in PREMIUM_STATUSES:
            return

        if not await database.is_scout_channel(str(message.channel.id)):
            return

        images = [
            a for a in message.attachments
            if a.content_type and a.content_type.split(";")[0].strip() in IMAGE_TYPES
        ]
        # Accept attachments without content_type as fallback (some clients don't send it)
        if not images:
            images = [
                a for a in message.attachments
                if not a.content_type and any(
                    a.filename.lower().endswith(ext)
                    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")
                )
            ]
        if not images:
            return

        config = await database.get_guild_config(str(message.guild.id))
        if not config or not config.get("archive_channel_id"):
            print(f"[archiver] No archive_channel_id configured for guild {message.guild.id}")
            return

        archive_channel = message.guild.get_channel(int(config["archive_channel_id"]))
        if not archive_channel:
            print(f"[archiver] Archive channel {config['archive_channel_id']} not found in guild {message.guild.id}")
            return

        scout = await database.get_scout_channel_info(str(message.channel.id))

        embed = discord.Embed(color=discord.Color.green())
        if scout:
            embed.title = "📁 Scout Archive"
            embed.add_field(name="Player", value=scout["player"] or "—", inline=True)
            embed.add_field(name="Village", value=scout["village"] or "—", inline=True)
            embed.add_field(name="Coordinates", value=scout["coordinates"] or "—", inline=True)
            embed.add_field(name="Scout Time", value=scout["scout_time"] or "—", inline=True)
            if scout.get("additional_info"):
                embed.add_field(name="Additional Info", value=scout["additional_info"], inline=False)
            embed.add_field(name="Posted by", value=message.author.mention, inline=True)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        else:
            embed.description = f"📁 From {message.channel.mention} · by {message.author.mention}"
        embed.set_footer(**travops_footer(f"Requested {scout['created_at'][:16] if scout else ''} · Jump → {message.jump_url}"))

        files = []
        for attachment in images:
            try:
                files.append(await attachment.to_file(use_cached=True))
            except Exception as e:
                print(f"[archiver] Failed to download attachment {attachment.filename}: {e}")
                # Fallback: embed the image URL directly if download fails
                embed.set_image(url=attachment.url)

        try:
            await archive_channel.send(embed=embed, files=files if files else discord.utils.MISSING)
            print(f"[archiver] Archived {len(files)} image(s) from #{message.channel.name} → #{archive_channel.name}")
        except discord.Forbidden:
            print(f"[archiver] No permission to send in archive channel {archive_channel.id}")
        except Exception as e:
            print(f"[archiver] Failed to send to archive channel: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Archiver(bot))
