import asyncio
import io
import json
import re

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium

# ---------------------------------------------------------------------------
# Travian Scout-Report Parser
# ---------------------------------------------------------------------------

# Troop names by tribe (1=Roman, 2=Teuton, 3=Gaul, 4=Natar, 5=Hun, 6=Egyptian, 7=Spartan)
# 10 troops + optional hero at position 10
_TRIBE_TROOPS: dict[int, list[str]] = {
    1: ["Legionnaire","Praetorian","Imperian","Equites Legati","Equites Imperatoris",
        "Equites Caesaris","Battering Ram","Fire Catapult","Senator","Settler"],
    2: ["Clubswinger","Spearman","Axeman","Scout","Paladin",
        "Teutonic Knight","Ram","Catapult","Chief","Settler"],
    3: ["Phalanx","Swordsman","Pathfinder","Theutates Thunder","Druidrider",
        "Haeduan","Battering Ram","Trebuchet","Chieftain","Settler"],
    4: ["Natarian Soldier","Natarian Spearman","Natarian Marksman","Natarian Vanguard",
        "Natarian Horseman","Natarian Champion","Natarian Battering Ram","Natarian Catapult",
        "Natarian Chief","Natarian Settler"],
    5: ["Mercenary","Bowman","Spotter","Steppe Rider","Marksman",
        "Marauder","Ram","Catapult","Logades","Settler"],
    6: ["Slave Militia","Ash Warden","Khopesh Warrior","Sopdu Explorer","Anhur Guard",
        "Resheph Chariot","Stone Slinger","Rocket Catapult","Nomarch","Settler"],
    7: ["Hoplite","Sentinel","Shieldsman","Servant","Ephor",
        "Strategos","Battering Ram","Stone Catapult","Governor","Settler"],
}

def _extract_troop_row(line: str) -> list[int]:
    """Extract a sequence of integers from a troop table row (OCR-noisy)."""
    # Replace common OCR artifacts: () → 0, 0) → 0, )0 → 0, lone ) → 0
    cleaned = re.sub(r'\(\)', '0', line)
    cleaned = re.sub(r'(\d)\)', r'\1', cleaned)
    cleaned = re.sub(r'\)(\d)', r'\1', cleaned)
    cleaned = re.sub(r'\)', '0', cleaned)
    nums = re.findall(r'\d+', cleaned)
    # First token is likely the row icon text, skip if non-numeric word precedes
    return [int(n) for n in nums]

def _parse_troop_section(text: str, section: str) -> tuple[list[int] | None, list[int] | None]:
    """Extract the 'sent' and 'losses' rows from ATTACKER/ANGREIFER or DEFENDER/VERTEIDIGER section.
    Returns (sent_row, losses_row)."""
    # Support both EN and DE keywords
    _DE = {"attacker": "angreifer", "defender": "verteidiger", "statistics": "statistik"}
    alt = _DE.get(section.lower(), section)
    sec_start = re.search(rf'\b(?:{section}|{alt})\b', text, re.IGNORECASE)
    if not sec_start:
        return None, None
    next_sec = re.search(
        r'\b(ATTACKER|ANGREIFER|DEFENDER|VERTEIDIGER|STATISTICS|STATISTIK)\b',
        text[sec_start.end():], re.IGNORECASE)
    block = text[sec_start.end(): sec_start.end() + next_sec.start()] if next_sec else text[sec_start.end():]

    rows = []
    for line in block.splitlines():
        nums = _extract_troop_row(line)
        if len(nums) >= 5:
            rows.append(nums)
    # Row 0 = sent, row 1 = losses (dead)
    sent   = rows[0] if len(rows) > 0 else None
    losses = rows[1] if len(rows) > 1 else None
    return sent, losses

# Troop-name aliases (DE + EN) → canonical name
_TROOP_ALIASES: dict[str, str] = {
    # Römisch / Roman
    "legionnaire": "Legionnaire", "legionär": "Legionnaire", "legionäre": "Legionnaire",
    "praetorian": "Praetorian", "prätorianer": "Praetorian",
    "imperian": "Imperian", "imperier": "Imperian",
    "equites legati": "Equites Legati", "aufklärer": "Equites Legati",
    "equites imperatoris": "Equites Imperatoris",
    "equites caesaris": "Equites Caesaris",
    "battering ram": "Battering Ram", "rammbock": "Battering Ram",
    "fire catapult": "Fire Catapult", "feuerwerkzeug": "Fire Catapult",
    "senator": "Senator",
    "settler": "Settler", "siedler": "Settler",
    # Gallisch / Gaul
    "phalanx": "Phalanx",
    "swordsman": "Swordsman", "schwertkämpfer": "Swordsman",
    "pathfinder": "Pathfinder", "pfadfinder": "Pathfinder",
    "theutates thunder": "Theutates Thunder", "theutates-donner": "Theutates Thunder",
    "druidrider": "Druidrider", "druidentreiter": "Druidrider",
    "haeduan": "Haeduan", "haeduer": "Haeduan",
    "ram": "Gaul Ram", "gallische ramme": "Gaul Ram",
    "trebuchet": "Trebuchet", "trebuchet": "Trebuchet",
    "chieftain": "Chieftain", "häuptling (gallisch)": "Chieftain",
    # Teutonisch / Teuton
    "clubswinger": "Clubswinger", "keulenträger": "Clubswinger",
    "spearman": "Spearman", "speerträger": "Spearman",
    "axeman": "Axeman", "axtkämpfer": "Axeman",
    "scout": "Scout", "späher": "Scout",
    "paladin": "Paladin",
    "teutonic knight": "Teutonic Knight", "teut. ritter": "Teutonic Knight", "teutonischer ritter": "Teutonic Knight",
    "teutonen-rammbock": "Teuton Ram", "teuton ram": "Teuton Ram",
    "kriegsmaschine": "Catapult", "catapult": "Catapult",
    "häuptling": "Chief", "chief": "Chief",
    # Natars
    "natarian": "Natarian", "natarianische": "Natarian",
}

_RESOURCE_RE = re.compile(
    r"(?:holz|wood|lumber)[:\s]+([0-9.,]+).*?"
    r"(?:lehm|ton|clay)[:\s]+([0-9.,]+).*?"
    r"(?:eisen|iron)[:\s]+([0-9.,]+).*?"
    r"(?:korn|getreide|crop)[:\s]+([0-9.,]+)",
    re.IGNORECASE | re.DOTALL,
)
# 4 numbers on one line = stolen resources (wood clay iron crop)
_RES_4NUM_RE = re.compile(r"(\d[\d.,]*)\s+(\d[\d.,]*)\s+(\d[\d.,]*)\s+(\d[\d.,]*)")
# Statistics — DE + EN
_COMBAT_STR_RE   = re.compile(
    r"(?:combat strength|kampfst[äa]rke)[^\d]*(\d[\d.,]*)[^\d]+(\d[\d.,]*)", re.IGNORECASE)
_RES_LOST_RE     = re.compile(
    r"(?:resources lost|verlorene ressourcen|ressourcenverlust)[^\d]*(\d[\d.,]*)[^\d]+(\d[\d.,]*)", re.IGNORECASE)
_SUPPLY_BEFORE_RE= re.compile(
    r"(?:supply before|versorgung vor)[^\d]*(\d[\d.,]*)[^\d]+(\d[\d.,]*)", re.IGNORECASE)
# Experience DE + EN
_COORD_RE   = re.compile(r"\((-?\d+)[|\]](-?\d+)\)")
# EN: "from village" / DE: "aus Dorf"
_FROM_VILLAGE_RE = re.compile(r"(.+?)\s+(?:from village|aus Dorf)\s+(.+)", re.IGNORECASE)
# Fallback: explicit label
_PLAYER_RE  = re.compile(r"(?:spieler|player)[:\s]+(.+)", re.IGNORECASE)
_VILLAGE_RE = re.compile(r"(?:dorf|village)[:\s]+(.+)", re.IGNORECASE)
_NUM_RE     = re.compile(r"[\d.,]+")
# Strike detection via text
_STRIKE_RE  = re.compile(
    r"keine truppen des angreifers sind zurückgekehrt|"
    r"no troops of the attacker returned|"
    r"all attacking troops were destroyed",
    re.IGNORECASE
)

# Line patterns that suggest this is a troop row:
# "Legionnaire  10  0" or "Legionär: 10"
_TROOP_LINE_RE = re.compile(
    r"^(.+?)\s{2,}(\d+)(?:\s+(\d+))?$|^(.+?):\s*(\d+)$",
    re.MULTILINE,
)


def _clean_num(s: str) -> int:
    return int(re.sub(r"[.,]", "", s)) if s else 0


def parse_scout_report(text: str) -> dict:
    """Parse a Travian scout report pasted as plain text.
    Returns a dict with all extracted fields (may be partially filled)."""
    result: dict = {
        "target_player": None, "target_village": None, "target_coords": None,
        "attacker_player": None, "attacker_village": None,
        "resources": None, "troops": {}, "losses": {}, "experience": 0,
        "text_strike": bool(_STRIKE_RE.search(text)),
    }

    # Resources — try label-based first, then 4-number line (OCR icon fallback)
    m = _RESOURCE_RE.search(text)
    if m:
        wood, clay, iron, crop = [_clean_num(x) for x in m.groups()]
        result["resources"] = {"wood": wood, "clay": clay, "iron": iron, "crop": crop,
                               "total": wood + clay + iron + crop}
    else:
        # Find all 4-number lines, pick the largest total (= stolen resources)
        best, best_total = None, 0
        for mm in _RES_4NUM_RE.finditer(text):
            vals = [_clean_num(x) for x in mm.groups()]
            t = sum(vals)
            if t > best_total:
                best, best_total = vals, t
        if best and best_total > 0:
            result["resources"] = {"wood": best[0], "clay": best[1],
                                   "iron": best[2], "crop": best[3], "total": best_total}

    # Statistics section
    m = _COMBAT_STR_RE.search(text)
    if m:
        result.setdefault("stats", {})["combat_attacker"] = _clean_num(m.group(1))
        result["stats"]["combat_defender"] = _clean_num(m.group(2))
    m = _RES_LOST_RE.search(text)
    if m:
        result.setdefault("stats", {})["res_lost_attacker"] = _clean_num(m.group(1))
        result["stats"]["res_lost_defender"] = _clean_num(m.group(2))
    m = _SUPPLY_BEFORE_RE.search(text)
    if m:
        result.setdefault("stats", {})["supply_attacker"] = _clean_num(m.group(1))
        result["stats"]["supply_defender"] = _clean_num(m.group(2))

    # Raw troop rows (positions) — tribe resolved later in on_message
    attacker_sent, attacker_losses = _parse_troop_section(text, "ATTACKER")
    defender_sent, defender_losses = _parse_troop_section(text, "DEFENDER")
    if attacker_sent:
        result["attacker_troop_positions"] = attacker_sent
    if attacker_losses:
        result["attacker_loss_positions"] = attacker_losses
    if defender_sent:
        result["defender_troop_positions"] = defender_sent
    if defender_losses:
        result["defender_loss_positions"] = defender_losses

    # Coordinates — first match = target
    coords = _COORD_RE.findall(text)
    if coords:
        result["target_coords"] = f"({coords[0][0]}|{coords[0][1]})"

    # Player / Village names — Travian format: "[Alliance] PlayerName from village VillageName"
    from_village_matches = _FROM_VILLAGE_RE.findall(text)
    if from_village_matches:
        # First match = attacker, second = defender
        def _clean_player(raw: str) -> str:
            # Strip alliance tag like "[TD] " or "(TD) "
            raw = raw.strip()
            raw = re.sub(r"^\[.*?\]\s*", "", raw)
            raw = re.sub(r"^\(.*?\)\s*", "", raw)
            return raw.strip()

        attacker_raw, attacker_village = from_village_matches[0]
        result["attacker_player"]  = _clean_player(attacker_raw)
        result["attacker_village"] = attacker_village.strip()
        if len(from_village_matches) > 1:
            defender_raw, defender_village = from_village_matches[1]
            result["target_player"]  = _clean_player(defender_raw)
            result["target_village"] = defender_village.strip()
    else:
        # Fallback: explicit "Spieler:" / "Player:" labels
        players = _PLAYER_RE.findall(text)
        villages = _VILLAGE_RE.findall(text)
        if players:
            result["target_player"] = players[0].strip()
            if len(players) > 1:
                result["attacker_player"] = players[1].strip()
        if villages:
            result["target_village"] = villages[0].strip()
            if len(villages) > 1:
                result["attacker_village"] = villages[1].strip()

    # Experience
    m = _EXP_RE.search(text)
    if m:
        result["experience"] = int(m.group(1))

    # Troops: scan every line for "TroopName  count  losses" pattern
    troops: dict[str, int] = {}
    losses: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 80:
            continue
        # Split on 2+ whitespace
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 2:
            name_raw = parts[0].strip()
            # Try to match numbers
            nums = [_clean_num(p) for p in parts[1:] if _NUM_RE.fullmatch(p.strip())]
            if not nums:
                continue
            canonical = _TROOP_ALIASES.get(name_raw.lower())
            if canonical:
                troops[canonical] = nums[0]
                if len(nums) >= 2:
                    losses[canonical] = nums[1]
        # Also handle "Name: N" format
        elif ":" in line:
            name_raw, _, val = line.partition(":")
            name_raw = name_raw.strip()
            val = val.strip()
            canonical = _TROOP_ALIASES.get(name_raw.lower())
            if canonical and _NUM_RE.fullmatch(val):
                troops[canonical] = _clean_num(val)

    result["troops"] = troops
    result["losses"] = losses
    return result


async def _try_ocr(image_bytes: bytes) -> str | None:
    """Attempt OCR on image bytes with preprocessing for Travian dark-UI screenshots."""
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageEnhance
        import io as _io

        img = Image.open(_io.BytesIO(image_bytes)).convert("RGB")

        # Scale up small images — Tesseract works better at higher DPI
        w, h = img.size
        if w < 1200:
            scale = 1200 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Enhance contrast & sharpness for dark Travian UI
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        # Try colour + grayscale, pick longer result
        cfg = "--oem 3 --psm 6"
        text_color = pytesseract.image_to_string(img, lang="deu+eng", config=cfg)
        gray = img.convert("L")
        text_gray  = pytesseract.image_to_string(gray, lang="deu+eng", config=cfg)
        text = text_color if len(text_color) >= len(text_gray) else text_gray

        print(f"[scout][ocr] extracted {len(text)} chars", flush=True)
        return text if text.strip() else None
    except ImportError:
        return None  # tesseract not installed
    except Exception as e:
        print(f"[scout][ocr] error: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_channel_after(channel: discord.TextChannel, delay: int = 120):
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="Scout channel closed")
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"[scout] Failed to delete channel {channel.id}: {e}")


async def _do_close(interaction: discord.Interaction, label: str):
    await interaction.message.edit(view=_all_disabled_view())
    await interaction.response.send_message(
        f"🔒 **{label}** by {interaction.user.mention}.\n"
        "This channel will be **deleted in 2 minutes**."
    )
    asyncio.create_task(_delete_channel_after(interaction.channel, delay=120))


def _all_disabled_view(taken_label: str = "Taken by") -> discord.ui.View:
    """All buttons disabled — used as final state after cancel/close."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=taken_label, style=discord.ButtonStyle.success, disabled=True))
    view.add_item(discord.ui.Button(label="Can't do this job", style=discord.ButtonStyle.secondary, disabled=True))
    view.add_item(discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, disabled=True))
    view.add_item(discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, disabled=True))
    return view


# ---------------------------------------------------------------------------
# View: job taken — "Can't do this job" still active to release
# ---------------------------------------------------------------------------

class ScoutTakenView(discord.ui.View):
    """Shown after someone claims the job. Can still be released."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Taken by …",
        style=discord.ButtonStyle.success,
        disabled=True,
        custom_id="persistent:scout_taken_label",
    )
    async def taken_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # disabled — never fires

    @discord.ui.button(
        label="Can't do this job",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_release",
    )
    async def cant_do(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Release the job back to open."""
        await interaction.message.edit(view=ScoutActionView())
        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} can't do this job. The request is **open again**!"
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:scout_taken_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout request cancelled")

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_taken_close",
    )
    async def close_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout channel closed")


# ---------------------------------------------------------------------------
# View: initial action buttons
# ---------------------------------------------------------------------------

class ScoutActionView(discord.ui.View):
    """Persistent view attached to the info message in each scout channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Taken by",
        style=discord.ButtonStyle.success,
        custom_id="persistent:scout_taken",
    )
    async def taken_by(self, interaction: discord.Interaction, button: discord.ui.Button):
        taken_view = ScoutTakenView()
        taken_view.taken_label.label = f"Taken by {interaction.user.display_name}"
        await interaction.message.edit(view=taken_view)
        await interaction.response.send_message(
            f"✋ **{interaction.user.mention}** has taken this scout job!"
        )

    @discord.ui.button(
        label="Can't do this job",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_cant",
    )
    async def cant_do(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"❌ **{interaction.user.mention}** can't do this job. Still looking for a scout..."
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:scout_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout request cancelled")

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_close",
    )
    async def close_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout channel closed")


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class ScoutModal(discord.ui.Modal, title="Scout Request"):
    coordinates = discord.ui.TextInput(label="Coordinates", placeholder="e.g. 500|500", required=True, max_length=50)
    player = discord.ui.TextInput(label="Player", placeholder="Player name", required=True, max_length=100)
    village = discord.ui.TextInput(label="Village", placeholder="Village name", required=True, max_length=100)
    time = discord.ui.TextInput(label="Time", placeholder="e.g. 14:30 UTC", required=True, max_length=50)
    additional_info = discord.ui.TextInput(
        label="Additional Info", placeholder="Any additional information...",
        required=False, style=discord.TextStyle.paragraph, max_length=500,
    )

    def __init__(self, corn_scout: bool = False):
        super().__init__()
        self.corn_scout = corn_scout

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_premium(interaction):
            return
        guild = interaction.guild
        config = await database.get_guild_config(str(guild.id))

        if not config or not config.get("category_id"):
            await interaction.response.send_message(
                "⚠️ The bot is not fully configured yet. Ask an admin to set it up in the web panel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        category = guild.get_channel(int(config["category_id"]))
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("⚠️ Configured category not found.", ephemeral=True)
            return

        # Build channel permissions
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        for role_id_str in (config.get("allowed_role_ids") or "").split(","):
            role_id_str = role_id_str.strip()
            if not role_id_str:
                continue
            role = guild.get_role(int(role_id_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)

        safe_player = re.sub(r"[^a-z0-9]", "-", self.player.value.lower())
        safe_coords = self.coordinates.value.replace("|", "-").replace(" ", "")
        channel_name = f"scout-{safe_player}-{safe_coords}"[:100]

        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Scout: {self.player.value} @ {self.coordinates.value}",
            overwrites=overwrites,
        )

        await database.add_scout_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            player=self.player.value, coordinates=self.coordinates.value,
            village=self.village.value, scout_time=self.time.value,
            additional_info=self.additional_info.value or "",
            requested_by_id=str(interaction.user.id),
            requested_by_name=interaction.user.display_name,
            corn_scout=self.corn_scout,
        )

        embed = discord.Embed(
            title="🌾🌾 Kornspäh-Anfrage" if self.corn_scout else "📡 Scout Request",
            color=discord.Color.gold() if self.corn_scout else discord.Color.blurple(),
        )
        embed.add_field(name="Player", value=self.player.value, inline=True)
        embed.add_field(name="Village", value=self.village.value, inline=True)
        embed.add_field(name="Coordinates", value=self.coordinates.value, inline=True)
        embed.add_field(name="Time", value=self.time.value, inline=True)
        if self.corn_scout:
            embed.add_field(name="🌾🌾 Kornspäh", value="Ja", inline=True)
        if self.additional_info.value:
            embed.add_field(name="Additional Info", value=self.additional_info.value, inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        await new_channel.send(
            content=f"New scout request from {interaction.user.mention}",
            embed=embed,
            view=ScoutActionView(),
        )
        await interaction.followup.send(f"✅ Scout channel created: {new_channel.mention}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        try:
            await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        except Exception:
            pass
        raise error


# ---------------------------------------------------------------------------
# Persistent Scout Request button
# ---------------------------------------------------------------------------

class ScoutRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Scout", style=discord.ButtonStyle.primary,
        emoji="🔍", custom_id="persistent:scout_request",
    )
    async def scout_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutModal(corn_scout=False))

    @discord.ui.button(
        label="Kornspäh", style=discord.ButtonStyle.secondary,
        emoji="🌾", custom_id="persistent:corn_scout",
    )
    async def corn_scout_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutModal(corn_scout=True))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Scout(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup-scout", description="Post the Scout Request button in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_scout(self, interaction: discord.Interaction):
        if not await require_premium(interaction):
            return
        config = await database.get_guild_config(str(interaction.guild.id))
        if not config or not config.get("category_id") or not config.get("archive_channel_id"):
            await interaction.response.send_message(
                "⚠️ Please configure **Category ID** and **Archive Channel ID** in the web admin panel first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📡 Scout Request",
            description="Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.",
            color=discord.Color.blurple(),
        )
        msg = await interaction.channel.send(embed=embed, view=ScoutRequestView())
        await database.update_scout_channel_and_button(
            guild_id=str(interaction.guild.id),
            scout_channel_id=str(interaction.channel.id),
            button_message_id=str(msg.id),
        )
        await interaction.response.send_message("✅ Scout Request button posted!", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for scout reports posted in scout channels."""
        if message.author.bot:
            return
        channel_id = str(message.channel.id)
        if not await database.is_scout_channel(channel_id):
            return

        guild_id = str(message.guild.id) if message.guild else ""
        ch_info  = await database.get_scout_channel_info(channel_id)

        # ── Text report ──────────────────────────────────────────────────────
        if message.content and len(message.content) > 30:
            parsed = parse_scout_report(message.content)
            # Only save if we extracted something meaningful
            if parsed["troops"] or parsed["resources"] or parsed["target_player"]:
                # Fallback: fill coords/player from channel meta if not in text
                if not parsed["target_coords"] and ch_info:
                    parsed["target_coords"] = ch_info.get("coordinates")
                if not parsed["target_player"] and ch_info:
                    parsed["target_player"] = ch_info.get("player")
                if not parsed["target_village"] and ch_info:
                    parsed["target_village"] = ch_info.get("village")
                await database.save_scout_report(
                    channel_id=channel_id, guild_id=guild_id, source="text",
                    raw_text=message.content,
                    target_player=parsed["target_player"],
                    target_village=parsed["target_village"],
                    target_coords=parsed["target_coords"],
                    attacker_player=parsed["attacker_player"],
                    attacker_village=parsed["attacker_village"],
                    resources_json=json.dumps(parsed["resources"]) if parsed["resources"] else None,
                    troops_json=json.dumps(parsed["troops"]) if parsed["troops"] else None,
                    losses_json=json.dumps(parsed["losses"]) if parsed["losses"] else None,
                    experience=parsed["experience"],
                )
                await message.add_reaction("📋")
                print(f"[scout] text report saved for ch {channel_id}: {parsed['troops']}", flush=True)
                return

        # ── Image / OCR report ───────────────────────────────────────────────
        print(f"[scout] message in scout ch {channel_id}: {len(message.attachments)} attachments, content len={len(message.content)}", flush=True)
        for attachment in message.attachments:
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                continue
            img_bytes = await attachment.read()

            # Always save the image URL (regardless of OCR success)
            image_url = attachment.url

            ocr_text = await _try_ocr(img_bytes)
            if not ocr_text:
                # OCR unavailable — save image reference only
                await database.save_scout_image(
                    guild_id=guild_id, channel_id=channel_id,
                    discord_url=image_url,
                    discord_message_id=str(message.id),
                )
                await message.add_reaction("🖼️")
                # Also upsert enemy if we know the target from channel meta
                if ch_info and ch_info.get("player"):
                    await database.upsert_enemy(
                        guild_id=guild_id,
                        player_name=ch_info["player"],
                        coordinates=ch_info.get("coordinates", ""),
                        village=ch_info.get("village", ""),
                    )
                continue

            parsed = parse_scout_report(ocr_text)

            # Always use channel meta as primary source for enemy identity
            enemy_player = (ch_info or {}).get("player") or parsed.get("attacker_player") or parsed.get("target_player") or ""
            enemy_coords = (ch_info or {}).get("coordinates") or parsed.get("target_coords") or ""
            enemy_village = (ch_info or {}).get("village") or parsed.get("target_village") or ""

            # Resolve troop positions → names using tribe from DB
            async def _resolve(pos_key: str, player_name: str) -> dict:
                positions = parsed.get(pos_key)
                if not positions:
                    return {}
                tribe = await database.get_player_tribe(guild_id, player_name or "")
                troop_names = _TRIBE_TROOPS.get(tribe, [])
                if not troop_names:
                    return {}
                return {troop_names[i]: c for i, c in enumerate(positions[:len(troop_names)]) if c > 0}

            if not parsed.get("troops"):
                attacker_troops = await _resolve("attacker_troop_positions", parsed.get("attacker_player") or "")
                defender_troops = await _resolve("defender_troop_positions", enemy_player)
                if attacker_troops:
                    parsed["troops"] = attacker_troops
                if defender_troops:
                    parsed.setdefault("troops", {}).update(defender_troops)

            if not parsed.get("losses"):
                attacker_losses = await _resolve("attacker_loss_positions", parsed.get("attacker_player") or "")
                defender_losses = await _resolve("defender_loss_positions", enemy_player)
                if attacker_losses:
                    parsed["losses"] = attacker_losses
                if defender_losses:
                    parsed.setdefault("losses", {}).update(defender_losses)

            troops_json = json.dumps(parsed["troops"]) if parsed.get("troops") else None
            losses_json = json.dumps(parsed["losses"]) if parsed.get("losses") else None

            # Save report — target_player = the channel's enemy
            report_id = await database.save_scout_report(
                channel_id=channel_id, guild_id=guild_id, source="ocr",
                raw_text=ocr_text,
                target_player=enemy_player,
                target_village=enemy_village,
                target_coords=enemy_coords,
                attacker_player=parsed.get("attacker_player"),
                attacker_village=parsed.get("attacker_village"),
                resources_json=json.dumps(parsed["resources"]) if parsed.get("resources") else None,
                troops_json=troops_json,
                losses_json=losses_json,
                experience=parsed.get("experience", 0),
                stats_json=json.dumps({**(parsed.get("stats") or {}), "text_strike": parsed.get("text_strike", False)}),
            )
            # Save image linked to this report
            await database.save_scout_image(
                guild_id=guild_id, channel_id=channel_id,
                discord_url=image_url,
                discord_message_id=str(message.id),
                scout_report_id=report_id,
            )
            # Upsert enemy from channel meta
            if enemy_player:
                await database.upsert_enemy(
                    guild_id=guild_id,
                    player_name=enemy_player,
                    coordinates=enemy_coords,
                    village=enemy_village,
                )
            await message.add_reaction("🔍")
            print(f"[scout] ocr report saved for ch {channel_id}, enemy={enemy_player}", flush=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """When a message in a scout channel is deleted in Discord, mark the request closed."""
        channel_id = str(payload.channel_id)
        if not await database.is_scout_channel(channel_id):
            return
        await database.close_scout_channel_by_message(str(payload.message_id))
        print(f"[scout] Message {payload.message_id} deleted → scout request closed", flush=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Remove scout channel from DB when deleted directly in Discord."""
        info = await database.get_scout_channel_info(str(channel.id))
        if info:
            await database.delete_scout_channel(str(channel.id))
            print(f"[scout] Channel {channel.id} deleted in Discord → removed from DB", flush=True)


async def setup(bot: commands.Bot):
    bot.add_view(ScoutRequestView())
    bot.add_view(ScoutActionView())
    bot.add_view(ScoutTakenView())
    await bot.add_cog(Scout(bot))
