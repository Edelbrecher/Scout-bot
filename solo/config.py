"""Konfiguration der Einzelwelt-Anwendung.

TravOps war eine Anwendung für viele Allianzen: Anmeldung über Discord,
Abomodell, Serverauswahl. Hier läuft genau *eine* Welt. Damit fallen drei
Dinge weg, die den größten Teil der alten Anwendung ausgemacht haben —
Nutzerkonten, Zahlungen und die Frage „welcher Server ist gemeint".

Was bleibt, ist die Datenbank: sie wird unverändert weiterbenutzt. Die Daten
aller anderen Server bleiben darin liegen und werden schlicht nicht mehr
angezeigt.
"""
import os

# Die eine Welt. Alles, was die Anwendung zeigt, kommt aus dieser guild_id.
WORLD_ID = os.environ.get("WORLD_ID", "1508779721133916200")

DB_PATH = os.environ.get("DB_PATH", "/app/data/scouter.db")

# Name im Kopf der Seite — nur Anzeige
WORLD_NAME = os.environ.get("WORLD_NAME", "")

COOKIE = "travops_solo"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365      # ein Jahr: niemand soll sich neu einladen müssen

# Zeitzone für alle Anzeigen
TZ = os.environ.get("TZ_DISPLAY", "Europe/Berlin")
