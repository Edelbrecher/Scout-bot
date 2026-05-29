/**
 * TravOps Guided Tour System
 * Multi-page, interactive onboarding & per-feature tours.
 *
 * State: localStorage key 'travops_tour' → JSON { name, step, guildId }
 * Done flags: 'travops_tour_done_{name}' per tour
 */
(function () {
  'use strict';

  const lang = localStorage.getItem('beb_lang') || 'de';
  const t = (de, en) => lang === 'en' ? en : de;

  // ── Tour registry ──────────────────────────────────────────────────────────
  // Each tour = function(guildId) → array of steps
  // Step props:
  //   page      – absolute path this step belongs to (null = any)
  //   target    – CSS selector to spotlight (null = centered modal)
  //   title     – headline
  //   body      – description (supports \n)
  //   hint      – small callout below body (optional)
  //   action    – { label, url } – link button (optional)
  //   next      – button label
  //   isLast    – marks final step

  const TOURS = {

    // ── 1. START / EINRICHTUNGS-TOUR ────────────────────────────────────────
    start(guildId) {
      const base = `/guild/${guildId}`;
      return [
        {
          page: null, target: null,
          title: t('Willkommen bei TravOps! 👋', 'Welcome to TravOps! 👋'),
          body: t(
            'TravOps ist dein All-in-One Werkzeug für Travian-Allianzen.\n\nDieser Assistent führt dich in 2 Minuten durch die wichtigsten Einstellungen, damit du sofort loslegen kannst.',
            'TravOps is your all-in-one tool for Travian alliances.\n\nThis assistant walks you through the key settings in 2 minutes so you can get started right away.'
          ),
          next: t("Los geht's →", "Let's go →"),
        },
        {
          page: `${base}/map/world-settings`, target: null,
          title: t('1 / 3 · Serverzeit einstellen ⏰', '1 / 3 · Set server time ⏰'),
          body: t(
            'Alle Marschzeiten und Countdowns basieren auf der Uhrzeit deines Travian-Servers.\n\n🇪🇺 Europa → UTC+1\n🌍 Arabische Server → UTC+3\n\nWähle rechts die passende Zeitzone und speichere.',
            'All march times and countdowns are based on your Travian server clock.\n\n🇪🇺 Europe → UTC+1\n🌍 Arabic servers → UTC+3\n\nSelect the timezone on the right and save.'
          ),
          hint: t('✅ Zeitzone wählen & speichern, dann "Weiter" klicken', '✅ Select timezone & save, then click "Next"'),
          highlight: 'select[name*="utc"], select[name*="tz"], .timezone-select, #timezone-select',
          next: t('Weiter', 'Next'),
        },
        {
          page: `${base}/my-ally`, target: null,
          title: t('2 / 3 · Allianz einrichten 👥', '2 / 3 · Set up alliance 👥'),
          body: t(
            'Hier verwaltest du deine Allianz: Mitglieder einladen, Rollen vergeben und Flügel anlegen.\n\nTeile den Einladungslink mit deinen Mitspielern — nach dem Beitritt können sie ihre Dörfer und Truppen hinterlegen.',
            'Manage your alliance here: invite members, assign roles and create wings.\n\nShare the invitation link with your players — after joining they can add their villages and troops.'
          ),
          hint: t('✅ Einladungslink kopieren und an Mitglieder weitergeben', '✅ Copy invitation link and share with members'),
          highlight: '.invite-link, [data-tour="invite"], input[readonly]',
          next: t('Weiter', 'Next'),
        },
        {
          page: `${base}/map/own-villages`, target: null,
          title: t('3 / 3 · Dörfer hochladen 🏘️', '3 / 3 · Upload villages 🏘️'),
          body: t(
            'Lade deine Dorf-Koordinaten hoch — das ermöglicht exakte Marschzeit-Berechnungen im Einsatzplaner.\n\nKlicke auf "Dörfer hinzufügen" und füge die Daten aus Travian ein.',
            'Upload your village coordinates — this enables exact march time calculations in the operation planner.\n\nClick "Add villages" and paste data from Travian.'
          ),
          hint: t('✅ Optional, aber für präzise Marschzeiten empfohlen', '✅ Optional but recommended for precise march times'),
          next: t('Weiter', 'Next'),
        },
        {
          page: base, target: '.feature-grid, .features-section, main',
          title: t('Alles bereit! 🎉', 'All set! 🎉'),
          body: t(
            'Du hast TravOps eingerichtet. Alle Module sind jetzt verfügbar:\n\n⚔️ Einsatzplanung  🛡️ Angriffserkennung\n🦸 Helden-Scout  📊 Farming-Analyse\n🏥 Hospital  👥 Allianz-Verwaltung\n\nJedes Modul hat eine eigene Tour — klicke oben auf ❓ Tour.',
            'TravOps is set up. All modules are available:\n\n⚔️ Operations  🛡️ Attack Detection\n🦸 Hero Scout  📊 Farming Analysis\n🏥 Hospital  👥 Alliance Management\n\nEach module has its own tour — click ❓ Tour above.'
          ),
          next: t('Tour abschließen ✓', 'Finish tour ✓'),
          isLast: true,
        },
      ];
    },

    // ── 2. MY ACCOUNT TOUR ──────────────────────────────────────────────────
    'my-account'() {
      return [
        {
          page: null, target: null,
          title: t('Mein Profil 👤', 'My Profile 👤'),
          body: t(
            'Hier findest du deinen persönlichen Bereich:\n· TravOps-Points sammeln & einlösen\n· Deinen Einladungslink teilen\n· Pro-Zugang verlängern',
            'Your personal area:\n· Collect & redeem TravOps points\n· Share your invitation link\n· Extend Pro access'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.card:first-of-type',
          title: t('TravOps-Points 🌟', 'TravOps Points 🌟'),
          body: t(
            '10 Points = 1 Monat Pro kostenlos (ca. 10 € Ersparnis).\n\nFortschrittsbalken zeigt wie nah du der nächsten Einlösung bist.',
            '10 points = 1 month Pro for free (~€10 savings).\n\nProgress bar shows how close you are to your next redemption.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '#refLinkInput',
          title: t('Einladungslink 🔗', 'Invitation Link 🔗'),
          body: t(
            'Teile diesen Link mit anderen Spielern.\n\nSobald jemand darüber ein Pro-Abo kauft → +1 Point für dich. Automatisch, einmalig pro Person, Points verfallen nie.',
            'Share this link with other players.\n\nWhenever someone buys Pro through it → +1 point for you. Automatic, once per person, points never expire.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: 'button[onclick*="restartAccountTour"], button[onclick*="restartTour"]',
          title: t('Tour jederzeit neu starten 🗺️', 'Restart tour anytime 🗺️'),
          body: t(
            'Hast du etwas verpasst oder möchtest das System einem neuen Mitglied zeigen?\n\nEinfach hier die gewünschte Tour neu starten.',
            'Missed something or want to show the system to a new member?\n\nJust restart the desired tour here.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 3. EINSATZPLANUNG ───────────────────────────────────────────────────
    operations(guildId) {
      const base = `/guild/${guildId}/operations`;
      return [
        {
          page: null, target: null,
          title: t('Einsatzplanung ⚔️', 'Operation Planning ⚔️'),
          body: t(
            'Koordiniere Angriffe mit automatischer Marschzeit-Berechnung.\n\nKein manuelles Rechnen mehr — TravOps berechnet für jede Einheit aus jedem Dorf die exakte Abmarschzeit.',
            'Coordinate attacks with automatic march time calculation.\n\nNo more manual calculations — TravOps computes the exact send time for every unit from every village.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: 'button[onclick*="createPlan"], .create-plan-btn, [data-tour="create-plan"]',
          title: t('Neuen Einsatzplan anlegen', 'Create new operation plan'),
          body: t(
            'Klicke hier um einen neuen Einsatz zu erstellen.\n\nGib Ziel-Allianz, Landungszeit und Geschwindigkeit des Servers an — der Rest wird automatisch berechnet.',
            'Click here to create a new operation.\n\nSet the target alliance, landing time and server speed — the rest is calculated automatically.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.op-plan-list, .plans-list, [data-tour="plan-list"]',
          title: t('Deine Einsatzpläne', 'Your operation plans'),
          body: t(
            'Alle Einsätze auf einen Blick — mit Status (Entwurf / Aktiv / Abgebrochen).\n\nAktive Pläne werden automatisch per Discord-DM an alle Teilnehmer gesendet.',
            'All operations at a glance — with status (Draft / Active / Cancelled).\n\nActive plans are automatically sent to all participants via Discord DM.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '[data-tour="my-waves"], .my-waves-section, .personal-plan',
          title: t('Deine persönlichen Wellen', 'Your personal waves'),
          body: t(
            'Hier siehst du nur deine eigenen zugewiesenen Angriffe mit Abmarschzeit und Countdown.\n\nSo verlierst du nie den Überblick — auch bei komplexen Einsätzen mit vielen Teilnehmern.',
            'See only your own assigned attacks with send time and countdown.\n\nNever lose track — even in complex operations with many participants.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 4. ATTACK DETECTION ─────────────────────────────────────────────────
    attacks(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Angriffserkennung 🛡️', 'Attack Detection 🛡️'),
          body: t(
            'Analysiere eingehende Angriffe und unterscheide Fakes von echten Bedrohungen.\n\nKopiiere einfach deinen Rallypoint-Inhalt rein — TravOps wertet alles automatisch aus.',
            'Analyze incoming attacks and distinguish fakes from real threats.\n\nJust paste your rally point content — TravOps evaluates everything automatically.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: 'button[onclick*="importModal"], .import-btn, [data-tour="import"]',
          title: t('Rallypoint importieren', 'Import rally point'),
          body: t(
            'Öffne in Travian deinen Rallypoint → "Eingehende" → alles markieren → hier einfügen.\n\nTravOps erkennt automatisch: Dorfname, Koordinaten, Einheiten, Ankunftszeit.',
            'In Travian open Rally Point → "Incoming" → select all → paste here.\n\nTravOps auto-detects: village name, coordinates, units, arrival time.'
          ),
          hint: t('✅ Strg+A im Rallypoint, dann hier einfügen', '✅ Ctrl+A in rally point, then paste here'),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.fake-score, [data-tour="fake-score"], .attack-card',
          title: t('Fake-Erkennung 🎯', 'Fake detection 🎯'),
          body: t(
            'Jeder Angriff erhält einen Fake-Score von 0–100:\n\n🟢 80–100 = sehr wahrscheinlich Fake\n🟡 40–79  = unklar\n🔴 0–39   = wahrscheinlich echt\n\nSichtbare Truppen < 20 = 100% Fake (außer der Gegner hat Unique-Späher-Artefakt).',
            'Each attack gets a fake score from 0–100:\n\n🟢 80–100 = very likely fake\n🟡 40–79  = unclear\n🔴 0–39   = likely real\n\nVisible troops < 20 = 100% fake (unless enemy has Unique Scout artifact).'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.wave-group, [data-tour="waves"], .zwischendef',
          title: t('Wellen & Zwischendef ⚠️', 'Waves & Intermediate defense ⚠️'),
          body: t(
            'Angriffe im Abstand ≤ 1 Sekunde = eine Welle.\n\nLiegen mehrere Wellen zeitlich auseinander, erscheint eine Warnung zur Zwischendef: In der Lücke könnten Verteidiger abziehen.',
            'Attacks within ≤ 1 second gap = one wave.\n\nIf multiple waves are spread over time, an intermediate defense warning appears: defenders could leave in the gap.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.alliance-view, [data-tour="alliance-view"], .alliance-section',
          title: t('Allianz-Übersicht', 'Alliance overview'),
          body: t(
            'Der zweite Tab zeigt alle Angriffe gruppiert nach betroffenen Dörfern in der Allianz.\n\nSo sieht die Allianz-Leitung sofort wer angegriffen wird und kann Verteidiger koordinieren.',
            'The second tab shows all attacks grouped by affected villages in the alliance.\n\nAlliance leadership immediately sees who is being attacked and can coordinate defenders.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 5. HERO SCOUT ───────────────────────────────────────────────────────
    'hero-scout'(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Helden-Scout 🦸', 'Hero Scout 🦸'),
          body: t(
            'Behalte die Ausrüstung gegnerischer Helden im Blick.\n\nJeder Scout-Bericht wird gespeichert — du siehst die vollständige Zeitlinie wann welche Items getragen wurden.',
            'Keep track of enemy hero equipment.\n\nEvery scout report is saved — see the full timeline of when which items were worn.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.hero-list, [data-tour="hero-list"], table',
          title: t('Gegner-Helden-Liste', 'Enemy hero list'),
          body: t(
            'Alle bisher gescouteten gegnerischen Helden auf einen Blick.\n\nKlicke auf einen Helden für die vollständige Ausrüstungs-Zeitlinie.',
            'All scouted enemy heroes at a glance.\n\nClick on a hero for the full equipment timeline.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: 'a[href*="manual"], button[onclick*="manual"], [data-tour="manual-entry"]',
          title: t('Manuell anlegen ✏️', 'Manual entry ✏️'),
          body: t(
            'Kein Scout-Screenshot? Kein Problem.\n\nTrage Held und Ausrüstung manuell ein — mit Spieler-Suche damit keine Tippfehler passieren.',
            'No scout screenshot? No problem.\n\nEnter hero and equipment manually — with player search to avoid typos.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.artefakt-section, [data-tour="artifacts"], .artifact-buttons',
          title: t('Artefakte verwalten 💎', 'Manage artifacts 💎'),
          body: t(
            'Auf der Detailseite eines Gegners kannst du angeben welche Artefakte sein Account bzw. sein Dorf hat.\n\nDas beeinflusst die Fake-Bewertung in der Angriffserkennung — z.B. Unique Späher = versteckte Truppen trotzdem möglicherweise Fake.',
            'On an enemy\'s detail page you can specify which artifacts their account or village has.\n\nThis affects fake scoring in attack detection — e.g. Unique Scout = hidden troops may still be fake.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 6. FARMING INTEL ────────────────────────────────────────────────────
    farming(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Farming Intel 🌾', 'Farming Intel 🌾'),
          body: t(
            'Finde inaktive Spieler auf der Karte und verwalte deine Farm-Liste.\n\nNie wieder manuell nach Farmen suchen — TravOps filtert Inaktive automatisch heraus.',
            'Find inactive players on the map and manage your farm list.\n\nNever manually search for farms again — TravOps filters inactives automatically.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.inactive-search, [data-tour="inactive-search"], .search-section',
          title: t('Inaktive Spieler finden', 'Find inactive players'),
          body: t(
            'Gib einen Koordinaten-Bereich ein und TravOps zeigt dir alle Dörfer ohne kürzliche Aktivität.\n\nFilterbar nach Bevölkerung, Stamm und Entfernung zu deinen eigenen Dörfern.',
            'Enter a coordinate range and TravOps shows all villages without recent activity.\n\nFilterable by population, tribe and distance to your own villages.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.farm-list, [data-tour="farm-list"], .farmlist-section',
          title: t('Farm-Liste verwalten', 'Manage farm list'),
          body: t(
            'Füge Dörfer zur Farm-Liste hinzu und behalte den Überblick wer farmt.\n\nDie Liste ist für alle Allianz-Mitglieder sichtbar — so vermeidet ihr Doppel-Farming.',
            'Add villages to the farm list and track who farms where.\n\nThe list is visible to all alliance members — avoid double farming.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 7. KARTE ────────────────────────────────────────────────────────────
    map(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Interaktive Karte 🗺️', 'Interactive Map 🗺️'),
          body: t(
            'Sieh die gesamte Spielwelt auf einen Blick — mit Farben für Allianz, Feinde und neutrale Spieler.\n\nKlicke auf ein Dorf für Details: Spieler, Allianz, Bevölkerung.',
            'See the entire game world at a glance — colored by alliance, enemies and neutral players.\n\nClick a village for details: player, alliance, population.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '#map-canvas, canvas, .map-container',
          title: t('Karten-Ansicht', 'Map view'),
          body: t(
            '🟣 Eigene Allianz\n🔴 Feinde\n⬜ Neutrale Spieler\n🟡 Auf der Farm-Liste\n\nZoomen mit Mausrad, verschieben durch Klicken & Ziehen.',
            '🟣 Own alliance\n🔴 Enemies\n⬜ Neutral players\n🟡 On farm list\n\nZoom with mouse wheel, pan by clicking & dragging.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.map-controls, .filter-bar, [data-tour="map-filters"]',
          title: t('Filter & Suche', 'Filters & Search'),
          body: t(
            'Filtere nach Allianz, Bevölkerung oder Stamm.\n\nSuche direkt nach Spielernamen oder Koordinaten — TravOps springt sofort zur Position.',
            'Filter by alliance, population or tribe.\n\nSearch directly for player names or coordinates — TravOps jumps immediately to the position.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 8. HOSPITAL ─────────────────────────────────────────────────────────
    hospital(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Hospital 🏥', 'Hospital 🏥'),
          body: t(
            'Verwalte verwundete Truppen nach Kämpfen.\n\nHalte fest wie viele Einheiten im Hospital regenerieren und berechne wann deine Armee wieder voll einsatzfähig ist.',
            'Manage wounded troops after battles.\n\nTrack how many units are regenerating in the hospital and calculate when your army is battle-ready again.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.hospital-form, [data-tour="hospital-entry"], form',
          title: t('Verluste eintragen', 'Enter casualties'),
          body: t(
            'Trage nach einem Kampf die Anzahl verwundeter Einheiten ein.\n\nTravOps berechnet automatisch die Heilungszeit basierend auf deinem Krankenhaus-Level.',
            'After a battle, enter the number of wounded units.\n\nTravOps automatically calculates healing time based on your hospital level.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.hospital-list, [data-tour="hospital-list"], table',
          title: t('Übersicht für die Leitung', 'Overview for leadership'),
          body: t(
            'Die Allianz-Leitung sieht alle eingetragenen Verwundeten aller Mitglieder.\n\nSo lässt sich schnell einschätzen wer für den nächsten Einsatz verfügbar ist.',
            'Alliance leadership sees all entered casualties from all members.\n\nQuickly assess who is available for the next operation.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 9. MEIN ACCOUNT ─────────────────────────────────────────────────────
    'mein-account'(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Mein Account 🏘️', 'My Account 🏘️'),
          body: t(
            'Dein persönlicher Bereich in der Allianz.\n\nHier hinterlegst du deine Dörfer, Truppen und Kontaktdaten — sichtbar für die Allianz-Leitung.',
            'Your personal area in the alliance.\n\nAdd your villages, troops and contact info here — visible to alliance leadership.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.village-list, [data-tour="own-villages"], .own-villages',
          title: t('Deine Dörfer', 'Your villages'),
          body: t(
            'Hinterlege alle deine Dörfer mit Koordinaten und Typ.\n\nDie Daten werden für die Einsatzplanung genutzt — exakte Marschzeiten aus deinen Dörfern zu den Zielen.',
            'Add all your villages with coordinates and type.\n\nThe data is used for operation planning — exact march times from your villages to targets.'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.troop-form, [data-tour="troops"], .troops-section',
          title: t('Truppenbestand eintragen', 'Enter troop count'),
          body: t(
            'Trage deinen aktuellen Truppenbestand ein.\n\nDie Allianz-Leitung sieht die gesamte Kampfkraft aller Mitglieder und kann Einsätze realistisch planen.',
            'Enter your current troop count.\n\nAlliance leadership sees the total combat power of all members and can plan operations realistically.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

    // ── 10. MY ALLY ─────────────────────────────────────────────────────────
    'my-ally'(guildId) {
      return [
        {
          page: null, target: null,
          title: t('Allianz-Verwaltung 👥', 'Alliance Management 👥'),
          body: t(
            'Verwalte deine Allianz: Mitglieder, Rollen, Flügel und Discord-Zuordnung.\n\nAlle Mitglieder können hier eingeladen werden und ihren Account mit Discord verknüpfen.',
            'Manage your alliance: members, roles, wings and Discord mapping.\n\nAll members can be invited here and link their account with Discord.'
          ),
          next: t('Tour starten →', 'Start tour →'),
        },
        {
          page: null, target: '.member-table, [data-tour="members"], .ally-members',
          title: t('Mitglieder-Liste', 'Member list'),
          body: t(
            'Alle Allianz-Mitglieder mit Discord-Name, Travian-Name, Rolle und Flügel.\n\n✅ Grün = Discord verknüpft\n⚠️ Gelb = noch nicht verknüpft',
            'All alliance members with Discord name, Travian name, role and wing.\n\n✅ Green = Discord linked\n⚠️ Yellow = not yet linked'
          ),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: '.invite-link, [data-tour="invite-link"], input[readonly]',
          title: t('Einladungslink 🔗', 'Invitation link 🔗'),
          body: t(
            'Teile diesen Link mit neuen Mitgliedern.\n\nNach dem Klick loggen sie sich mit Discord ein und werden automatisch der Allianz zugeordnet.',
            'Share this link with new members.\n\nAfter clicking they log in with Discord and are automatically assigned to the alliance.'
          ),
          next: t('Verstanden ✓', 'Got it ✓'),
          isLast: true,
        },
      ];
    },

  };

  // ── Page → Tour mapping ────────────────────────────────────────────────────
  // Used by the ❓ button to auto-detect which tour fits the current page

  function detectTour(path, guildId) {
    if (!guildId) {
      if (path === '/profile') return 'my-account';
      return null;
    }
    const sub = path.replace(`/guild/${guildId}`, '').replace(/^\//, '');
    if (!sub || sub === '')             return 'start';
    if (sub.startsWith('operations'))  return 'operations';
    if (sub.startsWith('attacks'))     return 'attacks';
    if (sub.startsWith('defense/hero-scout')) return 'hero-scout';
    if (sub.startsWith('farming') || sub.startsWith('farmlist')) return 'farming';
    if (sub.startsWith('map') && !sub.includes('world-settings')) return 'map';
    if (sub.startsWith('allianz/hospital')) return 'hospital';
    if (sub.startsWith('mein-account')) return 'mein-account';
    if (sub.startsWith('my-ally'))     return 'my-ally';
    return null;
  }

  // ── State ──────────────────────────────────────────────────────────────────
  const STATE_KEY = 'travops_tour';
  const getState  = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY)); } catch { return null; } };
  const setState  = s  => s ? localStorage.setItem(STATE_KEY, JSON.stringify(s)) : localStorage.removeItem(STATE_KEY);
  const doneKey   = n  => `travops_tour_done_${n}`;
  const isDone    = n  => !!localStorage.getItem(doneKey(n));
  const markDone  = n  => localStorage.setItem(doneKey(n), '1');
  // Legacy compat
  const clearAll  = () => {
    localStorage.removeItem(STATE_KEY);
    localStorage.removeItem('beb_tour_done');
    localStorage.removeItem('beb_account_tour_done');
  };

  // ── UI ─────────────────────────────────────────────────────────────────────
  function buildUI() {
    ['beb-tour-overlay','beb-tour-spotlight','beb-tour-tooltip'].forEach(id => document.getElementById(id)?.remove());

    const overlay = document.createElement('div');
    overlay.id = 'beb-tour-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.78);backdrop-filter:blur(2px);transition:opacity .3s;';

    const spotlight = document.createElement('div');
    spotlight.id = 'beb-tour-spotlight';
    spotlight.style.cssText = 'position:fixed;z-index:10000;pointer-events:none;border-radius:10px;transition:all .35s cubic-bezier(.22,1,.36,1);box-shadow:0 0 0 9999px rgba(0,0,0,0.78);';

    const tooltip = document.createElement('div');
    tooltip.id = 'beb-tour-tooltip';
    tooltip.style.cssText = `
      position:fixed;z-index:10001;
      background:#1e293b;border:1px solid #334155;
      border-radius:16px;padding:1.4rem 1.5rem;max-width:390px;width:90vw;
      box-shadow:0 20px 60px rgba(0,0,0,.65);
      transition:opacity .25s,transform .25s;
    `;
    tooltip.innerHTML = `
      <div id="tt-badge"  style="font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#6366f1;margin-bottom:.4rem;display:none;"></div>
      <div id="tt-title"  style="font-size:1.08rem;font-weight:700;color:#f1f5f9;margin-bottom:.45rem;"></div>
      <div id="tt-body"   style="font-size:.875rem;color:#94a3b8;line-height:1.65;white-space:pre-line;margin-bottom:.7rem;"></div>
      <div id="tt-hint"   style="display:none;font-size:.78rem;background:rgba(99,102,241,.12);border:1px solid rgba(99,102,241,.25);border-radius:8px;padding:.4rem .7rem;color:#a5b4fc;margin-bottom:.8rem;"></div>
      <div id="tt-action" style="display:none;margin-bottom:.8rem;">
        <a id="tt-action-a" href="#" style="display:inline-flex;align-items:center;gap:.35rem;background:rgba(99,102,241,.18);border:1px solid rgba(99,102,241,.4);border-radius:8px;padding:.38rem .85rem;color:#a5b4fc;font-size:.82rem;text-decoration:none;font-weight:600;">
          🔗 <span id="tt-action-lbl"></span>
        </a>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem;">
        <div id="tt-dots" style="display:flex;gap:5px;"></div>
        <div style="display:flex;gap:.45rem;">
          <button id="tt-skip" style="background:none;border:1px solid #334155;border-radius:8px;padding:.38rem .85rem;color:#94a3b8;font-size:.82rem;cursor:pointer;">${t('Beenden','Exit')}</button>
          <button id="tt-next" style="background:linear-gradient(135deg,#6366f1,#4f46e5);border:none;border-radius:8px;padding:.38rem 1.1rem;color:#fff;font-size:.85rem;font-weight:700;cursor:pointer;white-space:nowrap;"></button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(tooltip);
    return { overlay, spotlight, tooltip };
  }

  // ── Render step ────────────────────────────────────────────────────────────
  function renderStep(step, idx, total, ui) {
    const { overlay, spotlight, tooltip } = ui;
    const targetEl = findTarget(step.target);

    tooltip.style.opacity = '0';
    setTimeout(() => {
      // Badge
      const badge = document.getElementById('tt-badge');
      if (idx > 0 && !step.isLast && total > 3) {
        badge.textContent = t(`Schritt ${idx} von ${total - 2}`, `Step ${idx} of ${total - 2}`);
        badge.style.display = 'block';
      } else { badge.style.display = 'none'; }

      document.getElementById('tt-title').textContent = step.title;
      document.getElementById('tt-body').textContent  = step.body;
      document.getElementById('tt-next').textContent  = step.next;

      const hint = document.getElementById('tt-hint');
      if (step.hint) { hint.textContent = step.hint; hint.style.display = 'block'; }
      else hint.style.display = 'none';

      const action = document.getElementById('tt-action');
      if (step.action) {
        document.getElementById('tt-action-lbl').textContent = step.action.label;
        document.getElementById('tt-action-a').href = step.action.url;
        action.style.display = 'block';
      } else action.style.display = 'none';

      buildDots(total, idx);
      positionSpotlight(targetEl, spotlight, overlay);
      positionTooltip(targetEl, tooltip);

      tooltip.style.opacity = '1';
      tooltip.style.transform = 'none';

      // Highlight ring
      clearHighlights();
      if (step.highlight) {
        const h = findTarget(step.highlight);
        if (h) { h.style.outline = '2px solid #6366f1'; h.style.outlineOffset = '3px'; h.dataset.tourHl = '1'; }
      }
      if (targetEl) targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 180);
  }

  function buildDots(total, current) {
    const c = document.getElementById('tt-dots');
    if (!c) return;
    c.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const d = document.createElement('div');
      d.style.cssText = `width:7px;height:7px;border-radius:50%;background:${i===current?'#6366f1':'#334155'};transition:background .2s;`;
      c.appendChild(d);
    }
  }

  function positionSpotlight(el, spotlight, overlay) {
    if (!el) {
      spotlight.style.cssText += 'width:0;height:0;top:-999px;left:-999px;box-shadow:none;';
      overlay.style.display = 'block';
      return;
    }
    const r = el.getBoundingClientRect(), p = 10;
    spotlight.style.top    = (r.top  - p) + 'px';
    spotlight.style.left   = (r.left - p) + 'px';
    spotlight.style.width  = (r.width  + p*2) + 'px';
    spotlight.style.height = (r.height + p*2) + 'px';
    spotlight.style.boxShadow = '0 0 0 9999px rgba(0,0,0,0.78)';
    overlay.style.display = 'none';
  }

  function positionTooltip(el, tooltip) {
    if (!el) {
      tooltip.style.top = '50%'; tooltip.style.left = '50%';
      tooltip.style.transform = 'translate(-50%,-50%)';
      return;
    }
    const r = el.getBoundingClientRect(), m = 16, h = 300;
    const top  = r.bottom + h + m < window.innerHeight ? r.bottom + m : Math.max(m, r.top - h - m);
    const left = Math.min(Math.max(m, r.left), window.innerWidth - 400);
    tooltip.style.top = top + 'px'; tooltip.style.left = left + 'px';
    tooltip.style.transform = 'none';
  }

  function findTarget(sel) {
    if (!sel) return null;
    for (const s of sel.split(',').map(s => s.trim())) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  }

  function clearHighlights() {
    document.querySelectorAll('[data-tour-hl]').forEach(el => {
      el.style.outline = ''; el.style.outlineOffset = ''; delete el.dataset.tourHl;
    });
  }

  function fadeOut(ui) {
    clearHighlights();
    [ui.overlay, ui.spotlight, ui.tooltip].forEach(el => {
      if (!el) return;
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    });
  }

  // ── Tour runner ────────────────────────────────────────────────────────────
  function runTour(name, startStep, guildId) {
    const factory = TOURS[name];
    if (!factory) return;
    const steps = factory(guildId);
    if (!steps.length) return;

    const ui = buildUI();
    let cur = startStep;

    function next() {
      clearHighlights();
      cur++;
      if (cur >= steps.length) {
        markDone(name);
        if (name === 'start') localStorage.setItem('beb_tour_done', '1');
        if (name === 'my-account') localStorage.setItem('beb_account_tour_done', '1');
        fadeOut(ui);
        return;
      }
      const step = steps[cur];
      if (step.page) {
        const target = new URL(step.page, location.origin).pathname;
        if (location.pathname !== target) {
          setState({ name, step: cur, guildId });
          fadeOut(ui);
          window.location.href = step.page;
          return;
        }
      }
      renderStep(step, cur, steps.length, ui);
    }

    document.getElementById('tt-next').addEventListener('click', next);
    document.getElementById('tt-skip').addEventListener('click', () => { markDone(name); fadeOut(ui); });
    ui.overlay.addEventListener('click', () => { markDone(name); fadeOut(ui); });

    setTimeout(() => renderStep(steps[cur], cur, steps.length, ui), 600);
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    const path = location.pathname;
    const guildMatch = path.match(/\/guild\/(\d{17,20})/);
    const guildId = guildMatch ? guildMatch[1] : null;

    // Resume after page navigation
    const state = getState();
    if (state) {
      const factory = TOURS[state.name];
      if (factory) {
        const steps = factory(state.guildId || guildId);
        const step  = steps[state.step];
        if (step) {
          const target = step.page ? new URL(step.page, location.origin).pathname : null;
          if (!target || location.pathname === target) {
            setState(null);
            runTour(state.name, state.step, state.guildId || guildId);
            return;
          }
        }
      }
      setState(null);
    }

    // Auto-start: first guild visit
    if (guildId && /^\/guild\/\d{17,20}\/?$/.test(path) && !isDone('start') && !localStorage.getItem('beb_tour_done')) {
      runTour('start', 0, guildId);
      return;
    }

    // Auto-start: profile page
    if (path === '/profile' && !isDone('my-account') && !localStorage.getItem('beb_account_tour_done')) {
      runTour('my-account', 0, null);
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.TravOpsTour = {
    start(name, guildId) {
      setState(null);
      runTour(name, 0, guildId || (location.pathname.match(/\/guild\/(\d{17,20})/) || [])[1]);
    },
    detect(path, guildId) { return detectTour(path, guildId); },
  };

  // navStartTour called by the ❓ button in base.html
  window.navStartTour = function () {
    const path = location.pathname;
    const gm   = path.match(/\/guild\/(\d{17,20})/);
    const gId  = gm ? gm[1] : null;
    const name = detectTour(path, gId);
    if (name) {
      TravOpsTour.start(name, gId);
    } else {
      // No tour for this page yet — offer start tour from dashboard
      if (gId) { TravOpsTour.start('start', gId); }
    }
  };

  // Profile restart buttons
  window.restartAccountTour = function () {
    localStorage.removeItem(doneKey('my-account'));
    localStorage.removeItem('beb_account_tour_done');
    TravOpsTour.start('my-account', null);
  };
  window.restartTourFull = function () {
    clearAll();
    Object.keys(TOURS).forEach(n => localStorage.removeItem(doneKey(n)));
    const gm = location.pathname.match(/\/guild\/(\d{17,20})/);
    window.location.href = gm ? `/guild/${gm[1]}` : '/dashboard';
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
