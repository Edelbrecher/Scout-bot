"""
parsers.py — Shared Travian report parsers used by the bot.

_parse_battle_report is kept in sync with the version in web/main.py.
"""
import re

# ---------------------------------------------------------------------------
# Unit name → canonical key map
# ---------------------------------------------------------------------------

_UNIT_MAP: dict[str, str] = {
    # Romans
    "legionär": "legionnaire", "legionäre": "legionnaire", "legionnaire": "legionnaire", "legionnaires": "legionnaire",
    "prätorianer": "praetorian", "praetorian": "praetorian", "praetorians": "praetorian",
    "imperianer": "imperian", "imperian": "imperian", "imperians": "imperian",
    "equites legati": "equites_legati", "späher": "equites_legati",
    "equites imperatoris": "equites_imperatoris",
    "equites caesaris": "equites_caesaris",
    "feuerkatapult": "fire_catapult", "fire catapult": "fire_catapult",
    "rammbock": "battering_ram", "battering ram": "battering_ram",
    "senator": "senator",
    # Teutons
    "keulenschwinger": "clubswinger", "clubswinger": "clubswinger", "clubswingers": "clubswinger",
    "speerkämpfer": "spearman", "spearman": "spearman", "spearmen": "spearman",
    "axtkämpfer": "axeman", "axeman": "axeman", "axemen": "axeman",
    "aufklärerin": "scout", "scout": "scout", "scouts": "scout",
    "paladin": "paladin", "paladine": "paladin",
    "teutonischer ritter": "teutonic_knight", "teutonic knight": "teutonic_knight",
    "katapult": "catapult", "catapult": "catapult",
    "häuptling": "chief", "chief": "chief",
    # Gauls
    "phalanx": "phalanx",
    "schwertkämpfer": "swordsman", "swordsman": "swordsman", "swordsmen": "swordsman",
    "pfadfinder": "pathfinder", "pathfinder": "pathfinder",
    "thureophor": "thureophor", "theurophor": "thureophor",
    "druide": "druidrider", "druidenreiter": "druidrider", "druid rider": "druidrider",
    "haeduer": "haeduan", "haeduan": "haeduan",
    "trebuchet": "trebuchet", "trebusche": "trebuchet",
    # Huns
    "mercenary": "mercenary", "söldner": "mercenary",
    "bowman": "bowman", "bogenschütze": "bowman",
    "spotter": "spotter",
    "steppe rider": "steppe_rider", "steppenreiter": "steppe_rider",
    "marksman": "marksman", "heckenschütze": "marksman",
    "marauder": "marauder",
    "ram": "battering_ram",
    # Egyptians
    "slave militia": "slave_militia", "sklavenmiliz": "slave_militia",
    "ash warden": "ash_warden",
    "khopesh warrior": "khopesh_warrior",
    "sopdu explorer": "sopdu_explorer",
    "anhur guard": "anhur_guard",
    "resheph chariot": "resheph_chariot",
    # Spartans
    "hoplite": "hoplite", "hoplit": "hoplite",
    "sentinel": "sentinel",
    "shieldsman": "shieldsman",
    "twinsteel thureophoros": "thureophoros",
    "elpida rider": "elpida_rider",
    "corinthian crusher": "corinthian_crusher",
    # Common
    "siedler": "settler", "settler": "settler", "settlers": "settler",
    "held": "hero", "hero": "hero",
}

_SIEGE_UNITS = {"catapult", "trebuchet", "battering_ram", "fire_catapult"}
_CHEAP_UNITS = {
    "clubswinger", "legionnaire", "phalanx", "spearman", "axeman",
    "mercenary", "slave_militia", "hoplite", "sentinel", "shieldsman",
}
_STRONG_OFF = {
    "teutonic_knight", "equites_imperatoris", "equites_caesaris",
    "haeduan", "steppe_rider", "marksman", "marauder",
    "resheph_chariot", "corinthian_crusher", "elpida_rider",
}
_SIEGE_REAL_THRESHOLD = 20


def _detect_fake(troops_sent: dict) -> dict:
    """Analyse troop composition and return fake likelihood."""
    if not troops_sent:
        return {"fake_confidence": "none", "fake_reason": ""}

    total = sum(troops_sent.values())
    if total == 0:
        return {"fake_confidence": "none", "fake_reason": ""}

    siege_count  = sum(troops_sent.get(u, 0) for u in _SIEGE_UNITS)
    cheap_count  = sum(troops_sent.get(u, 0) for u in _CHEAP_UNITS)
    strong_count = sum(troops_sent.get(u, 0) for u in _STRONG_OFF)
    hero_count   = troops_sent.get("hero", 0)

    # Real siege wave: catapults ≥ threshold → never fake
    if siege_count >= _SIEGE_REAL_THRESHOLD:
        return {"fake_confidence": "real", "fake_reason": f"{siege_count} siege units"}

    # Only cheap + hero → likely fake
    non_cheap = total - cheap_count - hero_count
    if total <= 10 and non_cheap == 0:
        return {"fake_confidence": "fake", "fake_reason": f"Only {total} cheap/hero troops"}

    if cheap_count >= total * 0.95 and total > 0 and strong_count == 0 and siege_count == 0:
        return {"fake_confidence": "likely_fake", "fake_reason": f"{int(cheap_count/total*100)}% cheap troops"}

    if strong_count > 0 or siege_count > 0:
        return {"fake_confidence": "real", "fake_reason": "Contains strong/siege units"}

    return {"fake_confidence": "unknown", "fake_reason": ""}


def parse_battle_report(text: str) -> dict:
    """Parse a pasted Travian battle/spy/farm report text.
    Returns a dict with all extracted fields. Missing fields are None/empty.
    """
    result: dict = {
        "report_type": "unknown",
        "report_date": None,
        "attacker_name": None, "attacker_village": None,
        "attacker_x": None, "attacker_y": None,
        "defender_name": None, "defender_village": None,
        "defender_x": None, "defender_y": None,
        "troops_sent": {}, "troops_lost": {}, "def_troops": {},
        "spy_resources": {}, "plunder": {}, "plunder_total": 0,
        "luck": None, "hero_hp": None,
        "raw_text": text,
        "parse_warnings": [],
    }

    lines = [l.strip() for l in text.splitlines()]
    text_lower = text.lower()

    # ── Report type ─────────────────────────────────────────────────────────
    for kw, rt in [
        (["spionage", "espionage", "spy report", "spionbericht"], "spy"),
        (["angriff", "attack", "raid report"], "attack"),
        (["verteidigung", "defense", "defence", "defending"], "defense"),
        (["marktbericht", "market report", "trade report"], "market"),
    ]:
        if any(k in text_lower for k in kw):
            result["report_type"] = rt
            break
    if result["report_type"] == "unknown" and any(k in text_lower for k in ["beute", "plunder", "loot"]):
        result["report_type"] = "attack"

    # ── Clean text ────────────────────────────────────────────────────────────
    text_clean = re.sub(r'[‎‏‪-‮⁦-⁩­]', '', text)

    # ── Date ─────────────────────────────────────────────────────────────────
    date_re = re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})[,\s]+(\d{1,2}:\d{2}:\d{2})')
    dm = date_re.search(text_clean)
    if dm:
        try:
            yr = dm.group(3)
            if len(yr) == 2:
                yr = "20" + yr
            result["report_date"] = f"{yr}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d} {dm.group(4)}"
        except Exception:
            pass

    # ── Coordinates ──────────────────────────────────────────────────────────
    coord_re = re.compile(r'\(\s*(-?\d+)\s*\|\s*(-?\d+)\s*\)')

    _VILLAGE_SUFFIX_RE = re.compile(
        r'\s+(?:from village|aus Dorf|von Dorf)\s+(.+?)(?:\s*\(|$)',
        re.IGNORECASE
    )

    def _clean_name(raw: str):
        raw = re.sub(r'\[/?(?:player|village|ally)\]', '', raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r'^\s*\[[^\]]{1,10}\]\s*', '', raw).strip()
        vm = coord_re.search(raw)
        coord = (int(vm.group(1)), int(vm.group(2))) if vm else None
        base = raw[:vm.start()].strip() if vm else raw
        vsm = _VILLAGE_SUFFIX_RE.search(base)
        if vsm:
            village = vsm.group(1).strip().rstrip(',').strip()
            player = base[:vsm.start()].strip()
        else:
            village = None
            player = base
        player = player.rstrip(',').strip() or None
        return player, village, coord

    lines_clean = text_clean.splitlines()
    stripped = [l.strip() for l in lines_clean]

    def _find_block(keyword_re: str) -> str | None:
        for idx, line in enumerate(stripped):
            m = re.match(keyword_re + r'\s*[:\-–]?\s*(.*)', line, re.IGNORECASE)
            if not m:
                continue
            inline = m.group(1).strip()
            if inline:
                return inline
            for nxt in stripped[idx + 1: idx + 3]:
                if nxt:
                    return nxt
        return None

    att_raw    = _find_block(r'(?:Angreifer|Attacker)')
    def_raw    = _find_block(r'(?:Verteidiger|Defender)')
    att_origin = _find_block(r'(?:Herkunft(?:sdorf)?|Origin)')
    def_village = _find_block(r'(?:Verteidigt mit|Defending village)')

    if att_raw:
        pname, vname, coord = _clean_name(att_raw)
        result["attacker_name"] = pname
        if vname: result["attacker_village"] = vname
        if coord: result["attacker_x"], result["attacker_y"] = coord
    if att_origin:
        _, vname, coord = _clean_name(att_origin)
        if vname and not result.get("attacker_village"): result["attacker_village"] = vname
        if coord: result["attacker_x"], result["attacker_y"] = coord
    if def_raw:
        pname, vname, coord = _clean_name(def_raw)
        result["defender_name"] = pname
        if vname: result["defender_village"] = vname
        if coord: result["defender_x"], result["defender_y"] = coord
    if def_village:
        _, vname, coord = _clean_name(def_village)
        if vname and not result.get("defender_village"): result["defender_village"] = vname
        if coord: result["defender_x"], result["defender_y"] = coord

    # ── Resources / Plunder ───────────────────────────────────────────────────
    res_inline = re.compile(
        r'(?:Holz|Wood|Lumber)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Lehm|Ton|Clay)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Eisen|Iron)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Getreide|Crop|Grain|Korn)[:\s]+(\d[\d\.,]*)',
        re.IGNORECASE | re.DOTALL
    )
    def _ri(s): return int(re.sub(r'[.,\s]', '', s))

    for m in res_inline.finditer(text_clean):
        w, c, i, g = _ri(m.group(1)), _ri(m.group(2)), _ri(m.group(3)), _ri(m.group(4))
        ctx = text_clean[max(0, m.start()-80):m.start()].lower()
        is_spy = any(k in ctx for k in ["spion", "spy", "rohstoff", "resource", "im dorf", "in village"])
        if is_spy:
            result["spy_resources"] = {"wood": w, "clay": c, "iron": i, "crop": g, "total": w+c+i+g}
            if result["report_type"] == "unknown": result["report_type"] = "spy"
        else:
            result["plunder"] = {"wood": w, "clay": c, "iron": i, "crop": g}
            result["plunder_total"] = w + c + i + g

    if not result["plunder"]:
        bounty_m = re.search(
            r'(?:Bounty|Beute)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)',
            text_clean, re.IGNORECASE
        )
        if bounty_m:
            w, c, i, g = (int(bounty_m.group(k)) for k in (1,2,3,4))
            result["plunder"] = {"wood": w, "clay": c, "iron": i, "crop": g}
            result["plunder_total"] = w + c + i + g

    # ── Luck ─────────────────────────────────────────────────────────────────
    luck_m = re.search(
        r'(?:Glück|Luck)[:\s]*(?:↑|↓|[+-])?\s*([+-]?\d+(?:[.,]\d+)?)\s*%',
        text_clean, re.IGNORECASE
    )
    if luck_m:
        try: result["luck"] = float(luck_m.group(1).replace(',', '.'))
        except Exception: pass

    # ── Hero HP ───────────────────────────────────────────────────────────────
    hero_m = re.search(r'Hero\s*\d*\s*\n?\s*(\d+)\s*%', text_clean, re.IGNORECASE)
    if hero_m:
        try: result["hero_hp"] = int(hero_m.group(1))
        except Exception: pass

    # ── Troop table parsing ───────────────────────────────────────────────────
    def _parse_troop_table(txt: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict] = {"sent": {}, "lost": {}, "hospital": {}, "def": {}, "def_lost": {}, "def_hospital": {}}

        def _parse_nums(line: str) -> list[int | None]:
            parts = re.split(r'\s{2,}|\t', line.strip())
            result_nums = []
            for p in parts:
                p = p.strip()
                if p == '?' or p == '':
                    result_nums.append(None)
                else:
                    try:
                        result_nums.append(int(p.replace('.','').replace(',','').replace('−','-')))
                    except Exception:
                        result_nums.append(None)
            return result_nums

        clean_lines = [l.rstrip() for l in txt.splitlines()]
        i = 0
        tables_found = 0
        while i < len(clean_lines):
            line = clean_lines[i].strip()
            parts = re.split(r'\s{2,}|\t', line)
            parts = [p.strip() for p in parts if p.strip()]

            mapped = [_UNIT_MAP.get(p.lower()) for p in parts]
            hits = sum(1 for m in mapped if m)
            if hits < 2:
                i += 1
                continue

            unit_header = [_UNIT_MAP.get(p.lower(), p.lower()) for p in parts]
            dest_key = "def" if tables_found > 0 else "sent"

            value_rows: list[tuple[str | None, list]] = []
            j = i + 1
            while j < min(i + 10, len(clean_lines)):
                vline = clean_lines[j].strip()
                if not vline:
                    j += 1
                    break

                label_m = re.match(
                    r'(gesendet|sent|verluste|losses?|im dorf|in village|überlebende|survivors?'
                    r'|im hospital|in hospital|hospital)'
                    r'\s*[:\-–]?\s*(.*)',
                    vline, re.IGNORECASE
                )
                if label_m:
                    label = label_m.group(1).lower()
                    nums_str = label_m.group(2).strip() or (clean_lines[j + 1].strip() if j + 1 < len(clean_lines) else "")
                    nums = _parse_nums(nums_str)
                    lbl_dest = ("sent"     if any(k in label for k in ["gesendet","sent"]) else
                                "lost"     if any(k in label for k in ["verluste","loss"]) else
                                "hospital" if any(k in label for k in ["hospital"]) else
                                "def"      if any(k in label for k in ["im dorf","in village"]) else None)
                    if lbl_dest:
                        value_rows.append((lbl_dest, nums))
                    j += 1
                    continue

                vparts = re.split(r'\s{2,}|\t', vline)
                vparts = [p.strip() for p in vparts if p.strip()]

                if not vparts:
                    break

                if all(p == '?' for p in vparts):
                    value_rows.append((None, [None] * len(vparts)))
                    j += 1
                    continue

                nums = []
                row_ok = True
                for p in vparts:
                    try:
                        nums.append(int(p.replace('.', '').replace(',', '').replace('−', '-')))
                    except ValueError:
                        row_ok = False
                        break

                if not row_ok or not nums:
                    break

                value_rows.append((None, nums))
                j += 1

            unlabeled = [(dest, nums) for dest, nums in value_rows if dest is None]
            labeled   = [(dest, nums) for dest, nums in value_rows if dest is not None]

            is_defender_table = tables_found > 0

            if unlabeled and not labeled:
                if len(unlabeled) == 1:
                    labeled = [("troops", unlabeled[0][1])]
                elif len(unlabeled) == 2:
                    labeled = [("troops", unlabeled[0][1]), ("losses", unlabeled[1][1])]
                elif len(unlabeled) >= 3:
                    labeled = [("troops", unlabeled[0][1]), ("losses", unlabeled[1][1]), ("hospital", unlabeled[2][1])]

            for dest, nums in labeled:
                if is_defender_table:
                    actual_dest = ("def"          if dest == "troops"   else
                                   "def_hospital" if dest == "hospital" else
                                   "def_lost")
                else:
                    actual_dest = ("sent"     if dest == "troops"   else
                                   "hospital" if dest == "hospital" else
                                   "lost")

                if actual_dest not in out:
                    out[actual_dest] = {}
                for ui, unit in enumerate(unit_header):
                    if ui < len(nums) and nums[ui] is not None and nums[ui] > 0:
                        out[actual_dest][unit] = out[actual_dest].get(unit, 0) + nums[ui]

            tables_found += 1
            i = j

        return out

    troop_data = _parse_troop_table(text_clean)
    result["troops_sent"]          = troop_data.get("sent", {})
    result["troops_lost"]          = troop_data.get("lost", {})
    result["troops_hospital"]      = troop_data.get("hospital", {})
    result["def_troops"]           = troop_data.get("def", {})
    result["def_troops_lost"]      = troop_data.get("def_lost", {})
    result["def_troops_hospital"]  = troop_data.get("def_hospital", {})

    # ── Building damage ───────────────────────────────────────────────────────
    _BLDG_RE = re.compile(
        r'([A-ZÄÖÜa-zäöü][A-Za-zäöüÄÖÜ\s]+?)'
        r'\s+(?:level|stufe|Stufe|Level)\s+(\d+)'
        r'(?:'
            r'\s+(?:destroyed|zerstört)'
            r'|'
            r'\s+(?:damaged(?:\s+to\s+level\s+(\d+))?'
            r'|(?:auf\s+Stufe\s+(\d+)\s+)?beschädigt)'
        r')',
        re.IGNORECASE
    )
    buildings_hit: list[dict] = []
    for line in text_clean.splitlines():
        m = _BLDG_RE.search(line.strip())
        if not m:
            continue
        bname = m.group(1).strip()
        lvl_before = int(m.group(2))
        line_l = line.lower()
        destroyed = any(k in line_l for k in ("destroyed", "zerstört"))
        lvl_after_str = m.group(3) or m.group(4)
        lvl_after = int(lvl_after_str) if lvl_after_str else (0 if destroyed else lvl_before - 1)
        buildings_hit.append({
            "building": bname,
            "level_before": lvl_before,
            "level_after": lvl_after,
            "destroyed": destroyed,
        })
    result["buildings_hit"] = buildings_hit

    # ── Fake detection ────────────────────────────────────────────────────────
    fake_info = _detect_fake(result.get("troops_sent", {}))
    result["fake_confidence"] = fake_info["fake_confidence"]
    result["fake_reason"]     = fake_info["fake_reason"]

    return result
