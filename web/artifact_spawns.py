"""
Estimated artifact spawn positions for Travian servers.
Based on typical server patterns — admins can override individual coordinates per guild.

Artifact types: boots, architect, trainer, diet, confusion, eyes, fool, storage
Sizes: unique (1x), great (3-4x), slight (10x)

Coordinates are ESTIMATES from image analysis. Actual spawns vary by ~5-15 tiles.
"""

# (key, label, type, size, x, y)
_SPAWN_DATA = [
    # ── UNIQUE (near center, within ~20 tiles) ──────────────────────────────
    ("unique_boots",      "Unique Boots",      "boots",     "unique",   5,  -15),
    ("unique_fool",       "Unique Fool",        "fool",      "unique",  -8,   -5),
    ("unique_eyes",       "Unique Eyes",        "eyes",      "unique", -20,    2),
    ("unique_architect",  "Unique Architect",   "architect", "unique",   5,    1),
    ("unique_trainer",    "Unique Trainer",     "trainer",   "unique",  -5,  -20),
    ("unique_diet",       "Unique Diet",        "diet",      "unique",  10,  -20),
    ("unique_confusion",  "Unique Confusion",   "confusion", "unique", -14,  -13),
    ("unique_storage",    "Unique Storage",     "storage",   "unique",   0,   10),

    # ── GREAT (mid ring, ~40-80 tiles) ──────────────────────────────────────
    # Boots
    ("great_boots_1",     "Great Boots I",      "boots",     "great",   30,   43),
    ("great_boots_2",     "Great Boots II",     "boots",     "great",  -60,  -38),
    ("great_boots_3",     "Great Boots III",    "boots",     "great",   20,   65),
    ("great_boots_4",     "Great Boots IV",     "boots",     "great",   80,   35),
    # Architect
    ("great_arch_1",      "Great Architect I",  "architect", "great",   -5,   70),
    ("great_arch_2",      "Great Architect II", "architect", "great",   65,   10),
    ("great_arch_3",      "Great Architect III","architect", "great",  -40,  -55),
    ("great_arch_4",      "Great Architect IV", "architect", "great",    0,  -45),
    # Trainer
    ("great_train_1",     "Great Trainer I",    "trainer",   "great",  -30,   43),
    ("great_train_2",     "Great Trainer II",   "trainer",   "great",  -70,  -55),
    ("great_train_3",     "Great Trainer III",  "trainer",   "great",   50,  -35),
    ("great_train_4",     "Great Trainer IV",   "trainer",   "great",   75,  -22),
    # Diet
    ("great_diet_1",      "Great Diet I",       "diet",      "great",   60,   38),
    ("great_diet_2",      "Great Diet II",      "diet",      "great",  -55,   30),
    ("great_diet_3",      "Great Diet III",     "diet",      "great",   10,  -65),
    ("great_diet_4",      "Great Diet IV",      "diet",      "great",   60,  -40),
    # Confusion
    ("great_conf_1",      "Great Confusion I",  "confusion", "great",   65,   73),
    ("great_conf_2",      "Great Confusion II", "confusion", "great",  -70,   42),
    ("great_conf_3",      "Great Confusion III","confusion", "great",  -42,  -60),
    ("great_conf_4",      "Great Confusion IV", "confusion", "great",   80,  -60),
    # Eyes
    ("great_eyes_1",      "Great Eyes I",       "eyes",      "great",  -70,  -65),
    ("great_eyes_2",      "Great Eyes II",      "eyes",      "great",  -75,   58),
    ("great_eyes_3",      "Great Eyes III",     "eyes",      "great",   80,   68),
    ("great_eyes_4",      "Great Eyes IV",      "eyes",      "great",   55,  -65),
    # Fool
    ("great_fool_1",      "Great Fool I",       "fool",      "great",   35,  -68),
    ("great_fool_2",      "Great Fool II",      "fool",      "great",  -65,   65),
    ("great_fool_3",      "Great Fool III",     "fool",      "great",   60,   60),
    # Storage
    ("great_stor_1",      "Great Storage I",    "storage",   "great",  -40,   50),
    ("great_stor_2",      "Great Storage II",   "storage",   "great",  -70,  -10),
    ("great_stor_3",      "Great Storage III",  "storage",   "great",   10,   65),
    ("great_stor_4",      "Great Storage IV",   "storage",   "great",   85,   38),

    # ── SLIGHT (outer ring, ~80-130 tiles) ──────────────────────────────────
    # Boots
    ("slight_boots_1",    "Slight Boots I",     "boots",     "slight",  125,   68),
    ("slight_boots_2",    "Slight Boots II",    "boots",     "slight",   90,  115),
    ("slight_boots_3",    "Slight Boots III",   "boots",     "slight",  -35,  120),
    ("slight_boots_4",    "Slight Boots IV",    "boots",     "slight", -120,   55),
    ("slight_boots_5",    "Slight Boots V",     "boots",     "slight", -130,  -20),
    ("slight_boots_6",    "Slight Boots VI",    "boots",     "slight",  -90,  -95),
    ("slight_boots_7",    "Slight Boots VII",   "boots",     "slight",   20, -108),
    ("slight_boots_8",    "Slight Boots VIII",  "boots",     "slight",  100,  -90),
    ("slight_boots_9",    "Slight Boots IX",    "boots",     "slight", -155,   38),
    ("slight_boots_10",   "Slight Boots X",     "boots",     "slight",  -75,  100),
    # Architect
    ("slight_arch_1",     "Slight Architect I", "architect", "slight",   -5,  120),
    ("slight_arch_2",     "Slight Architect II","architect", "slight",  100,   80),
    ("slight_arch_3",     "Slight Architect III","architect","slight",  155,   38),
    ("slight_arch_4",     "Slight Architect IV","architect", "slight",  120,  -50),
    ("slight_arch_5",     "Slight Architect V", "architect", "slight",   70, -110),
    ("slight_arch_6",     "Slight Architect VI","architect", "slight", -117,  139),
    ("slight_arch_7",     "Slight Architect VII","architect","slight",  -70, -120),
    ("slight_arch_8",     "Slight Architect VIII","architect","slight",-165,  -10),
    ("slight_arch_9",     "Slight Architect IX","architect", "slight", -130,   80),
    ("slight_arch_10",    "Slight Architect X", "architect", "slight",   -5,  122),
    # Trainer
    ("slight_train_1",    "Slight Trainer I",   "trainer",   "slight",   45,   80),
    ("slight_train_2",    "Slight Trainer II",  "trainer",   "slight",  130,   40),
    ("slight_train_3",    "Slight Trainer III", "trainer",   "slight",  120,  -80),
    ("slight_train_4",    "Slight Trainer IV",  "trainer",   "slight",   30, -110),
    ("slight_train_5",    "Slight Trainer V",   "trainer",   "slight",  -40, -115),
    ("slight_train_6",    "Slight Trainer VI",  "trainer",   "slight",  -85,   80),
    ("slight_train_7",    "Slight Trainer VII", "trainer",   "slight", -130,  -60),
    ("slight_train_8",    "Slight Trainer VIII","trainer",   "slight",  -73, -184),
    ("slight_train_9",    "Slight Trainer IX",  "trainer",   "slight",  -80,  -42),
    ("slight_train_10",   "Slight Trainer X",   "trainer",   "slight",  -80,   42),
    # Diet
    ("slight_diet_1",     "Slight Diet I",      "diet",      "slight",  120,  -55),
    ("slight_diet_2",     "Slight Diet II",     "diet",      "slight",   90,   82),
    ("slight_diet_3",     "Slight Diet III",    "diet",      "slight",  -45,  115),
    ("slight_diet_4",     "Slight Diet IV",     "diet",      "slight",  -80, -120),
    ("slight_diet_5",     "Slight Diet V",      "diet",      "slight",  -55, -100),
    ("slight_diet_6",     "Slight Diet VI",     "diet",      "slight",  100,  -55),
    ("slight_diet_7",     "Slight Diet VII",    "diet",      "slight",   65, -115),
    ("slight_diet_8",     "Slight Diet VIII",   "diet",      "slight", -105,   -8),
    ("slight_diet_9",     "Slight Diet IX",     "diet",      "slight",  -55,  -95),
    ("slight_diet_10",    "Slight Diet X",      "diet",      "slight",   90,   -5),
    # Confusion
    ("slight_conf_1",     "Slight Confusion I", "confusion", "slight",   80,  110),
    ("slight_conf_2",     "Slight Confusion II","confusion", "slight", -110,   90),
    ("slight_conf_3",     "Slight Confusion III","confusion","slight", -120, -100),
    ("slight_conf_4",     "Slight Confusion IV","confusion", "slight",   80,  -80),
    ("slight_conf_5",     "Slight Confusion V", "confusion", "slight",  -20,   43),
    ("slight_conf_6",     "Slight Confusion VI","confusion", "slight",   60,   43),
    ("slight_conf_7",     "Slight Confusion VII","confusion","slight",   95,  -52),
    ("slight_conf_8",     "Slight Confusion VIII","confusion","slight",  65,  -95),
    ("slight_conf_9",     "Slight Confusion IX","confusion", "slight", -130,  -45),
    ("slight_conf_10",    "Slight Confusion X", "confusion", "slight",  -70,   -8),
    # Eyes
    ("slight_eyes_1",     "Slight Eyes I",      "eyes",      "slight", -160,   -8),
    ("slight_eyes_2",     "Slight Eyes II",     "eyes",      "slight",   15,   43),
    ("slight_eyes_3",     "Slight Eyes III",    "eyes",      "slight",   80,  -20),
    ("slight_eyes_4",     "Slight Eyes IV",     "eyes",      "slight",   75,   78),
    ("slight_eyes_5",     "Slight Eyes V",      "eyes",      "slight",  -25,   43),
    ("slight_eyes_6",     "Slight Eyes VI",     "eyes",      "slight",  -60,   95),
    ("slight_eyes_7",     "Slight Eyes VII",    "eyes",      "slight",   55,  -15),
    ("slight_eyes_8",     "Slight Eyes VIII",   "eyes",      "slight", -130,  -85),
    ("slight_eyes_9",     "Slight Eyes IX",     "eyes",      "slight",  -70,  -90),
    ("slight_eyes_10",    "Slight Eyes X",      "eyes",      "slight",  -45,   85),
    # Fool
    ("slight_fool_1",     "Slight Fool I",      "fool",      "slight",  120,   38),
    ("slight_fool_2",     "Slight Fool II",     "fool",      "slight",   90,  -92),
    ("slight_fool_3",     "Slight Fool III",    "fool",      "slight",   80,  -95),
    ("slight_fool_4",     "Slight Fool IV",     "fool",      "slight",   60, -113),
    ("slight_fool_5",     "Slight Fool V",      "fool",      "slight",  -55,  -80),
    ("slight_fool_6",     "Slight Fool VI",     "fool",      "slight", -100,  -65),
    ("slight_fool_7",     "Slight Fool VII",    "fool",      "slight", -115,   -8),
    ("slight_fool_8",     "Slight Fool VIII",   "fool",      "slight",  -80,   55),
    ("slight_fool_9",     "Slight Fool IX",     "fool",      "slight",  -45,   85),
    ("slight_fool_10",    "Slight Fool X",      "fool",      "slight",   75,   78),
    # Storage
    ("slight_stor_1",     "Slight Storage I",   "storage",   "slight",   65,  -80),
    ("slight_stor_2",     "Slight Storage II",  "storage",   "slight",  -70,  -10),
    ("slight_stor_3",     "Slight Storage III", "storage",   "slight",  -40,   65),
    ("slight_stor_4",     "Slight Storage IV",  "storage",   "slight",   80,   38),
    ("slight_stor_5",     "Slight Storage V",   "storage",   "slight",   10,   65),
    ("slight_stor_6",     "Slight Storage VI",  "storage",   "slight",   90,   38),
    ("slight_stor_7",     "Slight Storage VII", "storage",   "slight",   75,  -75),
    ("slight_stor_8",     "Slight Storage VIII","storage",   "slight",  -60,  -85),
    ("slight_stor_9",     "Slight Storage IX",  "storage",   "slight", -100,   45),
    ("slight_stor_10",    "Slight Storage X",   "storage",   "slight",   30,  110),
]

DEFAULT_SPAWNS = [
    {
        "key": key, "label": label, "type": atype, "size": size, "x": x, "y": y
    }
    for key, label, atype, size, x, y in _SPAWN_DATA
]

ARTIFACT_TYPE_LABELS = {
    "boots":     "Boots (Geschwindigkeit)",
    "architect": "Architect (Baugeschwindigkeit)",
    "trainer":   "Trainer (Truppenbau)",
    "diet":      "Diet (Getreideverbrauch)",
    "confusion": "Confusion (Verwirrung)",
    "eyes":      "Eyes (Aufklärung)",
    "fool":      "Fool (Narr)",
    "storage":   "Storage (Lager)",
}

SIZE_ORDER = {"unique": 0, "great": 1, "slight": 2}
