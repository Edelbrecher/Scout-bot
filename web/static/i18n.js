/**
 * BIG-EYE-BOT i18n system
 * Usage: add data-i18n="key" to any element.
 * Call setLanguage('en') or setLanguage('de') to switch.
 * Language persists in localStorage under 'beb_lang'.
 */

const TRANSLATIONS = {
  en: {
    // Navbar / global
    dashboard: "Dashboard",
    logout: "🚪 Log out",
    settings: "Settings",
    // Dashboard page
    "servers_connected": "server(s) connected",
    "invite_bot": "+ Invite Bot",
    "no_servers": "No servers found yet.",
    "no_servers_hint": "Invite the bot to a Discord server — it will appear here automatically.",
    "configured": "✅ Configured",
    "needs_setup": "⚠️ Needs setup",
    "button_active": "🔘 Button active",
    // Guild page
    "guild_settings": "Settings",
    "save": "Save",
    "saved": "Saved!",
    // Billing
    "billing": "Billing",
    "upgrade": "Upgrade",
    "current_plan": "Current plan",
    // General
    "loading": "Loading…",
    "error": "Error",
    "cancel": "Cancel",
    "confirm": "Confirm",
  },
  de: {
    // Navbar / global
    dashboard: "Dashboard",
    logout: "🚪 Abmelden",
    settings: "Einstellungen",
    // Dashboard page
    "servers_connected": "Server verbunden",
    "invite_bot": "+ Bot einladen",
    "no_servers": "Noch keine Server gefunden.",
    "no_servers_hint": "Lade den Bot auf einen Discord-Server ein — er erscheint hier automatisch.",
    "configured": "✅ Konfiguriert",
    "needs_setup": "⚠️ Einrichtung nötig",
    "button_active": "🔘 Button aktiv",
    // Guild page
    "guild_settings": "Einstellungen",
    "save": "Speichern",
    "saved": "Gespeichert!",
    // Billing
    "billing": "Abrechnung",
    "upgrade": "Upgrade",
    "current_plan": "Aktueller Plan",
    // General
    "loading": "Lädt…",
    "error": "Fehler",
    "cancel": "Abbrechen",
    "confirm": "Bestätigen",
  },
};

function applyTranslations(lang) {
  const dict = TRANSLATIONS[lang] || TRANSLATIONS['de'];
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key] !== undefined) {
      el.textContent = dict[key];
    }
  });
  document.documentElement.lang = lang === 'en' ? 'en' : 'de';
}

function setLanguage(lang) {
  if (!TRANSLATIONS[lang]) return;
  localStorage.setItem('beb_lang', lang);
  applyTranslations(lang);
}

// Auto-apply on load
(function () {
  const lang = localStorage.getItem('beb_lang') || 'de';
  applyTranslations(lang);
})();
