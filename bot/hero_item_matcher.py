"""
hero_item_matcher.py — Bild-basierte Item-Erkennung per Perceptual Hashing.

Ablauf:
1. build_library(world_url): lädt alle Item-Icons vom Travian-CDN und
   berechnet pHashes → speichert in hero_item_library.json
2. match_slot(crop_img): vergleicht einen Slot-Crop gegen die Bibliothek
   → gibt (item_id, confidence) zurück

Travian CDN URL-Muster (T4.4 / T4.6):
  https://{world}/img/heroItems/{id}.png    ← primär
  https://{world}/img/heroItem/{id}.png     ← Alternative
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from typing import Optional

try:
    import imagehash
    from PIL import Image
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False
    print("[hero_item_matcher] imagehash/Pillow not available", flush=True)

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from hero_item_db import HERO_ITEMS, all_item_ids

LIBRARY_PATH = Path("/app/data/hero_item_library.json")
ICONS_DIR    = Path("/app/data/hero_item_icons")

# Hamming-Distanz-Schwellenwert für "Match" (0=identisch, 64=komplett verschieden)
# pHash mit hash_size=8 → 64 Bit. Schwellenwert 12 = ~80% Ähnlichkeit
MATCH_THRESHOLD = 12

# pHash-Größe
HASH_SIZE = 8

# CDN URL-Muster die wir ausprobieren
CDN_PATTERNS = [
    "{world}/img/heroItems/{id}.png",
    "{world}/img/hero/items/{id}.png",
    "{world}/img/heroItem/{id}.png",
    "{world}/img/hero_item/{id}.png",
    "{world}/img/uni/heroItem/{id}.png",
]

# ---------------------------------------------------------------------------
# In-Memory Bibliothek
# ---------------------------------------------------------------------------

_library: dict[int, str] = {}   # item_id → pHash-String
_library_loaded = False


def _phash(img: "Image.Image") -> str:
    """Berechnet den perceptual hash eines Bildes."""
    # Auf einheitliche Größe bringen (64×64) und grau
    img_resized = img.convert("RGB").resize((64, 64), Image.LANCZOS)
    h = imagehash.phash(img_resized, hash_size=HASH_SIZE)
    return str(h)


def _hamming(h1: str, h2: str) -> int:
    """Hamming-Distanz zwischen zwei pHash-Strings."""
    if not h1 or not h2 or len(h1) != len(h2):
        return 64
    # pHash-Strings sind hex — in Bits umwandeln
    try:
        i1 = imagehash.hex_to_hash(h1)
        i2 = imagehash.hex_to_hash(h2)
        return i1 - i2
    except Exception:
        return 64


def load_library() -> bool:
    """Lädt die gespeicherte Bibliothek in den Speicher."""
    global _library, _library_loaded
    if not LIBRARY_PATH.exists():
        return False
    try:
        with open(LIBRARY_PATH) as f:
            data = json.load(f)
        _library = {int(k): v for k, v in data.items()}
        _library_loaded = len(_library) > 0
        print(f"[hero_item_matcher] Bibliothek geladen: {len(_library)} Items", flush=True)
        return True
    except Exception as e:
        print(f"[hero_item_matcher] Bibliothek-Ladefehler: {e}", flush=True)
        return False


def match_slot(crop_img: "Image.Image") -> tuple[int | None, int]:
    """
    Vergleicht einen Slot-Crop gegen die Bibliothek.
    Gibt (item_id, hamming_distance) zurück.
    item_id ist None wenn kein guter Match gefunden.
    """
    if not IMAGEHASH_AVAILABLE or not _library:
        return None, 64

    try:
        slot_hash = _phash(crop_img)
    except Exception:
        return None, 64

    best_id = None
    best_dist = 64

    for item_id, ref_hash in _library.items():
        dist = _hamming(slot_hash, ref_hash)
        if dist < best_dist:
            best_dist = dist
            best_id = item_id

    if best_dist <= MATCH_THRESHOLD:
        return best_id, best_dist
    return None, best_dist


# ---------------------------------------------------------------------------
# Bibliothek aufbauen (einmaliger Download)
# ---------------------------------------------------------------------------

async def _try_download_icon(session: "aiohttp.ClientSession", world: str, item_id: int) -> bytes | None:
    """Versucht, ein Item-Icon vom CDN zu laden. Probiert mehrere URL-Muster."""
    # Sicherstellen dass world kein trailing slash hat
    world = world.rstrip("/")
    if not world.startswith("http"):
        world = "https://" + world

    for pattern in CDN_PATTERNS:
        url = pattern.replace("{world}", world).replace("{id}", str(item_id))
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("content-type", "")
                    if "image" in ct or url.endswith(".png"):
                        data = await resp.read()
                        if len(data) > 100:  # minimale Dateigröße prüfen
                            return data
        except Exception:
            continue
    return None


async def build_library(world_url: str) -> dict:
    """
    Lädt alle Item-Icons vom Travian-CDN und baut die pHash-Bibliothek auf.
    Gibt {"downloaded": N, "failed": M, "total": T} zurück.
    """
    if not IMAGEHASH_AVAILABLE:
        return {"error": "imagehash not available"}
    if not AIOHTTP_AVAILABLE:
        return {"error": "aiohttp not available"}

    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    library: dict[int, str] = {}
    downloaded = 0
    failed = 0
    ids = all_item_ids()

    print(f"[hero_item_matcher] Starte Download von {len(ids)} Items von {world_url}", flush=True)

    async with aiohttp.ClientSession() as session:
        for item_id in ids:
            # Zuerst lokalen Cache prüfen
            local_path = ICONS_DIR / f"{item_id}.png"
            if local_path.exists():
                try:
                    img = Image.open(local_path)
                    library[item_id] = _phash(img)
                    downloaded += 1
                    continue
                except Exception:
                    pass

            img_data = await _try_download_icon(session, world_url, item_id)
            if img_data:
                try:
                    img = Image.open(io.BytesIO(img_data))
                    library[item_id] = _phash(img)
                    # Lokal cachen
                    with open(local_path, "wb") as f:
                        f.write(img_data)
                    downloaded += 1
                    item_name = HERO_ITEMS.get(item_id, {}).get("en", f"ID {item_id}")
                    print(f"[hero_item_matcher] ✅ {item_id}: {item_name}", flush=True)
                except Exception as e:
                    print(f"[hero_item_matcher] ❌ Parse-Fehler {item_id}: {e}", flush=True)
                    failed += 1
            else:
                failed += 1
                print(f"[hero_item_matcher] ❌ Download fehlgeschlagen: ID {item_id}", flush=True)

    # Bibliothek speichern
    with open(LIBRARY_PATH, "w") as f:
        json.dump({str(k): v for k, v in library.items()}, f, indent=2)

    # In-Memory aktualisieren
    global _library, _library_loaded
    _library = library
    _library_loaded = len(library) > 0

    print(f"[hero_item_matcher] Fertig: {downloaded} geladen, {failed} fehlgeschlagen", flush=True)
    return {"downloaded": downloaded, "failed": failed, "total": len(ids)}


def get_library_status() -> dict:
    """Gibt den aktuellen Status der Bibliothek zurück."""
    return {
        "loaded": _library_loaded,
        "item_count": len(_library),
        "library_path": str(LIBRARY_PATH),
        "library_exists": LIBRARY_PATH.exists(),
        "imagehash_available": IMAGEHASH_AVAILABLE,
    }
