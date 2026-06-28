import discord
from discord.ext import commands
import database

PRIO_LABELS = {"high": "🔴 High", "mid": "🟡 Mid", "low": "🟢 Low"}
PRIO_COLORS = {"high": discord.ButtonStyle.danger, "mid": discord.ButtonStyle.secondary, "low": discord.ButtonStyle.success}


class PriorityModal(discord.ui.Modal, title="Artifact Interest"):
    priority = discord.ui.Select(
        placeholder="Select priority",
        options=[
            discord.SelectOption(label="High", value="high", emoji="🔴"),
            discord.SelectOption(label="Mid", value="mid", emoji="🟡"),
            discord.SelectOption(label="Low", value="low", emoji="🟢"),
        ],
    )

    def __init__(self, guild_id: str, artifact_id: int, artifact_name: str):
        super().__init__(title=f"Interest: {artifact_name[:40]}")
        self.guild_id = guild_id
        self.artifact_id = artifact_id
        self.artifact_name = artifact_name
        self.priority = discord.ui.Select(
            placeholder="Select priority",
            options=[
                discord.SelectOption(label="🔴 High — I need this urgently", value="high"),
                discord.SelectOption(label="🟡 Mid — Would be nice to have", value="mid"),
                discord.SelectOption(label="🟢 Low — Interested if available", value="low"),
            ],
        )
        self.add_item(self.priority)

    async def on_submit(self, interaction: discord.Interaction):
        prio = self.priority.values[0] if self.priority.values else "mid"
        await database.upsert_artifact_interest(
            self.guild_id, self.artifact_id,
            str(interaction.user.id), interaction.user.display_name, prio
        )
        label = PRIO_LABELS.get(prio, prio)
        await interaction.response.send_message(
            f"✅ Your interest in **{self.artifact_name}** registered as **{label}**.",
            ephemeral=True
        )


class ArtifactButton(discord.ui.Button):
    def __init__(self, guild_id: str, artifact_id: int, artifact_name: str, artifact_type: str, size: str):
        size_emoji = {"unique": "⭐", "large": "🟡", "small": "🔵"}.get(size, "🏺")
        super().__init__(
            label=artifact_name[:60],
            emoji=size_emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"persistent:art_interest:{guild_id}:{artifact_id}",
        )
        self.guild_id = guild_id
        self.artifact_id = artifact_id
        self.artifact_name = artifact_name

    async def callback(self, interaction: discord.Interaction):
        modal = PriorityModal(self.guild_id, self.artifact_id, self.artifact_name)
        await interaction.response.send_modal(modal)


class ArtifactInterestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


class ArtifactInterest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(ArtifactInterestView())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if not cid.startswith("persistent:art_interest:"):
            return
        parts = cid.split(":")
        if len(parts) < 4:
            return
        guild_id = parts[2]
        artifact_id = int(parts[3])
        artifacts = await database.get_artifacts(guild_id)
        art = next((a for a in artifacts if a["id"] == artifact_id), None)
        name = art["name"] if art else f"Artifact #{artifact_id}"
        modal = PriorityModal(guild_id, artifact_id, name)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArtifactInterest(bot))
