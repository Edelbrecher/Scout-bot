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

  // ── CSS injekt ────────────────────────────────────────────────────────────
  const _style = document.createElement('style');
  _style.textContent = `
    #tt-card { position:fixed; z-index:10001; width:min(560px,94vw);
      background:linear-gradient(160deg,#0f172a 0%,#1e293b 100%);
      border:1px solid rgba(99,102,241,.35); border-radius:20px;
      box-shadow:0 32px 80px rgba(0,0,0,.7), 0 0 0 1px rgba(99,102,241,.1);
      overflow:hidden; }
    #tt-card-inner { padding:0; }
    #tt-header { padding:1.6rem 1.8rem 1.1rem;
      border-bottom:1px solid rgba(255,255,255,.06); }
    #tt-progress-bar-wrap { height:3px; background:rgba(255,255,255,.06); }
    #tt-progress-bar { height:3px; background:linear-gradient(90deg,#6366f1,#818cf8);
      transition:width .4s cubic-bezier(.4,0,.2,1); border-radius:3px; }
    #tt-step-label { font-size:.7rem; font-weight:700; letter-spacing:.09em;
      text-transform:uppercase; color:#6366f1; margin-bottom:.5rem; }
    #tt-title { font-size:1.25rem; font-weight:800; color:#f8fafc;
      line-height:1.3; margin-bottom:.1rem; }
    #tt-body-wrap { padding:1.1rem 1.8rem; }
    #tt-body { font-size:.95rem; color:#94a3b8; line-height:1.7;
      white-space:pre-line; }
    #tt-hint { margin-top:.85rem; display:flex; align-items:flex-start; gap:.5rem;
      background:rgba(99,102,241,.1); border:1px solid rgba(99,102,241,.25);
      border-radius:10px; padding:.65rem .9rem; font-size:.84rem; color:#a5b4fc; }
    #tt-footer { padding:.9rem 1.8rem 1.4rem;
      border-top:1px solid rgba(255,255,255,.06);
      display:flex; align-items:center; justify-content:space-between; gap:.75rem; }
    #tt-dots { display:flex; gap:6px; align-items:center; }
    .tt-dot { width:8px; height:8px; border-radius:50%;
      background:#1e3a5f; transition:background .25s, transform .25s; }
    .tt-dot.active { background:#6366f1; transform:scale(1.3); }
    .tt-dot.done   { background:#334155; }
    #tt-btns { display:flex; gap:.55rem; }
    #tt-skip { background:none; border:1px solid #334155; border-radius:10px;
      padding:.5rem 1rem; color:#64748b; font-size:.84rem; cursor:pointer;
      transition:border-color .15s, color .15s; }
    #tt-skip:hover { border-color:#64748b; color:#94a3b8; }
    #tt-next { background:linear-gradient(135deg,#6366f1,#4338ca);
      border:none; border-radius:10px; padding:.55rem 1.4rem;
      color:#fff; font-size:.9rem; font-weight:700; cursor:pointer;
      box-shadow:0 4px 14px rgba(99,102,241,.4);
      transition:box-shadow .15s, transform .1s; white-space:nowrap; }
    #tt-next:hover { box-shadow:0 6px 20px rgba(99,102,241,.55); transform:translateY(-1px); }
    #tt-next:active { transform:translateY(0); }

    /* Content fade – inner content fades independently, card stays put */
    #tt-content { transition:opacity .18s ease, transform .18s ease; }
    #tt-content.fading { opacity:0; transform:translateY(6px); }

    /* Spotlight */
    #beb-tour-spotlight { position:fixed; z-index:10000; pointer-events:none;
      border-radius:12px;
      transition:top .38s cubic-bezier(.4,0,.2,1),
                 left .38s cubic-bezier(.4,0,.2,1),
                 width .38s cubic-bezier(.4,0,.2,1),
                 height .38s cubic-bezier(.4,0,.2,1),
                 box-shadow .3s; }
    #beb-tour-overlay { position:fixed; inset:0; z-index:9999;
      background:rgba(0,0,0,.78); backdrop-filter:blur(1.5px);
      transition:opacity .3s; }
  `;
  document.head.appendChild(_style);

  // ── UI ─────────────────────────────────────────────────────────────────────
  function buildUI() {
    ['beb-tour-overlay','beb-tour-spotlight','tt-card'].forEach(id => document.getElementById(id)?.remove());

    const overlay = document.createElement('div');
    overlay.id = 'beb-tour-overlay';

    const spotlight = document.createElement('div');
    spotlight.id = 'beb-tour-spotlight';

    const card = document.createElement('div');
    card.id = 'tt-card';
    card.innerHTML = `
      <div id="tt-progress-bar-wrap"><div id="tt-progress-bar" style="width:0%"></div></div>
      <div id="tt-card-inner">
        <div id="tt-header">
          <div id="tt-step-label"></div>
          <div id="tt-title"></div>
        </div>
        <div id="tt-body-wrap">
          <div id="tt-content">
            <div id="tt-body"></div>
            <div id="tt-hint" style="display:none"></div>
          </div>
        </div>
        <div id="tt-footer">
          <div id="tt-dots"></div>
          <div id="tt-btns">
            <button id="tt-skip">${t('Tour beenden','Exit tour')}</button>
            <button id="tt-next"></button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(card);

    // Initial position: centered
    card.style.cssText += 'top:50%;left:50%;transform:translate(-50%,-50%);opacity:0;transition:opacity .3s;';
    requestAnimationFrame(() => { card.style.opacity = '1'; });

    return { overlay, spotlight, card };
  }

  // ── Render step (smooth: only content fades, card slides) ─────────────────
  function renderStep(step, idx, total, ui, instant) {
    const { overlay, spotlight, card } = ui;
    const targetEl = findTarget(step.target);
    const content  = document.getElementById('tt-content');

    function applyContent() {
      // Step label
      const lbl = document.getElementById('tt-step-label');
      if (idx === 0) lbl.textContent = t('Einführung','Introduction');
      else if (step.isLast) lbl.textContent = t('Fertig 🎉','Done 🎉');
      else lbl.textContent = t(`Schritt ${idx} von ${total-2}`, `Step ${idx} of ${total-2}`);

      document.getElementById('tt-title').textContent = step.title;
      document.getElementById('tt-body').textContent  = step.body;
      document.getElementById('tt-next').textContent  = step.next;

      const hint = document.getElementById('tt-hint');
      if (step.hint) { hint.innerHTML = '💡 ' + step.hint; hint.style.display = 'flex'; }
      else hint.style.display = 'none';

      // Progress bar
      const pct = total > 1 ? Math.round((idx / (total-1)) * 100) : 100;
      document.getElementById('tt-progress-bar').style.width = pct + '%';

      // Dots
      const dotsEl = document.getElementById('tt-dots');
      dotsEl.innerHTML = '';
      for (let i = 0; i < total; i++) {
        const d = document.createElement('div');
        d.className = 'tt-dot' + (i === idx ? ' active' : i < idx ? ' done' : '');
        dotsEl.appendChild(d);
      }

      // Spotlight + card position
      positionSpotlight(targetEl, spotlight, overlay);
      positionCard(targetEl, card);

      // Highlight ring
      clearHighlights();
      if (step.highlight) {
        const h = findTarget(step.highlight);
        if (h) { h.style.outline = '2px solid #6366f1'; h.style.outlineOffset = '4px'; h.dataset.tourHl = '1'; }
      }
      if (targetEl) targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    if (instant) {
      applyContent();
      return;
    }

    // Fade content out → apply → fade in (card stays, no flicker)
    content.classList.add('fading');
    setTimeout(() => {
      applyContent();
      content.classList.remove('fading');
    }, 190);
  }

  function positionCard(el, card) {
    card.style.transition = 'top .38s cubic-bezier(.4,0,.2,1), left .38s cubic-bezier(.4,0,.2,1), transform .38s cubic-bezier(.4,0,.2,1), opacity .3s';
    if (!el) {
      card.style.top = '50%'; card.style.left = '50%';
      card.style.transform = 'translate(-50%,-50%)';
      return;
    }
    const r = el.getBoundingClientRect();
    const cw = Math.min(560, window.innerWidth * 0.94);
    const ch = 340; // approx card height
    const m  = 18;

    // Prefer right side, then below, then above, then left
    let top, left, transform = 'none';

    if (r.right + cw + m < window.innerWidth) {
      // Right
      left = r.right + m;
      top  = Math.min(Math.max(m, r.top + r.height/2 - ch/2), window.innerHeight - ch - m);
    } else if (r.bottom + ch + m < window.innerHeight) {
      // Below
      top  = r.bottom + m;
      left = Math.min(Math.max(m, r.left + r.width/2 - cw/2), window.innerWidth - cw - m);
    } else if (r.top - ch - m > 0) {
      // Above
      top  = r.top - ch - m;
      left = Math.min(Math.max(m, r.left + r.width/2 - cw/2), window.innerWidth - cw - m);
    } else {
      // Centered fallback
      card.style.top = '50%'; card.style.left = '50%';
      card.style.transform = 'translate(-50%,-50%)';
      return;
    }

    card.style.top  = top  + 'px';
    card.style.left = left + 'px';
    card.style.transform = transform;
  }

  function positionSpotlight(el, spotlight, overlay) {
    if (!el) {
      spotlight.style.boxShadow = 'none';
      spotlight.style.width  = '0px';
      spotlight.style.height = '0px';
      spotlight.style.top    = '-999px';
      spotlight.style.left   = '-999px';
      overlay.style.display = 'block';
      return;
    }
    const r = el.getBoundingClientRect(), p = 10;
    spotlight.style.top    = (r.top  - p) + 'px';
    spotlight.style.left   = (r.left - p) + 'px';
    spotlight.style.width  = (r.width  + p*2) + 'px';
    spotlight.style.height = (r.height + p*2) + 'px';
    spotlight.style.boxShadow = '0 0 0 9999px rgba(0,0,0,.78), 0 0 0 2px rgba(99,102,241,.5)';
    overlay.style.display = 'none';
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
    [ui.overlay, ui.spotlight, ui.card].forEach(el => {
      if (!el) return;
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 320);
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

    setTimeout(() => renderStep(steps[cur], cur, steps.length, ui, true), 400);
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
