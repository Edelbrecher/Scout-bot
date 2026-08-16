"""Zugang über Join-Links statt Anmeldung.

Der Gedanke: es gibt kein Konto und kein Passwort. Wer den Link kennt, ist
drin — aber der Link ist ein Geheimnis, das sich einzeln zurückziehen lässt.
Das ist der Unterschied zu „einfach offen": geht ein Link herum, sperrst du
diesen einen und gibst einen neuen aus, ohne dass alle anderen betroffen sind.

Wie es abläuft:

  1. Du erzeugst einen Link ``/join/<token>`` und gibst ihn weiter.
  2. Wer ihn öffnet, bekommt ein Cookie mit demselben Geheimnis und wird
     weitergeleitet. Ab da ist das Gerät dauerhaft angemeldet — ein Jahr lang.
  3. Jede Seite prüft nur, ob das Cookie zu einem gültigen, nicht gesperrten
     Link gehört.

Zwei Stufen: ``leitung`` darf alles, ``mitglied`` darf lesen und mitmachen.
Mehr Rollen wären für eine Allianz ausgedacht — wer Truppen schickt, muss auch
Calls anlegen können.
"""
import secrets
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH, WORLD_ID, COOKIE

STUFEN = ("leitung", "mitglied")


def _db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Eigene Tabelle, damit der Umbau nichts Bestehendes anfasst."""
    with _db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS solo_links (
                token      TEXT PRIMARY KEY,
                world_id   TEXT NOT NULL,
                name       TEXT NOT NULL DEFAULT '',
                stufe      TEXT NOT NULL DEFAULT 'mitglied',
                created_at TEXT NOT NULL,
                last_seen  TEXT,
                uses       INTEGER NOT NULL DEFAULT 0,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.commit()

    # Ohne einen ersten Leitungs-Link käme niemand hinein — auch du nicht.
    with _db() as c:
        offen = c.execute(
            "SELECT COUNT(*) FROM solo_links WHERE world_id=? AND stufe='leitung' AND revoked=0",
            (WORLD_ID,)).fetchone()[0]
    if not offen:
        token = link_anlegen("Erster Zugang (Leitung)", "leitung")
        print(f"\n{'=' * 64}\n  Erster Zugangslink für diese Welt:\n"
              f"    /join/{token}\n"
              f"  Nur einmal hier zu sehen — er steht danach in der Verwaltung.\n{'=' * 64}\n",
              flush=True)


def link_anlegen(name: str, stufe: str = "mitglied") -> str:
    if stufe not in STUFEN:
        stufe = "mitglied"
    token = secrets.token_urlsafe(24)
    with _db() as c:
        c.execute(
            "INSERT INTO solo_links (token, world_id, name, stufe, created_at) VALUES (?,?,?,?,?)",
            (token, WORLD_ID, name.strip()[:60] or "Ohne Namen", stufe,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        c.commit()
    return token


def link_sperren(token: str, gesperrt: bool = True):
    with _db() as c:
        c.execute("UPDATE solo_links SET revoked=? WHERE token=? AND world_id=?",
                  (1 if gesperrt else 0, token, WORLD_ID))
        c.commit()


def link_loeschen(token: str):
    with _db() as c:
        c.execute("DELETE FROM solo_links WHERE token=? AND world_id=?", (token, WORLD_ID))
        c.commit()


def links():
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM solo_links WHERE world_id=? ORDER BY revoked, created_at DESC",
            (WORLD_ID,))]


def pruefe(token: str | None) -> dict | None:
    """Gehört dieses Geheimnis zu einem gültigen Link? Sonst None."""
    if not token:
        return None
    with _db() as c:
        r = c.execute(
            "SELECT * FROM solo_links WHERE token=? AND world_id=? AND revoked=0",
            (token, WORLD_ID)).fetchone()
        if not r:
            return None
        # Mitzählen, damit in der Verwaltung sichtbar ist, welcher Link lebt
        c.execute("UPDATE solo_links SET last_seen=?, uses=uses+1 WHERE token=?",
                  (datetime.now(timezone.utc).isoformat(timespec="seconds"), token))
        c.commit()
        return dict(r)


def aus_request(request) -> dict | None:
    return pruefe(request.cookies.get(COOKIE))
