"""
hero_scout.py — Scannt Helden-Screenshots aus Discord und speichert die Daten.

Discord-User postet Screenshot in einem konfigurierten Channel.
Der Bot liest per OCR: Spielername, Serverzeit, Stamm, Allianz, Heldenlevel etc.
Zusätzlich werden die 6 Ausrüstungsslots als Bild-Crops gespeichert (da Icons, kein Text).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
from datetime import datetime
from functools import partial
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[hero_scout] pytesseract/Pillow not available — OCR disabled")

try:
    import hero_item_matcher
    import hero_item_db
    MATCHER_AVAILABLE = True
except ImportError:
    MATCHER_AVAILABLE = False
    print("[hero_scout] hero_item_matcher not available")

import database

DB_PATH = database.DB_PATH
IMAGES_DIR = Path("/app/data/hero_scout_images")

# ------------------------------------------------------------------
# Slot-Positionen als RELATIVE Anteile des Gesamt-Screenshots (0.0–1.0)
# Berechnet aus typischem 1101×709-Vollbild:
#   Modal: x=435..1010, y=100..700 → Breite=575, Höhe=600
#   Slots: rechte Seite des Modals, 6 Stück vertikal
# ------------------------------------------------------------------
SLOT_REL = [
    # (x1_rel, y1_rel, x2_rel, y2_rel) — Anteile vom Gesamt-Screenshot
    (0.857, 0.251, 0.908, 0.330),   # Helm
    (0.857, 0.350, 0.908, 0.429),   # Rüstung
    (0.857, 0.449, 0.908, 0.528),   # Schuhe
    (0.857, 0.548, 0.908, 0.627),   # Waffe
    (0.857, 0.647, 0.908, 0.726),   # Pferd
    (0.857, 0.746, 0.908, 0.825),   # Sonstiges
]

SLOT_NAMES = ["helm", "armor", "boots", "weapon", "mount", "misc"]

# XP-Schwellenwert ab dem eine Warnung ausgelöst wird
XP_JUMP_THRESHOLD = 5000


# ------------------------------------------------------------------
# DB-Hilfsfunktionen
# ------------------------------------------------------------------

async def init_hero_scout_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hero_scout_channels (
                guild_id    TEXT PRIMARY KEY,
                channel_id  TEXT NOT NULL,
                channel_name TEXT DEFAULT '',
                set_by      TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hero_scout_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        TEXT NOT NULL,
                player_name     TEXT NOT NULL,
                tribe           TEXT DEFAULT '',
                alliance        TEXT DEFAULT '',
                villages        INTEGER DEFAULT 0,
                hero_level      INTEGER DEFAULT 0,
                hero_xp         INTEGER DEFAULT 0,
                attacker_rank   INTEGER DEFAULT 0,
                defender_rank   INTEGER DEFAULT 0,
                server_time     TEXT DEFAULT '',
                reporter_id     TEXT DEFAULT '',
                reporter_name   TEXT DEFAULT '',
                discord_url     TEXT DEFAULT '',
                slots_hash      TEXT DEFAULT '',
                changed         INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hero_scout_slots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL,
                guild_id    TEXT NOT NULL,
                slot_index  INTEGER NOT NULL,
                slot_name   TEXT NOT NULL,
                image_path  TEXT DEFAULT '',
                img_hash    TEXT DEFAULT ''
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hse_guild ON hero_scout_entries(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hse_player ON hero_scout_entries(player_name)")
        await db.commit()


async def get_hero_scout_channel(guild_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_id FROM hero_scout_channels WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_hero_scout_channel(guild_id: str, channel_id: str, channel_name: str, set_by: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO hero_scout_channels (guild_id, channel_id, channel_name, set_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                channel_name=excluded.channel_name,
                set_by=excluded.set_by
        """, (guild_id, channel_id, channel_name, set_by, now))
        await db.commit()


async def get_last_entry_for_player(guild_id: str, player_name: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM hero_scout_entries
            WHERE guild_id=? AND lower(player_name)=lower(?)
            ORDER BY created_at DESC LIMIT 1
        """, (guild_id, player_name)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_entry(guild_id: str, data: dict) -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO hero_scout_entries
                (guild_id, player_name, tribe, alliance, villages, hero_level, hero_xp,
                 attacker_rank, defender_rank, server_time, reporter_id, reporter_name,
                 discord_url, slots_hash, changed, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            guild_id,
            data.get("player_name", ""),
            data.get("tribe", ""),
            data.get("alliance", ""),
            data.get("villages", 0),
            data.get("hero_level", 0),
            data.get("hero_xp", 0),
            data.get("attacker_rank", 0),
            data.get("defender_rank", 0),
            data.get("server_time", ""),
            data.get("reporter_id", ""),
            data.get("reporter_name", ""),
            data.get("discord_url", ""),
            data.get("slots_hash", ""),
            1 if data.get("changed") else 0,
            now,
        ))
        await db.commit()
        return cur.lastrowid


async def save_slot(entry_id: int, guild_id: str, idx: int, name: str, path: str,
                    img_hash: str, item_name: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        # Migration: item_name Spalte hinzufügen falls nicht vorhanden
        try:
            await db.execute("ALTER TABLE hero_scout_slots ADD COLUMN item_name TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        await db.execute("""
            INSERT INTO hero_scout_slots (entry_id, guild_id, slot_index, slot_name, image_path, img_hash, item_name)
            VALUES (?,?,?,?,?,?,?)
        """, (entry_id, guild_id, idx, name, path, img_hash, item_name))
        await db.commit()


# ------------------------------------------------------------------
# OCR + Bild-Verarbeitung
# ------------------------------------------------------------------

def _prep_light_on_dark(img: "Image.Image", scale: int = 2) -> "Image.Image":
    """Heller Text auf dunklem Hintergrund → invertieren + Kontrast."""
    from PIL import ImageEnhance, ImageOps
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    gray = img.convert("L")
    inv = ImageOps.invert(gray)
    return ImageEnhance.Contrast(inv).enhance(2.5)


def _ocr_serverzeit(img: "Image.Image") -> str:
    """
    Liest die Serverzeit aus dem Top-Left-Bereich (absolut ~erste 5% der Höhe).
    Text ist hell auf dunklem Hintergrund, relativ einfache Schrift.
    """
    w, h = img.size
    # Serverzeit-Banner: immer oben-links, ca. 28px hoch, ~270px breit
    # Proportional: erste 5% der Höhe, erste 28% der Breite
    y2 = max(30, int(h * 0.06))
    x2 = max(300, int(w * 0.30))
    crop = img.crop((0, 0, x2, y2))
    processed = _prep_light_on_dark(crop, scale=4)
    text = pytesseract.image_to_string(processed, config="--psm 7 -l eng+deu").strip()
    print(f"[hero_scout] Serverzeit OCR: '{text}'", flush=True)
    return text


def _run_ocr(img: "Image.Image") -> str:
    """OCR auf dem Modal-Bereich (rechte Hälfte, ohne Top-Banner)."""
    w, h = img.size
    # Modal ist rechts — linke Sidebar (ca. 31%) und Top-Banner (ca. 6%) abschneiden
    modal_crop = img.crop((int(w * 0.31), int(h * 0.06), w, h))
    processed = _prep_light_on_dark(modal_crop, scale=2)
    # PSM 4 = einzelne Textspalte, passt zum Modal
    text = pytesseract.image_to_string(processed, config="--psm 4 -l eng+deu")
    if len(text.strip()) < 20:
        text = pytesseract.image_to_string(processed, config="--psm 11 -l eng+deu")
    return text


KNOWN_TRIBES = {"romans", "teutons", "gauls", "egyptians", "huns", "spartans",
                "römer", "romer", "germanen", "gallier", "ägypter", "agypter",
                "hunnen", "spartaner", "roman", "teuton", "gaul"}


def _parse_ocr_text(text: str) -> dict:
    """Extrahiert Felder aus dem OCR-Text — unterstützt EN und DE."""
    result: dict = {}

    print(f"[hero_scout] OCR raw ({len(text)} chars):\n{text[:700]}", flush=True)

    # ── Serverzeit ───────────────────────────────────────────────────────
    # DE: "Serverzeit: 1:00:58 (UTC +01:00)"  EN: "Server time: 13:24:58 (UTC +01:00)"
    m = re.search(r"Server(?:zeit|[-\s]*time)\s*:?\s*(\d{1,2}:\d{2}:\d{2}[^\n]*)",
                  text, re.IGNORECASE)
    if m:
        result["server_time"] = m.group(1).strip()
    else:
        # Fallback: einfach einen Zeitstempel im Format HH:MM:SS suchen
        m = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\s*\(UTC", text)
        if m:
            result["server_time"] = m.group(1).strip()

    # ── Stamm  (EN: Tribe / DE: Volk / Stamm) ───────────────────────────
    TRIBE_PATTERN = (r"(?:Tribe|Volk|Stamm)\s*:?\s*"
                     r"(Romans?|Teutons?|Gauls?|Egyptians?|Huns?|Spartans?|"
                     r"R[oö]mer|Germanen|Gallier|[AÄ]gypter|Hunnen|Spartaner)")
    m = re.search(TRIBE_PATTERN, text, re.IGNORECASE)
    if m:
        result["tribe"] = m.group(1).strip()
    else:
        for word in text.split():
            if word.lower().rstrip(",.:-") in KNOWN_TRIBES:
                result["tribe"] = word.rstrip(",.:-")
                break

    # ── Allianz  (EN: Alliance / DE: Allianz) ────────────────────────────
    m = re.search(r"(?:Alliance|Allianz)\s*:?\s*([A-Za-z0-9_\-]{1,20})",
                  text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        BAD = {"not", "none", "—", "lial", "lia", "li", "nicht", "angegeben"}
        if val.lower() not in BAD:
            result["alliance"] = val

    # ── Dörfer  (EN: Villages / DE: Dörfer / Dorfer) ─────────────────────
    m = re.search(r"(?:Villages|D[oö]rfer)\s*:?\s*(\d+)", text, re.IGNORECASE)
    if m:
        result["villages"] = int(m.group(1))

    # ── Heldenlevel  (EN: Hero level / DE: Heldenstufe) ──────────────────
    m = re.search(r"(?:Hero\s+level|Heldenstufe)\s*:?\s*(\d+)", text, re.IGNORECASE)
    if m:
        result["hero_level"] = int(m.group(1))

    # ── Hero XP  (EN: Experience / DE: Erfahrung / OCR-Fehler: Erfahning etc.) ──
    # Zahl kann direkt daneben stehen: "383390.Erfahrung" oder "383390 Erfahrung"
    m = re.search(r"(\d[\d,\.]*)\s*[.\s]?\s*Erfahr\w*", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d[\d,\.]*)\s*[.\s]?\s*Experi\w*", text, re.IGNORECASE)
    if m:
        try:
            raw = re.sub(r"[,\.](?=\d{3})", "", m.group(1))  # Tausender-Trenner weg
            raw = raw.replace(",", "").replace(".", "")
            result["hero_xp"] = int(raw)
        except ValueError:
            pass

    # ── Attacker Rang  (EN: Attacker / DE: Angreifer) ────────────────────
    m = re.search(r"(?:Attacker|Angreifer)\s+(\d+)\s+[\d,\.]+\s*(?:Points?|Punkte?)",
                  text, re.IGNORECASE)
    if m:
        result["attacker_rank"] = int(m.group(1))

    # ── Defender Rang  (EN: Defender / DE: Verteidiger) ──────────────────
    m = re.search(r"(?:Defender|Verteidiger)\s+(\d+)\s+[\d,\.]+\s*(?:Points?|Punkte?)",
                  text, re.IGNORECASE)
    if m:
        result["defender_rank"] = int(m.group(1))

    return result


def _ocr_modal_header(img: "Image.Image") -> str:
    """
    Croppt den Modal-Titel (Spielername) und liest ihn via Farb-Extraktion.
    Der Name ist goldener/oranger Text auf dunklem Ornament-Hintergrund.
    Farb-Maske: Pixel mit hohem R-Kanal (> 160) und niedrigem B-Kanal (< 120)
    → golden/orange Text isolieren, Rest weiß.
    """
    w, h = img.size
    sx = w / 1101
    sy = h / 709

    # Etwas großzügiger Bereich um den Header
    x1 = int(450 * sx); y1 = int(120 * sy)
    x2 = int(965 * sx); y2 = int(175 * sy)
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w, x2); y2 = min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return ""

    crop = img.crop((x1, y1, x2, y2))
    # 4× skalieren
    cw, ch = crop.size
    crop = crop.resize((cw * 4, ch * 4), Image.LANCZOS)

    # Farb-Extraktion: goldene/helle Pixel isolieren
    # R > 160, G > 110, B < 130  → golden/orange Text
    # R > 200, G > 200, B > 200  → weißer Text (Fallback)
    rgb = crop.convert("RGB")
    pixels = rgb.load()
    cw2, ch2 = crop.size
    from PIL import Image as _Img
    result = _Img.new("L", (cw2, ch2), 255)  # weißer Hintergrund
    rpx = result.load()
    for y in range(ch2):
        for x in range(cw2):
            r, g, b = pixels[x, y]
            is_golden = r > 160 and g > 110 and b < 130
            is_white  = r > 200 and g > 200 and b > 200
            if is_golden or is_white:
                rpx[x, y] = 0  # schwarzer Text

    # Zweiter Versuch: einfach nur grau ohne Invertierung
    gray_crop = crop.convert("L")

    best = ""
    for mode_img, label in [(result, "color"), (gray_crop, "gray")]:
        raw = pytesseract.image_to_string(mode_img, config="--psm 7 -l eng").strip()
        clean = re.sub(r"[^A-Za-z0-9\-_çÇğĞıİöÖşŞüÜáéíóúäåæøñ]", "", raw).strip()
        print(f"[hero_scout] Header OCR ({label}): '{raw}' → '{clean}'", flush=True)
        if len(clean) >= 2 and not best:
            best = clean

    return best


def _crop_slots(img: "Image.Image") -> list[tuple]:
    """
    Schneidet die 6 Ausrüstungsslots aus dem Screenshot.
    Koordinaten sind RELATIV zur Bildgröße → funktioniert für jede Auflösung.
    """
    slots = []
    w, h = img.size

    for (rx1, ry1, rx2, ry2) in SLOT_REL:
        bx1 = int(rx1 * w); by1 = int(ry1 * h)
        bx2 = int(rx2 * w); by2 = int(ry2 * h)
        bx1 = max(0, bx1); by1 = max(0, by1)
        bx2 = min(w, bx2); by2 = min(h, by2)

        if bx2 <= bx1 or by2 <= by1:
            slots.append((None, ""))
            continue

        crop = img.crop((bx1, by1, bx2, by2))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        img_hash = hashlib.md5(buf.getvalue()).hexdigest()
        slots.append((crop, img_hash))

    return slots


def _extract_names_from_message(content: str) -> list[str]:
    """
    Extrahiert alle Spielernamen aus dem Nachrichtentext.
    Unterstützte Formate:
      "ZalmecAwp"                      → ["ZalmecAwp"]
      "Scout: ZalmecAwp"               → ["ZalmecAwp"]
      "ZalmecAwp Currax"               → ["ZalmecAwp", "Currax"]  (2 Bilder)
      "ZalmecAwp (Teutons)"            → ["ZalmecAwp"]
    Gibt leere Liste zurück wenn nichts Sinnvolles gefunden.
    """
    if not content or not content.strip():
        return []
    text = content.strip()
    # Präfix entfernen: "Scout:", "Held:", "Hero:", "Name:" etc.
    text = re.sub(r"^(scout|held|hero|name|spieler)[:\s]+", "", text, flags=re.IGNORECASE)
    # Klammern-Inhalte entfernen: "(Teutons)" etc.
    text = re.sub(r"\s*\([^)]*\)", "", text)
    # Alle Token extrahieren
    tokens = text.split()
    names = []
    for word in tokens:
        word = word.strip(",.:-")
        if 2 <= len(word) <= 30 and re.match(r"^[A-Za-z0-9\-_çÇğĞıİöÖşŞüÜáéíóúäåæøñ]+$", word):
            names.append(word)
    return names


def _extract_name_from_message(content: str) -> str:
    """Rückwärtskompatibel: ersten Namen aus Nachricht zurückgeben."""
    names = _extract_names_from_message(content)
    return names[0] if names else ""


def _slots_combined_hash(slot_hashes: list[str]) -> str:
    combined = "".join(slot_hashes)
    return hashlib.md5(combined.encode()).hexdigest()


# ------------------------------------------------------------------
# Cog
# ------------------------------------------------------------------

class HeroScoutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await init_hero_scout_tables()
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        if MATCHER_AVAILABLE:
            hero_item_matcher.load_library()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not message.attachments:
            return

        guild_id = str(message.guild.id)
        configured_channel = await get_hero_scout_channel(guild_id)
        if not configured_channel:
            return
        if str(message.channel.id) != configured_channel:
            return

        # Bilder sammeln
        image_attachments = [
            a for a in message.attachments
            if any(a.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp"))
        ]
        if not image_attachments:
            return

        # Namen aus Nachrichtentext extrahieren — Unterstützung für mehrere Namen
        # "ZalmecAwp Currax" → Bild 0 → ZalmecAwp, Bild 1 → Currax
        names_in_text = _extract_names_from_message(message.content)

        # Bilder NACHEINANDER (sequenziell) verarbeiten damit Tesseract nicht überlastet wird
        for i, attachment in enumerate(image_attachments):
            # Namen zuordnen: wenn genug Namen da sind, nimm Namen[i], sonst Namen[0]
            if len(names_in_text) > i:
                name_hint = names_in_text[i]
            elif len(names_in_text) == 1:
                name_hint = names_in_text[0]
            else:
                name_hint = ""
            await self._process_attachment(message, attachment, guild_id, name_hint=name_hint)

    async def _process_attachment(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
        guild_id: str,
        name_hint: str = "",
    ):
        reporter_id = str(message.author.id)
        reporter_name = message.author.display_name

        # Bild herunterladen
        img_bytes = await attachment.read()

        data: dict = {
            "reporter_id": reporter_id,
            "reporter_name": reporter_name,
            "discord_url": attachment.url,
        }
        if name_hint:
            data["player_name"] = name_hint

        slot_crops = []
        slot_hashes = []

        if OCR_AVAILABLE:
            try:
                loop = asyncio.get_event_loop()
                img = Image.open(io.BytesIO(img_bytes))

                # 1. Serverzeit separat aus Top-Left-Bereich (in Thread-Pool)
                szeit_text = await loop.run_in_executor(None, partial(_ocr_serverzeit, img))
                szeit_parsed = _parse_ocr_text(szeit_text)
                if szeit_parsed.get("server_time"):
                    data["server_time"] = szeit_parsed["server_time"]

                # 2. Modal-OCR für alle anderen Felder (in Thread-Pool)
                ocr_text = await loop.run_in_executor(None, partial(_run_ocr, img))
                parsed = _parse_ocr_text(ocr_text)
                # server_time nur überschreiben wenn noch nicht gesetzt
                if data.get("server_time"):
                    parsed.pop("server_time", None)
                data.update(parsed)

                # 3. Spielername aus Modal-Header (nur wenn noch keiner da)
                if not data.get("player_name"):
                    header_name = await loop.run_in_executor(None, partial(_ocr_modal_header, img))
                    if header_name and len(header_name) >= 2:
                        data["player_name"] = header_name

                # Slot-Crops
                raw_slots = _crop_slots(img)
                for crop_img, img_hash in raw_slots:
                    slot_crops.append(crop_img)
                    slot_hashes.append(img_hash)

                data["slots_hash"] = _slots_combined_hash(slot_hashes)

            except Exception as e:
                import traceback
                print(f"[hero_scout] OCR error: {e}\n{traceback.format_exc()}")
        else:
            # Kein OCR — nur URL speichern
            data["player_name"] = f"unbekannt_{datetime.utcnow().strftime('%H%M%S')}"

        if not data.get("player_name"):
            data["player_name"] = f"unbekannt_{datetime.utcnow().strftime('%H%M%S')}"

        # Vorherigen Eintrag prüfen → Änderungen erkennen
        prev = await get_last_entry_for_player(guild_id, data["player_name"])
        equipment_changed = False
        xp_jumped = False
        xp_delta = 0

        if prev:
            if prev.get("slots_hash") and data.get("slots_hash"):
                equipment_changed = prev["slots_hash"] != data["slots_hash"]
            prev_xp = prev.get("hero_xp") or 0
            cur_xp  = data.get("hero_xp") or 0
            if prev_xp and cur_xp and cur_xp > prev_xp:
                xp_delta = cur_xp - prev_xp
                xp_jumped = xp_delta >= XP_JUMP_THRESHOLD

        data["changed"] = equipment_changed or xp_jumped

        # Eintrag speichern
        entry_id = await save_entry(guild_id, data)

        # Slot-Bilder speichern
        guild_dir = IMAGES_DIR / guild_id / str(entry_id)
        guild_dir.mkdir(parents=True, exist_ok=True)

        identified_items: dict[str, str] = {}   # slot_name → item display name

        for idx, (crop_img, img_hash) in enumerate(zip(slot_crops, slot_hashes)):
            if crop_img is None:
                continue
            slot_name = SLOT_NAMES[idx] if idx < len(SLOT_NAMES) else f"slot{idx}"
            path = guild_dir / f"{slot_name}.png"
            try:
                crop_img.save(str(path))

                # Item-Erkennung per pHash
                item_name_str = ""
                if MATCHER_AVAILABLE:
                    item_id, dist = hero_item_matcher.match_slot(crop_img)
                    if item_id is not None:
                        item_info = hero_item_db.get_item_by_id(item_id)
                        if item_info:
                            item_name_str = item_info.get("name", item_info.get("en", ""))
                            identified_items[slot_name] = item_name_str
                            print(f"[hero_scout] Slot {slot_name} → {item_name_str} (dist={dist})", flush=True)

                await save_slot(entry_id, guild_id, idx, slot_name, str(path), img_hash,
                                item_name=item_name_str)
            except Exception as e:
                print(f"[hero_scout] Slot save error: {e}")

        # ── Bestätigung & Warnung im Channel ──────────────────────────
        player     = data.get("player_name", "?")
        tribe      = data.get("tribe", "?")
        hero_lvl   = data.get("hero_level", "?")
        alliance   = data.get("alliance", "?")
        server_time = data.get("server_time", "?")

        # Normales Bestätigungs-Embed (immer)
        name_missing = player.startswith("unbekannt_")
        confirm_embed = discord.Embed(
            title=f"🗡️ Helden-Scout gespeichert: {player}",
            color=discord.Color.yellow() if name_missing else discord.Color.blurple(),
        )
        if name_missing:
            confirm_embed.description = (
                "💡 **Spielername nicht erkannt** — tippe den Spielernamen einfach als Nachricht "
                "zusammen mit dem Screenshot, z.B.:\n```Currax```"
            )
        confirm_embed.add_field(name="Stamm", value=tribe, inline=True)
        confirm_embed.add_field(name="Allianz", value=alliance, inline=True)
        confirm_embed.add_field(name="Heldenlvl", value=str(hero_lvl), inline=True)
        confirm_embed.add_field(name="Serverzeit", value=server_time, inline=False)

        # Item-Erkennung via pHash deaktiviert (Library-Aufbau nicht zuverlässig)
        # Items werden manuell im Dashboard zugewiesen

        confirm_embed.set_footer(text=f"Gemeldet von {reporter_name}")

        try:
            await message.reply(embed=confirm_embed, mention_author=False)
        except Exception as e:
            print(f"[hero_scout] Reply error: {e}")

        # Warnungs-Embed wenn Änderung erkannt
        if equipment_changed or xp_jumped:
            warn_lines = []
            if equipment_changed:
                warn_lines.append("⚔️ **Ausrüstung wurde geändert** — mindestens ein Item-Slot ist anders als beim letzten Screenshot.")
            if xp_jumped:
                warn_lines.append(
                    f"📈 **Starker XP-Zuwachs** — +{xp_delta:,} Erfahrung seit dem letzten Screenshot "
                    f"(Schwelle: {XP_JUMP_THRESHOLD:,} XP)."
                )

            warn_embed = discord.Embed(
                title=f"🚨 Helden-Warnung: {player}",
                description="\n\n".join(warn_lines),
                color=discord.Color.red(),
            )
            warn_embed.add_field(name="Stamm", value=tribe, inline=True)
            warn_embed.add_field(name="Allianz", value=alliance, inline=True)
            warn_embed.add_field(name="Heldenlvl", value=str(hero_lvl), inline=True)
            if xp_jumped and data.get("hero_xp"):
                warn_embed.add_field(
                    name="Aktuelle XP",
                    value=f"{data['hero_xp']:,}",
                    inline=True,
                )
            warn_embed.set_footer(text=f"Screenshot von {reporter_name} · {server_time}")

            try:
                await message.channel.send(embed=warn_embed)
            except Exception as e:
                print(f"[hero_scout] Warning send error: {e}")

    # ------------------------------------------------------------------
    # Slash Command: /hero-scout setup
    # ------------------------------------------------------------------

    @commands.command(name="hero-scout-build-library")
    @commands.has_permissions(manage_guild=True)
    async def build_library_cmd(self, ctx: commands.Context):
        """Baut die Item-Icon-Bibliothek für das aktuelle Travian-World auf."""
        if not MATCHER_AVAILABLE:
            await ctx.send("❌ `imagehash` nicht verfügbar.")
            return

        # Travian-World-URL aus DB holen
        world_url = None
        try:
            import aiosqlite as _sq
            async with _sq.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT tw_world FROM guild_configs WHERE guild_id=?",
                    (str(ctx.guild.id),)
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        world_url = row[0]
        except Exception:
            pass

        if not world_url:
            await ctx.send("❌ Keine Travian-World-URL konfiguriert. Bitte erst im Dashboard eintragen.")
            return

        msg = await ctx.send(f"⏳ Lade Item-Icons von `{world_url}` …")
        result = await hero_item_matcher.build_library(world_url)

        if "error" in result:
            await msg.edit(content=f"❌ Fehler: {result['error']}")
        else:
            await msg.edit(content=(
                f"✅ Item-Bibliothek aufgebaut!\n"
                f"📦 {result['downloaded']}/{result['total']} Icons geladen · "
                f"❌ {result['failed']} fehlgeschlagen"
            ))

    @app_commands.command(
        name="hero-scout-setup",
        description="Legt den Channel fest, in dem Helden-Screenshots gepostet werden."
    )
    @app_commands.describe(channel="Der Channel für Helden-Screenshots")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hero_scout_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        guild_id = str(interaction.guild_id)
        await set_hero_scout_channel(
            guild_id,
            str(channel.id),
            channel.name,
            str(interaction.user.id),
        )
        await interaction.response.send_message(
            f"✅ Helden-Scout Channel gesetzt: {channel.mention}\n"
            f"Postet jetzt Helden-Screenshots dort — der Bot liest sie automatisch aus.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HeroScoutCog(bot))
