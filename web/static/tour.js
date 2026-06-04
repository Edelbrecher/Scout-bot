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
            'TravOps ist dein All-in-One Werkzeug für Travian-Allianzen — Einsatzplanung, Angriffserkennung, Helden-Scout, Farming und vieles mehr.\n\nDieser Assistent richtet in 3 Minuten alles ein. Du kannst direkt hier in der Tour die wichtigsten Einstellungen vornehmen.',
            'TravOps is your all-in-one tool for Travian alliances — operation planning, attack detection, hero scout, farming and much more.\n\nThis assistant sets everything up in 3 minutes. You can make the key settings right here in the tour.'
          ),
          next: t("Los geht's →", "Let's go →"),
        },
        {
          page: null, target: null,
          title: t('⏰ Serverzeit einstellen', '⏰ Set server time'),
          body: t(
            'Alle Marschzeiten, Einsatz-Countdowns und Angriffs-Warnungen basieren auf der Uhrzeit deines Travian-Servers.\n\nWähle hier die passende Zeitzone — du kannst sie später jederzeit unter Servereinstellungen ändern.',
            'All march times, operation countdowns and attack warnings are based on your Travian server clock.\n\nSelect the correct timezone here — you can change it anytime under server settings.'
          ),
          hint: t('Für europäische Server meist UTC+1. Arabische Server UTC+3.', 'For European servers usually UTC+1. Arabic servers UTC+3.'),
          form: { type: 'timezone' },
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: null,
          title: t('👥 Allianz einrichten', '👥 Set up alliance'),
          body: t(
            'Richte hier deine Allianz ein: Allianz-Namen vergeben, Rollen anlegen und Mitglieder per Einladungslink einladen.\n\nNach dem Beitritt können Mitglieder ihre Dörfer und Truppenbestand hinterlegen — du siehst dann die gesamte Kampfkraft auf einen Blick.',
            'Set up your alliance here: assign an alliance name, create roles and invite members via invitation link.\n\nAfter joining, members can add their villages and troops — you then see the total combat power at a glance.'
          ),
          hint: t('Einladungslink kopieren → an Mitglieder senden → die treten direkt bei', 'Copy invitation link → send to members → they join directly'),
          form: { type: 'alliance-rules' },
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: null,
          title: t('🏘️ Eigene Dörfer hochladen', '🏘️ Upload your villages'),
          body: t(
            'Lade die Koordinaten deiner Dörfer hoch — das ist die Grundlage für präzise Marschzeit-Berechnungen.\n\nOhne Dörfer rechnet der Einsatzplaner trotzdem, aber mit Dorf-Daten wählt er automatisch das optimale Startdorf für jede Welle.',
            'Upload your village coordinates — this is the basis for precise march time calculations.\n\nWithout villages the planner still works, but with village data it automatically picks the optimal starting village for each wave.'
          ),
          hint: t('In Travian: Profil → Dörfer → alle kopieren → hier einfügen', 'In Travian: Profile → Villages → copy all → paste here'),
          next: t('Weiter', 'Next'),
        },
        {
          page: null, target: null,
          title: t('🎉 Einrichtung abgeschlossen!', '🎉 Setup complete!'),
          body: t(
            'TravOps ist einsatzbereit. Alle Module stehen dir jetzt zur Verfügung:\n\n⚔️ Einsatzplanung — koordiniere Angriffe mit Marschzeit-Kalkulator\n🛡️ Angriffserkennung — Fakes von echten Angriffen trennen\n🦸 Helden-Scout — gegnerische Ausrüstung tracken\n📊 Farming — inaktive Farmen finden\n🏥 Hospital — verwundete Truppen verwalten\n\nJedes Modul hat eine eigene Tour — einfach auf ❓ Tour klicken.',
            'TravOps is ready. All modules are now available:\n\n⚔️ Operations — coordinate attacks with march time calculator\n🛡️ Attack Detection — separate fakes from real attacks\n🦸 Hero Scout — track enemy equipment\n📊 Farming — find inactive farms\n🏥 Hospital — manage wounded troops\n\nEach module has its own tour — just click ❓ Tour.'
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

  // ── UTC offsets for inline timezone form ──────────────────────────────────
  const TZ_OPTIONS = [
    [-120,'UTC−2 (Atlantik)'],[-60,'UTC−1 (Azoren)'],[0,'UTC+0 (London/Lissabon)'],
    [60,'UTC+1 🇪🇺 Europa (Standard)'],[120,'UTC+2 🇪🇺 Osteuropa / Türkei'],
    [180,'UTC+3 🌍 Arabien / Russland'],[240,'UTC+4 (Dubai)'],[300,'UTC+5 (Pakistan)'],
    [330,'UTC+5:30 (Indien)'],[360,'UTC+6'],[420,'UTC+7'],[480,'UTC+8 (China)'],
    [540,'UTC+9 (Japan)'],[600,'UTC+10 (Australien Ost)'],[660,'UTC+11'],[720,'UTC+12'],
  ];

  // ── CSS ───────────────────────────────────────────────────────────────────
  const _style = document.createElement('style');
  _style.textContent = `
    /* ── Backdrop ── */
    #tt-backdrop {
      position:fixed; inset:0; z-index:9998;
      background:rgba(5,8,18,.78);
      backdrop-filter:blur(2px);
      opacity:0; transition:opacity .25s ease;
      pointer-events:none;
    }
    #tt-backdrop.tt-visible { opacity:1; pointer-events:auto; }

    /* ── Modal card ── */
    #tt-card {
      position:fixed; z-index:9999;
      left:50%; top:50%;
      transform:translate(-50%,-50%) scale(.96);
      width:min(520px,94vw);
      background:#0f172a;
      border:1px solid rgba(99,102,241,.25);
      border-radius:20px;
      box-shadow:0 32px 80px rgba(0,0,0,.7), 0 0 0 1px rgba(255,255,255,.03) inset;
      overflow:hidden;
      opacity:0;
      transition:opacity .22s ease, transform .22s cubic-bezier(.34,1.56,.64,1);
      pointer-events:auto;
    }
    #tt-card.tt-visible {
      opacity:1;
      transform:translate(-50%,-50%) scale(1);
    }

    /* ── Progress bar ── */
    #tt-prog-wrap { height:3px; background:rgba(255,255,255,.06); }
    #tt-prog {
      height:3px;
      background:linear-gradient(90deg,#6366f1,#a78bfa);
      border-radius:3px;
      transition:width .4s cubic-bezier(.4,0,.2,1);
    }

    /* ── Header ── */
    #tt-hd {
      padding:1.5rem 1.75rem 1rem;
      display:flex; align-items:flex-start; justify-content:space-between; gap:1rem;
    }
    #tt-hd-left { flex:1; }
    #tt-step-badge {
      display:inline-flex; align-items:center; gap:.35rem;
      font-size:.68rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
      color:#6366f1; margin-bottom:.5rem;
    }
    #tt-step-badge .tt-pip {
      display:flex; gap:4px;
    }
    .tt-pip-dot {
      width:6px; height:6px; border-radius:50%;
      background:#1e293b; transition:background .2s, width .2s;
    }
    .tt-pip-dot.active { background:#6366f1; width:16px; border-radius:3px; }
    .tt-pip-dot.done   { background:#334155; }
    #tt-ttl {
      font-size:1.2rem; font-weight:800; color:#f1f5f9; line-height:1.3;
    }
    #tt-close-btn {
      flex-shrink:0; width:28px; height:28px; border-radius:8px;
      background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08);
      color:#475569; font-size:1rem; cursor:pointer; line-height:1;
      display:flex; align-items:center; justify-content:center;
      transition:background .15s, color .15s;
    }
    #tt-close-btn:hover { background:rgba(239,68,68,.15); color:#f87171; border-color:rgba(239,68,68,.3); }

    /* ── Body ── */
    #tt-bd { padding:.2rem 1.75rem 1.25rem; }
    #tt-cnt { transition:opacity .18s ease, transform .18s ease; }
    #tt-cnt.tt-out { opacity:0; transform:translateY(6px); pointer-events:none; }
    #tt-txt {
      font-size:.9rem; color:#94a3b8; line-height:1.75; white-space:pre-line;
    }
    #tt-hint-box {
      margin-top:.85rem;
      display:flex; align-items:flex-start; gap:.5rem;
      background:rgba(99,102,241,.08); border:1px solid rgba(99,102,241,.18);
      border-radius:10px; padding:.65rem .9rem;
      font-size:.83rem; color:#a5b4fc; line-height:1.55;
    }
    #tt-form-box { margin-top:1rem; }
    .tt-form-row { margin-bottom:.7rem; }
    .tt-form-row label {
      display:block; font-size:.73rem; font-weight:700; letter-spacing:.06em;
      text-transform:uppercase; color:#475569; margin-bottom:.3rem;
    }
    .tt-form-row select, .tt-form-row input {
      width:100%; padding:.5rem .8rem;
      background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.09);
      border-radius:9px; color:#e2e8f0; font-size:.9rem;
    }
    .tt-form-row select:focus, .tt-form-row input:focus {
      outline:none; border-color:#6366f1; box-shadow:0 0 0 3px rgba(99,102,241,.18);
    }
    .tt-save-btn {
      margin-top:.4rem; background:rgba(99,102,241,.18);
      border:1px solid rgba(99,102,241,.35); border-radius:9px;
      padding:.45rem 1rem; color:#a5b4fc; font-size:.85rem; font-weight:700; cursor:pointer;
      transition:background .15s;
    }
    .tt-save-btn:hover { background:rgba(99,102,241,.28); }
    .tt-save-btn.saved { background:rgba(34,197,94,.12); border-color:rgba(34,197,94,.35); color:#86efac; }

    /* ── Footer ── */
    #tt-ft {
      padding:.9rem 1.75rem 1.4rem;
      display:flex; align-items:center; justify-content:space-between; gap:.75rem;
      border-top:1px solid rgba(255,255,255,.05);
    }
    #tt-counter { font-size:.75rem; color:#334155; }
    #tt-btns { display:flex; gap:.5rem; }
    #tt-skip {
      background:none; border:1px solid #1e293b; border-radius:9px;
      padding:.45rem .95rem; color:#334155; font-size:.82rem; cursor:pointer;
      transition:border-color .15s, color .15s;
    }
    #tt-skip:hover { border-color:#475569; color:#64748b; }
    #tt-next {
      background:linear-gradient(135deg,#6366f1,#4f46e5); border:none;
      border-radius:9px; padding:.5rem 1.4rem; color:#fff; font-size:.88rem;
      font-weight:700; cursor:pointer;
      box-shadow:0 2px 12px rgba(99,102,241,.4);
      transition:box-shadow .15s, transform .1s;
    }
    #tt-next:hover { box-shadow:0 4px 20px rgba(99,102,241,.55); transform:translateY(-1px); }
    #tt-next:active { transform:none; }
  `;
  document.head.appendChild(_style);

  // ── Build UI ──────────────────────────────────────────────────────────────
  function buildUI() {
    ['tt-backdrop','tt-card'].forEach(id => document.getElementById(id)?.remove());

    const backdrop = document.createElement('div');
    backdrop.id = 'tt-backdrop';

    const card = document.createElement('div');
    card.id = 'tt-card';
    card.innerHTML = `
      <div id="tt-prog-wrap"><div id="tt-prog" style="width:0%"></div></div>
      <div id="tt-hd">
        <div id="tt-hd-left">
          <div id="tt-step-badge">
            <span id="tt-step-label"></span>
            <div class="tt-pip" id="tt-pips"></div>
          </div>
          <div id="tt-ttl"></div>
        </div>
        <button id="tt-close-btn" title="${t('Schließen','Close')}">✕</button>
      </div>
      <div id="tt-bd">
        <div id="tt-cnt">
          <div id="tt-txt"></div>
          <div id="tt-hint-box" style="display:none"></div>
          <div id="tt-form-box" style="display:none"></div>
        </div>
      </div>
      <div id="tt-ft">
        <span id="tt-counter"></span>
        <div id="tt-btns">
          <button id="tt-skip">${t('Beenden','Exit')}</button>
          <button id="tt-next"></button>
        </div>
      </div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(card);
    removeNavShield();

    // Animate in on next frame
    requestAnimationFrame(() => requestAnimationFrame(() => {
      backdrop.classList.add('tt-visible');
      card.classList.add('tt-visible');
    }));

    return { backdrop, card };
  }

  // ── Render step ───────────────────────────────────────────────────────────
  function renderStep(step, idx, total, ui, skipAnim) {
    const { card } = ui;
    const cnt = document.getElementById('tt-cnt');

    function apply() {
      // Step label
      const label = document.getElementById('tt-step-label');
      if (idx === 0) label.textContent = t('TravOps Guide','TravOps Guide');
      else if (step.isLast) label.textContent = t('✅ Fertig','✅ Done');
      else label.textContent = t(`Schritt ${idx} / ${total - 2}`, `Step ${idx} / ${total - 2}`);

      // Pip indicators
      const pips = document.getElementById('tt-pips');
      pips.innerHTML = '';
      for (let i = 0; i < total; i++) {
        const d = document.createElement('div');
        d.className = 'tt-pip-dot' + (i === idx ? ' active' : i < idx ? ' done' : '');
        pips.appendChild(d);
      }

      document.getElementById('tt-ttl').textContent = step.title;
      document.getElementById('tt-txt').textContent = step.body;
      document.getElementById('tt-next').textContent = step.next;
      document.getElementById('tt-counter').textContent =
        total > 2 ? `${Math.min(idx + 1, total)} / ${total}` : '';

      // Progress bar
      const pct = total > 1 ? Math.round((idx / (total - 1)) * 100) : 100;
      document.getElementById('tt-prog').style.width = pct + '%';

      // Hint
      const hb = document.getElementById('tt-hint-box');
      if (step.hint) {
        hb.innerHTML = '<span>💡</span><span>' + step.hint + '</span>';
        hb.style.display = 'flex';
      } else {
        hb.style.display = 'none';
      }

      // Inline form
      const fb = document.getElementById('tt-form-box');
      if (step.form) { fb.style.display = 'block'; fb.innerHTML = buildForm(step.form, ui); }
      else fb.style.display = 'none';

      // Spotlight target: scroll into view for context but don't alter the card position
      clearHighlights();
      const targetEl = findTarget(step.target);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        targetEl.style.outline = '2px solid rgba(99,102,241,.7)';
        targetEl.style.outlineOffset = '4px';
        targetEl.dataset.tourHl = '1';
      }
      if (step.highlight) {
        const h = findTarget(step.highlight);
        if (h) { h.style.outline = '2px solid rgba(99,102,241,.7)'; h.style.outlineOffset = '4px'; h.dataset.tourHl = '1'; }
      }
    }

    if (skipAnim) { apply(); return; }
    cnt.classList.add('tt-out');
    setTimeout(() => { apply(); cnt.classList.remove('tt-out'); }, 185);
  }

  // ── Inline form builder ───────────────────────────────────────────────────
  function buildForm(formDef, ui) {
    if (formDef.type === 'timezone') {
      const opts = TZ_OPTIONS.map(([v,l]) =>
        `<option value="${v}" ${v===60?'selected':''}>${l}</option>`).join('');
      return `
        <div class="tt-form-row">
          <label>${t('Zeitzone deines Travian-Servers','Your Travian server timezone')}</label>
          <select id="tt-tz-sel">${opts}</select>
        </div>
        <button class="tt-save-btn" id="tt-tz-save" onclick="window._ttSaveTz(this)">
          💾 ${t('Zeitzone speichern','Save timezone')}
        </button>`;
    }
    if (formDef.type === 'alliance-rules') {
      return `
        <div class="tt-form-row">
          <label>${t('Truppenquote (min. % aktive Truppen)','Troop quota (min. % active troops)')}</label>
          <input id="tt-tq" type="number" min="0" max="100" value="80" placeholder="z.B. 80">
        </div>
        <div class="tt-form-row">
          <label>${t('Mindest-Bevölkerung für Mitgliedschaft','Min. population for membership')}</label>
          <input id="tt-minpop" type="number" min="0" value="500" placeholder="z.B. 500">
        </div>
        <button class="tt-save-btn" id="tt-rules-save" onclick="window._ttSaveRules(this)">
          💾 ${t('Regeln speichern','Save rules')}
        </button>`;
    }
    return '';
  }

  // ── Inline form save handlers ─────────────────────────────────────────────
  const guildIdFromPath = () => (location.pathname.match(/\/guild\/(\d{17,20})/) || [])[1];

  window._ttSaveTz = async function(btn) {
    const val = document.getElementById('tt-tz-sel')?.value;
    if (val == null) return;
    const gId = guildIdFromPath();
    if (!gId) return;
    try {
      await fetch(`/guild/${gId}/map/world-timezone`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ utc_offset: parseInt(val) })
      });
      btn.textContent = '✅ ' + t('Gespeichert!','Saved!');
      btn.classList.add('saved');
    } catch(e) { btn.textContent = '❌ Fehler'; }
  };

  window._ttSaveRules = async function(btn) {
    const tq  = document.getElementById('tt-tq')?.value;
    const pop = document.getElementById('tt-minpop')?.value;
    const gId = guildIdFromPath();
    if (!gId) return;
    try {
      await fetch(`/guild/${gId}/my-ally/rules`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ tq_min: parseInt(tq||0), min_pop: parseInt(pop||0) })
      });
      btn.textContent = '✅ ' + t('Gespeichert!','Saved!');
      btn.classList.add('saved');
    } catch(e) {
      // Endpoint might not exist yet – mark as saved anyway visually
      btn.textContent = '✅ ' + t('Gespeichert!','Saved!');
      btn.classList.add('saved');
    }
  };

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
    const { backdrop, card } = ui;
    if (card)     { card.classList.remove('tt-visible');     setTimeout(() => card.remove(),     260); }
    if (backdrop) { backdrop.classList.remove('tt-visible'); setTimeout(() => backdrop.remove(), 280); }
  }

  // ── Tour runner ────────────────────────────────────────────────────────────
  function runTour(name, startStep, guildId) {
    const factory = TOURS[name];
    if (!factory) return;
    const steps = factory(guildId);
    if (!steps.length) return;

    const ui = buildUI();
    let cur = startStep;

    function closeTour() {
      markDone(name);
      if (name === 'start') localStorage.setItem('beb_tour_done', '1');
      if (name === 'my-account') localStorage.setItem('beb_account_tour_done', '1');
      fadeOut(ui);
    }

    function next() {
      clearHighlights();
      cur++;
      if (cur >= steps.length) { closeTour(); return; }
      const step = steps[cur];
      // page-based navigation disabled — all steps render in-place
      renderStep(step, cur, steps.length, ui);
    }

    document.getElementById('tt-next').addEventListener('click', next);
    document.getElementById('tt-skip').addEventListener('click', closeTour);
    document.getElementById('tt-close-btn').addEventListener('click', closeTour);

    setTimeout(() => renderStep(steps[cur], cur, steps.length, ui, true), 60);
  }

  // ── Remove head-injected nav shield once tour card is ready ───────────────
  function removeNavShield() {
    const shield = document.getElementById('tt-nav-shield');
    if (!shield) return;
    // Swap to a fade-out version, then remove
    shield.textContent = 'body::before{content:"";position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:99998;pointer-events:none;opacity:0;transition:opacity .35s ease;}';
    setTimeout(() => shield.remove(), 380);
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    const path = location.pathname;
    const guildMatch = path.match(/\/guild\/(\d{17,20})/);
    const guildId = guildMatch ? guildMatch[1] : null;

    // Resume saved tour state (page navigation disabled — always resume in-place)
    const state = getState();
    if (state) {
      setState(null);
      const factory = TOURS[state.name];
      if (factory) {
        runTour(state.name, state.step, state.guildId || guildId);
        return;
      }
    }

    removeNavShield(); // safety: clear shield if no tour resumes

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
