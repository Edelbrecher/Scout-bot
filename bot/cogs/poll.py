import discord
from discord.ext import commands

import database

RESPONSE_LABELS = {
    "available":   ("✅", "Dabei"),
    "maybe":       ("⏰", "Vielleicht"),
    "unavailable": ("❌", "Nicht dabei"),
}


class PollCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("poll_"):
            return

        # custom_id format: poll_available_{id} | poll_maybe_{id} | poll_unavailable_{id}
        parts = cid.split("_")
        if len(parts) < 3:
            return
        response_key = parts[1]   # available / maybe / unavailable
        poll_id_str  = parts[2]

        if response_key not in RESPONSE_LABELS:
            return
        if not poll_id_str.isdigit():
            return

        poll_id = int(poll_id_str)
        poll = await database.get_poll(poll_id)
        if not poll:
            await interaction.response.send_message("⚠️ Umfrage nicht gefunden.", ephemeral=True)
            return
        if poll.get("status") == "closed":
            await interaction.response.send_message("🔒 Diese Umfrage ist bereits geschlossen.", ephemeral=True)
            return

        user = interaction.user
        await database.upsert_poll_response(
            poll_id=poll_id,
            user_id=str(user.id),
            user_name=user.display_name,
            response=response_key,
        )

        emoji, label = RESPONSE_LABELS[response_key]
        await interaction.response.send_message(
            f"{emoji} Deine Antwort wurde gespeichert: **{label}**",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PollCog(bot))
