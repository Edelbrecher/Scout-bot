"""
i18n.py — Simple translation helper for TravOps bot.
Usage:
    from i18n import t, get_guild_lang

    lang = await get_guild_lang(guild_id)   # 'de' or 'en'
    text = t(lang, "scout.taken", user="Max")
"""

import database

# ──────────────────────────────────────────────────────────────────────────────
# Translation strings
# ──────────────────────────────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    # ── Generic ───────────────────────────────────────────────────────────────
    "not_configured": {
        "de": "⚠️ Bot nicht vollständig konfiguriert.",
        "en": "⚠️ Bot is not fully configured.",
    },
    "category_not_found": {
        "de": "⚠️ Kategorie nicht gefunden.",
        "en": "⚠️ Category not found.",
    },
    "channel_created": {
        "de": "✅ Channel erstellt: {channel}",
        "en": "✅ Channel created: {channel}",
    },
    "time_order_error": {
        "de": "⚠️ Die 2. Ankunftszeit muss nach der 1. Ankunftszeit liegen.",
        "en": "⚠️ The 2nd arrival time must be after the 1st arrival time.",
    },
    "requested_by": {
        "de": "Angefragt von {user}",
        "en": "Requested by {user}",
    },
    "reported_by": {
        "de": "Gemeldet von {user}",
        "en": "Reported by {user}",
    },

    # ── Scout ─────────────────────────────────────────────────────────────────
    "scout.title": {
        "de": "🔍 Scout-Request",
        "en": "🔍 Scout Request",
    },
    "scout.taken": {
        "de": "✋ **{user}** hat diesen Scout-Job übernommen!",
        "en": "✋ **{user}** has taken this scout job!",
    },
    "scout.released": {
        "de": "↩️ {user} kann den Job nicht erledigen. Anfrage ist wieder **offen**!",
        "en": "↩️ {user} can't do this job. The request is **open again**!",
    },
    "scout.cant_do": {
        "de": "❌ **{user}** kann den Job nicht erledigen. Suche weiter nach einem Späher…",
        "en": "❌ **{user}** can't do this job. Still looking for a scout...",
    },
    "scout.cancelled": {
        "de": "Scout-Anfrage abgebrochen",
        "en": "Scout request cancelled",
    },
    "scout.closed": {
        "de": "Scout-Channel geschlossen",
        "en": "Scout channel closed",
    },
    "scout.channel_delete_msg": {
        "de": "🔒 **{label}** von {user}.\nDieser Channel wird in **2 Minuten gelöscht**.",
        "en": "🔒 **{label}** by {user}.\nThis channel will be **deleted in 2 minutes**.",
    },
    "scout.new_request": {
        "de": "Neue Scout-Anfrage von {user}",
        "en": "New scout request from {user}",
    },
    "scout.btn.taken": {
        "de": "Übernommen von",
        "en": "Taken by",
    },
    "scout.btn.cant": {
        "de": "Kann nicht spähen",
        "en": "Can't do this job",
    },
    "scout.btn.cancel": {
        "de": "Abbrechen",
        "en": "Cancel",
    },
    "scout.btn.close": {
        "de": "Schließen",
        "en": "Close",
    },
    "scout.setup_missing": {
        "de": "⚠️ Bitte **Kategorie-ID** und **Archiv-Channel-ID** im Web-Dashboard konfigurieren.",
        "en": "⚠️ Please configure **Category ID** and **Archive Channel ID** in the web dashboard first.",
    },
    "scout.setup_done": {
        "de": "✅ Scout-Request Button gepostet!",
        "en": "✅ Scout Request button posted!",
    },
    "scout.embed.description": {
        "de": "Klicke den Button um einen Scout-Request einzureichen.\nTrage Koordinaten, Spieler, Dorf und Zeit ein.",
        "en": "Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.",
    },
    "scout.field.player": {
        "de": "Spieler",
        "en": "Player",
    },
    "scout.field.village": {
        "de": "Dorf",
        "en": "Village",
    },
    "scout.field.coords": {
        "de": "Koordinaten",
        "en": "Coordinates",
    },
    "scout.field.time": {
        "de": "Zeit",
        "en": "Time",
    },
    "scout.field.info": {
        "de": "Weitere Infos",
        "en": "Additional Info",
    },

    # ── Corn scout ────────────────────────────────────────────────────────────
    "corn.title": {
        "de": "🌾 Kornspäh-Anfrage",
        "en": "🌾 Corn Scout Request",
    },
    "corn.modal_title": {
        "de": "🌾 Kornspäh-Request",
        "en": "🌾 Corn Scout Request",
    },
    "corn.desc": {
        "de": "Kornspäh-Request für **{player}** @ {coords}",
        "en": "Corn scout request for **{player}** @ {coords}",
    },
    "corn.yes": {
        "de": "Ja",
        "en": "Yes",
    },

    # ── Perm Scout ────────────────────────────────────────────────────────────
    "perm_scout.title": {
        "de": "📡 Permanent-Scout Anfrage",
        "en": "📡 Permanent Scout Request",
    },
    "perm_scout.modal_title": {
        "de": "📡 Permanent-Scout Anfrage",
        "en": "📡 Permanent Scout Request",
    },
    "perm_scout.desc": {
        "de": "Dauerhaft stationierte Späher für Dorf **{village}** werden benötigt.\nSpieler: **{player}** | Koords: **{coords}**",
        "en": "Permanently stationed scouts needed for village **{village}**.\nPlayer: **{player}** | Coords: **{coords}**",
    },

    # ── Hub ───────────────────────────────────────────────────────────────────
    "hub.title": {
        "de": "📋 TravOps Anfragen-Hub",
        "en": "📋 TravOps Request Hub",
    },
    "hub.description": {
        "de": (
            "Klicke einen Button um einen Kanal zu erstellen:\n\n"
            "🔍 **Scout** — Gegner spähen lassen\n"
            "🌾 **Kornspäh** — Korn eines Gegners ausspähen\n"
            "📡 **Permanent-Scout** — Dauerhaft Späher im eigenen Dorf stationieren\n"
            "🪖 **Res-Push** — Ressourcen anfordern\n"
            "🛡️ **Defend** — Verteidigung koordinieren\n"
            "⏱️ **Timed-Defend** — Getimte Verteidigung koordinieren\n"
            "🔒 **Privater Channel** — Eigener permanenter Channel, nur für dich & die Leads\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗡️ **Helden-Scout** — Screenshots von Gegner-Helden in einem dedizierten Channel posten. "
            "Der Bot erkennt automatisch Ausrüstungswechsel & XP-Sprünge.\n"
            "→ Setup: `/hero-scout-setup #channel` oder [travops.online](https://travops.online)"
        ),
        "en": (
            "Click a button to create a channel:\n\n"
            "🔍 **Scout** — Request an enemy scout\n"
            "🌾 **Corn Scout** — Scout an enemy's granary\n"
            "📡 **Permanent Scout** — Station scouts permanently in a village\n"
            "🪖 **Res Push** — Request resources\n"
            "🛡️ **Defend** — Coordinate defense\n"
            "⏱️ **Timed Defend** — Coordinate timed defense\n"
            "🔒 **Private Channel** — Your own permanent channel, visible only to you & the leads\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗡️ **Hero Scout** — Post enemy hero screenshots in a dedicated channel. "
            "The bot automatically detects equipment changes & XP jumps.\n"
            "→ Setup: `/hero-scout-setup #channel` or [travops.online](https://travops.online)"
        ),
    },
    "hub.channel_topic": {
        "de": "Alle Anfragen auf einen Blick — Scout, Defend, Res-Push und mehr.",
        "en": "All requests at a glance — Scout, Defend, Res Push and more.",
    },
    "hub.channel_name": {
        "de": "travops-anfragen",
        "en": "travops-requests",
    },

    # ── Defend ────────────────────────────────────────────────────────────────
    "defend.title": {
        "de": "🛡️ Defend",
        "en": "🛡️ Defend",
    },
    "defend.timed_title": {
        "de": "⏱️ Timed-Defend",
        "en": "⏱️ Timed Defend",
    },
    "defend.field.defender": {
        "de": "Verteidiger",
        "en": "Defender",
    },
    "defend.field.attacker": {
        "de": "Angreifer",
        "en": "Attacker",
    },
    "defend.field.target": {
        "de": "Ziel",
        "en": "Target",
    },
    "defend.field.arrival": {
        "de": "Ankunft ⚔️",
        "en": "Arrival ⚔️",
    },
    "defend.field.wave1": {
        "de": "1. Welle ⚔️",
        "en": "1st Wave ⚔️",
    },
    "defend.field.wave2": {
        "de": "2. Welle ⚔️",
        "en": "2nd Wave ⚔️",
    },
    "defend.field.between": {
        "de": "🛡️ Zwischen-Defense — angestrebte Ankunft",
        "en": "🛡️ Between-Defense — target arrival",
    },
    "defend.between_desc": {
        "de": "**{time}**\n*Truppen so timen dass sie nach der 1. Welle, aber vor der 2. Welle ankommen*",
        "en": "**{time}**\n*Time troops to arrive after the 1st wave but before the 2nd wave*",
    },
    "defend.field.size": {
        "de": "Größe",
        "en": "Size",
    },
    "defend.field.notes": {
        "de": "Notizen",
        "en": "Notes",
    },
    "defend.ping": {
        "de": "🚨 {user} — {prefix}Defend-Anfrage!",
        "en": "🚨 {user} — {prefix}Defend request!",
    },
    "defend.timed_prefix": {
        "de": "Timed-",
        "en": "Timed ",
    },
    "defend.done": {
        "de": "✅ Defend als erledigt markiert von {user}.",
        "en": "✅ Defend marked as done by {user}.",
    },
    "defend.btn.done": {
        "de": "✅ Defend erledigt",
        "en": "✅ Defend done",
    },
    "defend.btn.close": {
        "de": "🔒 Channel schließen",
        "en": "🔒 Close channel",
    },
    "defend.closing": {
        "de": "🔒 Channel wird von {user} geschlossen…",
        "en": "🔒 Channel is being closed by {user}…",
    },
    "defend.channel_closed_reason": {
        "de": "Defend Channel geschlossen",
        "en": "Defend channel closed",
    },

    # ── Res Push ──────────────────────────────────────────────────────────────
    "res_push.title": {
        "de": "🪖 Res-Push Anfrage",
        "en": "🪖 Res Push Request",
    },
    "res_push.field.recipient": {
        "de": "Empfänger",
        "en": "Recipient",
    },
    "res_push.field.village": {
        "de": "Dorf",
        "en": "Village",
    },
    "res_push.field.until": {
        "de": "Bis wann",
        "en": "Until",
    },
    "res_push.field.needed": {
        "de": "Benötigt",
        "en": "Needed",
    },
    "res_push.field.notes": {
        "de": "Notizen",
        "en": "Notes",
    },
    "res_push.channel_created": {
        "de": "✅ Res-Push Channel erstellt: {channel}",
        "en": "✅ Res-Push channel created: {channel}",
    },

    # ── Hub modals (field labels) ──────────────────────────────────────────────
    "hub.scout.modal_title": {
        "de": "🔍 Scout-Request",
        "en": "🔍 Scout Request",
    },
    "hub.scout.field.player": {
        "de": "Spieler-Name",
        "en": "Player Name",
    },
    "hub.scout.field.coords": {
        "de": "Koordinaten",
        "en": "Coordinates",
    },
    "hub.scout.field.village": {
        "de": "Dorfname",
        "en": "Village Name",
    },
    "hub.scout.field.time": {
        "de": "Bis wann?",
        "en": "Until when?",
    },
    "hub.scout.field.info": {
        "de": "Zusätzliche Infos",
        "en": "Additional Info",
    },
    "hub.scout.desc": {
        "de": "Scout-Request für **{player}** @ {coords}",
        "en": "Scout request for **{player}** @ {coords}",
    },
    "hub.scout.field.player_embed": {
        "de": "Spieler",
        "en": "Player",
    },
    "hub.scout.field.coords_embed": {
        "de": "Koordinaten",
        "en": "Coordinates",
    },
    "hub.scout.field.village_embed": {
        "de": "Dorf",
        "en": "Village",
    },
    "hub.scout.field.time_embed": {
        "de": "Bis wann",
        "en": "Until",
    },
    "hub.scout.field.info_embed": {
        "de": "Infos",
        "en": "Info",
    },
    "hub.scout.new_request": {
        "de": "Scout-Anfrage von {user}",
        "en": "Scout request from {user}",
    },

    # ── Private Channel ───────────────────────────────────────────────────────
    "private.already_exists": {
        "de": "📌 Du hast bereits einen privaten Channel: {channel}",
        "en": "📌 You already have a private channel: {channel}",
    },
    "private.created": {
        "de": "✅ Dein privater Channel wurde erstellt: {channel}",
        "en": "✅ Your private channel has been created: {channel}",
    },
    "private.category_name": {
        "de": "Private-Channels",
        "en": "Private-Channels",
    },
    "private.welcome_title": {
        "de": "🔒 Privater Channel — {user}",
        "en": "🔒 Private Channel — {user}",
    },
    "private.welcome_desc": {
        "de": (
            "Dies ist dein persönlicher privater Channel.\n"
            "Nur du, die Leads und von dir freigegebene Mitglieder haben Zugriff.\n\n"
            "Nutze den Button unten um anderen Spielern Zugriff zu gewähren."
        ),
        "en": (
            "This is your personal private channel.\n"
            "Only you, the leads and members you grant access to can see it.\n\n"
            "Use the button below to grant other players access."
        ),
    },
    "private.btn.grant": {
        "de": "➕ Zugriff gewähren",
        "en": "➕ Grant Access",
    },
    "private.btn.revoke": {
        "de": "➖ Zugriff entziehen",
        "en": "➖ Revoke Access",
    },
    "private.grant.modal_title": {
        "de": "Zugriff gewähren",
        "en": "Grant Access",
    },
    "private.grant.field_label": {
        "de": "Spieler-Name oder @Mention",
        "en": "Player Name or @Mention",
    },
    "private.grant.field_placeholder": {
        "de": "z.B. Currax oder @Currax",
        "en": "e.g. Currax or @Currax",
    },
    "private.grant.not_owner": {
        "de": "⚠️ Nur der Channel-Besitzer kann Zugriff gewähren.",
        "en": "⚠️ Only the channel owner can grant access.",
    },
    "private.grant.not_found": {
        "de": "❌ Mitglied `{name}` nicht gefunden. Bitte den genauen Nicknamen oder eine @Erwähnung verwenden.",
        "en": "❌ Member `{name}` not found. Please use the exact nickname or an @mention.",
    },
    "private.grant.success": {
        "de": "✅ {mention} hat jetzt Zugriff auf diesen Channel.",
        "en": "✅ {mention} now has access to this channel.",
    },
    "private.grant.already": {
        "de": "ℹ️ {mention} hat bereits Zugriff.",
        "en": "ℹ️ {mention} already has access.",
    },
    "private.revoke.not_owner": {
        "de": "⚠️ Nur der Channel-Besitzer kann Zugriff entziehen.",
        "en": "⚠️ Only the channel owner can revoke access.",
    },
    "private.revoke.modal_title": {
        "de": "Zugriff entziehen",
        "en": "Revoke Access",
    },
    "private.revoke.field_label": {
        "de": "Spieler-Name oder @Mention",
        "en": "Player Name or @Mention",
    },
    "private.revoke.not_found": {
        "de": "❌ Mitglied `{name}` nicht gefunden.",
        "en": "❌ Member `{name}` not found.",
    },
    "private.revoke.success": {
        "de": "✅ Zugriff für {mention} wurde entzogen.",
        "en": "✅ Access revoked for {mention}.",
    },

    # ── Report channel welcome ────────────────────────────────────────────────
    "report_channel.welcome": {
        "de": (
            "📋 **Bericht-Eingang aktiv** — Postet hier eure Kampfberichte als Screenshot. "
            "Der Bot analysiert sie automatisch und trägt sie in die Gegner-Kartei ein."
        ),
        "en": (
            "📋 **Report intake active** — Post your battle reports here as screenshots. "
            "The bot analyses them automatically and adds them to the enemy registry."
        ),
    },
    "report_channel.name": {
        "de": "battle-reports",
        "en": "battle-reports",
    },
    "report_channel.topic": {
        "de": "Kampfberichte-Eingang — Bot scannt alle Bilder automatisch.",
        "en": "Battle report intake — Bot scans all images automatically.",
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def t(lang: str, key: str, **kwargs) -> str:
    """Translate *key* into *lang* ('de' or 'en'), applying optional format kwargs."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key  # fallback: return the raw key
    text = entry.get(lang) or entry.get("de") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    return text


async def get_guild_lang(guild_id: str) -> str:
    """Return the configured bot language for a guild ('de' or 'en')."""
    try:
        config = await database.get_guild_config(guild_id)
        lang = (config or {}).get("bot_language") or "de"
        return lang if lang in ("de", "en") else "de"
    except Exception:
        return "de"
