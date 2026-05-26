"""
hub.py — Global Request Hub cog.
One Discord channel with all request buttons.
Each button opens a modal and creates a specific channel in the configured category.
"""
import asyncio
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium, travops_footer
from i18n import t, get_guild_lang


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
        lang = await get_guild_lang(str(guild.id))
        config, category = await _get_config_and_category(guild)
        if not config:
            await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
            return
        if not category:
            await interaction.response.send_message(t(lang, "category_not_found"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        prefix = "perm-scout" if self.permanent else ("corn-scout" if self.corn else "scout")
        channel_name = f"{prefix}-{_safe(self.player.value)}-{_safe(self.coordinates.value)}"[:100]

        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"{'Permanent-Scout' if self.permanent else ('Corn-Scout' if self.corn else 'Scout')}: {self.player.value} @ {self.coordinates.value}",
            overwrites=overwrites,
        )

        if self.permanent:
            color = discord.Color.teal()
            title = t(lang, "perm_scout.title")
            desc = t(lang, "perm_scout.desc", village=self.village.value or "—",
                     player=self.player.value, coords=self.coordinates.value)
        elif self.corn:
            color = discord.Color.gold()
            title = t(lang, "corn.title")
            desc = t(lang, "corn.desc", player=self.player.value, coords=self.coordinates.value)
        else:
            color = discord.Color.blurple()
            title = t(lang, "scout.title")
            desc = t(lang, "hub.scout.desc", player=self.player.value, coords=self.coordinates.value)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name=t(lang, "hub.scout.field.player_embed"), value=self.player.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.coords_embed"), value=self.coordinates.value, inline=True)
        if self.village.value:
            embed.add_field(name=t(lang, "hub.scout.field.village_embed"), value=self.village.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.time_embed"), value=self.time.value, inline=True)
        if self.additional_info.value:
            embed.add_field(name=t(lang, "hub.scout.field.info_embed"), value=self.additional_info.value, inline=False)
        embed.set_footer(**travops_footer(t(lang, "requested_by", user=interaction.user.display_name)))

        from cogs.scout import ScoutActionView
        await new_channel.send(
            content=t(lang, "hub.scout.new_request", user=interaction.user.mention),
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

        await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)


def _parse_time(s: str) -> datetime | None:
    """Try to parse HH:MM (or HH:MM:SS) from a string. Returns a datetime on today's date."""
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= sec < 60):
        return None
    now = datetime.utcnow().replace(second=0, microsecond=0)
    return now.replace(hour=h, minute=mi, second=sec)


def _between_time(t1: datetime, t2: datetime) -> str:
    """Return HH:MM UTC of the midpoint between t1 and t2.
    If t2 <= t1, assume t2 is the next day."""
    if t2 <= t1:
        t2 += timedelta(days=1)
    mid = t1 + (t2 - t1) / 2
    return mid.strftime("%H:%M") + " UTC"


async def _create_defend_channel(
    interaction: discord.Interaction,
    defender: str, attacker: str, coords: str, size: str, notes: str,
    arrival_1: str, arrival_2: str, timed: bool,
):
    """Shared logic for both Defend and TimedDefend modals."""
    guild = interaction.guild
    lang = await get_guild_lang(str(guild.id))
    config, category = await _get_config_and_category(guild)
    if not config:
        await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
        return
    if not category:
        await interaction.response.send_message(t(lang, "category_not_found"), ephemeral=True)
        return

    # Validate & calculate between-time for timed defend
    between_str = ""
    if timed and arrival_2:
        t1 = _parse_time(arrival_1)
        t2 = _parse_time(arrival_2)
        if t1 and t2:
            if t2 <= t1:
                t2 += timedelta(days=1)
            delta = (t2 - t1).total_seconds()
            if delta <= 0:
                await interaction.response.send_message(
                    t(lang, "time_order_error"),
                    ephemeral=True,
                )
                return
            elif delta < 60:
                # Gap too small for a between-wave — defend on the first wave
                between_str = t1.strftime("%H:%M") + " UTC"
            else:
                between_str = _between_time(t1, t2)

    await interaction.response.defer(ephemeral=True)

    prefix = "timed-def" if timed else "defend"
    channel_name = f"{prefix}-{_safe(defender)}-{_safe(coords)}"[:100]
    topic = (f"{'Timed-Defend' if timed else 'Defend'}: {attacker} @ {coords} | "
             f"{arrival_1}{' → ' + arrival_2 if arrival_2 else ''}")

    overwrites = _build_overwrites(guild, config, interaction.user)
    new_channel = await guild.create_text_channel(
        name=channel_name, category=category, topic=topic, overwrites=overwrites,
    )

    embed = discord.Embed(
        title=t(lang, "defend.timed_title") if timed else t(lang, "defend.title"),
        color=discord.Color.from_rgb(239, 68, 68),
    )
    embed.add_field(name=t(lang, "defend.field.defender"), value=defender, inline=True)
    embed.add_field(name=t(lang, "defend.field.attacker"), value=attacker, inline=True)
    embed.add_field(name=t(lang, "defend.field.target"),   value=coords,   inline=True)

    if timed and arrival_2:
        embed.add_field(name=t(lang, "defend.field.wave1"), value=arrival_1, inline=True)
        embed.add_field(name=t(lang, "defend.field.wave2"), value=arrival_2, inline=True)
        if between_str:
            embed.add_field(
                name=t(lang, "defend.field.between"),
                value=t(lang, "defend.between_desc", time=between_str),
                inline=False,
            )
    else:
        embed.add_field(name=t(lang, "defend.field.arrival"), value=arrival_1, inline=True)

    if size:
        embed.add_field(name=t(lang, "defend.field.size"), value=size, inline=True)
    if notes:
        embed.add_field(name=t(lang, "defend.field.notes"), value=notes, inline=False)
    embed.set_footer(**travops_footer(t(lang, "reported_by", user=interaction.user.display_name)))

    timed_prefix = t(lang, "defend.timed_prefix") if timed else ""
    await new_channel.send(
        content=t(lang, "defend.ping", user=interaction.user.mention, prefix=timed_prefix),
        embed=embed,
        view=DefendCloseView(),
    )

    arrival_db = arrival_1
    if arrival_2:
        arrival_db = f"{arrival_1} → {arrival_2}"
        if between_str:
            arrival_db += f" (Zwischen: {between_str})"

    await database.add_defend_channel(
        channel_id=str(new_channel.id), guild_id=str(guild.id),
        type="timed_defend" if timed else "defend",
        attacker=attacker, coords=coords,
        arrival_time=arrival_db, notes=notes,
        requested_by_id=str(interaction.user.id),
        requested_by_name=interaction.user.display_name,
    )
    await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)


class DefendModal(discord.ui.Modal, title="🛡️ Defend Anfrage"):
    """Plain defend — single arrival time."""
    defender = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords   = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival  = discord.ui.TextInput(label="Ankunftszeit", placeholder="z.B. 23:45 UTC", max_length=30)
    size     = discord.ui.TextInput(label="Angriffsgröße", placeholder="z.B. klein / mittel / ~500 Axt", required=False, max_length=60)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_defend_channel(
            interaction,
            defender=self.defender.value.strip(),
            attacker=self.attacker.value.strip(),
            coords=self.coords.value.strip(),
            size=self.size.value.strip(),
            notes="",
            arrival_1=self.arrival.value.strip(),
            arrival_2="",
            timed=False,
        )


class TimedDefendModal(discord.ui.Modal, title="⏱️ Timed-Defend Anfrage"):
    """Timed defend — two arrival times, calculates between-defense window."""
    defender  = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker  = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords    = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival   = discord.ui.TextInput(label="1. Ankunftszeit (frühere Welle)", placeholder="z.B. 23:45 UTC", max_length=30)
    arrival_2 = discord.ui.TextInput(label="2. Ankunftszeit (spätere Welle)", placeholder="z.B. 00:10 UTC (muss später sein)", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_defend_channel(
            interaction,
            defender=self.defender.value.strip(),
            attacker=self.attacker.value.strip(),
            coords=self.coords.value.strip(),
            size=self.size.value.strip(),
            notes="",
            arrival_1=self.arrival.value.strip(),
            arrival_2=self.arrival_2.value.strip(),
            timed=True,
        )


# ---------------------------------------------------------------------------
# Defend close view
# ---------------------------------------------------------------------------

class DefendCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Defend done",
        style=discord.ButtonStyle.success,
        custom_id="persistent:defend_done",
    )
    async def done_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        await database.close_defend_channel(str(interaction.channel.id))
        await interaction.response.send_message(
            t(lang, "defend.done", user=interaction.user.mention)
        )
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(
        label="🔒 Close channel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:defend_channel_close",
    )
    async def close_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.response.send_message(
            t(lang, "defend.closing", user=interaction.user.mention)
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=t(lang, "defend.channel_closed_reason"))
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
        lang = await get_guild_lang(str(guild.id))
        config, category = await _get_config_and_category(guild)
        if not config or not category:
            await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        channel_name = f"res-push-{_safe(interaction.user.display_name)}"[:100]
        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Res-Push: {interaction.user.display_name}: {self.resources.value[:80]}",
            overwrites=overwrites,
        )

        embed = discord.Embed(title=t(lang, "res_push.title"), color=discord.Color.orange())
        embed.add_field(name=t(lang, "res_push.field.recipient"), value=interaction.user.mention, inline=True)
        embed.add_field(name=t(lang, "res_push.field.village"), value=self.village.value, inline=True)
        embed.add_field(name=t(lang, "res_push.field.until"), value=self.until.value, inline=True)
        embed.add_field(name=t(lang, "res_push.field.needed"), value=self.resources.value, inline=False)
        if self.notes.value:
            embed.add_field(name=t(lang, "res_push.field.notes"), value=self.notes.value, inline=False)
        embed.set_footer(**travops_footer(t(lang, "requested_by", user=interaction.user.display_name)))

        await new_channel.send(content=f"🪖 {interaction.user.mention}", embed=embed)
        await interaction.followup.send(t(lang, "res_push.channel_created", channel=new_channel.mention), ephemeral=True)


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
        await interaction.response.send_modal(DefendModal())

    @discord.ui.button(
        label="Timed-Defend", emoji="⏱️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_timed_defend", row=1,
    )
    async def hub_timed_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TimedDefendModal())


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
