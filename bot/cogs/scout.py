import asyncio
import io
import json
import re

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium, travops_footer
from i18n import t, get_guild_lang

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

# All known section-header keywords (used to split blocks)
_SECTION_HEADER_RE = re.compile(
    r'\b(ATTACKER|ANGREIFER|DEFENDER|VERTEIDIGER|STATISTICS|STATISTIK'
    r'|REINFORCEMENTS|VERSTÄRKUNGEN|SUPPORT|UNTERSTÜTZUNG)\b',
    re.IGNORECASE,
)

def _find_section(text: str, section: str) -> tuple[int, int] | None:
    """Return (start, end) char positions for a named section block.
    section should be the EN name ('ATTACKER' or 'DEFENDER').
    Returns None if the section header is not found."""
    alt_map = {
        "attacker": "angreifer", "defender": "verteidiger",
        "angreifer": "attacker", "verteidiger": "defender",
    }
    alt = alt_map.get(section.lower(), "")
    pattern = (
        rf'\b(?:{re.escape(section)}|{re.escape(alt)})\b' if alt
        else rf'\b{re.escape(section)}\b'
    )
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    next_m = _SECTION_HEADER_RE.search(text, m.end())
    end = next_m.start() if next_m else len(text)
    return m.start(), end


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
_EXP_RE     = re.compile(r"(?:erfahrung|experience)[:\s]+([0-9]+)", re.IGNORECASE)
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


def _clean_player_name(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^\[.*?\]\s*", "", raw)
    raw = re.sub(r"^\(.*?\)\s*", "", raw)
    return raw.strip()


def _extract_player_from_block(block: str) -> tuple[str | None, str | None]:
    """Extract (player_name, village_name) from a section block."""
    m = _FROM_VILLAGE_RE.search(block)
    if m:
        return _clean_player_name(m.group(1)), m.group(2).strip()
    pm = _PLAYER_RE.search(block)
    vm = _VILLAGE_RE.search(block)
    player  = _clean_player_name(pm.group(1)) if pm else None
    village = vm.group(1).strip() if vm else None
    return player, village


def _extract_troop_rows(block: str) -> tuple[list[int] | None, list[int] | None]:
    """Extract (sent_row, losses_row) from any block by scanning for
    lines with ≥5 numbers — does NOT rely on section header keywords."""
    rows = []
    for line in block.splitlines():
        nums = _extract_troop_row(line)
        if len(nums) >= 5:
            rows.append(nums)
    sent   = rows[0] if len(rows) > 0 else None
    losses = rows[1] if len(rows) > 1 else None
    return sent, losses


def _split_report_blocks(text: str) -> tuple[str, str] | None:
    """Split a report into (attacker_block, defender_block) using
    'X from village Y' / 'X aus Dorf Y' as anchors.
    Returns None if fewer than 2 such lines are found.
    The first occurrence = attacker; the second = defender."""
    matches = list(_FROM_VILLAGE_RE.finditer(text))
    if len(matches) < 2:
        return None
    att_block = text[matches[0].start() : matches[1].start()]
    def_block = text[matches[1].start():]
    return att_block, def_block


def parse_scout_report(text: str) -> dict:
    """Parse a Travian scout/battle report.

    Validation rules:
      - ATTACKER section must be present.
      - DEFENDER section must be present AND appear AFTER the ATTACKER section.
      - DEFENDER must contain a readable player name.
    If any rule fails, result['valid'] is False and 'invalid_reason' explains why.
    """
    result: dict = {
        "valid": False, "invalid_reason": None,
        "target_player": None, "target_village": None, "target_coords": None,
        "attacker_player": None, "attacker_village": None,
        "resources": None, "troops": {}, "losses": {}, "experience": 0,
        "text_strike": bool(_STRIKE_RE.search(text)),
    }

    # ── Split into attacker / defender blocks via "from village" anchors ─────
    # This is deliberately NOT based on section-header keywords because OCR
    # frequently garbles "DEFENDER" → "perenoer" etc. due to surrounding icons.
    blocks = _split_report_blocks(text)
    if not blocks:
        result["invalid_reason"] = "cannot_split_blocks"
        return result
    att_block, def_block = blocks

    # ── Player names ─────────────────────────────────────────────────────────
    att_player, att_village = _extract_player_from_block(att_block)
    def_player, def_village = _extract_player_from_block(def_block)

    if not att_player:
        result["invalid_reason"] = "attacker_player_unreadable"
        return result
    if not def_player:
        result["invalid_reason"] = "defender_player_unreadable"
        return result

    # ── Troop rows must be present in BOTH blocks ─────────────────────────────
    attacker_sent, attacker_losses = _extract_troop_rows(att_block)
    defender_sent, defender_losses = _extract_troop_rows(def_block)

    if not attacker_sent:
        result["invalid_reason"] = "attacker_troops_missing"
        return result
    if not defender_sent:
        result["invalid_reason"] = "defender_troops_missing"
        return result

    # ── Report is valid ──────────────────────────────────────────────────────
    result["valid"] = True
    result["attacker_player"]  = att_player
    result["attacker_village"] = att_village
    result["target_player"]    = def_player
    result["target_village"]   = def_village

    # Coordinates — prefer one from defender block, fallback to first in full text
    coords_def = _COORD_RE.findall(def_block)
    coords_all = _COORD_RE.findall(text)
    if coords_def:
        result["target_coords"] = f"({coords_def[0][0]}|{coords_def[0][1]})"
    elif coords_all:
        result["target_coords"] = f"({coords_all[0][0]}|{coords_all[0][1]})"

    # Store troop positions (already extracted during validation above)
    result["attacker_troop_positions"] = attacker_sent
    if attacker_losses: result["attacker_loss_positions"] = attacker_losses
    result["defender_troop_positions"] = defender_sent
    if defender_losses: result["defender_loss_positions"] = defender_losses

    # Resources — label-based first, then context-aware 4-number fallback
    m = _RESOURCE_RE.search(text)
    if m:
        w, c, i, cr = [_clean_num(x) for x in m.groups()]
        result["resources"] = {"wood": w, "clay": c, "iron": i, "crop": cr, "total": w+c+i+cr}
    else:
        # Fallback: look for 4-number group near a resource-related keyword,
        # OR in a "Loot" / "Beute" / "Haul" section.
        # Avoid picking troop rows (which are usually in a table with many columns).
        _RES_CONTEXT_RE = re.compile(
            r"(?:ressourcen|resources|beute|loot|haul|stolen|gestohlen)[^\n]*\n?"
            r"[^\n]*?(\d[\d.,]*)\s+(\d[\d.,]*)\s+(\d[\d.,]*)\s+(\d[\d.,]*)",
            re.IGNORECASE,
        )
        best, best_total = None, 0
        # Prefer context match
        for mm in _RES_CONTEXT_RE.finditer(text):
            vals = [_clean_num(x) for x in mm.groups()]
            t = sum(vals)
            if t > best_total:
                best, best_total = vals, t
        # Generic fallback only if no context match — skip rows that look like
        # troop tables (line has more than 4 numbers = troop row)
        if not best:
            for mm in _RES_4NUM_RE.finditer(text):
                line_start = text.rfind("\n", 0, mm.start()) + 1
                line_end   = text.find("\n", mm.end())
                line = text[line_start: line_end if line_end != -1 else len(text)]
                if len(re.findall(r"\d[\d.,]*", line)) > 5:
                    continue  # too many numbers → troop row, skip
                vals = [_clean_num(x) for x in mm.groups()]
                t = sum(vals)
                if t > best_total:
                    best, best_total = vals, t
        if best and best_total > 0:
            result["resources"] = {"wood": best[0], "clay": best[1],
                                   "iron": best[2], "crop": best[3], "total": best_total}

    # Statistics (search full text — usually in its own section below)
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

    # Experience
    m = _EXP_RE.search(text)
    if m:
        result["experience"] = int(m.group(1))

    # Troop name-based extraction (alias table) — search full text
    troops: dict[str, int] = {}
    losses: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 80:
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 2:
            name_raw = parts[0].strip()
            nums = [_clean_num(p) for p in parts[1:] if _NUM_RE.fullmatch(p.strip())]
            if not nums:
                continue
            canonical = _TROOP_ALIASES.get(name_raw.lower())
            if canonical:
                troops[canonical] = nums[0]
                if len(nums) >= 2:
                    losses[canonical] = nums[1]
        elif ":" in line:
            name_raw, _, val = line.partition(":")
            canonical = _TROOP_ALIASES.get(name_raw.strip().lower())
            if canonical and _NUM_RE.fullmatch(val.strip()):
                troops[canonical] = _clean_num(val.strip())

    result["troops"] = troops
    result["losses"] = losses
    return result


def _run_ocr_sync(image_bytes: bytes) -> str | None:
    """Synchronous OCR — runs in a thread pool via _try_ocr."""
    try:
        import pytesseract
        from PIL import Image, ImageEnhance
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


async def _try_ocr(image_bytes: bytes) -> str | None:
    """Run OCR in a thread pool so the event loop stays free for other messages."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _run_ocr_sync, image_bytes)
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
    lang = await get_guild_lang(str(interaction.guild_id))
    await interaction.response.send_message(
        t(lang, "scout.channel_delete_msg", label=label, user=interaction.user.mention)
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
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.message.edit(view=ScoutActionView())
        await interaction.response.send_message(
            t(lang, "scout.released", user=interaction.user.mention)
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

    def __init__(self, troop_link: str = ""):
        super().__init__(timeout=None)
        if troop_link:
            self.add_item(discord.ui.Button(
                label="🏘️ Zum Dorf",
                style=discord.ButtonStyle.link,
                url=troop_link,
                row=1,
            ))

    @discord.ui.button(
        label="Taken by",
        style=discord.ButtonStyle.success,
        custom_id="persistent:scout_taken",
    )
    async def taken_by(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        taken_view = ScoutTakenView()
        taken_view.taken_label.label = f"{t(lang, 'scout.btn.taken')} {interaction.user.display_name}"
        await interaction.message.edit(view=taken_view)
        await interaction.response.send_message(
            t(lang, "scout.taken", user=interaction.user.mention)
        )

    @discord.ui.button(
        label="Can't do this job",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_cant",
    )
    async def cant_do(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.response.send_message(
            t(lang, "scout.cant_do", user=interaction.user.mention)
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
        lang = await get_guild_lang(str(guild.id))
        config = await database.get_guild_config(str(guild.id))

        if not config or not config.get("category_id"):
            await interaction.response.send_message(
                t(lang, "not_configured"),
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
            title=t(lang, "corn.title") if self.corn_scout else t(lang, "scout.title"),
            color=discord.Color.gold() if self.corn_scout else discord.Color.blurple(),
        )
        embed.add_field(name=t(lang, "scout.field.player"), value=self.player.value, inline=True)
        embed.add_field(name=t(lang, "scout.field.village"), value=self.village.value, inline=True)
        embed.add_field(name=t(lang, "scout.field.coords"), value=self.coordinates.value, inline=True)
        embed.add_field(name=t(lang, "scout.field.time"), value=self.time.value, inline=True)
        if self.corn_scout:
            embed.add_field(name="🌾 Corn Scout", value=t(lang, "corn.yes"), inline=True)
        if self.additional_info.value:
            embed.add_field(name=t(lang, "scout.field.info"), value=self.additional_info.value, inline=False)
        embed.set_footer(**travops_footer(t(lang, "requested_by", user=interaction.user.display_name)))

        tw_world = (config or {}).get("tw_world") or ""
        coord_match = re.search(r"(-?\d+)\s*[|/]\s*(-?\d+)", self.coordinates.value)
        scout_troop_link = ""
        if coord_match and tw_world:
            cx, cy = coord_match.group(1), coord_match.group(2)
            scout_troop_link = f"{tw_world.rstrip('/')}/build.php?gid=16&tt=2&eventType=4&x={cx}&y={cy}"

        await new_channel.send(
            content=t(lang, "scout.new_request", user=interaction.user.mention),
            embed=embed,
            view=ScoutActionView(troop_link=scout_troop_link),
        )
        await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)

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
        lang = await get_guild_lang(str(interaction.guild.id))
        config = await database.get_guild_config(str(interaction.guild.id))
        if not config or not config.get("category_id") or not config.get("archive_channel_id"):
            await interaction.response.send_message(
                t(lang, "scout.setup_missing"),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=t(lang, "scout.title"),
            description=t(lang, "scout.embed.description"),
            color=discord.Color.blurple(),
        )
        msg = await interaction.channel.send(embed=embed, view=ScoutRequestView())
        await database.update_scout_channel_and_button(
            guild_id=str(interaction.guild.id),
            scout_channel_id=str(interaction.channel.id),
            button_message_id=str(msg.id),
        )
        await interaction.response.send_message(t(lang, "scout.setup_done"), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for scout reports posted in scout channels or report channels."""
        if message.author.bot:
            return
        channel_id = str(message.channel.id)

        is_scout  = await database.is_scout_channel(channel_id)
        is_report = (not is_scout) and await database.is_report_channel(channel_id)

        if not is_scout and not is_report:
            return

        guild_id = str(message.guild.id) if message.guild else ""
        ch_info  = await database.get_scout_channel_info(channel_id) if is_scout else None

        # ── Text report ──────────────────────────────────────────────────────
        if message.content and len(message.content) > 30:
            parsed = parse_scout_report(message.content)
            # Only save if report is valid (both sections present, defender readable)
            if parsed.get("valid"):
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
            try:
                img_bytes  = await attachment.read()
                image_url  = attachment.url
                ocr_text   = await _try_ocr(img_bytes)

                if not ocr_text:
                    await database.save_scout_image(
                        guild_id=guild_id, channel_id=channel_id,
                        discord_url=image_url, discord_message_id=str(message.id),
                    )
                    if not is_report and ch_info and ch_info.get("player"):
                        await database.upsert_enemy(
                            guild_id=guild_id, player_name=ch_info["player"],
                            coordinates=ch_info.get("coordinates", ""),
                            village=ch_info.get("village", ""),
                        )
                    try:
                        await message.add_reaction("🖼️")
                    except discord.HTTPException:
                        pass
                    continue

                parsed = parse_scout_report(ocr_text)

                if not parsed.get("valid"):
                    reason = parsed.get("invalid_reason", "unknown")
                    print(f"[scout] report rejected ({reason}) in ch {channel_id}", flush=True)
                    print(f"[scout][ocr_dump]\n{ocr_text}\n[/ocr_dump]", flush=True)
                    # Save the image unlinked so it's not lost, but don't create an enemy entry
                    await database.save_scout_image(
                        guild_id=guild_id, channel_id=channel_id,
                        discord_url=image_url, discord_message_id=str(message.id),
                    )
                    try:
                        await message.add_reaction("❓")
                    except discord.HTTPException:
                        pass
                    continue

                if is_report:
                    # Report channel: enemy = defender (target), attacker = our player
                    enemy_player  = parsed.get("target_player") or ""
                    enemy_coords  = parsed.get("target_coords") or ""
                    enemy_village = parsed.get("target_village") or ""
                else:
                    # Scout channel: enemy identity from channel metadata (reliable),
                    # coords/village enriched from OCR defender block
                    enemy_player  = (ch_info or {}).get("player") or parsed.get("target_player") or ""
                    enemy_coords  = (ch_info or {}).get("coordinates") or parsed.get("target_coords") or ""
                    enemy_village = (ch_info or {}).get("village") or parsed.get("target_village") or ""

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
                    at = await _resolve("attacker_troop_positions", parsed.get("attacker_player") or "")
                    dt = await _resolve("defender_troop_positions", enemy_player)
                    if at: parsed["troops"] = at
                    if dt: parsed.setdefault("troops", {}).update(dt)

                if not parsed.get("losses"):
                    al = await _resolve("attacker_loss_positions", parsed.get("attacker_player") or "")
                    dl = await _resolve("defender_loss_positions", enemy_player)
                    if al: parsed["losses"] = al
                    if dl: parsed.setdefault("losses", {}).update(dl)

                report_id = await database.save_scout_report(
                    channel_id=channel_id, guild_id=guild_id, source="ocr",
                    raw_text=ocr_text,
                    target_player=enemy_player, target_village=enemy_village, target_coords=enemy_coords,
                    attacker_player=parsed.get("attacker_player"),
                    attacker_village=parsed.get("attacker_village"),
                    resources_json=json.dumps(parsed["resources"]) if parsed.get("resources") else None,
                    troops_json=json.dumps(parsed["troops"]) if parsed.get("troops") else None,
                    losses_json=json.dumps(parsed["losses"]) if parsed.get("losses") else None,
                    experience=parsed.get("experience", 0),
                    stats_json=json.dumps({**(parsed.get("stats") or {}), "text_strike": parsed.get("text_strike", False)}),
                )
                await database.save_scout_image(
                    guild_id=guild_id, channel_id=channel_id,
                    discord_url=image_url, discord_message_id=str(message.id),
                    scout_report_id=report_id,
                )
                if enemy_player:
                    await database.upsert_enemy(
                        guild_id=guild_id, player_name=enemy_player,
                        coordinates=enemy_coords, village=enemy_village,
                    )
                try:
                    await message.add_reaction("📋" if is_report else "🔍")
                except discord.HTTPException:
                    pass
                print(f"[scout] {'report' if is_report else 'ocr'} report saved for ch {channel_id}, enemy={enemy_player}", flush=True)

            except Exception as e:
                import traceback
                print(f"[scout] ERROR processing attachment in ch {channel_id}: {e}", flush=True)
                traceback.print_exc()

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

    @commands.Cog.listener()
    async def on_ready(self):
        """Catch up on missed messages in report/scout channels since last online."""
        from datetime import datetime, timezone
        last_online = await database.get_bot_last_online()
        await database.set_bot_last_online()

        if not last_online:
            print("[scout][catchup] No last_online timestamp — skipping catch-up.", flush=True)
            return

        print(f"[scout][catchup] Catching up since {last_online.isoformat()} UTC", flush=True)
        processed = 0

        # Gather all channels to check
        channels_to_check = []
        for rc in await database.get_all_report_channels():
            channels_to_check.append((rc["channel_id"], rc["guild_id"], True))
        for sc in await database.get_all_active_scout_channels():
            channels_to_check.append((sc["channel_id"], sc["guild_id"], False))

        after_dt = last_online.replace(tzinfo=timezone.utc)

        for channel_id, guild_id, is_report in channels_to_check:
            ch = self.bot.get_channel(int(channel_id))
            if not ch:
                continue
            try:
                async for message in ch.history(after=after_dt, oldest_first=True, limit=50):
                    if message.author.bot:
                        continue
                    if not message.attachments:
                        continue
                    for attachment in message.attachments:
                        if not attachment.content_type or not attachment.content_type.startswith("image/"):
                            continue
                        try:
                            img_bytes = await attachment.read()
                            ocr_text = await _try_ocr(img_bytes)
                            if not ocr_text:
                                continue
                            parsed = parse_scout_report(ocr_text)
                            if not parsed.get("valid"):
                                continue
                            ch_info = await database.get_scout_channel_info(channel_id) if not is_report else None
                            enemy_player  = parsed.get("target_player") or (ch_info or {}).get("player") or ""
                            enemy_coords  = parsed.get("target_coords") or (ch_info or {}).get("coordinates") or ""
                            enemy_village = parsed.get("target_village") or (ch_info or {}).get("village") or ""
                            await database.save_scout_report(
                                channel_id=channel_id, guild_id=guild_id, source="ocr_catchup",
                                raw_text=ocr_text,
                                target_player=enemy_player, target_village=enemy_village, target_coords=enemy_coords,
                                attacker_player=parsed.get("attacker_player"),
                                attacker_village=parsed.get("attacker_village"),
                                resources_json=json.dumps(parsed["resources"]) if parsed.get("resources") else None,
                                troops_json=json.dumps(parsed["troops"]) if parsed.get("troops") else None,
                                losses_json=json.dumps(parsed["losses"]) if parsed.get("losses") else None,
                                experience=parsed.get("experience", 0),
                                stats_json=json.dumps(parsed.get("stats") or {}),
                            )
                            if enemy_player:
                                await database.upsert_enemy(
                                    guild_id=guild_id, player_name=enemy_player,
                                    coordinates=enemy_coords, village=enemy_village,
                                )
                            processed += 1
                            print(f"[scout][catchup] Saved missed report in ch {channel_id}: {enemy_player}", flush=True)
                        except Exception as e:
                            print(f"[scout][catchup] Error processing attachment: {e}", flush=True)
            except Exception as e:
                print(f"[scout][catchup] Error reading channel {channel_id}: {e}", flush=True)

        print(f"[scout][catchup] Done — {processed} missed report(s) processed.", flush=True)


async def setup(bot: commands.Bot):
    bot.add_view(ScoutRequestView())
    bot.add_view(ScoutActionView())
    bot.add_view(ScoutTakenView())
    await bot.add_cog(Scout(bot))
