"""
hub.py — Global Request Hub cog.
One Discord channel with all request buttons.
Each button opens a modal and creates a specific channel in the configured category.
"""
import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium


# ---------------------------------------------------------------------------
# Helpers (shared with scout.py logic)
# ---------------------------------------------------------------------------

async def _get_config_and_category(guild: discord.Guild) -> tuple[dict | None, discord.CategoryChannel | None]:
    config = await database.get_guild_config(str(guild.id))
    if not config or not config.get("category_id"):
        return None, None
    category = guild.get_channel(int(config["category_id"]))
    if not category or not isinstance(category, discord.CategoryChannel):
        return config, None
    return config, category


def _build_overwrites(guild: discord.Guild, config: dict, requester: discord.Member) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True,
        ),
        requester: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
    }
    for role_id_str in (config.get("allowed_role_ids") or "").split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
    return overwrites


def _safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "-", s.lower())[:40].strip("-")


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class ScoutHubModal(discord.ui.Modal, title="🔍 Scout-Request"):
    player = discord.ui.TextInput(label="Spieler-Name", placeholder="z.B. Currax", max_length=100)
    coordinates = discord.ui.TextInput(label="Koordinaten", placeholder="z.B. (102|47)", max_length=30)
    village = discord.ui.TextInput(label="Dorfname", placeholder="z.B. Hauptdorf", required=False, max_length=100)
    time = discord.ui.TextInput(label="Bis wann?", placeholder="z.B. heute 22:00 UTC", max_length=60)
    additional_info = discord.ui.TextInput(
        label="Zusätzliche Infos", required=False,
        style=discord.TextStyle.paragraph, max_length=300,
    )

    def __init__(self, corn: bool = False, permanent: bool = False):
        if corn:
            self.title = "🌾 Kornspäh-Request"
        elif permanent:
            self.title = "📡 Permanent-Scout Anfrage"
        super().__init__()
        self.corn = corn
        self.permanent = permanent

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_premium(interaction):
            return
        guild = interaction.guild
        config, category = await _get_config_and_category(guild)
        if not config:
            await interaction.response.send_message("⚠️ Bot nicht vollständig konfiguriert.", ephemeral=True)
            return
        if not category:
            await interaction.response.send_message("⚠️ Kategorie nicht gefunden.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        prefix = "perm-scout" if self.permanent else ("kornspäh" if self.corn else "scout")
        channel_name = f"{prefix}-{_safe(self.player.value)}-{_safe(self.coordinates.value)}"[:100]

        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"{'Permanent-Scout' if self.permanent else ('Kornspäh' if self.corn else 'Scout')}: {self.player.value} @ {self.coordinates.value}",
            overwrites=overwrites,
        )

        if self.permanent:
            color = discord.Color.teal()
            title = "📡 Permanent-Scout Anfrage"
            desc = (
                f"Dauerhaft stationierte Späher für Dorf **{self.village.value or '—'}** werden benötigt.\n"
                f"Spieler: **{self.player.value}** | Koords: **{self.coordinates.value}**"
            )
        elif self.corn:
            color = discord.Color.gold()
            title = "🌾 Kornspäh-Anfrage"
            desc = f"Kornspäh-Request für **{self.player.value}** @ {self.coordinates.value}"
        else:
            color = discord.Color.blurple()
            title = "🔍 Scout-Request"
            desc = f"Scout-Request für **{self.player.value}** @ {self.coordinates.value}"

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="Spieler", value=self.player.value, inline=True)
        embed.add_field(name="Koordinaten", value=self.coordinates.value, inline=True)
        if self.village.value:
            embed.add_field(name="Dorf", value=self.village.value, inline=True)
        embed.add_field(name="Bis wann", value=self.time.value, inline=True)
        if self.additional_info.value:
            embed.add_field(name="Infos", value=self.additional_info.value, inline=False)
        embed.set_footer(text=f"Angefragt von {interaction.user.display_name}")

        from cogs.scout import ScoutActionView
        await new_channel.send(
            content=f"Scout-Anfrage von {interaction.user.mention}",
            embed=embed,
            view=ScoutActionView(),
        )

        # Save to DB (reuse scout channel registration)
        await database.add_scout_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            player=self.player.value, coordinates=self.coordinates.value,
            village=self.village.value or "", scout_time=self.time.value,
            additional_info=self.additional_info.value or "",
            requested_by_id=str(interaction.user.id),
            requested_by_name=interaction.user.display_name,
            corn_scout=self.corn,
        )

        await interaction.followup.send(f"✅ Channel erstellt: {new_channel.mention}", ephemeral=True)


class DefendModal(discord.ui.Modal):
    attacker = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival = discord.ui.TextInput(label="Ankunftszeit", placeholder="z.B. 23:45 UTC", max_length=60)
    size = discord.ui.TextInput(label="Angriffsgröße (schätzen)", placeholder="z.B. klein / mittel / groß", required=False, max_length=60)
    notes = discord.ui.TextInput(label="Notizen", required=False, style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, timed: bool = False):
        self.title = "⏱️ Timed-Defend Anfrage" if timed else "🛡️ Defend Anfrage"
        super().__init__()
        self.timed = timed

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        config, category = await _get_config_and_category(guild)
        if not config:
            await interaction.response.send_message("⚠️ Bot nicht konfiguriert.", ephemeral=True)
            return
        if not category:
            await interaction.response.send_message("⚠️ Kategorie nicht gefunden.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        prefix = "timed-def" if self.timed else "defend"
        channel_name = f"{prefix}-{_safe(self.attacker.value)}-{_safe(self.coords.value)}"[:100]

        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"{'Timed-Defend' if self.timed else 'Defend'}: {self.attacker.value} @ {self.coords.value} | Ankunft: {self.arrival.value}",
            overwrites=overwrites,
        )

        color = discord.Color.from_rgb(239, 68, 68)
        title = "⏱️ Timed-Defend" if self.timed else "🛡️ Defend"
        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Angreifer", value=self.attacker.value, inline=True)
        embed.add_field(name="Ziel", value=self.coords.value, inline=True)
        embed.add_field(name="Ankunft", value=self.arrival.value, inline=True)
        if self.size.value:
            embed.add_field(name="Größe", value=self.size.value, inline=True)
        if self.notes.value:
            embed.add_field(name="Notizen", value=self.notes.value, inline=False)
        embed.set_footer(text=f"Gemeldet von {interaction.user.display_name}")

        # Close button view
        view = DefendCloseView()
        await new_channel.send(
            content=f"🚨 {interaction.user.mention} — Defend-Anfrage!",
            embed=embed,
            view=view,
        )

        await database.add_defend_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            type="timed_defend" if self.timed else "defend",
            attacker=self.attacker.value, coords=self.coords.value,
            arrival_time=self.arrival.value, notes=self.notes.value or "",
            requested_by_id=str(interaction.user.id),
            requested_by_name=interaction.user.display_name,
        )

        await interaction.followup.send(f"✅ Defend-Channel erstellt: {new_channel.mention}", ephemeral=True)


# ---------------------------------------------------------------------------
# Defend close view
# ---------------------------------------------------------------------------

class DefendCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Defend erledigt",
        style=discord.ButtonStyle.success,
        custom_id="persistent:defend_close",
    )
    async def close_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await database.close_defend_channel(str(interaction.channel.id))
        await interaction.response.send_message(
            f"✅ Defend als erledigt markiert von {interaction.user.mention}.\n"
            "Channel wird in 2 Minuten gelöscht."
        )
        button.disabled = True
        await interaction.message.edit(view=self)
        await asyncio.sleep(120)
        try:
            await interaction.channel.delete(reason="Defend erledigt")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Res-Push Modal
# ---------------------------------------------------------------------------

class ResPushHubModal(discord.ui.Modal, title="🪖 Res-Push Anfrage"):
    village = discord.ui.TextInput(label="Dein Dorf (Empfänger)", placeholder="z.B. Hauptdorf (102|47)", max_length=100)
    resources = discord.ui.TextInput(
        label="Was brauchst du?",
        placeholder="z.B. 50k Holz, 30k Lehm",
        style=discord.TextStyle.paragraph, max_length=300,
    )
    until = discord.ui.TextInput(label="Bis wann?", placeholder="z.B. heute 22:00 UTC", max_length=60)
    notes = discord.ui.TextInput(label="Weitere Infos", required=False, style=discord.TextStyle.paragraph, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        config, category = await _get_config_and_category(guild)
        if not config or not category:
            await interaction.response.send_message("⚠️ Bot nicht konfiguriert.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        channel_name = f"res-push-{_safe(interaction.user.display_name)}"[:100]
        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Res-Push für {interaction.user.display_name}: {self.resources.value[:80]}",
            overwrites=overwrites,
        )

        embed = discord.Embed(title="🪖 Res-Push Anfrage", color=discord.Color.orange())
        embed.add_field(name="Empfänger", value=interaction.user.mention, inline=True)
        embed.add_field(name="Dorf", value=self.village.value, inline=True)
        embed.add_field(name="Bis wann", value=self.until.value, inline=True)
        embed.add_field(name="Benötigt", value=self.resources.value, inline=False)
        if self.notes.value:
            embed.add_field(name="Notizen", value=self.notes.value, inline=False)
        embed.set_footer(text=f"Angefragt von {interaction.user.display_name}")

        await new_channel.send(content=f"🪖 {interaction.user.mention}", embed=embed)
        await interaction.followup.send(f"✅ Res-Push Channel erstellt: {new_channel.mention}", ephemeral=True)


# ---------------------------------------------------------------------------
# Request Hub View (6 buttons, persistent)
# ---------------------------------------------------------------------------

class RequestHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Scout", emoji="🔍", style=discord.ButtonStyle.primary,
        custom_id="persistent:hub_scout", row=0,
    )
    async def hub_scout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=False, permanent=False))

    @discord.ui.button(
        label="Kornspäh", emoji="🌾", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_corn", row=0,
    )
    async def hub_corn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=True, permanent=False))

    @discord.ui.button(
        label="Permanent-Scout", emoji="📡", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_perm_scout", row=0,
    )
    async def hub_perm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=False, permanent=True))

    @discord.ui.button(
        label="Res-Push", emoji="🪖", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_res_push", row=1,
    )
    async def hub_res_push(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResPushHubModal())

    @discord.ui.button(
        label="Defend", emoji="🛡️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_defend", row=1,
    )
    async def hub_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DefendModal(timed=False))

    @discord.ui.button(
        label="Timed-Defend", emoji="⏱️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_timed_defend", row=1,
    )
    async def hub_timed_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DefendModal(timed=True))


# ---------------------------------------------------------------------------
# Hub Cog
# ---------------------------------------------------------------------------

class Hub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Mark defend channel as closed when deleted in Discord."""
        await database.close_defend_channel(str(channel.id))

    @commands.Cog.listener()
    async def on_ready(self):
        """Restore persistent hub views on startup."""
        # The views are registered globally via setup(), nothing else needed
        print("[hub] RequestHubView registered.", flush=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hub(bot))
    bot.add_view(RequestHubView())
    bot.add_view(DefendCloseView())
