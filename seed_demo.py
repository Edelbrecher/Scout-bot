"""
Demo-Seed Script für TravOps
Erstellt eine vollständige Demo-Guild mit realistischen Fake-Daten für Tutorial-Videos.

Verwendung:
  python seed_demo.py                    # Seed einspielen
  python seed_demo.py --wipe             # Demo-Daten vorher löschen
  python seed_demo.py --guild-id XXXXX  # Eigene Guild-ID verwenden

Die Demo-Guild-ID ist standardmäßig: 999000000000000001
"""

import asyncio
import aiosqlite
import hashlib
import json
import random
import argparse
from datetime import datetime, timedelta, timezone

# ── Konfiguration ─────────────────────────────────────────────────────────────

DEMO_GUILD_ID   = "999000000000000001"
DEMO_GUILD_NAME = "DEMO · TravOps Beispiel-Allianz"
DB_PATH         = "/app/data/scouter.db"

# Fiktive Allianz-Mitglieder (Discord-ID, Discord-Name, Travian-Name, Stamm)
MEMBERS = [
    ("100000000000000001", "MaxMuster",    "MaxMuster",    "Römer"),
    ("100000000000000002", "SilverShield", "SilverShield", "Germanen"),
    ("100000000000000003", "FlyingArrow",  "FlyingArrow",  "Gallier"),
    ("100000000000000004", "DesertRose",   "DesertRose",   "Ägypter"),
    ("100000000000000005", "SteppeRider",  "SteppeRider",  "Hunnen"),
    ("100000000000000006", "IronWall",     "IronWall",     "Römer"),
    ("100000000000000007", "NightOwl",     "NightOwl",     "Germanen"),
    ("100000000000000008", "SwiftFox",     "SwiftFox",     "Gallier"),
]

# Fiktive eigene Dörfer
OWN_VILLAGES = [
    ("- 01 Hauptdorf",    0,   0,  "MaxMuster",    "Römer",    3200),
    ("- 02 Holzfäller",   2,  -3,  "MaxMuster",    "Römer",    1800),
    ("- 03 Hammer",       5,   1,  "SilverShield", "Germanen", 2700),
    ("- 04 Nordwacht",   -4,   6,  "FlyingArrow",  "Gallier",  1500),
    ("- 05 Sandburg",     8,  -5,  "DesertRose",   "Ägypter",  2100),
    ("- 06 Reiternest",  -7,  -2,  "SteppeRider",  "Hunnen",   1900),
    ("- 07 Festung",      3,   9,  "IronWall",     "Römer",    3500),
    ("- 08 Eulenhorst",  -9,   3,  "NightOwl",     "Germanen", 1200),
    ("- 09 Fuchsbau",     6,   7,  "SwiftFox",     "Gallier",  2300),
    ("- 10 Grenzposten", -2,  -8,  "MaxMuster",    "Römer",    900),
]

# Fiktive Feinde
ENEMIES = [
    ("Currax",    "- 04 Abracabalada", 104, 46, "BLOOD"),
    ("ZalmecAwp", "- 12 Kronos",       -35, 22, "BLOOD"),
    ("DarkForce", "- 01 Zentrum",       50,-15, "WAR"),
    ("ShadowKing","- 07 Schatten",      -8, 40, "WAR"),
    ("NightBane", "- 03 Dunkel",        20, 35, "DOOM"),
]

# Feind-Artefakte
ENEMY_ARTIFACTS = [
    ("Currax",    104, 46, "unique_scout",   "unique"),
    ("ZalmecAwp", -35, 22, "large_speed",    "large"),
    ("DarkForce",  50,-15, "small_trainer",  "small"),
    ("ShadowKing", -8, 40, "large_storage",  "large"),
]

# Karten-Dörfer (map_snapshots) — Mix aus eigenen, Ally, Feinden und Neutralen
def make_map_villages():
    now = datetime.utcnow().isoformat()
    villages = []
    vid = 1000

    # Eigene Allianz
    for vname, x, y, player, tribe, pop in OWN_VILLAGES:
        villages.append((vid, x, y, vname, player, "DEMO", pop, tribe_to_int(tribe), now))
        vid += 1

    # Feinde
    for player, vname, x, y, ally in ENEMIES:
        villages.append((vid, x, y, vname, player, ally, random.randint(800, 4000), random.randint(1,5), now))
        vid += 1

    # Neutrale Spieler (Umgebung)
    neutrals = [
        ("Trader Joe",    12,  4, "NewbieVillage", None, 340),
        ("FarmTarget1",   -5, 10, "- 01 Stroh",   None, 180),
        ("FarmTarget2",    9, -8, "- 01 Holz",    None, 220),
        ("FarmTarget3",  -12, -5, "- 02 Aufbau",  None, 410),
        ("MidPlayer",      7, 15, "- 03 Mitte",   None, 950),
        ("BigPlayer",    -20, 10, "- 01 Festung", "ALLY2", 5200),
        ("GrowingFast",   15,-10, "- 02 Wachstum","ALLY2", 3100),
        ("Defender_X",    -3, -6, "- 05 Schild",  "ALLY2", 2800),
    ]
    for player, x, y, vname, ally, pop in neutrals:
        villages.append((vid, x, y, vname, player, ally, pop, random.randint(1,5), now))
        vid += 1

    return villages

def tribe_to_int(t):
    return {"Römer":1,"Germanen":2,"Gallier":3,"Ägypter":5,"Hunnen":4,"Spartaner":6}.get(t, 1)

# ── Seed-Funktionen ────────────────────────────────────────────────────────────

async def seed(db, wipe=False):
    now = datetime.utcnow().isoformat()
    tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()

    if wipe:
        print("🗑️  Lösche alte Demo-Daten …")
        for tbl in ["guild_configs","ally_groups","ally_roles","ally_members",
                    "map_snapshots","guild_own_villages","enemies","enemy_artifacts",
                    "incoming_attacks","op_plans","op_targets","op_waves",
                    "scout_channels","scout_reports"]:
            await db.execute(f"DELETE FROM {tbl} WHERE guild_id = ?", (DEMO_GUILD_ID,))
        await db.commit()

    # ── guild_config ──────────────────────────────────────────────────────────
    print("⚙️  Guild-Config …")
    await db.execute("""
        INSERT OR REPLACE INTO guild_configs
          (guild_id, guild_name, scout_channel_id, hero_scout_channel_id,
           server_utc_offset, workspace_status, subscription_status, subscription_plan)
        VALUES (?,?,?,?,?,?,?,?)
    """, (DEMO_GUILD_ID, DEMO_GUILD_NAME,
          "800000000000000001",  # scout channel
          "800000000000000002",  # hero scout channel
          60,                    # UTC+1
          "active", "active", "pro"))

    # ── ally_groups + roles + members ─────────────────────────────────────────
    print("👥 Allianz-Struktur …")
    await db.execute("""
        INSERT OR IGNORE INTO ally_groups
          (guild_id, group_name, wing1_name, wing2_name)
        VALUES (?, ?, ?, ?)
    """, (DEMO_GUILD_ID, "DEMO Main", "DEMO2", "DEMO3"))
    row = await (await db.execute(
        "SELECT id FROM ally_groups WHERE guild_id=? LIMIT 1", (DEMO_GUILD_ID,))).fetchone()
    group_id = row[0]

    roles = [("Leiter","leader"),("Officer","officer"),("Mitglied","member"),("Rekrut","recruit")]
    for rname, rkey in roles:
        await db.execute("""
            INSERT OR IGNORE INTO ally_roles (ally_group_id, role_name, role_key)
            VALUES (?,?,?)
        """, (group_id, rname, rkey))

    role_map = {}
    async with db.execute("SELECT id, role_key FROM ally_roles WHERE ally_group_id=?", (group_id,)) as c:
        async for row in c:
            role_map[row[1]] = row[0]

    member_roles = ["leader","officer","officer","member","member","member","recruit","recruit"]
    for (did, dname, tname, _), rkey in zip(MEMBERS, member_roles):
        await db.execute("""
            INSERT OR IGNORE INTO ally_members
              (ally_group_id, discord_id, discord_username, travian_name, role_id, wing)
            VALUES (?,?,?,?,?,?)
        """, (group_id, did, dname, tname, role_map.get(rkey), 0))

    # ── map_snapshots ─────────────────────────────────────────────────────────
    print("🗺️  Kartendaten …")
    for vid, x, y, vname, player, ally, pop, tribe, ts in make_map_villages():
        await db.execute("""
            INSERT OR IGNORE INTO map_snapshots
              (guild_id, fetched_at, village_id, x, y, village_name,
               player_id, player_name, alliance_id, alliance_name, population, tribe)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (DEMO_GUILD_ID, ts, f"DEMO_{vid}", x, y, vname,
              f"PID_{vid}", player, f"AID_{ally}" if ally else None, ally, pop, tribe))

    # ── eigene Dörfer ─────────────────────────────────────────────────────────
    print("🏘️  Eigene Dörfer …")
    for vname, x, y, player, tribe, pop in OWN_VILLAGES:
        await db.execute("""
            INSERT OR IGNORE INTO guild_own_villages
              (guild_id, village_name, x, y, player_name, tribe, population,
               added_by_id, added_by_name, added_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (DEMO_GUILD_ID, vname, x, y, player, tribe, pop,
              MEMBERS[0][0], MEMBERS[0][1], now))

    # ── Feinde ────────────────────────────────────────────────────────────────
    print("⚔️  Feinde & Artefakte …")
    for player, vname, x, y, ally in ENEMIES:
        await db.execute("""
            INSERT OR IGNORE INTO enemies
              (guild_id, player_name, coordinates, village, notes, first_seen, last_seen, scout_count)
            VALUES (?,?,?,?,?,?,?,?)
        """, (DEMO_GUILD_ID, player, f"{x}|{y}", vname,
              f"Mitglied von {ally}. Aktiver Angreifer.", yesterday, now, random.randint(1,5)))

    for player, vx, vy, atype, asize in ENEMY_ARTIFACTS:
        try:
            await db.execute("""
                INSERT INTO enemy_artifacts
                  (guild_id, player_name, village_x, village_y, artifact_type, artifact_size)
                VALUES (?,?,?,?,?,?)
            """, (DEMO_GUILD_ID, player, vx, vy, atype, asize))
        except Exception:
            pass

    # ── incoming_attacks (Attack Detection) ───────────────────────────────────
    print("🛡️  Eingehende Angriffe …")
    base_time = datetime.utcnow() + timedelta(hours=2)
    attacks = [
        # Welle 1: 3 Fakes (1 Truppe) + 1 echter (20 Truppen) — selbe Sekunde
        dict(own_village_name="- 01 Hauptdorf", own_x=0, own_y=0,
             attacker="Currax", atk_village="- 04 Abracabalada", atk_x=104, atk_y=46,
             atype="attack", hidden=0, count=1, score=95,
             arrival=(base_time).isoformat(),
             reasons=json.dumps(["visible_troops","single_troop"])),
        dict(own_village_name="- 01 Hauptdorf", own_x=0, own_y=0,
             attacker="Currax", atk_village="- 04 Abracabalada", atk_x=104, atk_y=46,
             atype="attack", hidden=0, count=1, score=95,
             arrival=(base_time + timedelta(milliseconds=200)).isoformat(),
             reasons=json.dumps(["visible_troops","single_troop"])),
        dict(own_village_name="- 01 Hauptdorf", own_x=0, own_y=0,
             attacker="Currax", atk_village="- 04 Abracabalada", atk_x=104, atk_y=46,
             atype="attack", hidden=0, count=1, score=95,
             arrival=(base_time + timedelta(milliseconds=700)).isoformat(),
             reasons=json.dumps(["visible_troops","single_troop"])),
        dict(own_village_name="- 01 Hauptdorf", own_x=0, own_y=0,
             attacker="Currax", atk_village="- 04 Abracabalada", atk_x=104, atk_y=46,
             atype="attack", hidden=1, count=20, score=30,
             arrival=(base_time + timedelta(milliseconds=900)).isoformat(),
             reasons=json.dumps(["hidden_troops","enemy_has_unique_scout"])),
        # Welle 2 (5 Sekunden später — Zwischendef-Warnung!)
        dict(own_village_name="- 07 Festung", own_x=3, own_y=9,
             attacker="ZalmecAwp", atk_village="- 12 Kronos", atk_x=-35, atk_y=22,
             atype="attack", hidden=0, count=1, score=98,
             arrival=(base_time + timedelta(seconds=5)).isoformat(),
             reasons=json.dumps(["visible_troops","single_troop"])),
        dict(own_village_name="- 07 Festung", own_x=3, own_y=9,
             attacker="ZalmecAwp", atk_village="- 12 Kronos", atk_x=-35, atk_y=22,
             atype="attack", hidden=1, count=20, score=25,
             arrival=(base_time + timedelta(seconds=5, milliseconds=400)).isoformat(),
             reasons=json.dumps(["hidden_troops"])),
        # Raubzug auf ein anderes Dorf
        dict(own_village_name="- 03 Hammer", own_x=5, own_y=1,
             attacker="DarkForce", atk_village="- 01 Zentrum", atk_x=50, atk_y=-15,
             atype="raid", hidden=0, count=5, score=75,
             arrival=(base_time + timedelta(minutes=15)).isoformat(),
             reasons=json.dumps(["raid_type","few_troops"])),
    ]
    for a in attacks:
        await db.execute("""
            INSERT INTO incoming_attacks
              (guild_id, imported_by_discord_id, imported_by_name, import_time,
               own_village_name, own_village_x, own_village_y,
               attacker_player, attacker_village_name, attacker_x, attacker_y,
               attack_type, troops_hidden, troop_count, troop_details,
               arrival_time, fake_score, fake_reasons, is_dismissed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (DEMO_GUILD_ID,
              MEMBERS[0][0], MEMBERS[0][1], now,
              a["own_village_name"], a["own_x"], a["own_y"],
              a["attacker"], a["atk_village"], a["atk_x"], a["atk_y"],
              a["atype"], a["hidden"], a["count"], "{}",
              a["arrival"], a["score"], a["reasons"], 0))

    # ── Einsatzplan (EP) ───────────────────────────────────────────────────────
    print("📋 Einsatzplan …")
    ep_landing = (datetime.utcnow() + timedelta(days=2, hours=3)).isoformat()
    await db.execute("""
        INSERT OR IGNORE INTO op_plans
          (guild_id, name, status, landing_time, server_speed, target_ally, notes,
           created_by, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (DEMO_GUILD_ID, "EP gegen BLOOD", "active", ep_landing,
          1.0, "BLOOD", "Koordinierter Angriff auf Hauptdörfer. Alle bereit sein!",
          MEMBERS[0][0], now))

    plan_id = (await (await db.execute(
        "SELECT id FROM op_plans WHERE guild_id=? ORDER BY id DESC LIMIT 1",
        (DEMO_GUILD_ID,))).fetchone())[0]

    targets = [
        ("Currax",     "- 04 Abracabalada", 104,  46, 3200, 0),
        ("ZalmecAwp",  "- 12 Kronos",       -35,  22, 2800, 1),
        ("ShadowKing", "- 07 Schatten",      -8,  40, 1900, 2),
    ]
    for player, vname, x, y, pop, idx in targets:
        await db.execute("""
            INSERT INTO op_targets
              (plan_id, guild_id, player_name, village_name, x, y, population, order_idx)
            VALUES (?,?,?,?,?,?,?,?)
        """, (plan_id, DEMO_GUILD_ID, player, vname, x, y, pop, idx))

    target_ids = [row[0] async for row in await db.execute(
        "SELECT id FROM op_targets WHERE plan_id=? ORDER BY order_idx", (plan_id,))]

    waves = [
        # Target 1: 2 Fakes + 1 Echter
        (target_ids[0], MEMBERS[0][0], MEMBERS[0][2], "- 01 Hauptdorf", 0,  0, "fake",  "romans", 2, "Legionäre"),
        (target_ids[0], MEMBERS[1][0], MEMBERS[1][2], "- 03 Hammer",    5,  1, "fake",  "teutons",2, "Klubs"),
        (target_ids[0], MEMBERS[2][0], MEMBERS[2][2], "- 04 Nordwacht",-4,  6, "real",  "gauls",  3, "Phalanx"),
        # Target 2: 1 Fake + 1 Echter
        (target_ids[1], MEMBERS[3][0], MEMBERS[3][2], "- 05 Sandburg",  8, -5, "fake",  "egyptians",2,"Khopesh"),
        (target_ids[1], MEMBERS[4][0], MEMBERS[4][2], "- 06 Reiternest",-7,-2, "real",  "huns",   3, "Söldner"),
        # Target 3: 1 Echter
        (target_ids[2], MEMBERS[5][0], MEMBERS[5][2], "- 07 Festung",   3,  9, "real",  "romans", 3, "Imp."),
    ]
    for tid, attacker_id, attacker_name, orig_v, ox, oy, wtype, tribe, oidx, unit in waves:
        await db.execute("""
            INSERT INTO op_waves
              (target_id, plan_id, guild_id, attacker_discord_id, attacker_name,
               origin_village, origin_x, origin_y, wave_type, tribe,
               troop_json, arrival_time, order_idx)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tid, plan_id, DEMO_GUILD_ID, attacker_id, attacker_name,
              orig_v, ox, oy, wtype, tribe,
              json.dumps({unit: 100}), ep_landing, oidx))

    # ── Helden-Scout Einträge (scout_reports) ─────────────────────────────────
    print("🦸 Helden-Scout-Berichte …")
    scout_entries = [
        ("Currax",    "Helm der Stärke T3",  "SilverShield", yesterday),
        ("ZalmecAwp", "Schwert T2",          "FlyingArrow",  now),
        ("DarkForce", "Rüstung T1",          "SwiftFox",     now),
    ]
    for player, item, reporter, ts in scout_entries:
        await db.execute("""
            INSERT INTO scout_channels
              (channel_id, guild_id, created_at, player, additional_info)
            VALUES (?,?,?,?,?)
        """, (f"DEMO_CH_{player}", DEMO_GUILD_ID, ts, player,
              f"Ausrüstung gesichtet: {item}. Gemeldet von {reporter}."))

    await db.commit()
    print("\n✅ Demo-Daten erfolgreich eingespielt!")
    print(f"   Guild-ID : {DEMO_GUILD_ID}")
    print(f"   Guild    : {DEMO_GUILD_NAME}")
    print(f"\n   Nächste Schritte:")
    print(f"   1. Discord-Server anlegen")
    print(f"   2. Bot einladen + /setup ausführen mit Guild-ID {DEMO_GUILD_ID}")
    print(f"   3. Im Admin-Panel die Guild verknüpfen")
    print(f"   4. https://travops.online/dashboard aufrufen → Demo-Guild wählen")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="TravOps Demo-Seed")
    parser.add_argument("--wipe",     action="store_true", help="Demo-Daten vorher löschen")
    parser.add_argument("--guild-id", default=DEMO_GUILD_ID, help="Guild-ID überschreiben")
    args = parser.parse_args()

    guild_id = args.guild_id

    print(f"🌱 TravOps Demo-Seed startet …")
    print(f"   DB     : {DB_PATH}")
    print(f"   Guild  : {guild_id}\n")

    async with aiosqlite.connect(DB_PATH) as db:
        # Prüfe ob Tabellen existieren
        row = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='guild_configs'"
        )).fetchone()
        if not row:
            print("❌ Datenbank nicht initialisiert. Starte erst die Web-App einmal.")
            return

        # Override module-level constant so seed() uses the right guild_id
        import sys
        sys.modules[__name__].DEMO_GUILD_ID = guild_id
        await seed(db, wipe=args.wipe)

if __name__ == "__main__":
    asyncio.run(main())
