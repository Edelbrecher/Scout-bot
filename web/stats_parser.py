"""
Parser for Travian Legends statistics page (copy-paste text).

Handles two formats:

1. WEEKLY stats page ("General" tab) — sections like:
     PvP of the week  /  PvE of the week  /  Defenders of the week  /  Robbers of the week
   Each section: Rank | Player | Points

2. OVERALL ranking table — one big table:
     Rank | Player | Alliance | Population | Off | Def | Raids
"""

import re
from typing import Optional


# ── Unicode cleaner ──────────────────────────────────────────────────────────
# Travian injects LTR/RTL marks and non-breaking spaces into numbers
_UNICODE_JUNK = re.compile(r'[‎‏‪-‮⁦-⁩ ​﻿]')

def _clean(s: str) -> str:
    return _UNICODE_JUNK.sub('', s).strip()


def _parse_num(s: str) -> int:
    s = _clean(s)
    if not s or s in ('-', '—', ''):
        return 0
    # Remove thousands separators (., ,) — keep last dot only if decimal
    # Travian uses ‭1,234,567‬ or ‭1.234.567‬
    s = s.replace('‭', '').replace('‬', '')
    # Multiple dots → European thousands  e.g. 1.234.567
    if s.count('.') > 1:
        s = s.replace('.', '')
    # Multiple commas → US thousands  e.g. 1,234,567
    elif s.count(',') > 1:
        s = s.replace(',', '')
    elif ',' in s and '.' in s:
        # e.g. 1.234,56  European decimal
        s = s.replace('.', '').replace(',', '.')
    else:
        # Single separator — likely thousands
        s = s.replace(',', '').replace('.', '')
    try:
        return int(float(s))
    except ValueError:
        return 0


def _strip_rank(s: str) -> int:
    """'1.' or '1' → 1"""
    s = _clean(s).rstrip('.')
    try:
        return int(s)
    except ValueError:
        return 0


# ── Section header detection ─────────────────────────────────────────────────
_SECTION_HEADERS = {
    # EN: "Attacker of the week" / "PvP of the week"
    # DE: "Angreifer der Woche"
    'pvp':      re.compile(r'pvp\s+of\s+the\s+week|attacker\s+of\s+the\s+week|angreifer\s+der\s+woche', re.I),
    # EN: "PvE of the week" / "Farmer of the week"
    # DE: "Tiervertilger der Woche" / "Tierjäger der Woche"
    'pve':      re.compile(r'pve\s+of\s+the\s+week|farmer\s+of\s+the\s+week|tiervertilger|tierj[äa]ger', re.I),
    # EN: "Defender"  DE: "Verteidiger"
    'def':      re.compile(r'defender|verteidiger', re.I),
    # EN: "Robber" / "Raider"  DE: "Räuber" / "Plünderer"
    'raid':     re.compile(r'robber|raider|räuber|plünderer', re.I),
    # EN: "Climber"  DE: "Aufsteiger"
    'climber':  re.compile(r'climber|aufsteiger', re.I),
}

_COL_HEADER = re.compile(
    r'no\.?\s*\t|rank\s*\t|player|spieler|points|punkte|resources|ressourcen',
    re.I
)


def _classify_line(line: str) -> Optional[str]:
    """Return section key if line is a section header, else None."""
    line_clean = _clean(line)
    for key, pat in _SECTION_HEADERS.items():
        if pat.search(line_clean):
            return key
    return None


def _is_col_header(line: str) -> bool:
    return bool(_COL_HEADER.search(_clean(line)))


def _parse_row(parts: list[str]) -> Optional[tuple[int, str, int]]:
    """
    Parse a data row from a weekly section.
    Returns (rank, player_name, points) or None.
    Parts come from tab-split, may have empty strings from double-tab alignment.
    """
    # Remove empty strings at start (double-tab padding)
    parts = [_clean(p) for p in parts]
    non_empty = [p for p in parts if p]
    if len(non_empty) < 2:
        return None

    # First non-empty should be rank
    rank_str = non_empty[0].rstrip('.')
    if not rank_str.isdigit():
        return None
    rank = int(rank_str)

    # Last non-empty should be the number (points/resources)
    points_str = non_empty[-1]
    points_clean = points_str.replace(',', '').replace('.', '')
    if not points_clean.isdigit():
        return None
    points = _parse_num(points_str)

    # Middle part(s) = player name
    player = ' '.join(non_empty[1:-1]) if len(non_empty) > 2 else non_empty[1]
    if not player:
        return None

    return rank, player, points


def parse_weekly_stats(text: str) -> tuple[str, list[dict]]:
    """
    Parse the Travian weekly stats page (General tab).
    Returns (detected_type, entries) where detected_type is 'weekly'.
    Entries are merged per player across all sections.
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    players: dict[str, dict] = {}
    current_section = None

    for line in lines:
        clean_line = _clean(line)
        if not clean_line:
            continue

        # Check section header
        sec = _classify_line(clean_line)
        if sec:
            current_section = sec
            continue

        # Skip column headers
        if _is_col_header(clean_line):
            continue

        # Skip navigation / footer noise
        if any(w in clean_line.lower() for w in [
            'homepage', 'discord', 'support', 'game rules', 'imprint', 'travian games',
            'overview', 'attacker', 'defender', 'top 10', 'general', 'wonderof',
            'statistics', 'alliance', 'player(s)', 'village', 'hero', 'link list',
            'farm list', 'recall', 'incoming', 'troop', 'kirilloid', 'friso',
            'switch to', 'server time', 'privacy', 'population:', 'loyalty:',
            'villages', 'spawn', 'task overview', 'farm builder',
        ]):
            continue

        if current_section is None:
            continue

        parts = line.split('\t')
        result = _parse_row(parts)
        if not result:
            continue

        rank, player, points = result

        if player not in players:
            players[player] = {
                'player_name':   player,
                'alliance_name': '',
                'population':    0,
                'off_points':    0,
                'def_points':    0,
                'raid_points':   0,
                'pve_points':    0,
                'off_rank':      0,
                'def_rank':      0,
                'raid_rank':     0,
                'pop_rank':      0,
                'pve_rank':      0,
            }

        if current_section == 'pvp':
            players[player]['off_points'] = points
            players[player]['off_rank']   = rank
        elif current_section == 'pve':
            players[player]['pve_points'] = points
            players[player]['pve_rank']   = rank
        elif current_section == 'def':
            players[player]['def_points'] = points
            players[player]['def_rank']   = rank
        elif current_section == 'raid':
            players[player]['raid_points'] = points
            players[player]['raid_rank']   = rank
        elif current_section == 'climber':
            players[player]['population'] = points
            players[player]['pop_rank']   = rank

    return 'weekly', list(players.values())


# ── Overall ranking table parser ─────────────────────────────────────────────

def parse_overall_stats(text: str) -> list[dict]:
    """
    Parse the Travian overall player ranking table.
    Format: Rank | Player | Alliance | Population | Off | Def | Raids
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    rows = []
    for line in lines:
        line_clean = _clean(line)
        if not line_clean:
            continue
        parts = [_clean(p) for p in line.split('\t')]
        if len(parts) < 3:
            continue
        rows.append(parts)

    entries = []
    for row in rows:
        non_empty = [p for p in row if p]
        if len(non_empty) < 3:
            continue
        rank_str = non_empty[0].rstrip('.')
        if not rank_str.isdigit():
            continue
        rank = int(rank_str)

        # Col 2: alliance (text) or number?
        col2 = non_empty[2].replace(',', '').replace('.', '').strip()
        has_alliance = not col2.isdigit() if col2 else True

        if has_alliance:
            player_name   = non_empty[1]
            alliance_name = non_empty[2]
            nums          = [_parse_num(v) for v in non_empty[3:]]
        else:
            player_name   = non_empty[1]
            alliance_name = ''
            nums          = [_parse_num(v) for v in non_empty[2:]]

        while len(nums) < 4:
            nums.append(0)

        entries.append({
            'player_name':   player_name,
            'alliance_name': alliance_name,
            'pop_rank':      rank,
            'population':    nums[0],
            'off_points':    nums[1],
            'def_points':    nums[2],
            'raid_points':   nums[3],
            'off_rank':      0,
            'def_rank':      0,
            'raid_rank':     0,
        })

    return entries


# ── Auto-detect & dispatch ────────────────────────────────────────────────────

def parse_travian_stats_smart(text: str) -> list[dict]:
    """
    Auto-detect format and parse.
    Returns list of player dicts with standardised keys.
    """
    text_lower = text.lower()

    # Weekly stats detection: look for known section headers
    is_weekly = any(
        pat.search(text_lower)
        for pat in _SECTION_HEADERS.values()
    )

    if is_weekly:
        _, entries = parse_weekly_stats(text)
        return entries

    # Fallback: overall ranking table
    return parse_overall_stats(text)


# ---------------------------------------------------------------------------
# Travian Player Profile Parser (village list)
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(r'\(\s*(-?\d+)\s*[|,]\s*(-?\d+)\s*\)')
_POP_RE    = re.compile(r'Population:\s*([\d.,\s‭‬]+)', re.I)
_VIL_COUNT = re.compile(r'Villages?\s+([\d,. ]+)/([\d,. ]+)', re.I)

_LABEL_KEYWORDS = {
    'capital': 'Hauptstadt', 'hauptstadt': 'Hauptstadt',
    'off villa': 'Off Villa', 'off village': 'Off Villa',
    'def villa': 'Def Villa', 'def village': 'Def Villa',
    'sup': 'Support', 'support': 'Support',
    'spawn': 'Spawn',
    'oasis': 'Oase',
}

_NOISE_LINES = re.compile(
    r'^(Homepage|Discord|Support|Game rules|Terms|Imprint|Travian Games|'
    r'Switch to|Server time|Privacy|©|Alliance|Members|Ranking|Profile|'
    r'Attacks|Bonuses|Forum|Options|Overview|Statistics|Task overview|'
    r'Village groups|Farm List|Recall|Incoming|Troop|CP |Send Hero|'
    r'Farm Builder|Kirilloid|Friso|GT |Elephant|Cropper|Smithy|TravOps|'
    r'top10|Loyalty:|Population:|Link list)',
    re.I
)


def parse_player_profile(text: str) -> dict:
    """
    Parse a Travian player profile page (copy-paste).
    Returns:
      {
        player_name: str,
        population: int,
        village_count: int,
        villages: [ {village_name, coords_x, coords_y, is_capital, label, population} ]
      }
    """
    text = _UNICODE_JUNK.sub('', text)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    player_name = ""
    population  = 0
    vil_count   = 0
    villages    = []

    # Extract player name (first non-noise non-number line)
    for line in lines[:10]:
        if _NOISE_LINES.match(line):
            continue
        if re.match(r'^[\d.,%]+$', line):
            continue
        if len(line) > 2 and not any(kw in line.lower() for kw in ['travian', 'alliance', 'server']):
            player_name = line
            break

    # Extract global population
    for line in lines:
        m = _POP_RE.search(line)
        if m:
            population = _parse_num(m.group(1))
            break

    # Extract village count
    for line in lines:
        m = _VIL_COUNT.search(line)
        if m:
            vil_count = _parse_num(m.group(1))
            break

    # Parse village list
    # Pattern: a line has coords (x|y), preceded or followed by village name
    # Labels like "Capital", "Off Villa" may be on the same line or next line

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip noisy lines
        if _NOISE_LINES.match(line):
            i += 1
            continue

        coord_m = _COORD_RE.search(line)
        if coord_m:
            cx = int(coord_m.group(1))
            cy = int(coord_m.group(2))

            # Village name: everything before the coord on this line
            before = line[:coord_m.start()].strip()
            # or previous line if before is empty/just whitespace
            if not before and i > 0:
                prev = lines[i-1].strip()
                if prev and not _NOISE_LINES.match(prev) and not _COORD_RE.search(prev):
                    before = prev

            # Label: everything after coord on this line, or next line
            after = line[coord_m.end():].strip()
            label_src = (after + ' ' + (lines[i+1].strip() if i+1 < len(lines) else '')).lower()

            # Detect label
            label = ""
            is_capital = False
            for kw, lbl in _LABEL_KEYWORDS.items():
                if kw in label_src:
                    label = lbl
                    if lbl == 'Hauptstadt':
                        is_capital = True
                    break
            # Capital also detected by "Capital" keyword alone
            if 'capital' in label_src or 'hauptdorf' in label_src or 'hauptstadt' in label_src:
                is_capital = True
                label = label or 'Hauptstadt'

            village_name = _clean(before) or f"({cx}|{cy})"
            villages.append({
                "village_name": village_name,
                "coords_x":     cx,
                "coords_y":     cy,
                "population":   0,
                "is_capital":   is_capital,
                "label":        label,
            })

        i += 1

    # Deduplicate by coords
    seen = set()
    unique = []
    for v in villages:
        k = (v["coords_x"], v["coords_y"])
        if k not in seen:
            seen.add(k)
            unique.append(v)

    return {
        "player_name":   player_name,
        "population":    population,
        "village_count": vil_count or len(unique),
        "villages":      unique,
    }
