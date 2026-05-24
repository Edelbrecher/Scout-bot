"""
hero_item_db.py — Alle Travian Legends Hero-Items mit Metadaten.

Item-IDs entsprechen den in-game IDs aus Travian T4.4/T4.6.
Icons werden vom Travian-CDN geladen (Pfad: /img/heroItems/{id}.png).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Item-Datenbank
# ID → {name, category, tier, bonus_desc}
# ---------------------------------------------------------------------------

HERO_ITEMS: dict[int, dict] = {
    # ── Helme ────────────────────────────────────────────────────────────
    1:  {"name": "Helm des Söldners",       "en": "Helmet of the Mercenary",  "cat": "helmet", "tier": 1},
    2:  {"name": "Helm des Kriegers",        "en": "Helmet of the Warrior",    "cat": "helmet", "tier": 2},
    3:  {"name": "Helm des Anführers",       "en": "Helmet of the Leader",     "cat": "helmet", "tier": 3},
    4:  {"name": "Helm des Kommandeurs",     "en": "Helmet of the Commander",  "cat": "helmet", "tier": 4},
    # ── Rüstungen ────────────────────────────────────────────────────────
    11: {"name": "Plattenrüstung des Söldners",  "en": "Cuirass of the Mercenary",  "cat": "armor", "tier": 1},
    12: {"name": "Plattenrüstung des Kriegers",  "en": "Cuirass of the Warrior",    "cat": "armor", "tier": 2},
    13: {"name": "Plattenrüstung des Anführers", "en": "Cuirass of the Leader",     "cat": "armor", "tier": 3},
    14: {"name": "Plattenrüstung des Kommandeurs","en": "Cuirass of the Commander", "cat": "armor", "tier": 4},
    # ── Schuhe ───────────────────────────────────────────────────────────
    21: {"name": "Stiefel des Söldners",     "en": "Boots of the Mercenary",   "cat": "boots", "tier": 1},
    22: {"name": "Stiefel des Kriegers",     "en": "Boots of the Warrior",     "cat": "boots", "tier": 2},
    23: {"name": "Stiefel des Anführers",    "en": "Boots of the Leader",      "cat": "boots", "tier": 3},
    24: {"name": "Stiefel des Kommandeurs",  "en": "Boots of the Commander",   "cat": "boots", "tier": 4},
    # ── Waffen — Römer ───────────────────────────────────────────────────
    31: {"name": "Schwert des Söldners",     "en": "Sword of the Mercenary",   "cat": "weapon", "tier": 1, "tribe": "Romans"},
    32: {"name": "Schwert des Kriegers",     "en": "Sword of the Warrior",     "cat": "weapon", "tier": 2, "tribe": "Romans"},
    33: {"name": "Schwert des Anführers",    "en": "Sword of the Leader",      "cat": "weapon", "tier": 3, "tribe": "Romans"},
    34: {"name": "Schwert des Kommandeurs",  "en": "Sword of the Commander",   "cat": "weapon", "tier": 4, "tribe": "Romans"},
    # ── Waffen — Germanen ────────────────────────────────────────────────
    41: {"name": "Keule des Söldners",       "en": "Club of the Mercenary",    "cat": "weapon", "tier": 1, "tribe": "Teutons"},
    42: {"name": "Keule des Kriegers",       "en": "Club of the Warrior",      "cat": "weapon", "tier": 2, "tribe": "Teutons"},
    43: {"name": "Keule des Anführers",      "en": "Club of the Leader",       "cat": "weapon", "tier": 3, "tribe": "Teutons"},
    44: {"name": "Keule des Kommandeurs",    "en": "Club of the Commander",    "cat": "weapon", "tier": 4, "tribe": "Teutons"},
    # ── Waffen — Gallier ─────────────────────────────────────────────────
    51: {"name": "Dolch des Söldners",       "en": "Dagger of the Mercenary",  "cat": "weapon", "tier": 1, "tribe": "Gauls"},
    52: {"name": "Dolch des Kriegers",       "en": "Dagger of the Warrior",    "cat": "weapon", "tier": 2, "tribe": "Gauls"},
    53: {"name": "Dolch des Anführers",      "en": "Dagger of the Leader",     "cat": "weapon", "tier": 3, "tribe": "Gauls"},
    54: {"name": "Dolch des Kommandeurs",    "en": "Dagger of the Commander",  "cat": "weapon", "tier": 4, "tribe": "Gauls"},
    # ── Waffen — Ägypter ─────────────────────────────────────────────────
    61: {"name": "Khopesh des Söldners",     "en": "Khopesh of the Mercenary", "cat": "weapon", "tier": 1, "tribe": "Egyptians"},
    62: {"name": "Khopesh des Kriegers",     "en": "Khopesh of the Warrior",   "cat": "weapon", "tier": 2, "tribe": "Egyptians"},
    63: {"name": "Khopesh des Anführers",    "en": "Khopesh of the Leader",    "cat": "weapon", "tier": 3, "tribe": "Egyptians"},
    64: {"name": "Khopesh des Kommandeurs",  "en": "Khopesh of the Commander", "cat": "weapon", "tier": 4, "tribe": "Egyptians"},
    # ── Waffen — Hunnen ──────────────────────────────────────────────────
    71: {"name": "Lanze des Söldners",       "en": "Lance of the Mercenary",   "cat": "weapon", "tier": 1, "tribe": "Huns"},
    72: {"name": "Lanze des Kriegers",       "en": "Lance of the Warrior",     "cat": "weapon", "tier": 2, "tribe": "Huns"},
    73: {"name": "Lanze des Anführers",      "en": "Lance of the Leader",      "cat": "weapon", "tier": 3, "tribe": "Huns"},
    74: {"name": "Lanze des Kommandeurs",    "en": "Lance of the Commander",   "cat": "weapon", "tier": 4, "tribe": "Huns"},
    # ── Pferde ───────────────────────────────────────────────────────────
    81: {"name": "Kleines Pferd",            "en": "Small Horse",              "cat": "mount",  "tier": 1},
    82: {"name": "Pferd",                    "en": "Horse",                    "cat": "mount",  "tier": 2},
    83: {"name": "Großes Pferd",             "en": "Large Horse",              "cat": "mount",  "tier": 3},
    84: {"name": "Majestätisches Pferd",     "en": "Majestic Horse",           "cat": "mount",  "tier": 4},
    # ── Sonstiges / Accessoires ──────────────────────────────────────────
    91: {"name": "Bandage",                  "en": "Bandage",                  "cat": "misc",   "tier": 1},
    92: {"name": "Kleine Verbandkiste",      "en": "Small Bandage Box",        "cat": "misc",   "tier": 1},
    93: {"name": "Verbandkiste",             "en": "Bandage Box",              "cat": "misc",   "tier": 2},
    94: {"name": "Große Verbandkiste",       "en": "Large Bandage Box",        "cat": "misc",   "tier": 3},
    101:{"name": "Buch der Weisheit (kl.)",  "en": "Small Tome of Wisdom",     "cat": "misc",   "tier": 1},
    102:{"name": "Buch der Weisheit",        "en": "Tome of Wisdom",           "cat": "misc",   "tier": 2},
    103:{"name": "Großes Buch der Weisheit", "en": "Large Tome of Wisdom",     "cat": "misc",   "tier": 3},
    111:{"name": "Cage",                     "en": "Cage",                     "cat": "misc",   "tier": 1},
    # ── Artefakte (Sonderitems) ──────────────────────────────────────────
    120:{"name": "Schatz des Fürsten",       "en": "Treasure of the Prince",   "cat": "artifact","tier": 5},
    121:{"name": "Schatz des Königs",        "en": "Treasure of the King",     "cat": "artifact","tier": 5},
    130:{"name": "Lanze der Weisheit",       "en": "Lance of Wisdom",          "cat": "special", "tier": 5},
}

# Slug-Mapping für schnelle Namenssuche
_NAME_INDEX: dict[str, int] = {}
for _id, _item in HERO_ITEMS.items():
    _NAME_INDEX[_item["en"].lower()] = _id
    _NAME_INDEX[_item["name"].lower()] = _id


def get_item_by_id(item_id: int) -> dict | None:
    return HERO_ITEMS.get(item_id)


def get_item_by_name(name: str) -> dict | None:
    item_id = _NAME_INDEX.get(name.lower())
    return HERO_ITEMS.get(item_id) if item_id else None


def all_item_ids() -> list[int]:
    return list(HERO_ITEMS.keys())


TIER_COLORS = {1: "#94a3b8", 2: "#22c55e", 3: "#3b82f6", 4: "#a855f7", 5: "#f59e0b"}
TIER_LABELS = {1: "Bronze", 2: "Silber", 3: "Gold", 4: "Legendär", 5: "Artefakt"}
CAT_LABELS  = {
    "helmet": "Helm", "armor": "Rüstung", "boots": "Schuhe",
    "weapon": "Waffe", "mount": "Pferd", "misc": "Sonstiges", "artifact": "Artefakt",
    "special": "Special",
}
