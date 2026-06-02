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

# URL / hyperlink pattern — catches http(s)://, www., discord.gg and markdown links
_URL_RE = re.compile(
    r'(https?://|www\.|discord\.gg/|\[.+?\]\(https?://)',
    re.IGNORECASE
)

async def _check_no_urls(interaction: discord.Interaction, *values: str) -> bool:
    """Return True (and send error) if any value contains a URL. Use as early-return guard."""
    if any(_URL_RE.search(v or "") for v in values):
        await interaction.response.send_message(
            "❌ URLs und Links sind in Anfrage-Feldern nicht erlaubt.",
            ephemeral=True,
        )
        return True
    return False
from i18n import t, get_guild_lang


# ---------------------------------------------------------------------------
# Helpers (shared with scout.py logic)
# ---------------------------------------------------------------------------

async def _get_config_and_category(guild: discord.Guild) -> tuple[dict | None, discord.CategoryChannel | None]:
    config = await database.get_guild_config(str(guild.id))
    if not config:
        return None, None
    category_id = config.get("category_id")
    if category_id:
        # Try cache first, then API fetch (handles stale cache / bot restart)
        category = guild.get_channel(int(category_id))
        if not category:
            try:
                category = await guild.fetch_channel(int(category_id))
            except Exception:
                category = None
        if category and isinstance(category, discord.CategoryChannel):
            return config, category
    # Fallback: find existing TravOps category by name (no new creation)
    for ch in guild.categories:
        if ch.name.lower() == "travops":
            # Persist so future lookups are fast
            await database.set_category(guild_id=str(guild.id), category_id=str(ch.id))
            return config, ch
    return config, None


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


def _build_defend_overwrites(guild: discord.Guild, config: dict, requester: discord.Member) -> dict:
    """Like _build_overwrites but uses defend_role_ids (fallback: allowed_role_ids)."""
    defend_ids = (config.get("defend_role_ids") or "").strip()
    role_source = defend_ids if defend_ids else (config.get("allowed_role_ids") or "")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True,
        ),
        requester: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
    }
    for role_id_str in role_source.split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
    return overwrites


def _safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "-", s.lower())[:40].strip("-")


def _clean_coords(raw: str) -> str:
    """Strip surrounding parentheses/brackets and whitespace from coordinate strings."""
    return raw.strip().strip("()[]")


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class HeroScoutHubModal(discord.ui.Modal, title="🦸 Helden-Scout melden"):
    player = discord.ui.TextInput(label="Spieler-Name", placeholder="z.B. Currax", max_length=100)
    coordinates = discord.ui.TextInput(label="Koordinaten", placeholder="z.B. (102|47)", max_length=30)
    hero_action = discord.ui.TextInput(
        label="Helden-Aktion / Zeitpunkt",
        placeholder="z.B. Items gewechselt um 19:15 Serverzeit",
        max_length=200,
    )
    additional_info = discord.ui.TextInput(
        label="Zusätzliche Infos (optional)", required=False,
        style=discord.TextStyle.paragraph, max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_premium(interaction):
            return
        if await _check_no_urls(interaction, self.player.value, self.coordinates.value,
                                 self.hero_action.value, self.additional_info.value):
            return
        guild = interaction.guild
        lang = await get_guild_lang(str(guild.id))

        from cogs.hero_scout import get_hero_scout_channel
        hero_channel_id = await get_hero_scout_channel(str(guild.id))

        if not hero_channel_id:
            await interaction.response.send_message(
                "❌ Kein Helden-Scout-Channel konfiguriert. Bitte einen Admin fragen.", ephemeral=True
            )
            return

        target_ch = guild.get_channel(int(hero_channel_id))
        if not target_ch:
            try:
                target_ch = await guild.fetch_channel(int(hero_channel_id))
            except Exception:
                target_ch = None

        if not target_ch:
            await interaction.response.send_message(
                "❌ Helden-Scout-Channel nicht gefunden.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🦸 Helden-Scout: {self.player.value}",
            color=discord.Color.teal(),
        )
        embed.add_field(name="📍 Koordinaten", value=self.coordinates.value, inline=True)
        embed.add_field(name="⚡ Helden-Aktion", value=self.hero_action.value, inline=False)
        if self.additional_info.value:
            embed.add_field(name="📝 Zusatz", value=self.additional_info.value, inline=False)
        embed.set_footer(text=f"Gemeldet von {interaction.user.display_name} • {travops_footer()}")

        await target_ch.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Helden-Scout gemeldet in {target_ch.mention}!", ephemeral=True
        )


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
        if await _check_no_urls(interaction, self.player.value, self.coordinates.value,
                                 self.village.value, self.time.value, self.additional_info.value):
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

        coords = _clean_coords(self.coordinates.value)
        prefix = "perm-scout" if self.permanent else ("corn-scout" if self.corn else "scout")
        channel_name = f"{prefix}-{_safe(self.player.value)}-{_safe(coords)}"[:100]

        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"{'Permanent-Scout' if self.permanent else ('Corn-Scout' if self.corn else 'Scout')}: {self.player.value} @ {coords}",
            overwrites=overwrites,
        )

        if self.permanent:
            color = discord.Color.teal()
            title = t(lang, "perm_scout.title")
            desc = t(lang, "perm_scout.desc", village=self.village.value or "—",
                     player=self.player.value, coords=coords)
        elif self.corn:
            color = discord.Color.gold()
            title = t(lang, "corn.title")
            desc = t(lang, "corn.desc", player=self.player.value, coords=coords)
        else:
            color = discord.Color.blurple()
            title = t(lang, "scout.title")
            desc = t(lang, "hub.scout.desc", player=self.player.value, coords=coords)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name=t(lang, "hub.scout.field.player_embed"), value=self.player.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.coords_embed"), value=coords, inline=True)
        if self.village.value:
            embed.add_field(name=t(lang, "hub.scout.field.village_embed"), value=self.village.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.time_embed"), value=self.time.value, inline=True)
        if self.additional_info.value:
            embed.add_field(name=t(lang, "hub.scout.field.info_embed"), value=self.additional_info.value, inline=False)
        embed.set_footer(**travops_footer(t(lang, "requested_by", user=interaction.user.display_name)))

        # Build Travian map link for the scout target
        tw_world = (config or {}).get("tw_world") or ""
        coord_match = re.search(r"(-?\d+)\s*[|/]\s*(-?\d+)", coords)
        scout_troop_link = ""
        if coord_match and tw_world:
            cx, cy = coord_match.group(1), coord_match.group(2)
            scout_troop_link = f"{tw_world.rstrip('/')}/build.php?gid=16&tt=2&eventType=4&x={cx}&y={cy}"

        from cogs.scout import ScoutActionView
        view = ScoutActionView(troop_link=scout_troop_link)
        await new_channel.send(
            content=t(lang, "hub.scout.new_request", user=interaction.user.mention),
            embed=embed,
            view=view,
        )

        # Save to DB (reuse scout channel registration)
        await database.add_scout_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            player=self.player.value, coordinates=coords,
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
    defender: str, attacker: str, coords: str,
    troop_goal: str, ratio: str, notes: str,
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

    overwrites = _build_defend_overwrites(guild, config, interaction.user)
    new_channel = await guild.create_text_channel(
        name=channel_name, category=category, topic=topic, overwrites=overwrites,
    )

    # Build Travian "send troops" link (rally point → reinforcement)
    tw_world = (config or {}).get("tw_world") or ""
    coord_match = re.search(r"(-?\d+)\s*[|/]\s*(-?\d+)", coords)
    troop_link = ""
    map_link = ""
    if coord_match and tw_world:
        cx, cy = coord_match.group(1), coord_match.group(2)
        base = tw_world.rstrip("/")
        # Rally point → send reinforcement (eventType=5 = reinforcement)
        troop_link = f"{base}/build.php?gid=16&tt=2&eventType=5&x={cx}&y={cy}"
        map_link   = f"{base}/karte.php?x={cx}&y={cy}"

    embed_kwargs = dict(
        title=t(lang, "defend.timed_title") if timed else t(lang, "defend.title"),
        color=discord.Color.from_rgb(239, 68, 68),
    )
    if troop_link:
        embed_kwargs["url"] = troop_link
    embed = discord.Embed(**embed_kwargs)

    # Coords field: clickable → opens rally point directly
    coords_display = f"[{coords}]({troop_link})" if troop_link else coords
    embed.add_field(name=t(lang, "defend.field.defender"), value=defender, inline=True)
    embed.add_field(name=t(lang, "defend.field.attacker"), value=attacker, inline=True)
    embed.add_field(name=t(lang, "defend.field.target"),   value=coords_display, inline=True)

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

    if troop_goal:
        embed.add_field(name="🎯 Kornziel", value=f"**{troop_goal}** 🌾/h", inline=True)
    if ratio:
        embed.add_field(name="⚖️ Verteilung Fuß/Pferd", value=ratio, inline=True)
    if notes:
        embed.add_field(name=t(lang, "defend.field.notes"), value=notes, inline=False)

    # Troop-send link as clickable button in embed description (always visible)
    if troop_link:
        embed.description = f"### [⚔️ Jetzt Truppen schicken →]({troop_link})"

    embed.set_footer(**travops_footer(t(lang, "reported_by", user=interaction.user.display_name)))

    timed_prefix = t(lang, "defend.timed_prefix") if timed else ""
    ping_content = t(lang, "defend.ping", user=interaction.user.mention, prefix=timed_prefix)
    # Post links as plain text so Discord always renders them (not suppressed by embed)
    if troop_link:
        ping_content += f"\n⚔️ **Truppen schicken:** <{troop_link}>"
    await new_channel.send(
        content=ping_content,
        embed=embed,
        view=DefendCloseView(troop_link=troop_link),
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
        goal=troop_goal,
        ratio=ratio,
        requested_by_id=str(interaction.user.id),
        requested_by_name=interaction.user.display_name,
    )
    await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)


# ---------------------------------------------------------------------------
# Defend — 2-step modal flow
# Step 1: Attacker/defender/coords/arrival  →  ephemeral button  →  Step 2: goal/ratio
# ---------------------------------------------------------------------------

# In-memory store for step-1 data while the user completes step 2
# keyed by (guild_id, user_id) so multiple users can run the flow simultaneously
_defend_pending: dict[tuple[int, int], dict] = {}


def _parse_coords_time(raw: str) -> tuple[str, str]:
    """Split a combined 'coords · arrival' string into (coords, arrival)."""
    coord_m = re.search(r'(\(?\s*-?\d+\s*\|\s*-?\d+\s*\)?)', raw)
    if coord_m:
        coords  = coord_m.group(1).strip()
        arrival = raw[coord_m.end():].strip(' ·-,')
    else:
        coords  = raw
        arrival = ""
    return coords, arrival


class DefendStep2Modal(discord.ui.Modal, title="🛡️ Defend (2/2) — Kornziel"):
    troop_goal = discord.ui.TextInput(
        label="Benötigtes Korn/h (Ziel)",
        placeholder="z.B. 80k oder 50000",
        required=False, max_length=20,
    )
    ratio = discord.ui.TextInput(
        label="Verteilung Fuß/Pferd (optional)",
        placeholder="z.B. 60/40  oder  reine Imps",
        required=False, max_length=80,
    )

    def __init__(self, key: tuple[int, int]):
        super().__init__()
        self._key = key

    async def on_submit(self, interaction: discord.Interaction):
        if await _check_no_urls(interaction, self.troop_goal.value, self.ratio.value):
            return
        data = _defend_pending.pop(self._key, None)
        if not data:
            await interaction.response.send_message(
                "❌ Session abgelaufen — bitte Defend-Anfrage neu starten.", ephemeral=True
            )
            return
        try:
            await _create_defend_channel(
                interaction,
                **data,
                troop_goal=self.troop_goal.value.strip(),
                ratio=self.ratio.value.strip(),
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[DefendStep2Modal] ERROR in _create_defend_channel: {e}")
            try:
                await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)
            except Exception:
                pass


class DefendStep2View(discord.ui.View):
    """Ephemeral view shown after step 1 — opens the step-2 modal."""
    def __init__(self, key: tuple[int, int]):
        super().__init__(timeout=300)
        self._key = key

    @discord.ui.button(label="⚔️ Weiter: Truppenziel →", style=discord.ButtonStyle.primary)
    async def open_step2(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_modal(DefendStep2Modal(self._key))


class DefendModal(discord.ui.Modal, title="🛡️ Defend Anfrage (1/2)"):
    """Step 1 — basic defend info."""
    defender    = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker    = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords      = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival     = discord.ui.TextInput(label="Ankunftszeit", placeholder="z.B. 23:45 UTC", max_length=40)
    notes       = discord.ui.TextInput(label="Notizen (optional)", required=False,
                                       style=discord.TextStyle.paragraph, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        if await _check_no_urls(interaction, self.defender.value, self.attacker.value,
                                 self.coords.value, self.arrival.value, self.notes.value):
            return
        key = (interaction.guild_id, interaction.user.id)
        _defend_pending[key] = dict(
            defender  = self.defender.value.strip(),
            attacker  = self.attacker.value.strip(),
            coords    = _clean_coords(self.coords.value),
            notes     = self.notes.value.strip(),
            arrival_1 = self.arrival.value.strip(),
            arrival_2 = "",
            timed     = False,
        )
        await interaction.response.send_message(
            "✅ **Schritt 1 gespeichert!**\nJetzt Truppenziel & Verteilung eingeben:",
            view=DefendStep2View(key),
            ephemeral=True,
        )


class TimedDefendModal(discord.ui.Modal, title="⏱️ Timed-Defend (1/2)"):
    """Step 1 — timed defend with two arrival times."""
    defender  = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker  = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords    = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival   = discord.ui.TextInput(label="1. Ankunftszeit (frühere Welle)", placeholder="z.B. 23:45 UTC", max_length=40)
    arrival_2 = discord.ui.TextInput(label="2. Ankunftszeit (spätere Welle)", placeholder="z.B. 00:10 UTC", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if await _check_no_urls(interaction, self.defender.value, self.attacker.value,
                                 self.coords.value, self.arrival.value, self.arrival_2.value):
            return
        key = (interaction.guild_id, interaction.user.id)
        _defend_pending[key] = dict(
            defender  = self.defender.value.strip(),
            attacker  = self.attacker.value.strip(),
            coords    = _clean_coords(self.coords.value),
            notes     = "",
            arrival_1 = self.arrival.value.strip(),
            arrival_2 = self.arrival_2.value.strip(),
            timed     = True,
        )
        await interaction.response.send_message(
            "✅ **Schritt 1 gespeichert!**\nJetzt Truppenziel eingeben:",
            view=DefendStep2View(key),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Defend — troop unit data (name, grain/h per troop)
# ---------------------------------------------------------------------------

_FOOT_UNITS: list[tuple[str, int]] = [
    ("— Keine —",        0),
    # ─ Römer ─
    ("Legionär",         1),
    ("Prätorianer",      1),
    ("Imperianer",       1),
    # ─ Teutonen ─
    ("Keule",            1),
    ("Speerträger",      1),
    ("Axtkämpfer",       1),
    ("Aufklärer",        1),
    # ─ Gallier ─
    ("Phalanx",          1),
    ("Schwertkämpfer",   1),
    # ─ Hunnen ─
    ("Soldat",           1),
    ("Slugger",          1),
    ("Lanzenkämpfer",    1),
    ("Bogenschütze",     1),
    # ─ Ägypter ─
    ("Schilf-Pfeil",     1),
    ("Edelgardist",      1),
    ("Khopesh-Kämpfer",  1),
]

_CAVALRY_UNITS: list[tuple[str, int]] = [
    ("— Keine —",              0),
    # ─ Römer ─
    ("Equites Legati",         2),
    ("Equites Imperatoris",    3),
    ("Equites Caesaris",       4),
    # ─ Teutonen ─
    ("Paladin",                2),
    ("Teutonischer Ritter",    3),
    # ─ Gallier ─
    ("Treverer-Späher",        2),
    ("Druidenreiter",          2),
    ("Haeduer",                3),
    # ─ Hunnen ─
    ("Steppenkämpfer",         2),
    ("Marksman",               3),
    ("Mameluk",                3),
    ("Amazonas",               3),
    # ─ Ägypter ─
    ("Sopdu-Speerkämpfer",     2),
    ("Anhur-Garde",            3),
    ("Asclepion",              2),
]


# ---------------------------------------------------------------------------
# Defend — tracking helpers
# ---------------------------------------------------------------------------

def _parse_defend_amount(s: str) -> int | None:
    from cogs.res_push import _parse_amount
    return _parse_amount(s)


def _fmt_troops(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _build_defend_tracking_embed(
    contributions: list[dict], lang: str, coords: str, tw_world: str | None,
    goal_raw: str = "", ratio: str = "",
) -> discord.Embed:
    from cogs.res_push import _parse_amount
    from collections import defaultdict

    total_troops = sum(c.get("amount_parsed", 0) for c in contributions)
    total_grain  = sum(
        (c.get("amount_parsed") or 0) * (c.get("grain_per_unit") or 1)
        for c in contributions
    )
    goal_parsed = _parse_amount(goal_raw.strip()) if goal_raw and goal_raw.strip() else None

    # ── Progress bar — in GRAIN (Korn/h) ────────────────────────────────────
    BAR_LEN = 20
    if goal_parsed and goal_parsed > 0:
        pct = min(total_grain / goal_parsed, 1.0)
        filled = round(pct * BAR_LEN)
        goal_reached = total_grain >= goal_parsed
        fill_emoji = "🟩" if goal_reached else "🟥"
        bar = fill_emoji * filled + "⬜" * (BAR_LEN - filled)
        pct_text = f"**{int(pct * 100)}%**  ({_fmt_troops(total_grain)} / {_fmt_troops(goal_parsed)}) 🌾/h"
        if total_grain > goal_parsed:
            pct_text += f"  (+{_fmt_troops(total_grain - goal_parsed)})"
        progress_text = f"{bar}\n{pct_text}"
        color = discord.Color.from_rgb(34, 197, 94) if goal_reached else discord.Color.from_rgb(239, 68, 68)
    else:
        bar_filled = min(BAR_LEN, max(1, len(contributions)))
        bar = "🟥" * bar_filled + "⬜" * (BAR_LEN - bar_filled)
        progress_text = f"{bar}\n**{_fmt_troops(total_grain)}** 🌾/h"
        color = discord.Color.from_rgb(239, 68, 68)

    # ── Travian link ──────────────────────────────────────────────────────────
    coord_match = re.search(r"(-?\d+)\s*[|]\s*(-?\d+)", coords)
    travian_link = ""
    if coord_match and tw_world:
        x, y = coord_match.group(1), coord_match.group(2)
        travian_link = f"{tw_world.rstrip('/')}/karte.php?x={x}&y={y}"

    # ── Build embed ───────────────────────────────────────────────────────────
    title = "⚔️ Truppen gesendet" if lang == "de" else "⚔️ Troops Sent"
    embed_kw = dict(title=title, color=color)
    if travian_link:
        embed_kw["url"] = travian_link
    embed = discord.Embed(**embed_kw)

    if travian_link:
        embed.description = f"### [🗺️ Ziel-Dorf öffnen — {coords}]({travian_link})"

    # ── Primary: Korn/h progress ──────────────────────────────────────────────
    embed.add_field(
        name="🌾 Korn/h gesamt" if lang == "de" else "🌾 Total Grain/h",
        value=progress_text,
        inline=False,
    )

    # ── Troop-type breakdown (how many of each type total) ────────────────────
    type_totals: dict[str, int] = defaultdict(int)
    for c in contributions:
        tt = (c.get("troop_type") or "").strip()
        if tt:
            type_totals[tt] += c.get("amount_parsed", 0)

    if type_totals:
        # Sort by count desc
        sorted_types = sorted(type_totals.items(), key=lambda x: x[1], reverse=True)
        troop_lines = [f"**{_fmt_troops(cnt)}×** {ttype}" for ttype, cnt in sorted_types]
        embed.add_field(
            name="🪖 Truppenarten (gesamt)" if lang == "de" else "🪖 Troop Types",
            value="\n".join(troop_lines) or "—",
            inline=True,
        )
        embed.add_field(
            name="📊 Truppen gesamt",
            value=f"**{_fmt_troops(total_troops)}**",
            inline=True,
        )

    if ratio:
        embed.add_field(name="⚖️ Verteilung", value=ratio, inline=True)

    # ── Individual contributions (newest 15) ──────────────────────────────────
    if contributions:
        lines = []
        for c in contributions[-15:]:
            grain = (c.get("amount_parsed") or 0) * (c.get("grain_per_unit") or 1)
            lines.append(
                f"• **{c['user_name']}** — {_fmt_troops(c.get('amount_parsed', 0))} {c.get('troop_type', '')} · {_fmt_troops(grain)} 🌾/h"
            )
        embed.add_field(
            name="👥 Beiträge" if lang == "de" else "👥 Contributions",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text=f"{len(contributions)} Einträge · {coords}")
    return embed


async def _save_and_update_tracking(
    interaction: discord.Interaction, lang: str,
    entries: list[tuple[str, int, str, int]],   # (amount_raw, amount_parsed, troop_type, grain_per_unit)
):
    """Save one or more troop entries to DB and refresh the tracking embed."""
    channel_id = str(interaction.channel.id)
    guild_id   = str(interaction.guild_id)

    for amount_raw, amount_parsed, troop_type, grain_per_unit in entries:
        if amount_parsed > 0:
            await database.add_defend_sent(
                channel_id=channel_id, guild_id=guild_id,
                user_id=str(interaction.user.id), user_name=interaction.user.display_name,
                amount_raw=amount_raw, amount_parsed=amount_parsed,
                troop_type=troop_type, grain_per_unit=grain_per_unit,
            )

    contributions = await database.get_defend_sent(channel_id)
    defend_rec    = await database.get_defend_channel(channel_id)
    config        = await database.get_guild_config(guild_id)
    tw_world      = (config or {}).get("tw_world") or ""
    coords        = (defend_rec or {}).get("coords", "")
    goal_raw      = (defend_rec or {}).get("goal", "") or ""
    ratio         = (defend_rec or {}).get("ratio", "") or ""

    tracking_embed = _build_defend_tracking_embed(contributions, lang, coords, tw_world, goal_raw, ratio)

    tracking_msg_id = (defend_rec or {}).get("tracking_msg_id")
    if tracking_msg_id:
        try:
            msg = await interaction.channel.fetch_message(int(tracking_msg_id))
            await msg.edit(embed=tracking_embed)
            tracking_msg_id = tracking_msg_id  # still valid
        except Exception:
            tracking_msg_id = None

    if not tracking_msg_id:
        new_msg = await interaction.channel.send(embed=tracking_embed)
        await database.set_defend_tracking_msg(channel_id, str(new_msg.id))

    total_grain = sum(
        (c.get("amount_parsed") or 0) * (c.get("grain_per_unit") or 1)
        for c in contributions
    )
    parts = [f"{_fmt_troops(ap)} {tt}" for _, ap, tt, _ in entries if ap > 0 and tt]
    confirm = (
        f"✅ Eingetragen: {' + '.join(parts) or '—'} — "
        f"Gesamt Getreide/h jetzt: {_fmt_troops(total_grain)}"
    )
    await interaction.followup.send(confirm, ephemeral=True)


# ---------------------------------------------------------------------------
# Defend — troop selection flow (Select → Modal)
# ---------------------------------------------------------------------------

class DefendAmountModal(discord.ui.Modal):
    """Dynamically built modal: label includes selected troop type."""

    def __init__(
        self, lang: str,
        foot_type: str, foot_grain: int,
        cav_type: str, cav_grain: int,
    ):
        title = "⚔️ Truppen eintragen" if lang == "de" else "⚔️ Log sent troops"
        super().__init__(title=title)
        self.lang = lang
        self.foot_type = foot_type
        self.foot_grain = foot_grain
        self.cav_type = cav_type
        self.cav_grain = cav_grain
        self._foot_inp = None
        self._cav_inp = None

        if foot_type:
            self._foot_inp = discord.ui.TextInput(
                label=f"{foot_type}  ({foot_grain} 🌾/Truppe)",
                placeholder="z.B. 500, 5k, 2.5k …",
                required=False,
                max_length=20,
            )
            self.add_item(self._foot_inp)

        if cav_type:
            self._cav_inp = discord.ui.TextInput(
                label=f"{cav_type}  ({cav_grain} 🌾/Truppe)",
                placeholder="z.B. 200, 1k …",
                required=False,
                max_length=20,
            )
            self.add_item(self._cav_inp)

    async def on_submit(self, interaction: discord.Interaction):
        lang = self.lang
        foot_raw = (self._foot_inp.value or "").strip() if self._foot_inp else ""
        cav_raw  = (self._cav_inp.value or "").strip()  if self._cav_inp  else ""

        foot_n = _parse_defend_amount(foot_raw) or 0
        cav_n  = _parse_defend_amount(cav_raw)  or 0

        if not foot_n and not cav_n:
            await interaction.response.send_message(
                "❌ Bitte mindestens eine Anzahl eingeben." if lang == "de"
                else "❌ Please enter at least one amount.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        entries = []
        if foot_n and self.foot_type:
            entries.append((foot_raw, foot_n, self.foot_type, self.foot_grain))
        if cav_n and self.cav_type:
            entries.append((cav_raw, cav_n, self.cav_type, self.cav_grain))

        await _save_and_update_tracking(interaction, lang, entries)


class DefendTroopSelectView(discord.ui.View):
    """Ephemeral view: pick foot type + cavalry type, then open amount modal."""

    def __init__(self, lang: str = "de"):
        super().__init__(timeout=120)
        self.lang = lang
        self.foot_type  = ""
        self.foot_grain = 0
        self.cav_type   = ""
        self.cav_grain  = 0

        foot_opts = [
            discord.SelectOption(label=name, value=f"{name}:{grain}")
            for name, grain in _FOOT_UNITS
        ]
        foot_sel = discord.ui.Select(
            placeholder="🥾 Fußtruppen auswählen…",
            options=foot_opts,
            row=0,
        )
        foot_sel.callback = self._on_foot
        self.add_item(foot_sel)

        cav_opts = [
            discord.SelectOption(label=name, value=f"{name}:{grain}")
            for name, grain in _CAVALRY_UNITS
        ]
        cav_sel = discord.ui.Select(
            placeholder="🐴 Reitertruppen auswählen…",
            options=cav_opts,
            row=1,
        )
        cav_sel.callback = self._on_cav
        self.add_item(cav_sel)

        btn = discord.ui.Button(
            label="Anzahl eingeben →",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        btn.callback = self._on_next
        self.add_item(btn)

    async def _on_foot(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        name, grain = val.rsplit(":", 1)
        self.foot_type  = "" if name == "— Keine —" else name
        self.foot_grain = int(grain)
        await interaction.response.defer()

    async def _on_cav(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        name, grain = val.rsplit(":", 1)
        self.cav_type  = "" if name == "— Keine —" else name
        self.cav_grain = int(grain)
        await interaction.response.defer()

    async def _on_next(self, interaction: discord.Interaction):
        if not self.foot_type and not self.cav_type:
            await interaction.response.send_message(
                "❌ Bitte mindestens einen Truppentyp auswählen." if self.lang == "de"
                else "❌ Select at least one troop type.",
                ephemeral=True,
            )
            return
        modal = DefendAmountModal(
            lang=self.lang,
            foot_type=self.foot_type, foot_grain=self.foot_grain,
            cav_type=self.cav_type,   cav_grain=self.cav_grain,
        )
        await interaction.response.send_modal(modal)


async def _can_manage_defend(interaction: discord.Interaction) -> bool:
    """True if the user may close/done a defend channel (admin, ally_manage, or defend_manage)."""
    if interaction.user.guild_permissions.administrator:
        return True
    perms = await database.get_member_permissions(
        str(interaction.guild_id), str(interaction.user.id)
    )
    return bool(perms & {"ally_manage", "defend_manage"})


class DefendCloseView(discord.ui.View):
    def __init__(self, troop_link: str = ""):
        super().__init__(timeout=None)
        if troop_link:
            self.add_item(discord.ui.Button(
                label="🏹 Rally Point",
                style=discord.ButtonStyle.link,
                url=troop_link,
                row=1,
            ))

    @discord.ui.button(
        label="⚔️ I send",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:defend_sent",
        row=0,
    )
    async def i_sent(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        view = DefendTroopSelectView(lang=lang)
        label = "Welche Truppen schickst du?" if lang == "de" else "Which troops are you sending?"
        await interaction.response.send_message(label, view=view, ephemeral=True)

    @discord.ui.button(
        label="✅ Defend done",
        style=discord.ButtonStyle.success,
        custom_id="persistent:defend_done",
        row=0,
    )
    async def done_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        if not await _can_manage_defend(interaction):
            msg = "⛔ Nur Leader/HC können Defend-Anfragen abschließen." if lang == "de" else "⛔ Only Leader/HC can close defend requests."
            await interaction.response.send_message(msg, ephemeral=True)
            return
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
        row=0,
    )
    async def close_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        if not await _can_manage_defend(interaction):
            msg = "⛔ Nur Leader/HC können den Channel schließen." if lang == "de" else "⛔ Only Leader/HC can close the channel."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await database.close_defend_channel(str(interaction.channel.id))
        await interaction.response.send_message(
            f"📦 Channel wird ins Archiv verschoben von {interaction.user.mention}…"
        )
        try:
            channel = interaction.channel
            guild   = interaction.guild
            ARCHIVE_NAME = "📦 Archiv"

            # Find or create archive category
            archive_cat = None
            for cat in guild.categories:
                if cat.name == ARCHIVE_NAME:
                    archive_cat = cat
                    break
            if not archive_cat:
                archive_cat = await guild.create_category(
                    ARCHIVE_NAME,
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(
                            view_channel=True, send_messages=True, manage_channels=True
                        ),
                    },
                    reason="Defend-Archiv-Kategorie erstellt",
                )

            # Build read-only overwrites
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False, send_messages=False
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True
                ),
            }
            for target, ow in channel.overwrites.items():
                if target in (guild.default_role, guild.me):
                    continue
                allow, deny = ow.pair()
                new_ow = discord.PermissionOverwrite.from_pair(allow, deny)
                new_ow.update(send_messages=False, add_reactions=False)
                overwrites[target] = new_ow

            await channel.edit(
                category=archive_cat,
                overwrites=overwrites,
                reason="Defend-Channel archiviert",
            )
        except Exception as e:
            print(f"[hub] archive defend channel error: {e}")


# ---------------------------------------------------------------------------
# Res-Push Modal
# ---------------------------------------------------------------------------

class HubResAnswerView(discord.ui.View):
    """Admin Accept/Reject/Hold view posted directly in the hub-created res-push channel."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _get_req_and_lang(self, interaction: discord.Interaction):
        from cogs.res_push import _is_authorized
        if not await _is_authorized(interaction):
            lang = await get_guild_lang(str(interaction.guild_id))
            await interaction.response.send_message(
                "⛔ Keine Berechtigung." if lang == "de" else "⛔ No permission.", ephemeral=True
            )
            return None, None
        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        lang = await get_guild_lang(str(interaction.guild_id))
        if not req:
            await interaction.response.send_message("⚠️ Anfrage nicht gefunden." if lang == "de" else "⚠️ Request not found.", ephemeral=True)
            return None, lang
        return req, lang

    @discord.ui.button(label="✅ Annehmen", style=discord.ButtonStyle.success, custom_id="persistent:hub_res_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_push_embed, ResPushChannelView
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await interaction.response.defer()

        # Transform this channel into the push tracking channel
        await database.update_res_request_status(
            answer_message_id=str(interaction.message.id),
            status="accepted",
            push_channel_id=str(interaction.channel.id),
        )

        # Fetch updated req (now has push_channel_id)
        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        push_embed = _build_push_embed(req, [])

        accepted_label = "✅ Angenommen" if lang == "de" else "✅ Accepted"
        done_view = discord.ui.View()
        done_view.add_item(discord.ui.Button(label=accepted_label, style=discord.ButtonStyle.success, disabled=True))

        # Replace the pending embed/view with a simple accepted note
        from cogs.res_push import _build_request_embed
        accepted_embed = _build_request_embed(req, "accepted")
        await interaction.message.edit(
            content=(f"✅ Angenommen von {interaction.user.mention}" if lang == "de" else f"✅ Accepted by {interaction.user.mention}"),
            embed=accepted_embed,
            view=done_view,
        )

        # Post the live push tracking embed below
        await interaction.channel.send(embed=push_embed, view=ResPushChannelView())

    @discord.ui.button(label="❌ Ablehnen", style=discord.ButtonStyle.danger, custom_id="persistent:hub_res_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_request_embed
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await database.update_res_request_status(str(interaction.message.id), "rejected")

        updated = _build_request_embed(req, "rejected")
        rejected_label = "❌ Abgelehnt" if lang == "de" else "❌ Rejected"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=rejected_label, style=discord.ButtonStyle.danger, disabled=True))
        await interaction.response.edit_message(
            content=(f"❌ Abgelehnt von {interaction.user.mention}" if lang == "de" else f"❌ Rejected by {interaction.user.mention}"),
            embed=updated, view=view,
        )

    @discord.ui.button(label="⏸️ Zurückstellen", style=discord.ButtonStyle.secondary, custom_id="persistent:hub_res_hold")
    async def hold(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_request_embed
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await database.update_res_request_status(str(interaction.message.id), "hold")
        updated = _build_request_embed(req, "hold")
        await interaction.response.edit_message(
            content=(f"⏸️ Zurückgestellt von {interaction.user.mention}" if lang == "de" else f"⏸️ Put on hold by {interaction.user.mention}"),
            embed=updated, view=HubResAnswerView(),
        )


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
        if await _check_no_urls(interaction, self.village.value, self.resources.value,
                                 self.until.value, self.notes.value):
            return
        from cogs.res_push import _build_request_embed
        from datetime import datetime

        guild = interaction.guild
        lang = await get_guild_lang(str(guild.id))
        config, category = await _get_config_and_category(guild)
        if not config or not category:
            await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Build channel + overwrites
        channel_name = f"res-push-{_safe(interaction.user.display_name)}"[:100]
        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Res-Push: {interaction.user.display_name}: {self.resources.value[:80]}",
            overwrites=overwrites,
        )

        # Build request data
        reason_parts = []
        if lang == "de":
            reason_parts.append(f"Bis wann: {self.until.value}")
        else:
            reason_parts.append(f"Until: {self.until.value}")
        if self.notes.value:
            reason_parts.append(self.notes.value)

        data = {
            "player_name": interaction.user.display_name,
            "coordinates": self.village.value,
            "push_height": self.resources.value,
            "reason": "\n".join(reason_parts),
            "user_name": interaction.user.display_name,
            "user_id": str(interaction.user.id),
            "created_at": datetime.utcnow().isoformat(),
        }

        # Post the pending embed with admin buttons in the new channel
        embed = _build_request_embed(data, "pending")
        content = (
            f"🪖 {interaction.user.mention} — {'Neue Res-Push Anfrage' if lang == 'de' else 'New Res-Push Request'}"
        )
        msg = await new_channel.send(content=content, embed=embed, view=HubResAnswerView())

        # Save to DB (using the in-channel message id as the answer_message_id)
        await database.add_res_request(
            guild_id=str(guild.id),
            answer_message_id=str(msg.id),
            user_id=data["user_id"],
            user_name=data["user_name"],
            player_name=data["player_name"],
            coordinates=data["coordinates"],
            push_height=data["push_height"],
            reason=data["reason"],
        )

        await interaction.followup.send(
            t(lang, "res_push.channel_created", channel=new_channel.mention), ephemeral=True
        )


# ---------------------------------------------------------------------------
# Private Channel — Grant / Revoke Access
# ---------------------------------------------------------------------------

def _find_member(guild: discord.Guild, name: str) -> discord.Member | None:
    """Find a guild member by display name, username, @name, or <@id> mention."""
    name = name.strip()
    # Real Discord mention: <@123456> or <@!123456>
    m = re.match(r"<@!?(\d+)>", name)
    if m:
        return guild.get_member(int(m.group(1)))
    # Plain user ID
    if name.isdigit():
        return guild.get_member(int(name))
    # Strip leading @ if user typed "@matze"
    if name.startswith("@"):
        name = name[1:]
    low = name.lower()
    # 1. Exact match on display name, global name, or username
    for member in guild.members:
        if (member.display_name.lower() == low
                or member.name.lower() == low
                or (member.global_name or "").lower() == low):
            return member
    # 2. Starts-with match
    for member in guild.members:
        if (member.display_name.lower().startswith(low)
                or member.name.lower().startswith(low)):
            return member
    # 3. Contains match
    for member in guild.members:
        if low in member.display_name.lower() or low in member.name.lower():
            return member
    return None


class GrantAccessSelect(discord.ui.View):
    """Ephemeral view with a UserSelect — shown when owner clicks 'Zugriff gewähren'."""

    def __init__(self, lang: str = "de"):
        super().__init__(timeout=60)
        self.lang = lang
        placeholder = "Spieler auswählen…" if lang == "de" else "Select a member…"
        select = discord.ui.UserSelect(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            custom_id="private_grant_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        lang = self.lang
        selected: discord.Member = interaction.data["resolved"]["members"]
        # interaction.data gives us the member via the select values
        user_id = interaction.data["values"][0]
        member = interaction.guild.get_member(int(user_id))
        if not member:
            await interaction.response.send_message(
                "❌ Mitglied nicht gefunden." if lang == "de" else "❌ Member not found.", ephemeral=True
            )
            return
        overwrite = interaction.channel.overwrites_for(member)
        if overwrite.view_channel is True:
            await interaction.response.send_message(
                t(lang, "private.grant.already", mention=member.mention), ephemeral=True
            )
            return
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True)
        await interaction.response.send_message(
            t(lang, "private.grant.success", mention=member.mention)
        )


class RevokeAccessSelect(discord.ui.View):
    """Ephemeral view with a UserSelect — shown when owner clicks 'Zugriff entziehen'."""

    def __init__(self, lang: str = "de"):
        super().__init__(timeout=60)
        self.lang = lang
        placeholder = "Zugriff entziehen für…" if lang == "de" else "Revoke access for…"
        select = discord.ui.UserSelect(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            custom_id="private_revoke_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        lang = self.lang
        user_id = interaction.data["values"][0]
        member = interaction.guild.get_member(int(user_id))
        if not member:
            await interaction.response.send_message(
                "❌ Mitglied nicht gefunden." if lang == "de" else "❌ Member not found.", ephemeral=True
            )
            return
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(
            t(lang, "private.revoke.success", mention=member.mention)
        )


class PrivateChannelView(discord.ui.View):
    """Persistent view pinned in a private channel — owner can grant/revoke access."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Zugriff gewähren",
        style=discord.ButtonStyle.success,
        custom_id="persistent:private_grant",
    )
    async def grant_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        rec = await database.get_private_channel_by_channel_id(str(interaction.channel.id))
        if not rec or str(interaction.user.id) != rec["owner_id"]:
            await interaction.response.send_message(t(lang, "private.grant.not_owner"), ephemeral=True)
            return
        # Send ephemeral UserSelect picker
        view = GrantAccessSelect(lang=lang)
        label = "Wähle ein Mitglied aus der Liste:" if lang == "de" else "Select a member from the list:"
        await interaction.response.send_message(label, view=view, ephemeral=True)

    @discord.ui.button(
        label="➖ Zugriff entziehen",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:private_revoke",
    )
    async def revoke_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        rec = await database.get_private_channel_by_channel_id(str(interaction.channel.id))
        if not rec or str(interaction.user.id) != rec["owner_id"]:
            await interaction.response.send_message(t(lang, "private.revoke.not_owner"), ephemeral=True)
            return
        view = RevokeAccessSelect(lang=lang)
        label = "Wähle ein Mitglied aus der Liste:" if lang == "de" else "Select a member from the list:"
        await interaction.response.send_message(label, view=view, ephemeral=True)


async def _get_or_create_private_category(guild: discord.Guild, lang: str) -> discord.CategoryChannel:
    """Return or create the 'Privat Channels' category.

    The category itself is visible to everyone so members can see the channels
    they personally have access to. Each individual channel has its own strict
    overwrites (view_channel=False for @everyone), so only the owner and
    explicitly granted members can see each channel inside.
    """
    cat_name = t(lang, "private.category_name")
    for cat in guild.categories:
        if cat.name.lower() == cat_name.lower():
            return cat
    # Category visible to all (so members can find their own channels),
    # but channels inside will deny @everyone individually.
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True),
    }
    return await guild.create_category(cat_name, overwrites=overwrites)


# ---------------------------------------------------------------------------
# Private Channel Name Modal
# ---------------------------------------------------------------------------

class PrivateChannelNameModal(discord.ui.Modal):
    """Ask user for a channel name (usually their Travian player name)."""

    channel_label = discord.ui.TextInput(
        label="Channel-Name / Travian-Spielername",
        placeholder="z.B. Currax",
        max_length=40,
    )

    def __init__(self, lang: str = "de"):
        title = "🔒 Privaten Channel erstellen" if lang == "de" else "🔒 Create Private Channel"
        super().__init__(title=title)
        self.lang = lang

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await _do_create_private_channel(interaction, self.channel_label.value.strip())
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.followup.send(f"⚠️ Fehler: {e}", ephemeral=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Request Hub View (7 buttons, persistent)
# ---------------------------------------------------------------------------
# Poll Hub Modal + auto-category helper
# ---------------------------------------------------------------------------

async def _get_or_create_poll_channels(guild: discord.Guild) -> tuple[discord.TextChannel | None, discord.TextChannel | None]:
    """Return (private_ch, public_ch). Creates both if missing, private = bot-only, public = read-only for everyone.
    The Polls category is placed just below the Hub category."""
    config = await database.get_guild_config(str(guild.id))

    # Try to resolve existing channels from DB
    private_ch = public_ch = None
    if config and config.get("poll_channel_id"):
        try:
            private_ch = guild.get_channel(int(config["poll_channel_id"])) or await guild.fetch_channel(int(config["poll_channel_id"]))
        except Exception:
            private_ch = None
    if config and config.get("poll_public_channel_id"):
        try:
            public_ch = guild.get_channel(int(config["poll_public_channel_id"])) or await guild.fetch_channel(int(config["poll_public_channel_id"]))
        except Exception:
            public_ch = None

    if private_ch and public_ch:
        return private_ch, public_ch

    # Create category directly below Hub category
    hub_cat_pos = 0
    if config and config.get("category_id"):
        hub_cat = guild.get_channel(int(config["category_id"]))
        if hub_cat:
            hub_cat_pos = hub_cat.position

    # Reuse existing Polls category if present
    poll_cat = discord.utils.get(guild.categories, name="Polls")
    if not poll_cat:
        poll_cat = await guild.create_category("Polls")
        try:
            await poll_cat.edit(position=hub_cat_pos + 1)
        except Exception:
            pass

    everyone = guild.default_role

    if not private_ch:
        # #polls — read-only for everyone so bot can add thread members; private threads still restrict content
        private_overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   embed_links=True, manage_threads=True),
        }
        private_ch = await guild.create_text_channel("polls", category=poll_cat, overwrites=private_overwrites)

    if not public_ch:
        # #polls-public — @everyone can read, not send
        public_overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
        }
        public_ch = await guild.create_text_channel("polls-public", category=poll_cat, overwrites=public_overwrites)

    await database.update_poll_channel(str(guild.id), str(private_ch.id), str(public_ch.id))
    return private_ch, public_ch


async def _get_or_create_poll_category(guild: discord.Guild) -> discord.TextChannel | None:
    """Legacy wrapper — returns the private channel (used by hub modal for public polls via public_ch)."""
    _, public_ch = await _get_or_create_poll_channels(guild)
    return public_ch


class PollHubModal(discord.ui.Modal, title="📊 Create Poll"):
    poll_title = discord.ui.TextInput(
        label="Title", placeholder="e.g. Saturday Raid", max_length=120
    )
    description = discord.ui.TextInput(
        label="Description (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Event details...",
        max_length=500,
        required=False,
    )
    event_datetime = discord.ui.TextInput(
        label="Date & Time",
        placeholder="e.g. 07.06.2026 20:00",
        max_length=40,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        # Defer so we can do async work
        await interaction.response.defer(ephemeral=True)

        poll_ch = await _get_or_create_poll_category(guild)
        if not poll_ch:
            await interaction.followup.send("❌ Could not create poll channel.", ephemeral=True)
            return

        title = self.poll_title.value.strip()
        desc = self.description.value.strip()
        event_dt = self.event_datetime.value.strip()

        poll_id = await database.create_poll(str(guild.id), title, desc, event_dt)

        embed = discord.Embed(
            title=f"📊 {title}",
            description=desc or discord.utils.MISSING,
            color=0x6366f1,
        )
        embed.add_field(name="📅 Date & Time", value=event_dt, inline=False)
        embed.set_footer(text=f"Poll #{poll_id} · Created by {interaction.user.display_name}")

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Going", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"poll_available_{poll_id}"))
        view.add_item(discord.ui.Button(label="Maybe", emoji="⏰", style=discord.ButtonStyle.secondary, custom_id=f"poll_maybe_{poll_id}"))
        view.add_item(discord.ui.Button(label="Not going", emoji="❌", style=discord.ButtonStyle.danger, custom_id=f"poll_unavailable_{poll_id}"))

        msg = await poll_ch.send(embed=embed, view=view)
        await database.set_poll_discord_message(poll_id, str(poll_ch.id), str(msg.id))

        await interaction.followup.send(
            f"✅ Poll created! → {poll_ch.mention}",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------

class EnemyScoutModal(discord.ui.Modal, title="👁️ Gegner-Scout melden"):
    victim_player  = discord.ui.TextInput(label="Dein Spielername (Gespähter)", placeholder="z.B. Currax", max_length=100)
    victim_village = discord.ui.TextInput(label="Dein Dorf", placeholder="z.B. Hauptdorf", max_length=100, required=False)
    victim_coords  = discord.ui.TextInput(label="Koordinaten deines Dorfes", placeholder="z.B. 102|47", max_length=30, required=False)
    enemy_player   = discord.ui.TextInput(label="Gegner-Spieler (hat gespäht)", placeholder="z.B. Maximus", max_length=100)
    scout_time     = discord.ui.TextInput(label="Uhrzeit des Scouts (UTC)", placeholder="z.B. 22:45 UTC oder 2025-05-30 22:45", max_length=60)

    async def on_submit(self, interaction: discord.Interaction):
        if _URL_RE.search(self.victim_player.value or "") or _URL_RE.search(self.enemy_player.value or "") \
                or _URL_RE.search(self.victim_village.value or "") or _URL_RE.search(self.scout_time.value or ""):
            await interaction.response.send_message("❌ URLs und Links sind in Anfrage-Feldern nicht erlaubt.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        coords = _clean_coords(self.victim_coords.value) if self.victim_coords.value else ""
        await database.add_scout_incident(
            guild_id=str(interaction.guild_id),
            reported_by_id=str(interaction.user.id),
            reported_by_name=interaction.user.display_name,
            victim_player=self.victim_player.value.strip(),
            victim_village=self.victim_village.value.strip() if self.victim_village.value else "",
            victim_coords=coords,
            enemy_player=self.enemy_player.value.strip(),
            enemy_village="",
            scout_time=self.scout_time.value.strip(),
            notes="",
        )
        await interaction.followup.send(
            f"✅ Gegner-Scout von **{self.enemy_player.value.strip()}** auf **{self.victim_player.value.strip()}** gespeichert.",
            ephemeral=True,
        )


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

    @discord.ui.button(
        label="Helden-Scout", emoji="🦸", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_hero_scout", row=2,
    )
    async def hub_hero_scout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(HeroScoutHubModal())

    @discord.ui.button(
        label="Privater Channel", emoji="🔒", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_private_channel", row=2,
    )
    async def hub_private_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.response.send_modal(PrivateChannelNameModal(lang=lang))

    @discord.ui.button(
        label="Gegner-Scout", emoji="👁️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_enemy_scout", row=2,
    )
    async def hub_enemy_scout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(EnemyScoutModal())

    @discord.ui.button(
        label="Poll", emoji="📊", style=discord.ButtonStyle.primary,
        custom_id="persistent:hub_poll", row=3,
    )
    async def hub_poll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(PollHubModal())

    async def _create_private_channel(self, interaction: discord.Interaction, channel_label: str):
        await _do_create_private_channel(interaction, channel_label)


async def _do_create_private_channel(interaction: discord.Interaction, channel_label: str):
    """Standalone helper — called from both PrivateChannelNameModal and RequestHubView."""
    guild = interaction.guild
    lang = await get_guild_lang(str(guild.id))

    # Check if the user already has a private channel
    existing = await database.get_private_channel(str(guild.id), str(interaction.user.id))
    if existing:
        ch = guild.get_channel(int(existing["channel_id"]))
        if ch:
            await interaction.followup.send(
                t(lang, "private.already_exists", channel=ch.mention), ephemeral=True
            )
            return
        # Channel was deleted externally — clean up and recreate
        await database.delete_private_channel_by_id(existing["channel_id"])

    config = await database.get_guild_config(str(guild.id))

    # Get or create 'Privat Channels' category
    category = await _get_or_create_private_category(guild, lang)

    # Build permission overwrites
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, manage_channels=True,
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True,
        ),
    }
    # Grant access to configured private-channel roles (Lead, Co-Lead…)
    # Falls back to allowed_role_ids if private_channel_role_ids is not set.
    priv_role_ids = ((config or {}).get("private_channel_role_ids") or "").strip()
    role_source = priv_role_ids if priv_role_ids else ((config or {}).get("allowed_role_ids") or "")
    for role_id_str in role_source.split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
            )

    channel_name = f"private-{_safe(channel_label)}"[:100]
    print(f"[hub] Creating private channel '{channel_name}' for {interaction.user} in guild {guild.id}", flush=True)

    new_channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        topic=f"private:{interaction.user.id}",
        overwrites=overwrites,
    )

    # Save to DB
    await database.set_private_channel(str(guild.id), str(interaction.user.id), str(new_channel.id))

    # Post welcome embed with grant/revoke buttons
    embed = discord.Embed(
        title=t(lang, "private.welcome_title", user=interaction.user.display_name),
        description=t(lang, "private.welcome_desc"),
        color=discord.Color.from_rgb(124, 58, 237),
    )
    embed.set_footer(**travops_footer(interaction.user.display_name))
    msg = await new_channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=PrivateChannelView(),
    )
    try:
        await msg.pin()
    except Exception:
        pass

    await interaction.followup.send(
        t(lang, "private.created", channel=new_channel.mention), ephemeral=True
    )


# ---------------------------------------------------------------------------
# Hub Cog
# ---------------------------------------------------------------------------

class Hub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Mark defend/private channel as closed when deleted in Discord."""
        await database.close_defend_channel(str(channel.id))
        await database.delete_private_channel_by_id(str(channel.id))

    @commands.Cog.listener()
    async def on_ready(self):
        """Restore persistent hub views on startup."""
        print("[hub] RequestHubView + PrivateChannelView registered.", flush=True)
        self.bot.loop.create_task(self._process_thread_invites())

    async def _process_thread_invites(self):
        """Background task: drain pending_thread_invites table every 5 seconds."""
        import json as _json
        import aiosqlite
        DB_PATH = database.DB_PATH
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT * FROM pending_thread_invites ORDER BY id LIMIT 20") as cur:
                        rows = [dict(r) for r in await cur.fetchall()]
                import os as _os
                import aiohttp as _aiohttp
                _token = _os.environ.get("DISCORD_TOKEN", "")
                _headers = {"Authorization": f"Bot {_token}"}

                for row in rows:
                    thread_id_int = int(row["thread_id"])
                    user_ids = _json.loads(row["user_ids"] or "[]")
                    print(f"[thread-invite] processing thread {thread_id_int}, {len(user_ids)} users", flush=True)
                    # Use HTTP API directly — no need for cache
                    async with _aiohttp.ClientSession() as _sess:
                        for uid in user_ids:
                            try:
                                async with _sess.put(
                                    f"https://discord.com/api/v10/channels/{thread_id_int}/thread-members/{uid}",
                                    headers=_headers,
                                ) as _r:
                                    if _r.status not in (200, 201, 204):
                                        _txt = await _r.text()
                                        print(f"[thread-invite] {uid} → {_r.status}: {_txt}", flush=True)
                                    else:
                                        print(f"[thread-invite] {uid} ✓", flush=True)
                            except Exception as e:
                                print(f"[thread-invite] {uid}: {e}", flush=True)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM pending_thread_invites WHERE id=?", (row["id"],))
                        await db.commit()
            except Exception as e:
                print(f"[thread-invite-task] {e}", flush=True)
            await asyncio.sleep(5)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hub(bot))
    bot.add_view(RequestHubView())
    bot.add_view(DefendCloseView())
    bot.add_view(PrivateChannelView())
    bot.add_view(HubResAnswerView())
