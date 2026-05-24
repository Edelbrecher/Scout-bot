"""
hero_scout.py — Scannt Helden-Screenshots aus Discord und speichert die Daten.

Discord-User postet Screenshot in einem konfigurierten Channel.
Der Bot liest per OCR: Spielername, Serverzeit, Stamm, Allianz, Heldenlevel etc.
Zusätzlich werden die 6 Ausrüstungsslots als Bild-Crops gespeichert (da Icons, kein Text).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime
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

import database

DB_PATH = database.DB_PATH
IMAGES_DIR = Path("/app/data/hero_scout_images")

# ------------------------------------------------------------------
# Slot-Positionen (relativ zur Gesamt-Screenshot-Größe ~1100×710)
# Sechs Slots auf der rechten Seite des Helden-Modals.
# Geschätzte Koordinaten (x1, y1, x2, y2):
# ------------------------------------------------------------------
SLOT_BOXES = [
    (944, 178, 1000, 234),   # Helm
    (944, 248, 1000, 304),   # Rüstung
    (944, 318, 1000, 374),   # Schuhe
    (944, 388, 1000, 444),   # Waffe
    (944, 458, 1000, 514),   # Pferd
    (944, 528, 1000, 584),   # Sonstiges
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


async def save_slot(entry_id: int, guild_id: str, idx: int, name: str, path: str, img_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO hero_scout_slots (entry_id, guild_id, slot_index, slot_name, image_path, img_hash)
            VALUES (?,?,?,?,?,?)
        """, (entry_id, guild_id, idx, name, path, img_hash))
        await db.commit()


# ------------------------------------------------------------------
# OCR + Bild-Verarbeitung
# ------------------------------------------------------------------

def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """
    Bereitet ein Screenshot-Bild für Tesseract vor.
    Travian: heller Text auf dunklem Hintergrund → invertieren + kontrastverstärken.
    """
    from PIL import ImageEnhance, ImageFilter, ImageOps

    # 2× hochskalieren (Tesseract mag größere Bilder)
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)

    # Graustufen
    gray = img.convert("L")

    # Invertieren: heller Text auf dunklem BG → dunkler Text auf hellem BG
    inverted = ImageOps.invert(gray)

    # Kontrast stark erhöhen
    enhancer = ImageEnhance.Contrast(inverted)
    contrasted = enhancer.enhance(2.5)

    return contrasted


def _run_ocr(img: "Image.Image") -> str:
    """Führt Tesseract mit optimalen Einstellungen für Travian-Screenshots aus."""
    processed = _preprocess_for_ocr(img)
    # PSM 4: einzelne Textspalte (passt gut zum Modal-Layout)
    # PSM 11: sparse text (wenn 4 nichts liefert, als Fallback)
    config = "--psm 4 -l eng"
    text = pytesseract.image_to_string(processed, config=config)
    if len(text.strip()) < 20:
        # Fallback: sparse text mode
        text = pytesseract.image_to_string(processed, config="--psm 11 -l eng")
    return text


def _parse_ocr_text(text: str) -> dict:
    """Extrahiert Felder aus dem OCR-Text des Helden-Profil-Modals."""
    result: dict = {}

    # Debug-Log (wird im Container sichtbar)
    print(f"[hero_scout] OCR raw ({len(text)} chars):\n{text[:600]}", flush=True)

    # Serverzeit: "Server time: 13:24:33 (UTC +01:00)"
    m = re.search(r"Server\s+time[:\s]+(\d{1,2}:\d{2}:\d{2}[^\n]*)", text, re.IGNORECASE)
    if m:
        result["server_time"] = m.group(1).strip()

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Spielername: steht im Modal-Header, vor "Details"
    # Suche nach "Details" oder "Tribe" und nehme Zeilen davor
    SKIP_WORDS = {"details", "tribe", "stamm", "alliance", "allianz", "villages",
                  "dörfer", "gender", "geschlecht", "days", "location", "ranks",
                  "population", "attacker", "defender", "hero", "server", "the"}
    for i, line in enumerate(lines):
        if line.lower() in ("tribe", "stamm", "details"):
            for j in range(i - 1, max(i - 6, -1), -1):
                c = lines[j].strip()
                if (c and len(c) >= 2
                        and c.lower() not in SKIP_WORDS
                        and not re.match(r"^\d+$", c)
                        and not re.match(r"server\s+time", c, re.IGNORECASE)):
                    result["player_name"] = c
                    break
            break

    # Stamm: Zeile nach "Tribe" (direkt oder mit Leerzeile)
    m = re.search(r"Tribe\s*[\n:]\s*([A-Za-z]+)", text, re.IGNORECASE)
    if m:
        result["tribe"] = m.group(1).strip()

    # Allianz: kann Buchstaben/Zahlen sein
    m = re.search(r"Alliance\s*[\n:]\s*([A-Za-z0-9_\-]+)", text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        if val.lower() not in ("not", "none", "—"):
            result["alliance"] = val

    # Dörfer — Zahl nach "Villages"
    m = re.search(r"Villages\s*[\n:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        result["villages"] = int(m.group(1))

    # Heldenlevel — "Hero level\n68" oder "Hero level: 68"
    m = re.search(r"Hero\s+level\s*[\n:]\s*(\d+)", text, re.IGNORECASE)
    if m:
        result["hero_level"] = int(m.group(1))

    # Hero XP — "119862 Experience" oder "119,862 Experience"
    m = re.search(r"([\d,\.]+)\s*Experience", text, re.IGNORECASE)
    if m:
        try:
            result["hero_xp"] = int(m.group(1).replace(",", "").replace(".", ""))
        except ValueError:
            pass

    # Attacker Rang — "Attacker  21  29655 Points"
    m = re.search(r"Attacker\s+(\d+)\s+[\d,]+\s*Points?", text, re.IGNORECASE)
    if m:
        result["attacker_rank"] = int(m.group(1))

    # Defender Rang
    m = re.search(r"Defender\s+(\d+)\s+[\d,]+\s*Points?", text, re.IGNORECASE)
    if m:
        result["defender_rank"] = int(m.group(1))

    return result


def _crop_slots(img: "Image.Image") -> list[tuple[str, str]]:
    """
    Schneidet die 6 Ausrüstungsslots aus dem Screenshot.
    Gibt Liste von (image_path_placeholder, hash) zurück.
    Bilder werden noch nicht gespeichert — erst nach Entry-ID bekannt.
    """
    slots = []
    w, h = img.size
    scale_x = w / 1101
    scale_y = h / 709

    for (x1, y1, x2, y2) in SLOT_BOXES:
        # Skaliere auf tatsächliche Bildgröße
        bx1 = int(x1 * scale_x)
        by1 = int(y1 * scale_y)
        bx2 = int(x2 * scale_x)
        by2 = int(y2 * scale_y)

        # Bounds-Check
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

        # Bilder verarbeiten
        processed = 0
        for attachment in message.attachments:
            if not any(attachment.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                continue
            await self._process_attachment(message, attachment, guild_id)
            processed += 1

        if processed == 0:
            return

    async def _process_attachment(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
        guild_id: str,
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

        slot_crops = []
        slot_hashes = []

        if OCR_AVAILABLE:
            try:
                img = Image.open(io.BytesIO(img_bytes))

                # OCR mit Preprocessing
                ocr_text = _run_ocr(img)
                parsed = _parse_ocr_text(ocr_text)
                data.update(parsed)

                # Slot-Crops
                raw_slots = _crop_slots(img)
                for crop_img, img_hash in raw_slots:
                    slot_crops.append(crop_img)
                    slot_hashes.append(img_hash)

                data["slots_hash"] = _slots_combined_hash(slot_hashes)

            except Exception as e:
                print(f"[hero_scout] OCR error: {e}")
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

        for idx, (crop_img, img_hash) in enumerate(zip(slot_crops, slot_hashes)):
            if crop_img is None:
                continue
            slot_name = SLOT_NAMES[idx] if idx < len(SLOT_NAMES) else f"slot{idx}"
            path = guild_dir / f"{slot_name}.png"
            try:
                crop_img.save(str(path))
                await save_slot(entry_id, guild_id, idx, slot_name, str(path), img_hash)
            except Exception as e:
                print(f"[hero_scout] Slot save error: {e}")

        # ── Bestätigung & Warnung im Channel ──────────────────────────
        player     = data.get("player_name", "?")
        tribe      = data.get("tribe", "?")
        hero_lvl   = data.get("hero_level", "?")
        alliance   = data.get("alliance", "?")
        server_time = data.get("server_time", "?")

        # Normales Bestätigungs-Embed (immer)
        confirm_embed = discord.Embed(
            title=f"🗡️ Helden-Scout gespeichert: {player}",
            color=discord.Color.blurple(),
        )
        confirm_embed.add_field(name="Stamm", value=tribe, inline=True)
        confirm_embed.add_field(name="Allianz", value=alliance, inline=True)
        confirm_embed.add_field(name="Heldenlvl", value=str(hero_lvl), inline=True)
        confirm_embed.add_field(name="Serverzeit", value=server_time, inline=False)
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
