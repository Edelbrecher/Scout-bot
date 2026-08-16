"""TravOps Solo — eine Welt, kein Konto, kein Abo.

Die alte Anwendung war für viele Allianzen gebaut: Discord-Anmeldung,
Serverauswahl, Abomodell, Verwaltungsoberfläche für Kunden. Davon bleibt hier
nichts. Es gibt eine Welt, Zugang über Join-Links, und Seiten, die genau die
Daten dieser Welt zeigen.

Die Datenbank ist dieselbe wie bisher. Die Daten aller anderen Server bleiben
unangetastet darin liegen — sie werden nur nicht mehr angezeigt. Deshalb steht
in jeder Abfrage die Weltkennung, und zwar als Parameter, nicht als
zusammengebauter Text.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import access
from config import DB_PATH, WORLD_ID, WORLD_NAME, COOKIE, COOKIE_MAX_AGE

app = FastAPI(title="TravOps Solo", docs_url=None, redoc_url=None)

# Pfade am Ort dieser Datei festmachen, nicht am Arbeitsverzeichnis: sonst
# findet die Anwendung ihre Vorlagen nur, wenn sie zufällig aus dem richtigen
# Ordner gestartet wurde.
HIER = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
templates = Jinja2Templates(directory=str(HIER / "templates"))


@app.on_event("startup")
def _start():
    access.init_db()


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def welt_name() -> str:
    if WORLD_NAME:
        return WORLD_NAME
    with db() as c:
        r = c.execute("SELECT guild_name FROM guild_configs WHERE guild_id=?", (WORLD_ID,)).fetchone()
    return (r["guild_name"] if r else "") or "Meine Welt"


# ── Zugang ────────────────────────────────────────────────────────
# Kein Login, kein Konto: die Prüfung ist eine Zeile, und sie steht am Anfang
# jeder Seite. Wer keinen gültigen Link hat, sieht die Einladungsseite.

def wache(request: Request):
    return access.aus_request(request)


def abweisen():
    return RedirectResponse("/kein-zugang", status_code=303)


@app.get("/join/{token}")
def join(token: str):
    """Den Link einlösen: Geheimnis ins Cookie, dann zur Übersicht."""
    if not access.pruefe(token):
        return RedirectResponse("/kein-zugang", status_code=303)
    antwort = RedirectResponse("/", status_code=303)
    antwort.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE,
                       httponly=True, samesite="lax")
    return antwort


@app.get("/kein-zugang", response_class=HTMLResponse)
def kein_zugang(request: Request):
    return templates.TemplateResponse(request, "kein_zugang.html", {})


@app.get("/abmelden")
def abmelden():
    """Nur dieses Gerät vergisst den Link — der Link selbst bleibt gültig."""
    antwort = RedirectResponse("/kein-zugang", status_code=303)
    antwort.delete_cookie(COOKIE)
    return antwort


# ── Übersicht ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def start(request: Request):
    zugang = wache(request)
    if not zugang:
        return abweisen()

    jetzt = datetime.now(timezone.utc)
    with db() as c:
        offene_calls = [dict(r) for r in c.execute(
            """SELECT * FROM defend_channels
                WHERE guild_id=? AND status='open'
                ORDER BY COALESCE(arrival_time, created_at)""", (WORLD_ID,))]

        # Zu jedem Call, was schon geschickt wurde — die eigentliche Frage bei
        # einem Defend-Call ist „reicht es?", nicht „gibt es ihn?"
        for call in offene_calls:
            r = c.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(amount_parsed),0) AS truppen
                     FROM defend_sent WHERE guild_id=? AND channel_id=?""",
                (WORLD_ID, call["channel_id"])).fetchone()
            call["helfer"] = r["n"]
            call["truppen"] = r["truppen"]

        offene_pushes = [dict(r) for r in c.execute(
            """SELECT * FROM res_requests
                WHERE guild_id=? AND status IN ('open','offen','active')
                ORDER BY created_at DESC LIMIT 20""", (WORLD_ID,))]

        angriffe = [dict(r) for r in c.execute(
            """SELECT * FROM incoming_attacks
                WHERE guild_id=? AND COALESCE(is_dismissed,0)=0
                ORDER BY arrival_time LIMIT 30""", (WORLD_ID,))]

        zahlen = {
            "mitglieder": c.execute("SELECT COUNT(*) FROM alliance_members WHERE guild_id=?",
                                    (WORLD_ID,)).fetchone()[0],
            "doerfer": c.execute("SELECT COUNT(*) FROM guild_own_villages WHERE guild_id=?",
                                 (WORLD_ID,)).fetchone()[0],
            "calls_gesamt": c.execute("SELECT COUNT(*) FROM defend_channels WHERE guild_id=?",
                                      (WORLD_ID,)).fetchone()[0],
        }

    return templates.TemplateResponse(request, "start.html", {
        "zugang": zugang, "welt": welt_name(),
        "calls": offene_calls, "pushes": offene_pushes, "angriffe": angriffe,
        "zahlen": zahlen, "jetzt": jetzt,
    })


# ── Join-Links verwalten ──────────────────────────────────────────

@app.get("/links", response_class=HTMLResponse)
def links_seite(request: Request):
    zugang = wache(request)
    if not zugang:
        return abweisen()
    # Wer Links ausgeben darf, entscheidet, wer mitliest — das ist Leitungssache
    if zugang["stufe"] != "leitung":
        return RedirectResponse("/", status_code=303)

    basis = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "links.html", {
        "zugang": zugang, "welt": welt_name(),
        "links": access.links(), "basis": basis,
    })


@app.post("/links/neu")
def link_neu(request: Request, name: str = Form(""), stufe: str = Form("mitglied")):
    zugang = wache(request)
    if not zugang or zugang["stufe"] != "leitung":
        return abweisen()
    access.link_anlegen(name, stufe)
    return RedirectResponse("/links", status_code=303)


@app.post("/links/{token}/sperren")
def link_sperren(request: Request, token: str, zurueck: str = Form("")):
    zugang = wache(request)
    if not zugang or zugang["stufe"] != "leitung":
        return abweisen()
    access.link_sperren(token, gesperrt=(zurueck != "1"))
    return RedirectResponse("/links", status_code=303)


@app.post("/links/{token}/loeschen")
def link_weg(request: Request, token: str):
    zugang = wache(request)
    if not zugang or zugang["stufe"] != "leitung":
        return abweisen()
    # Den eigenen Zugang zu löschen sperrt einen selbst aus — das wäre eine
    # Falle, kein Werkzeug
    if token == request.cookies.get(COOKIE):
        return RedirectResponse("/links?fehler=selbst", status_code=303)
    access.link_loeschen(token)
    return RedirectResponse("/links", status_code=303)


@app.get("/gesund")
def gesund():
    return {"ok": True, "welt": WORLD_ID}
