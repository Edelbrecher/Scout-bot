"""
Parser for Travian Legends statistics page (copy-paste text).

Travian stats tables when copy-pasted from browser typically look like:

  1\tPlayerName\tAllianceName\t12.345\t1.234.567\t987.654\t456.789
  2\tOtherPlayer\t...\t...

Columns vary by which stats page:
  - Overall rankings: Rank | Player | Alliance | Pop | Off | Def | Raids
  - Weekly:           Rank | Player | Alliance | Points this week | ...

Numbers use either '.' or ',' as thousands separator depending on locale.
We try to detect columns by position and header keywords.
"""

import re
from typing import Optional


_NUM_RE = re.compile(r'^[\d.,]+$')


def _parse_num(s: str) -> int:
    """Parse '1.234.567' or '1,234,567' or '1234567' → int."""
    s = s.strip()
    if not s or s == '-' or s == '—':
        return 0
    # Remove thousands separators: if there are multiple dots/commas, strip them
    # Keep the last separator as decimal only if it separates exactly 2 digits
    s = s.replace(' ', '')
    # European format: 1.234.567 → remove dots
    # US format: 1,234,567 → remove commas
    # Try to detect which is thousands separator
    if s.count('.') > 1:
        s = s.replace('.', '')
    elif s.count(',') > 1:
        s = s.replace(',', '')
    elif '.' in s and ',' in s:
        # e.g. 1.234,56 → European decimal
        s = s.replace('.', '').replace(',', '.')
    elif s.endswith(',00') or s.endswith('.00'):
        s = s[:-3]
    else:
        s = s.replace(',', '').replace('.', '')
    try:
        return int(float(s))
    except ValueError:
        return 0


def _is_number_col(values: list[str]) -> bool:
    """True if most non-empty values in a column look like numbers."""
    nums = [v for v in values if v.strip() and v.strip() not in ('-', '—')]
    if not nums:
        return False
    num_count = sum(1 for v in nums if _NUM_RE.match(v.replace('.', '').replace(',', '')))
    return num_count / len(nums) >= 0.7


def parse_travian_stats(text: str) -> list[dict]:
    """
    Parse copy-pasted Travian statistics text.
    Returns list of dicts with keys:
      player_name, alliance_name, population,
      off_points, def_points, raid_points,
      off_rank, def_rank, raid_rank, pop_rank
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 3:
            # Try space-separated fallback (unlikely but safe)
            continue
        rows.append([p.strip() for p in parts])

    if not rows:
        return []

    # Detect header row
    header_idx = 0
    for i, row in enumerate(rows[:5]):
        joined = ' '.join(row).lower()
        if any(k in joined for k in ['player', 'spieler', 'rank', 'alliance', 'allianz', 'population']):
            header_idx = i
            break

    # Try to figure out column layout from headers or positions
    data_rows = rows[header_idx + 1:] if header_idx < len(rows) - 1 else rows

    # Heuristic column detection:
    # Col 0: rank (number)
    # Col 1: player name (text)
    # Col 2: alliance name (text)  — sometimes missing
    # Remaining: numeric columns (pop, off, def, raid) in that rough order
    entries = []
    for row in data_rows:
        if len(row) < 3:
            continue
        # Skip rows that look like sub-headers or empty
        if not row[0] or (not row[0].replace('.', '').replace(',', '').isdigit() and
                           not row[0].isdigit()):
            # rank col should be a number
            clean = row[0].replace('.', '').replace(',', '').strip()
            if not clean.isdigit():
                continue

        try:
            rank = int(row[0].replace('.', '').replace(',', '').strip())
        except ValueError:
            continue

        if len(row) < 4:
            continue

        # Detect if col 2 is alliance (text) or a number (no alliance col)
        col2_clean = row[2].replace('.', '').replace(',', '').strip()
        has_alliance_col = not col2_clean.isdigit() if col2_clean else True

        if has_alliance_col:
            player_name   = row[1]
            alliance_name = row[2]
            num_cols      = row[3:]
        else:
            player_name   = row[1]
            alliance_name = ""
            num_cols      = row[2:]

        if not player_name:
            continue

        nums = [_parse_num(v) for v in num_cols]
        # Pad to at least 4 numeric columns
        while len(nums) < 4:
            nums.append(0)

        # Column order heuristic: pop, off, def, raids
        # Population is usually the smallest, raids usually largest among off/def/raids
        # We just assign positionally — user pastes all-player stats which has this order
        pop   = nums[0] if len(nums) > 0 else 0
        off   = nums[1] if len(nums) > 1 else 0
        defen = nums[2] if len(nums) > 2 else 0
        raid  = nums[3] if len(nums) > 3 else 0

        entries.append({
            "player_name":   player_name,
            "alliance_name": alliance_name,
            "pop_rank":      rank,
            "population":    pop,
            "off_points":    off,
            "def_points":    defen,
            "raid_points":   raid,
            "off_rank":      0,   # filled later if off-ranking page imported
            "def_rank":      0,
            "raid_rank":     0,
        })

    return entries


def parse_travian_stats_smart(text: str) -> list[dict]:
    """
    Smarter multi-section parser.
    Travian stats page often has sections:
      - Population ranking
      - Attacker ranking (off)
      - Defender ranking (def)
      - Raider ranking

    If we get multiple sections in one paste, merge them by player name.
    """
    # Split into sections by detecting rank-1 rows resetting
    sections = _split_sections(text)
    if len(sections) <= 1:
        return parse_travian_stats(text)

    merged: dict[str, dict] = {}
    section_types = ['pop', 'off', 'def', 'raid']

    for idx, section_text in enumerate(sections[:4]):
        entries = parse_travian_stats(section_text)
        stype = section_types[idx] if idx < len(section_types) else 'pop'
        for e in entries:
            pname = e["player_name"]
            if pname not in merged:
                merged[pname] = {
                    "player_name": pname,
                    "alliance_name": e["alliance_name"],
                    "population": 0, "off_points": 0,
                    "def_points": 0, "raid_points": 0,
                    "pop_rank": 0, "off_rank": 0,
                    "def_rank": 0, "raid_rank": 0,
                }
            if e["alliance_name"] and not merged[pname]["alliance_name"]:
                merged[pname]["alliance_name"] = e["alliance_name"]

            rank = e["pop_rank"]
            if stype == 'pop':
                merged[pname]["population"] = e["population"]
                merged[pname]["pop_rank"]   = rank
            elif stype == 'off':
                merged[pname]["off_points"] = e["off_points"] or e["population"]
                merged[pname]["off_rank"]   = rank
            elif stype == 'def':
                merged[pname]["def_points"] = e["def_points"] or e["population"]
                merged[pname]["def_rank"]   = rank
            elif stype == 'raid':
                merged[pname]["raid_points"] = e["raid_points"] or e["population"]
                merged[pname]["raid_rank"]   = rank

    return list(merged.values())


def _split_sections(text: str) -> list[str]:
    """Split text into sections when rank resets to 1."""
    lines = text.replace('\r\n', '\n').split('\n')
    sections = []
    current: list[str] = []
    last_rank = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                current.append(line)
            continue
        parts = stripped.split('\t')
        if parts:
            clean = parts[0].replace('.', '').replace(',', '').strip()
            if clean.isdigit():
                r = int(clean)
                if r == 1 and last_rank > 5 and current:
                    # New section starting
                    sections.append('\n'.join(current))
                    current = []
                last_rank = r
        current.append(line)

    if current:
        sections.append('\n'.join(current))
    return sections if sections else [text]
