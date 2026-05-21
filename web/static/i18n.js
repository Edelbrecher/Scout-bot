/**
 * BIG-EYE-BOT i18n — DE/EN
 * Add data-i18n="key" to any element. HTML allowed via data-i18n-html="key".
 */

const TRANSLATIONS = {
  en: {
    // Nav
    "nav.dashboard": "Dashboard",
    "nav.logout": "🚪 Log out",
    "nav.language": "Language",
    "nav.billing": "Subscription",
    "logout": "🚪 Log out",
    // Dashboard hero
    "hero.tagline": "Save 3+ hours every week — automatically.",
    "hero.sub": "BIG-EYE-BOT handles the tedious work so your alliance can focus on what matters: winning.",
    "benefits.title": "Why thousands of guilds trust BIG-EYE-BOT",
    "benefit.time.title": "Save hours every week",
    "benefit.time.body": "Auto farm-list snapshots, attack detection and march-time calculators replace hours of manual work — every single day.",
    "benefit.coord.title": "Perfect coordination",
    "benefit.coord.body": "Resource requests, attack alerts and operation planning land directly in Discord — your team reacts in seconds, not minutes.",
    "benefit.intel.title": "Always one step ahead",
    "benefit.intel.body": "The interactive map, inactive-farm radar and attack analysis give you information your opponents simply don't have.",
    "benefit.never.title": "Never miss an operation",
    "benefit.never.body": "Precise send-time reminders fire exactly when you need to click — with a direct 'Go Troops GO' link right in the alarm.",
    "benefit.setup.title": "Running in under 2 minutes",
    "benefit.setup.body": "One-click auto setup, no technical knowledge required. Everything works out of the box.",
    // Server cards
    "dashboard.servers": "Your servers",
    "dashboard.invite": "+ Invite Bot",
    "dashboard.empty": "No servers found yet.",
    "dashboard.empty.hint": "Invite the bot to a Discord server — it will appear here automatically.",
    "status.configured": "✅ Active",
    "status.setup": "⚠️ Setup needed",
    "sub.active": "✅ Pro",
    "sub.trialing": "🎁 Trial",
    "sub.free": "Free",
    "sub.past_due": "⚠️ Payment due",
  },
  de: {
    // Nav
    "nav.dashboard": "Dashboard",
    "nav.logout": "🚪 Abmelden",
    "nav.language": "Sprache",
    "nav.billing": "Abonnement",
    "logout": "🚪 Abmelden",
    // Dashboard hero
    "hero.tagline": "Spare 3+ Stunden pro Woche — automatisch.",
    "hero.sub": "BIG-EYE-BOT übernimmt die mühsame Arbeit, damit deine Allianz sich auf das Wesentliche konzentrieren kann: Gewinnen.",
    "benefits.title": "Warum tausende Gilden auf BIG-EYE-BOT vertrauen",
    "benefit.time.title": "Stunden pro Woche sparen",
    "benefit.time.body": "Automatische Karten-Snapshots, Angriffserkennung und Marschzeit-Kalkulator ersetzen stundenlange manuelle Arbeit — jeden einzelnen Tag.",
    "benefit.coord.title": "Perfekte Koordination",
    "benefit.coord.body": "Ressourcenanfragen, Angriffsmeldungen und Einsatzplanung landen direkt im Discord — dein Team reagiert in Sekunden, nicht Minuten.",
    "benefit.intel.title": "Immer einen Schritt voraus",
    "benefit.intel.body": "Interaktive Karte, Inaktiv-Farm-Radar und Angriffsanalyse liefern Informationen, die deine Gegner schlicht nicht haben.",
    "benefit.never.title": "Keinen Einsatz mehr verpassen",
    "benefit.never.body": "Präzise Absende-Erinnerungen feuern genau dann, wenn du klicken musst — mit direktem 'Go Troops GO' Link im Alarm.",
    "benefit.setup.title": "In unter 2 Minuten startklar",
    "benefit.setup.body": "Ein-Klick Auto-Setup, kein technisches Wissen erforderlich. Alles funktioniert sofort.",
    // Server cards
    "dashboard.servers": "Deine Server",
    "dashboard.invite": "+ Bot einladen",
    "dashboard.empty": "Noch keine Server gefunden.",
    "dashboard.empty.hint": "Lade den Bot auf einen Discord-Server ein — er erscheint hier automatisch.",
    "status.configured": "✅ Aktiv",
    "status.setup": "⚠️ Einrichtung nötig",
    "sub.active": "✅ Pro",
    "sub.trialing": "🎁 Testphase",
    "sub.free": "Free",
    "sub.past_due": "⚠️ Zahlung fällig",
  },
};

function applyTranslations(lang) {
  const dict = TRANSLATIONS[lang] || TRANSLATIONS['de'];
  // Text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key] !== undefined) el.textContent = dict[key];
  });
  // HTML content (for elements with bold/links inside)
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    const key = el.getAttribute('data-i18n-html');
    if (dict[key] !== undefined) el.innerHTML = dict[key];
  });
  document.documentElement.lang = lang === 'en' ? 'en' : 'de';
  // Update active button state
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.style.background = btn.dataset.lang === lang ? 'var(--accent)' : 'none';
    btn.style.color = btn.dataset.lang === lang ? '#fff' : 'var(--text-muted)';
  });
}

function setLanguage(lang) {
  if (!TRANSLATIONS[lang]) return;
  localStorage.setItem('beb_lang', lang);
  applyTranslations(lang);
}

// Auto-apply on DOM ready
document.addEventListener('DOMContentLoaded', function() {
  const lang = localStorage.getItem('beb_lang') || 'de';
  applyTranslations(lang);
});
// Also run immediately in case DOM is already loaded
(function() {
  const lang = localStorage.getItem('beb_lang') || 'de';
  applyTranslations(lang);
})();
