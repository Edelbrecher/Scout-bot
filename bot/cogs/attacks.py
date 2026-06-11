import json
import re
from datetime import datetime

import discord
from discord.ext import commands

import database
from utils import PREMIUM_STATUSES, travops_footer


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_travian_attacks(text: str) -> list[dict]:
    """
    Parse Travian Legends rally point copy-paste (German UI).

    Real format from Versammlungsplatz (copy Strg+A / Strg+C):
        {atk_village}\t[Angriff markieren]{player} {action} {def_village} aus
        ({x}|{y})\t{Troop1}\t{Troop2}\t...
        Einheiten\t{n1}\t{n2}\t...
        Ankunft\tin H:MM:SS Std.um HH:MM:SS
    """
    attacks = []

    # Strip Unicode bidirectional / invisible formatting chars that Travian embeds
    text = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ── Tribe detection from troop names ──────────────────────────────────
    # Maps canonical troop names (and aliases) to tribe
    ROMAN_TROOPS  = {"Legionär", "Prätorianer", "Imperianer",
                     "Equites Legati", "Equites Imperatoris", "Equites Caesaris",
                     "Rammbock", "Feuerkatapult", "Senator"}
    TEUTON_TROOPS = {"Keulenschwinger", "Speerkämpfer", "Axtkämpfer", "Späher",
                     "Paladin", "Teut. Ritter", "Teutonen-Rammbock", "Kriegsmaschine", "Häuptling"}
    GAUL_TROOPS   = {"Phalanx", "Schwertkämpfer", "Pathfinder", "Theutates-Blitz",
                     "Druidentreiter", "Haeduer", "Stammesältester"}

    # Aliases → canonical names (Teuton UI variants)
    ALIASES = {
        "Kundschafter":    "Späher",
        "Teutonen Reiter": "Teut. Ritter",
        "Ramme":           "Teutonen-Rammbock",
        "Katapult":        "Kriegsmaschine",
        "Stammesführer":   "Häuptling",
        # Gaul alias
        "Aufklärer":       "Pathfinder",
    }

    # Ordered troop lists per tribe (for positional count parsing)
    ROMAN_LIST  = ["Legionär", "Prätorianer", "Imperianer",
                   "Equites Legati", "Equites Imperatoris", "Equites Caesaris",
                   "Rammbock", "Feuerkatapult", "Senator", "Siedler", "Held"]
    TEUTON_LIST = ["Keulenschwinger", "Speerkämpfer", "Axtkämpfer", "Späher",
                   "Paladin", "Teut. Ritter", "Teutonen-Rammbock", "Kriegsmaschine", "Häuptling", "Siedler", "Held"]
    GAUL_LIST   = ["Phalanx", "Schwertkämpfer", "Pathfinder", "Theutates-Blitz",
                   "Druidentreiter", "Haeduer", "Stammesältester", "Siedler", "Held"]
    FALLBACK_LIST = ROMAN_LIST  # generic fallback

    def normalize_name(name: str) -> str:
        """Apply aliases to normalize troop name."""
        return ALIASES.get(name.strip(), name.strip())

    def detect_tribe(troop_header: str) -> str | None:
        """Given the tab-separated troop name header, detect tribe."""
        names = [normalize_name(n) for n in troop_header.split("\t") if n.strip()]
        for name in names:
            if name in ROMAN_TROOPS:
                return "Römer"
            if name in TEUTON_TROOPS:
                return "Germanen"
            if name in GAUL_TROOPS:
                return "Gallier"
        return None

    def tribe_troop_list(tribe: str | None) -> list[str]:
        if tribe == "Römer":     return ROMAN_LIST
        if tribe == "Germanen":  return TEUTON_LIST
        if tribe == "Gallier":   return GAUL_LIST
        return FALLBACK_LIST

    ACTION_TYPE = {
        "raubt": "raid",
        "greift an": "attack",
        "greift": "attack",
        "verstärkt": "reinforce",
        "bespitzelt": "spy",
        "siedelt": "settle",
    }

    def clean_int(s: str) -> int:
        s = s.strip()
        if s == "?" or not re.search(r"\d", s):
            return 0
        return int(re.sub(r"[^\d]", "", s))

    # ── Auto-detect defender coords from village sidebar ──────────────────
    # Travian rally point text contains a village list at the bottom/side like:
    #   VillageName\n(-52|27)\n  or  VillageName\t(-52|27)
    # Try to build a map of {village_name: (x, y)} from the full text
    def parse_village_sidebar(raw: str) -> dict[str, tuple[int, int]]:
        village_map = {}
        # Pattern 1: "VillageName\t(x|y)" on same line
        for m in re.finditer(r"^(.+?)\t\((-?\d+)\|(-?\d+)\)", raw, re.MULTILINE):
            name = m.group(1).strip()
            if name and not re.search(r"(Ankunft|Einheiten|Angriff|greift|raubt|aus|an)\b", name, re.IGNORECASE):
                village_map[name] = (int(m.group(2)), int(m.group(3)))
        # Pattern 2: village name on one line, coords on next line
        for m in re.finditer(r"^([^\t\n(]+)\n\((-?\d+)\|(-?\d+)\)", raw, re.MULTILINE):
            name = m.group(1).strip()
            if name and not re.search(r"(Ankunft|Einheiten|Angriff|greift|raubt|aus|an)\b", name, re.IGNORECASE):
                village_map[name] = (int(m.group(2)), int(m.group(3)))
        return village_map

    village_sidebar = parse_village_sidebar(text)

    # ── Primary: real Travian rally point block ────────────────────────────
    # Line 1: atk_village TAB [Angriff markieren]player action def_village aus
    # Line 2: (x|y) TAB troop types  ← NOW CAPTURED
    # Line 3: Einheiten TAB counts
    # Line 4: Ankunft TAB arrival text
    block_re = re.compile(
        r"^([^\t\n]+?)\t"                                           # attacker village
        r"(?:Angriff\s*markieren)?"                                 # optional UI button
        r"(.+?)\s+"                                                 # player name
        r"(raubt|greift\s+an|greift|verstärkt|bespitzelt|siedelt)\s+"  # action keyword
        r"(.+?)\s+(?:aus|an)\s*\n"                                 # defender village + "aus"/"an"
        r"\((-?\d+)\|(-?\d+)\)(?:\t([^\n]*))?\n"                  # (x|y) + optional troop header
        r"Einheiten\t([^\n]+)\n"                                    # Einheiten counts
        r"Ankunft\t([^\n]+)",                                       # Ankunft arrival
        re.IGNORECASE | re.MULTILINE,
    )

    for m in block_re.finditer(text):
        atk_village    = m.group(1).strip()
        player         = m.group(2).strip()
        action_raw     = m.group(3).strip().lower()
        def_village    = m.group(4).strip()
        x, y           = m.group(5), m.group(6)
        troop_header   = m.group(7) or ""   # tab-separated troop type names
        units_raw      = m.group(8).strip()
        arrival_raw    = m.group(9).strip()

        wave_type = next((wt for kw, wt in ACTION_TYPE.items() if kw in action_raw), "attack")

        # Detect tribe from troop name header
        tribe = detect_tribe(troop_header)
        troop_list = tribe_troop_list(tribe)

        # Normalize troop header names for positional mapping
        header_names = [normalize_name(n) for n in troop_header.split("\t") if n.strip()]

        # Parse troop counts
        counts = units_raw.split("\t")
        troops = {}
        if header_names:
            # Use header names for mapping (most accurate)
            for i, name in enumerate(header_names):
                if i < len(counts):
                    v = clean_int(counts[i])
                    if v > 0:
                        troops[name] = v
        else:
            # Fallback: positional mapping against tribe list
            for i, c in enumerate(counts):
                if i < len(troop_list):
                    v = clean_int(c)
                    if v > 0:
                        troops[troop_list[i]] = v

        # Clean arrival: "in 8:39:53 Std.um 09:52:17" → readable
        arrival = arrival_raw.replace("Std.", "Std. ").strip()

        # Try to resolve defender coords from village sidebar
        auto_def_x = auto_def_y = None
        if def_village and def_village in village_sidebar:
            auto_def_x, auto_def_y = village_sidebar[def_village]

        entry = {
            "attacker":         player,
            "attacker_village": atk_village,
            "village":          def_village,
            "coords":           f"({x}|{y})",
            "arrival":          arrival,
            "wave_type":        wave_type,
            "troops":           troops,
            "tribe":            tribe,
        }
        if auto_def_x is not None:
            entry["def_x"] = auto_def_x
            entry["def_y"] = auto_def_y

        attacks.append(entry)

    if attacks:
        return attacks

    # ── Fallback: simpler scan for "Ankunft" lines and surrounding context ─
    # Handles edge cases where the block structure is slightly different
    arrival_re = re.compile(
        r"^([^\t\n]*(?:Angriff\s*markieren|raubt|greift)[^\n]*)\n"
        r"(?:\([^\n]*\n)?"          # coords + optional troop type names
        r"(?:[^\t\n]*\t[^\n]*\n)?"  # optional extra header line (troop names row)
        r"(?:Einheiten[^\n]*\n)?"
        r"Ankunft\t([^\n]+)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in arrival_re.finditer(text):
        context = m.group(1)
        arrival = m.group(2).strip().replace("Std.", "Std. ")
        player_m = re.search(r"(?:markieren)(.+?)\s+(?:raubt|greift)", context, re.IGNORECASE)
        player = player_m.group(1).strip() if player_m else "Unbekannt"
        coords_m = re.search(r"\((-?\d+)\|(-?\d+)\)", context)
        coords = coords_m.group(0) if coords_m else ""
        attacks.append({
            "attacker": player,
            "attacker_village": "",
            "village": "",
            "coords": coords,
            "arrival": arrival,
            "wave_type": "attack",
            "troops": {},
        })

    return attacks


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class AttackModal(discord.ui.Modal, title="Angriff melden"):
    attack_text = discord.ui.TextInput(
        label="Truppenplatz kopieren",
        style=discord.TextStyle.paragraph,
        placeholder="Strg+A → Strg+C im Truppenplatz, dann hier einfügen",
        required=True,
        custom_id="attack_text",
        max_length=4000,
    )
    def_coords = discord.ui.TextInput(
        label="Koordinaten deines Dorfes (X|Y)",
        style=discord.TextStyle.short,
        placeholder="z.B. -52|27",
        required=False,
        custom_id="def_coords",
        max_length=20,
    )
    offline_time = discord.ui.TextInput(
        label="Wie lange warst du offline? (H:MM, optional)",
        style=discord.TextStyle.short,
        placeholder="z.B. 2:30 oder 0:45 — verlängert mögliche Marschzeit",
        required=False,
        custom_id="offline_time",
        max_length=10,
    )

    def __init__(self, alert_channel_id: str):
        super().__init__(custom_id="attack_modal")
        self.alert_channel_id = alert_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw = self.attack_text.value
            coords_raw = self.def_coords.value.strip().strip("()[]")
            offline_raw = self.offline_time.value.strip()

            # Parse defender coords
            def_x = def_y = None
            if coords_raw:
                cm = re.search(r"(-?\d+)[|,\s]+(-?\d+)", coords_raw)
                if cm:
                    def_x, def_y = int(cm.group(1)), int(cm.group(2))

            # Parse offline time → seconds
            offline_seconds = 0
            if offline_raw:
                om = re.search(r"(\d+):(\d{2})", offline_raw)
                if om:
                    offline_seconds = int(om.group(1)) * 3600 + int(om.group(2)) * 60

            attacks = parse_travian_attacks(raw)
            # Inject defender coords + offline time into each attack
            for atk in attacks:
                if def_x is not None:
                    # Manual input overrides auto-detected coords
                    atk["def_x"] = def_x
                    atk["def_y"] = def_y
                # else: auto-detected coords from village sidebar remain (if present)
                if offline_seconds > 0:
                    atk["offline_seconds"] = offline_seconds

            if not attacks:
                await interaction.response.send_message(
                    "❌ Keine Angriffe erkannt. Stelle sicher, dass du den kompletten Truppenplatz-Text (Strg+A + Strg+C) eingefügt hast.",
                    ephemeral=True,
                )
                return

            guild_id = str(interaction.guild_id)
            reporter_id = str(interaction.user.id)
            reporter_name = interaction.user.display_name
            attacks_json_str = json.dumps(attacks, ensure_ascii=False)

            report_id = await database.save_attack_report(
                guild_id, reporter_id, reporter_name, raw, attacks_json_str
            )

            # Build embed
            embed = discord.Embed(
                title="⚔️ Angriff gemeldet!",
                color=0xe74c3c,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(name="Gemeldet von", value=interaction.user.mention, inline=True)
            embed.add_field(name="Anzahl Angriffe", value=str(len(attacks)), inline=True)
            embed.set_footer(**travops_footer(f"Report ID: {report_id}"))

            TYPE_EMOJI = {"raid": "🪖", "attack": "⚔️", "reinforce": "🛡️", "spy": "🕵️", "settle": "🏘️"}
            for i, atk in enumerate(attacks[:10], 1):
                wt = atk.get("wave_type", "attack")
                emoji = TYPE_EMOJI.get(wt, "⚔️")
                name = f"{emoji} Angriff {i}"
                parts = []
                if atk.get("attacker"):
                    parts.append(f"**Angreifer:** {atk['attacker']}")
                if atk.get("attacker_village"):
                    parts.append(f"**Von:** {atk['attacker_village']} {atk.get('coords','')}")
                if atk.get("village"):
                    parts.append(f"**Ziel:** {atk['village']}")
                if atk.get("arrival"):
                    parts.append(f"**Ankunft:** {atk['arrival']}")
                troops = atk.get("troops", {})
                if troops:
                    troop_str = " · ".join(f"{v}× {k}" for k, v in list(troops.items())[:5])
                    parts.append(f"**Truppen:** {troop_str}")
                embed.add_field(name=name, value="\n".join(parts) or "—", inline=False)

            if len(attacks) > 10:
                embed.add_field(
                    name="...",
                    value=f"Und {len(attacks) - 10} weitere Angriffe (siehe Dashboard)",
                    inline=False,
                )

            # Post to alert channel
            alert_channel = interaction.guild.get_channel(int(self.alert_channel_id))
            channel_ok = False
            if alert_channel:
                # Ensure bot has permission to send
                bot_member = interaction.guild.me
                perms = alert_channel.permissions_for(bot_member)
                if not perms.send_messages or not perms.embed_links:
                    try:
                        await alert_channel.set_permissions(
                            bot_member,
                            view_channel=True,
                            send_messages=True,
                            embed_links=True,
                            attach_files=True,
                        )
                    except discord.Forbidden:
                        pass
                try:
                    await alert_channel.send(embed=embed)
                    channel_ok = True
                except discord.Forbidden:
                    print(f"[attacks] Cannot send to alert channel {self.alert_channel_id}: Forbidden", flush=True)

            if channel_ok:
                await interaction.response.send_message(
                    f"✅ **{len(attacks)} Angriff(e)** wurden gemeldet und im Alarm-Kanal gepostet.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"⚠️ **{len(attacks)} Angriff(e)** erkannt, aber der Bot hat keine Schreibrechte im Alarm-Kanal.\n"
                    f"Bitte den Alarm-Kanal im Dashboard neu einrichten oder die Bot-Berechtigungen prüfen.",
                    ephemeral=True,
                )
        except Exception as e:
            print(f"[attacks] on_submit error: {e}", flush=True)
            try:
                await interaction.response.send_message(
                    f"❌ Fehler beim Verarbeiten: {e}",
                    ephemeral=True,
                )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[attacks] Modal error: {error}", flush=True)
        try:
            await interaction.response.send_message(
                f"❌ Interner Fehler: {error}",
                ephemeral=True,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Persistent button view
# ---------------------------------------------------------------------------

class AttackReportView(discord.ui.View):
    def __init__(self, alert_channel_id: str):
        super().__init__(timeout=None)
        self.alert_channel_id = alert_channel_id

    @discord.ui.button(
        label="⚔️ Angriff melden",
        style=discord.ButtonStyle.danger,
        custom_id="report_attack",
    )
    async def report_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        channel_id = await database.get_attack_channel(guild_id)
        if not channel_id:
            await interaction.response.send_message(
                "❌ Kein Alarm-Kanal konfiguriert.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AttackModal(alert_channel_id=channel_id))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AttacksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register globally so any message with custom_id="report_attack" is handled
        bot.add_view(AttackReportView(alert_channel_id=""))

    @commands.Cog.listener()
    async def on_ready(self):
        """Restore persistent views for all guilds that have attack setup."""
        async with __import__("aiosqlite").connect(database.DB_PATH) as db:
            async with db.execute(
                "SELECT guild_id, attack_channel_id, attack_button_message_id FROM guild_configs "
                "WHERE attack_button_message_id IS NOT NULL AND attack_channel_id IS NOT NULL"
            ) as cur:
                rows = await cur.fetchall()

        for guild_id, channel_id, message_id in rows:
            try:
                # Skip non-premium guilds on startup restore
                sub_status = await database.get_subscription_status(guild_id)
                if sub_status not in PREMIUM_STATUSES:
                    continue
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                try:
                    msg = await channel.fetch_message(int(message_id))
                    view = AttackReportView(alert_channel_id=channel_id)
                    self.bot.add_view(view, message_id=int(message_id))
                except discord.NotFound:
                    pass
            except Exception as e:
                print(f"[attacks] Failed to restore view for guild {guild_id}: {e}")

        print(f"[attacks] Restored persistent views for {len(rows)} guild(s).")

async def setup(bot: commands.Bot):
    await bot.add_cog(AttacksCog(bot))
