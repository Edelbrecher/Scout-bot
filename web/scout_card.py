"""
scout_card.py — Generate a shareable PNG card for a scout report.
Uses only Pillow (no external fonts required, falls back to default).
"""
from __future__ import annotations
import io
import json
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# Card dimensions
W, H = 680, 420
PADDING = 32
BG_COLOR      = (15, 17, 23)        # #0f1117
CARD_COLOR    = (24, 27, 38)        # #181b26
BORDER_COLOR  = (45, 50, 70)        # #2d3246
ACCENT_GOLD   = (245, 158, 11)      # #f59e0b
ACCENT_RED    = (239, 68, 68)       # #ef4444
ACCENT_GREEN  = (34, 197, 94)       # #22c55e
ACCENT_BLUE   = (147, 197, 253)     # #93c5fd
TEXT_PRIMARY  = (224, 224, 240)     # #e0e0f0
TEXT_MUTED    = (120, 128, 160)     # #7880a0
WHITE         = (255, 255, 255)


def _font(size: int, bold: bool = False):
    """Try to load a system font, fall back to default."""
    candidates_bold   = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                          "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                          "/System/Library/Fonts/Helvetica.ttc"]
    candidates_normal = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                          "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                          "/System/Library/Fonts/Helvetica.ttc"]
    candidates = candidates_bold if bold else candidates_normal
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, outline_width=1):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                            outline=outline, width=outline_width)


def _short_num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n) if n else "–"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def generate_scout_card(report: dict[str, Any]) -> bytes:
    """Return PNG bytes for a scout report share card."""
    if not _PIL_OK:
        raise RuntimeError("Pillow not installed")

    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Background card ──
    _draw_rounded_rect(draw, [PADDING//2, PADDING//2, W - PADDING//2, H - PADDING//2],
                       radius=18, fill=CARD_COLOR, outline=BORDER_COLOR, outline_width=1)

    # ── Header stripe ──
    _draw_rounded_rect(draw, [PADDING//2, PADDING//2, W - PADDING//2, PADDING//2 + 60],
                       radius=18, fill=(20, 22, 32))
    # Gold left accent bar
    draw.rectangle([PADDING//2, PADDING//2 + 6, PADDING//2 + 4, PADDING//2 + 54], fill=ACCENT_GOLD)

    # Title
    f_title  = _font(20, bold=True)
    f_sub    = _font(13)
    f_label  = _font(11)
    f_value  = _font(16, bold=True)
    f_small  = _font(10)

    target  = report.get("target_player") or "Unbekannt"
    village = report.get("target_village") or ""
    coords  = report.get("target_coords") or ""
    attacker = report.get("attacker_player") or ""
    created_at = (report.get("created_at") or "")[:16].replace("T", " ")

    title_text = f"🔭 Scout: {target}"
    draw.text((PADDING + 10, PADDING//2 + 12), title_text, font=f_title, fill=TEXT_PRIMARY)

    sub_parts = []
    if village: sub_parts.append(village)
    if coords:  sub_parts.append(f"({coords})")
    sub_text = "  ".join(sub_parts) if sub_parts else "–"
    draw.text((PADDING + 12, PADDING//2 + 36), sub_text, font=f_sub, fill=TEXT_MUTED)

    # Date top-right
    draw.text((W - PADDING - 10, PADDING//2 + 20), created_at, font=f_small, fill=TEXT_MUTED,
              anchor="rm")

    # ── Resources block ──
    resources = {}
    try:
        raw = report.get("resources_json") or "{}"
        resources = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        pass

    RES_ICONS = {"wood": ("🪵", "Holz"), "clay": ("🧱", "Lehm"),
                 "iron": ("⚙️", "Eisen"), "crop": ("🌾", "Getreide")}

    res_y = PADDING//2 + 78
    draw.text((PADDING + 8, res_y), "RESSOURCEN", font=f_label, fill=TEXT_MUTED)
    res_y += 18

    col_w = (W - PADDING * 2 - 16) // 4
    for i, (key, (icon, label)) in enumerate(RES_ICONS.items()):
        x = PADDING + 8 + i * col_w
        val = _short_num(resources.get(key, 0))
        _draw_rounded_rect(draw, [x, res_y, x + col_w - 6, res_y + 52],
                           radius=8, fill=(28, 32, 46), outline=BORDER_COLOR, outline_width=1)
        draw.text((x + col_w//2 - 3, res_y + 7), icon, font=_font(14), fill=WHITE, anchor="mm")
        draw.text((x + col_w//2 - 3, res_y + 24), val, font=f_value, fill=ACCENT_GOLD, anchor="mm")
        draw.text((x + col_w//2 - 3, res_y + 40), label, font=f_small, fill=TEXT_MUTED, anchor="mm")

    # ── Troops block ──
    troops = {}
    try:
        raw = report.get("troops_json") or "{}"
        troops = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        pass

    troop_y = res_y + 64
    draw.text((PADDING + 8, troop_y), "TRUPPEN", font=f_label, fill=TEXT_MUTED)
    troop_y += 18

    if troops:
        items = [(k, v) for k, v in troops.items() if v and int(v or 0) > 0]
        max_cols = 6
        col_wt = (W - PADDING * 2 - 16) // max_cols
        for i, (name, count) in enumerate(items[:max_cols]):
            x = PADDING + 8 + i * col_wt
            short_name = name[:8] if len(name) > 8 else name
            _draw_rounded_rect(draw, [x, troop_y, x + col_wt - 4, troop_y + 44],
                               radius=6, fill=(28, 32, 46), outline=BORDER_COLOR, outline_width=1)
            draw.text((x + col_wt//2 - 2, troop_y + 10), short_name.capitalize(),
                      font=f_small, fill=TEXT_MUTED, anchor="mm")
            draw.text((x + col_wt//2 - 2, troop_y + 28), _short_num(count),
                      font=_font(13, bold=True), fill=ACCENT_RED, anchor="mm")
        if not items:
            draw.text((PADDING + 8, troop_y + 14), "Keine Truppen gefunden", font=f_sub, fill=TEXT_MUTED)
    else:
        draw.text((PADDING + 8, troop_y + 14), "Keine Truppen-Daten", font=f_sub, fill=TEXT_MUTED)

    # ── Losses ──
    losses = {}
    try:
        raw = report.get("losses_json") or "{}"
        losses = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        pass

    loss_items = [(k, v) for k, v in losses.items() if v and int(v or 0) > 0]
    loss_y = troop_y + 56
    if loss_items:
        draw.text((PADDING + 8, loss_y), "VERLUSTE", font=f_label, fill=ACCENT_RED)
        loss_y += 16
        col_wl = (W - PADDING * 2 - 16) // min(len(loss_items), 6)
        for i, (name, count) in enumerate(loss_items[:6]):
            x = PADDING + 8 + i * col_wl
            draw.text((x, loss_y), f"{name[:10]}: {_short_num(count)}", font=f_small, fill=ACCENT_RED)

    # ── Footer ──
    footer_y = H - PADDING//2 - 22
    draw.line([(PADDING, footer_y - 8), (W - PADDING, footer_y - 8)], fill=BORDER_COLOR, width=1)

    footer_left = f"Von {attacker}" if attacker else "TravOps Scout"
    draw.text((PADDING + 4, footer_y), footer_left, font=f_small, fill=TEXT_MUTED)

    # TravOps branding right
    draw.text((W - PADDING - 4, footer_y), "travops.online", font=_font(11, bold=True),
              fill=ACCENT_GOLD, anchor="rm")

    # ── Watermark dot ──
    draw.ellipse([W - PADDING - 120, footer_y - 2, W - PADDING - 108, footer_y + 10],
                 fill=ACCENT_GOLD)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()
