"""WE WIN cog — countdown embed with WINNER button that spams congratulations."""
import asyncio
import discord
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../web"))
import database

CONGRATS_LINES = [
    "🏆 **WE WON THE SERVER!!!** 🏆",
    "🎉🎉🎉 CONGRATULATIONS TO EVERY SINGLE ONE OF YOU! 🎉🎉🎉",
    "👑 THIS IS WHAT WE TRAINED FOR! 👑",
    "⚔️ WARRIORS! YOU DID IT! ⚔️",
    "🔥🔥🔥 ABSOLUTE LEGENDS! 🔥🔥🔥",
    "💪 NO ONE COULD STOP US! 💪",
    "🥇 SERVER CHAMPIONS! 🥇",
    "😤 UNDEFEATED. UNSTOPPABLE. US. 😤",
    "🎊 WE BUILT THIS TOGETHER — AND WE WON TOGETHER! 🎊",
    "🌟 THIS IS THE GREATEST TEAM I'VE EVER PLAYED WITH! 🌟",
]


async def _has_winner_perm(interaction: discord.Interaction) -> bool:
    """True if user is Discord admin or has ally_manage permission."""
    if interaction.user.guild_permissions.administrator:
        return True
    perms = await database.get_member_permissions(
        str(interaction.guild_id), str(interaction.user.id)
    )
    return bool(perms & {"ally_manage"})


class WinnerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🏆 WINNER",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:wewin_winner",
    )
    async def winner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _has_winner_perm(interaction):
            await interaction.response.send_message(
                "⛔ Only Leaders and Admins can trigger this.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        # Collect all non-bot member mentions — chunk into ≤200 chars each
        member_mentions = [m.mention for m in guild.members if not m.bot]
        chunks: list[str] = []
        current = ""
        for mention in member_mentions:
            if len(current) + len(mention) + 1 > 1900:
                chunks.append(current.strip())
                current = mention + " "
            else:
                current += mention + " "
        if current.strip():
            chunks.append(current.strip())

        # Disable the button so it can't be pressed again
        button.disabled = True
        button.label = "🏆 WE WON!"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        # Send the spam — congrats lines interleaved with mention chunks
        all_messages: list[str] = []
        # Start with a big header
        all_messages.append("# 🏆🏆🏆  WE WON THE SERVER!  🏆🏆🏆")

        # Interleave congrats lines with mention chunks
        congrats_cycle = CONGRATS_LINES[:]
        for i, chunk in enumerate(chunks):
            all_messages.append(chunk)
            if congrats_cycle:
                all_messages.append(congrats_cycle.pop(0))

        # Add any remaining congrats lines
        all_messages.extend(congrats_cycle)

        # Final epic closer + music
        all_messages.append("🎊🎊🎊 **CONGRATULATIONS TO EVERY WARRIOR IN THIS ALLIANCE!** 🎊🎊🎊")
        all_messages.append("@here 🏆 **WE ARE THE CHAMPIONS!** 🏆 @here")
        all_messages.append("🎵 **This one's for you, champions:** https://www.youtube.com/watch?v=04854XqcfBY")

        for msg_content in all_messages:
            try:
                await channel.send(msg_content)
                await asyncio.sleep(0.6)  # slight delay to avoid rate-limit
            except discord.HTTPException:
                await asyncio.sleep(2)
                try:
                    await channel.send(msg_content)
                except Exception:
                    pass

        await interaction.followup.send("🎉 Done! The server has been celebrated!", ephemeral=True)


class WeWinCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(WinnerView())


async def setup(bot):
    await bot.add_cog(WeWinCog(bot))
    # Register the persistent view immediately
    bot.add_view(WinnerView())
