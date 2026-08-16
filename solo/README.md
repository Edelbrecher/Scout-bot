# TravOps Solo

Eine Welt, kein Konto, kein Abo. Ersetzt schrittweise die alte Anwendung
(`web/`), die für viele Allianzen mit Discord-Anmeldung und Abomodell gebaut
war.

## Zugang

Es gibt keine Anmeldung. Wer einen gültigen Join-Link öffnet, bekommt ein
Cookie und ist auf diesem Gerät ein Jahr lang drin. Jeder Link ist ein eigenes
Geheimnis und einzeln sperrbar — geht einer herum, sperrst du genau diesen.

Zwei Stufen: **Leitung** darf zusätzlich Zugänge vergeben, **Mitglied** sieht
und macht mit.

Beim ersten Start legt die Anwendung einen Leitungs-Link an und schreibt ihn
ins Protokoll:

    docker compose logs solo | head -20

## Daten

Dieselbe SQLite-Datei wie bisher. Gelesen wird ausschließlich die eine Welt
(`WORLD_ID`); alles andere bleibt unangetastet liegen. Eigene Tabelle:
`solo_links`.

## Einstellungen

| Variable   | Bedeutung                                   |
|------------|---------------------------------------------|
| `WORLD_ID` | Die Welt, die angezeigt wird                |
| `DB_PATH`  | Pfad zur Datenbank                          |
| `WORLD_NAME` | Anzeigename (sonst aus `guild_configs`)   |

## Stand

Fertig: Zugang über Join-Links, Zugangsverwaltung, Übersicht mit offenen
Defend-Calls samt geleisteter Hilfe, angekündigten Angriffen und Res-Pushes.

Kommt als Nächstes: Karte, Scouting, Farmlisten, Operationen — die vorhandenen
Daten dafür liegen bereits in der Datenbank.
