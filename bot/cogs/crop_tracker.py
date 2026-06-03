"""
Def Crop Tracker — log sent defense troops + calculate crop obligations.
Discord slash command: /def-crop
"""
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import json

# ── Crop consumption per troop (crop/hour) ────────────────────────────────────
CROP = {
    # Romans
    "legionnaire": 1, "praetorian": 1, "imperian": 1,
    "equites_legati": 2, "equites_imperatoris": 2, "equites_caesaris": 3,
    "battering_ram_r": 3, "fire_catapult": 6, "senator": 5, "settler_r": 1,
    # Teutons
    "clubswinger": 1, "spearman": 1, "axeman": 1, "scout_t": 1,
    "paladin": 2, "teutonic_knight": 3, "ram_t": 3, "catapult_t": 6,
    "chief": 4, "settler_t": 1,
    # Gauls
    "phalanx": 1, "swordsman": 1, "pathfinder": 2, "theutates_thunder": 2,
    "druidrider": 2, "haeduan": 3, "ram_g": 3, "trebuchet": 6,
    "chieftain": 4, "settler_g": 1,
    # Huns
    "mercenary": 1, "bowman": 1, "spotter": 1, "steppe_rider": 2,
    "marksman": 2, "marauder": 3, "ram_h": 3, "catapult_h": 6,
    "logades": 4, "settler_h": 1,
    # Egyptians
    "slave_militia": 1, "ash_warden": 1, "khopesh_warrior": 1,
    "sopdu_explorer": 2, "anhur_guard": 3, "resheph_chariot": 3,
    "ram_e": 3, "stone_catapult": 6, "nomarch": 4, "settler_e": 1,
}

# Display names for troops grouped by tribe
TRIBE_TROOPS = {
    "Romans": [
        ("legionnaire",        "Legionnaire",         1),
        ("praetorian",         "Praetorian",          1),
        ("imperian",           "Imperian",            1),
        ("equites_legati",     "Equites Legati",      2),
        ("equites_imperatoris","Equites Imperatoris",  2),
        ("equites_caesaris",   "Equites Caesaris",    3),
        ("battering_ram_r",    "Battering Ram",       3),
        ("fire_catapult",      "Fire Catapult",       6),
        ("senator",            "Senator",             5),
    ],
    "Teutons": [
        ("clubswinger",   "Clubswinger",       1),
        ("spearman",      "Spearman",          1),
        ("axeman",        "Axeman",            1),
        ("scout_t",       "Scout",             1),
        ("paladin",       "Paladin",           2),
        ("teutonic_knight","Teutonic Knight",  3),
        ("ram_t",         "Ram",               3),
        ("catapult_t",    "Catapult",          6),
        ("chief",         "Chief",             4),
    ],
    "Gauls": [
        ("phalanx",          "Phalanx",          1),
        ("swordsman",        "Swordsman",         1),
        ("pathfinder",       "Pathfinder",        2),
        ("theutates_thunder","Theutates Thunder", 2),
        ("druidrider",       "Druidrider",        2),
        ("haeduan",          "Haeduan",           3),
        ("ram_g",            "Ram",               3),
        ("trebuchet",        "Trebuchet",         6),
        ("chieftain",        "Chieftain",         4),
    ],
    "Huns": [
        ("mercenary",    "Mercenary",    1),
        ("bowman",       "Bowman",       1),
        ("spotter",      "Spotter",      1),
        ("steppe_rider", "Steppe Rider", 2),
        ("marksman",     "Marksman",     2),
        ("marauder",     "Marauder",     3),
        ("ram_h",        "Ram",          3),
        ("catapult_h",   "Catapult",     6),
        ("logades",      "Logades",      4),
    ],
    "Egyptians": [
        ("slave_militia",    "Slave Militia",     1),
        ("ash_warden",       "Ash Warden",        1),
        ("khopesh_warrior",  "Khopesh Warrior",   1),
        ("sopdu_explorer",   "Sopdu Explorer",    2),
        ("anhur_guard",      "Anhur Guard",       3),
        ("resheph_chariot",  "Resheph Chariot",   3),
        ("ram_e",            "Ram",               3),
        ("stone_catapult",   "Stone Catapult",    6),
        ("nomarch",          "Nomarch",           4),
    ],
}

TRIBE_CHOICES = [
    app_commands.Choice(name="🏛️ Romans",   value="Romans"),
    app_commands.Choice(name="⚒️ Teutons",  value="Teutons"),
    app_commands.Choice(name="🍀 Gauls",    value="Gauls"),
    app_commands.Choice(name="🏹 Huns",     value="Huns"),
    app_commands.Choice(name="☀️ Egyptians",value="Egyptians"),
]


def calc_crop(troops: dict) -> float:
    return sum(CROP.get(k, 0) * v for k, v in troops.items() if v > 0)


def format_intervals(crop_ph: float) -> str:
    if crop_ph <= 0:
        return "No troops → no crop needed."
    lines = []
    for h in [1, 2, 3, 4, 6, 8, 12, 24]:
        amt = int(crop_ph * h)
        lines.append(f"Every **{h}h** → **{amt:,}** 🌾")
    return "\n".join(lines)


# ── Modal: enter troop counts for a tribe ─────────────────────────────────────
class TroopModal(discord.ui.Modal):
    def __init__(self, tribe: str, recipient: str, village: str, notes: str,
                 guild_id: str, web_url: str):
        super().__init__(title=f"Def Crop — {tribe} troops")
        self.tribe      = tribe
        self.recipient  = recipient
        self.village    = village
        self.notes_val  = notes
        self.guild_id   = guild_id
        self.web_url    = web_url

        troops = TRIBE_TROOPS.get(tribe, [])
        # Build 3 text inputs (Discord modals max 5 items)
        # Group troops into 3 fields of ~3 each, format: "Name: count"
        groups = [troops[i:i+3] for i in range(0, len(troops), 3)]
        self.inputs = []
        for idx, grp in enumerate(groups[:5]):
            placeholder = "\n".join(f"{t[1]}: 0" for t in grp)
            field = discord.ui.TextInput(
                label=f"Troops {idx+1} (Name: count)",
                placeholder=placeholder,
                style=discord.TextStyle.paragraph,
                required=False,
            )
            self.add_item(field)
            self.inputs.append((grp, field))

    async def on_submit(self, interaction: discord.Interaction):
        # Parse entries
        import re
        troops = {}
        for grp, field in self.inputs:
            val = field.value or ""
            for key, label, _ in grp:
                # Match "Label: number" or just a number on the same line as label
                pattern = re.compile(
                    rf"(?i){re.escape(label)}\s*:?\s*(\d[\d,\.]*)", re.MULTILINE
                )
                m = pattern.search(val)
                if m:
                    num = int(m.group(1).replace(",", "").replace(".", ""))
                    if num > 0:
                        troops[key] = num

        crop_ph = calc_crop(troops)
        total = sum(troops.values())

        # Save to web API
        saved_ok = False
        try:
            guild_row = await _get_guild_for_channel(interaction.channel_id)
            gid = guild_row["guild_id"] if guild_row else self.guild_id
            payload = {
                "guild_id":          gid,
                "sender_discord_id": str(interaction.user.id),
                "sender_name":       interaction.user.display_name,
                "recipient_name":    self.recipient,
                "recipient_village": self.village,
                "tribe":             self.tribe,
                "troops":            troops,
                "crop_per_hour":     crop_ph,
                "notes":             self.notes_val,
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://web:8080/api/def-crop/save",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    saved_ok = r.status == 200
        except Exception:
            pass

        # Build response embed
        troop_lines = "\n".join(
            f"• {TRIBE_TROOPS[self.tribe][[t[0] for t in TRIBE_TROOPS[self.tribe]].index(k)][1]}: {v:,}"
            for k, v in troops.items()
            if any(t[0] == k for t in TRIBE_TROOPS.get(self.tribe, []))
        ) if troops else "*(none entered)*"

        embed = discord.Embed(
            title="🌾 Def Crop Obligation saved",
            color=0xf59e0b,
        )
        embed.add_field(name="📍 Defending", value=f"**{self.recipient}**" + (f"\n{self.village}" if self.village else ""), inline=True)
        embed.add_field(name="🏛️ Tribe",    value=self.tribe, inline=True)
        embed.add_field(name="⚔️ Troops",   value=troop_lines, inline=False)
        embed.add_field(name="🌾 Crop/hour", value=f"**{crop_ph:,.1f}** per hour · {crop_ph*24:,.0f}/day", inline=False)
        embed.add_field(name="📦 Trade route guide", value=format_intervals(crop_ph), inline=False)
        if self.notes_val:
            embed.add_field(name="📝 Notes", value=self.notes_val, inline=False)
        if not saved_ok:
            embed.set_footer(text="⚠️ Could not save to dashboard — check bot connection")
        else:
            embed.set_footer(text="✅ Saved to your TravOps dashboard → My Account")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _get_guild_for_channel(channel_id: int):
    """Try to find guild_id by looking up the channel's guild."""
    return None  # resolved from interaction.guild_id below


# ── Slash command ──────────────────────────────────────────────────────────────
class CropTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="def-crop",
        description="Log sent def troops and get your crop trade route schedule"
    )
    @app_commands.describe(
        tribe="Tribe of your defending troops",
        recipient="Player name you are defending",
        village="Village name (optional)",
        notes="Optional notes",
    )
    @app_commands.choices(tribe=TRIBE_CHOICES)
    async def def_crop(
        self,
        interaction: discord.Interaction,
        tribe: app_commands.Choice[str],
        recipient: str,
        village: str = "",
        notes: str = "",
    ):
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        modal = TroopModal(
            tribe=tribe.value,
            recipient=recipient,
            village=village,
            notes=notes,
            guild_id=guild_id,
            web_url="",
        )
        # Store guild_id on modal after construction
        modal.guild_id = guild_id
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(CropTracker(bot))
