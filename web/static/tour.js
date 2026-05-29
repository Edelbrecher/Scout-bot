/**
 * TravOps Guided Tour System
 * Multi-page, interactive onboarding & feature tours.
 *
 * Tours:
 *   'start'      – First-time setup wizard (runs on first guild visit)
 *   'my-account' – Profile page tour
 *
 * State is kept in localStorage so tours survive page navigation.
 * Key: 'travops_tour'  → JSON: { name, step, guildId }
 */
(function () {
  'use strict';

  // ── i18n helper ──────────────────────────────────────────────────────────
  const lang = localStorage.getItem('beb_lang') || 'de';
  const t = (de, en) => lang === 'en' ? en : de;

  // ── Tour definitions ─────────────────────────────────────────────────────

  function startTourSteps(guildId) {
    const base = `/guild/${guildId}`;
    return [
      // 0 – Willkommen
      {
        page: null,          // any guild page
        target: null,
        title: t('Willkommen bei TravOps! 👋', 'Welcome to TravOps! 👋'),
        body: t(
          'Wir richten in 2 Minuten alles ein, damit du sofort loslegen kannst.\n\nDieser Assistent führt dich durch die wichtigsten Einstellungen.',
          'We\'ll set everything up in 2 minutes so you can get started right away.\n\nThis assistant guides you through the key settings.'
        ),
        next: t("Los geht's →", "Let's go →"),
        size: 'lg',
      },
      // 1 – Serverzeit einstellen (navigiert zu world-settings)
      {
        page: `${base}/map/world-settings`,
        target: null,
        title: t('1 / 4 · Serverzeit einstellen ⏰', '1 / 4 · Set Server Time ⏰'),
        body: t(
          'Wähle rechts die Zeitzone deines Travian-Servers.\n\n🇪🇺 Europäische Server → meist UTC+1\n🌍 Arabische Server → UTC+3\n\nAlle Einsatzzeiten und Countdowns basieren darauf.',
          'Select your Travian server\'s timezone on the right.\n\n🇪🇺 European servers → usually UTC+1\n🌍 Arabic servers → UTC+3\n\nAll operation times and countdowns depend on this.'
        ),
        next: t('Weiter nach Einstellung →', 'Continue after setting →'),
        action: {
          label: t('Zur Serverzeit-Einstellung', 'Go to server time settings'),
          url: `${base}/map/world-settings`,
        },
        highlight: '#timezone-select, [name="utc_offset"], select[name*="tz"], .timezone-form',
        hint: t('✅ Zeitzone auswählen & speichern, dann "Weiter" klicken', '✅ Select timezone & save, then click "Next"'),
      },
      // 2 – Allianz anlegen (navigiert zu ally setup)
      {
        page: `${base}/settings/ally-setup`,
        target: null,
        title: t('2 / 4 · Allianz einrichten 👥', '2 / 4 · Set up Alliance 👥'),
        body: t(
          'Trage deinen Allianz-Namen ein und lade Mitglieder per Einladungslink ein.\n\nSo können alle Mitspieler ihre Dörfer, Truppen und Einsätze verwalten.',
          'Enter your alliance name and invite members via invitation link.\n\nThis allows all players to manage their villages, troops and operations.'
        ),
        next: t('Weiter', 'Next'),
        highlight: '[name="ally_name"], .ally-name-input, input[placeholder*="Allianz"]',
        hint: t('✅ Allianz-Name eingeben & speichern', '✅ Enter alliance name & save'),
      },
      // 3 – Eigene Dörfer hochladen (navigiert zu own-villages)
      {
        page: `${base}/map/own-villages`,
        target: null,
        title: t('3 / 4 · Eigene Dörfer hochladen 🏘️', '3 / 4 · Upload your villages 🏘️'),
        body: t(
          'Lade deine Dorf-Daten aus Travian hoch — das ermöglicht präzise Marschzeit-Berechnungen für den Einsatzplaner.\n\nKlicke auf "Dörfer hinzufügen" und füge die Daten ein.',
          'Upload your village data from Travian — this enables precise march time calculations for the operation planner.\n\nClick "Add villages" and paste the data.'
        ),
        next: t('Weiter', 'Next'),
        highlight: '.upload-btn, [data-tour="upload"], button[onclick*="upload"], .add-village-btn',
        hint: t('✅ Optional, aber empfohlen für genaue Marschzeiten', '✅ Optional but recommended for accurate march times'),
      },
      // 4 – Features Übersicht (zurück zum Dashboard)
      {
        page: base,
        target: '.feature-grid, .guild-features, .features-section, main',
        title: t('4 / 4 · Alles auf einen Blick 🗺️', '4 / 4 · Everything at a glance 🗺️'),
        body: t(
          'Von hier aus erreichst du alle Module:\n\n⚔️ Einsatzplanung · 🛡️ Angriffserkennung\n🦸 Helden-Scout · 📊 Farming-Analyse\n🏥 Hospital · 👥 Allianz-Verwaltung\n\nJedes Modul hat seine eigene Tour — klicke dazu auf das ❓ in der jeweiligen Ansicht.',
          'From here you access all modules:\n\n⚔️ Operations · 🛡️ Attack Detection\n🦸 Hero Scout · 📊 Farming Analysis\n🏥 Hospital · 👥 Alliance Management\n\nEach module has its own tour — click ❓ in the respective view.'
        ),
        next: t('Tour abschließen 🎉', 'Finish tour 🎉'),
        isLast: true,
      },
    ];
  }

  function myAccountSteps() {
    return [
      // 0 – Einleitung
      {
        page: null,
        target: null,
        title: t('Mein Profil · Tour 👤', 'My Profile · Tour 👤'),
        body: t(
          'Diese Seite zeigt deine persönlichen TravOps-Daten.\n\nDu lernst hier:\n· TravOps-Points sammeln & einlösen\n· Deinen Einladungslink teilen\n· Pro-Zugang verlängern',
          'This page shows your personal TravOps data.\n\nYou\'ll learn:\n· Collecting & redeeming TravOps points\n· Sharing your invitation link\n· Extending Pro access'
        ),
        next: t('Tour starten →', 'Start tour →'),
      },
      // 1 – Points-Anzeige
      {
        page: null,
        target: '.card:first-of-type, [data-tour="points"]',
        title: t('TravOps-Points 🌟', 'TravOps Points 🌟'),
        body: t(
          'Hier siehst du dein aktuelles Points-Guthaben.\n\nMit 10 Points bekommst du 1 Monat Pro kostenlos — also ca. 10€ Ersparnis.',
          'Here you see your current points balance.\n\nWith 10 points you get 1 month Pro for free — saving ~€10.'
        ),
        next: t('Weiter', 'Next'),
      },
      // 2 – Einladungslink
      {
        page: null,
        target: '#refLinkInput, [data-tour="reflink"]',
        title: t('Dein Einladungslink 🔗', 'Your Invitation Link 🔗'),
        body: t(
          'Teile diesen Link mit anderen Spielern.\n\nSobald jemand über deinen Link ein Pro-Abo kauft, bekommst du automatisch +1 Point.\n\nPro Person nur einmal — Points verfallen nicht!',
          'Share this link with other players.\n\nWhenever someone buys a Pro subscription through your link, you automatically get +1 point.\n\nOnce per person — points never expire!'
        ),
        next: t('Weiter', 'Next'),
      },
      // 3 – Statistiken
      {
        page: null,
        target: '.card:nth-of-type(2) div[style*="display:flex"]',
        title: t('Deine Statistiken 📊', 'Your Statistics 📊'),
        body: t(
          'Einladungen gesamt, gesammelte Points und wie viele Monate du einlösen kannst — alles auf einen Blick.',
          'Total invitations, collected points, and how many months you can redeem — all at a glance.'
        ),
        next: t('Weiter', 'Next'),
      },
      // 4 – Einlösen
      {
        page: null,
        target: 'form[action*="redeem"] button, [data-tour="redeem"]',
        title: t('Pro-Monat einlösen 🎁', 'Redeem Pro Month 🎁'),
        body: t(
          'Sobald du 10 Points hast, erscheint hier der "Einlösen"-Button.\n\nEin Klick verlängert deinen Pro-Zugang sofort um 1 Monat.',
          'Once you have 10 points, the "Redeem" button appears here.\n\nOne click immediately extends your Pro access by 1 month.'
        ),
        next: t('Weiter', 'Next'),
      },
      // 5 – Tour-Neustart
      {
        page: null,
        target: 'button[onclick*="restartTour"], [data-tour="restart-tour"]',
        title: t('Tour jederzeit neu starten 🗺️', 'Restart tour anytime 🗺️'),
        body: t(
          'Du kannst die Einführungstour (und alle anderen Touren) jederzeit hier neu starten — praktisch wenn ein neues Mitglied das System erklärt bekommen soll.',
          'You can restart the introduction tour (and all other tours) here anytime — handy when explaining the system to a new member.'
        ),
        next: t('Verstanden ✓', 'Got it ✓'),
        isLast: true,
      },
    ];
  }

  // ── State helpers ─────────────────────────────────────────────────────────

  const STATE_KEY = 'travops_tour';

  function getState() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY)) || null; } catch { return null; }
  }
  function setState(s) {
    if (s) localStorage.setItem(STATE_KEY, JSON.stringify(s));
    else localStorage.removeItem(STATE_KEY);
  }
  function clearTour() { setState(null); }

  // ── Resolve steps for current tour ───────────────────────────────────────

  function resolveSteps(tourName, guildId) {
    if (tourName === 'start') return startTourSteps(guildId);
    if (tourName === 'my-account') return myAccountSteps();
    return [];
  }

  // ── UI construction ───────────────────────────────────────────────────────

  function buildUI() {
    // Remove existing if any
    document.getElementById('beb-tour-overlay')?.remove();
    document.getElementById('beb-tour-spotlight')?.remove();
    document.getElementById('beb-tour-tooltip')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'beb-tour-overlay';
    overlay.style.cssText = `position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.75);backdrop-filter:blur(2px);transition:opacity 0.3s;`;

    const spotlight = document.createElement('div');
    spotlight.id = 'beb-tour-spotlight';
    spotlight.style.cssText = `position:fixed;z-index:10000;pointer-events:none;border-radius:10px;transition:all 0.35s cubic-bezier(.22,1,.36,1);box-shadow:0 0 0 9999px rgba(0,0,0,0.75);`;

    const tooltip = document.createElement('div');
    tooltip.id = 'beb-tour-tooltip';
    tooltip.style.cssText = `
      position:fixed;z-index:10001;
      background:#1e293b;border:1px solid #334155;
      border-radius:16px;padding:1.5rem;max-width:380px;width:90vw;
      box-shadow:0 20px 60px rgba(0,0,0,0.6);
      transition:opacity 0.25s,transform 0.25s;
    `;
    tooltip.innerHTML = `
      <div id="beb-tour-badge" style="font-size:0.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#6366f1;margin-bottom:0.5rem;display:none;"></div>
      <div id="beb-tour-title" style="font-size:1.1rem;font-weight:700;color:#f1f5f9;margin-bottom:0.5rem;"></div>
      <div id="beb-tour-body" style="font-size:0.88rem;color:#94a3b8;line-height:1.65;white-space:pre-line;margin-bottom:0.75rem;"></div>
      <div id="beb-tour-hint" style="display:none;font-size:0.78rem;background:rgba(99,102,241,0.12);border:1px solid rgba(99,102,241,0.25);border-radius:8px;padding:0.4rem 0.7rem;color:#a5b4fc;margin-bottom:0.85rem;"></div>
      <div id="beb-tour-action-wrap" style="margin-bottom:0.85rem;display:none;">
        <a id="beb-tour-action-btn" href="#" style="display:inline-flex;align-items:center;gap:0.4rem;background:rgba(99,102,241,0.2);border:1px solid rgba(99,102,241,0.4);border-radius:8px;padding:0.4rem 0.9rem;color:#a5b4fc;font-size:0.82rem;text-decoration:none;font-weight:600;">
          🔗 <span id="beb-tour-action-label"></span>
        </a>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;">
        <div id="beb-tour-dots" style="display:flex;gap:6px;"></div>
        <div style="display:flex;gap:0.5rem;">
          <button id="beb-tour-skip" style="background:none;border:1px solid #334155;border-radius:8px;padding:0.4rem 0.9rem;color:#94a3b8;font-size:0.82rem;cursor:pointer;">
            ${t('Beenden', 'Exit')}
          </button>
          <button id="beb-tour-next" style="background:linear-gradient(135deg,#6366f1,#4f46e5);border:none;border-radius:8px;padding:0.4rem 1.1rem;color:#fff;font-size:0.85rem;font-weight:700;cursor:pointer;white-space:nowrap;"></button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(tooltip);
    return { overlay, spotlight, tooltip };
  }

  // ── Render a single step ──────────────────────────────────────────────────

  function renderStep(step, stepIndex, totalSteps, { overlay, spotlight, tooltip }) {
    const targetEl = findTarget(step.target);

    tooltip.style.opacity = '0';

    setTimeout(() => {
      // Badge (step counter for non-welcome steps)
      const badge = document.getElementById('beb-tour-badge');
      if (stepIndex > 0 && !step.isLast) {
        badge.textContent = t(`Schritt ${stepIndex} von ${totalSteps - 2}`, `Step ${stepIndex} of ${totalSteps - 2}`);
        badge.style.display = 'block';
      } else {
        badge.style.display = 'none';
      }

      document.getElementById('beb-tour-title').textContent = step.title;
      document.getElementById('beb-tour-body').textContent = step.body;
      document.getElementById('beb-tour-next').textContent = step.next;

      // Hint
      const hint = document.getElementById('beb-tour-hint');
      if (step.hint) {
        hint.textContent = step.hint;
        hint.style.display = 'block';
      } else {
        hint.style.display = 'none';
      }

      // Action button
      const actionWrap = document.getElementById('beb-tour-action-wrap');
      const actionBtn  = document.getElementById('beb-tour-action-btn');
      const actionLbl  = document.getElementById('beb-tour-action-label');
      if (step.action) {
        actionLbl.textContent = step.action.label;
        actionBtn.href = step.action.url;
        actionWrap.style.display = 'block';
      } else {
        actionWrap.style.display = 'none';
      }

      buildDots(totalSteps, stepIndex, tooltip);
      showSpotlight(targetEl, spotlight, overlay);
      positionTooltip(targetEl, tooltip);

      // Fade in
      tooltip.style.opacity = '1';
      tooltip.style.transform = 'none';
      if (targetEl) targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });

      // Highlight ring on target
      if (step.highlight) {
        const hEl = findTarget(step.highlight);
        if (hEl) {
          hEl.style.outline = '2px solid #6366f1';
          hEl.style.outlineOffset = '3px';
          hEl.dataset.tourHighlighted = '1';
        }
      }
    }, 180);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function findTarget(selector) {
    if (!selector) return null;
    for (const s of selector.split(',').map(s => s.trim())) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  }

  function buildDots(total, current, tooltip) {
    const container = tooltip.querySelector('#beb-tour-dots');
    container.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const dot = document.createElement('div');
      dot.style.cssText = `width:8px;height:8px;border-radius:50%;background:${i === current ? '#6366f1' : '#334155'};transition:background 0.2s;`;
      container.appendChild(dot);
    }
  }

  function positionTooltip(targetEl, tooltip) {
    if (!targetEl) {
      tooltip.style.top = '50%';
      tooltip.style.left = '50%';
      tooltip.style.transform = 'translate(-50%, -50%)';
      return;
    }
    const rect = targetEl.getBoundingClientRect();
    const ttH = 280, margin = 16;
    let top = rect.bottom + margin < window.innerHeight ? rect.bottom + margin : Math.max(margin, rect.top - ttH - margin);
    let left = Math.min(Math.max(margin, rect.left), window.innerWidth - 390);
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.transform = 'none';
  }

  function showSpotlight(targetEl, spotlight, overlay) {
    if (!targetEl) {
      spotlight.style.cssText += 'width:0;height:0;top:-999px;left:-999px;box-shadow:none;';
      overlay.style.display = 'block';
      return;
    }
    const rect = targetEl.getBoundingClientRect();
    const pad = 10;
    spotlight.style.top    = (rect.top  - pad) + 'px';
    spotlight.style.left   = (rect.left - pad) + 'px';
    spotlight.style.width  = (rect.width  + pad * 2) + 'px';
    spotlight.style.height = (rect.height + pad * 2) + 'px';
    spotlight.style.boxShadow = '0 0 0 9999px rgba(0,0,0,0.75)';
    overlay.style.display = 'none';
  }

  function removeHighlights() {
    document.querySelectorAll('[data-tour-highlighted]').forEach(el => {
      el.style.outline = '';
      el.style.outlineOffset = '';
      delete el.dataset.tourHighlighted;
    });
  }

  function fadeOutUI({ overlay, spotlight, tooltip }) {
    [overlay, spotlight, tooltip].forEach(el => {
      if (!el) return;
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    });
    removeHighlights();
  }

  // ── Page navigation with tour continuation ────────────────────────────────

  function navigateToStep(url, state) {
    setState(state);
    window.location.href = url;
  }

  // ── Main tour runner ──────────────────────────────────────────────────────

  function runTour(tourName, startStep, guildId) {
    const steps = resolveSteps(tourName, guildId);
    if (!steps.length) return;

    const ui = buildUI();
    let currentStep = startStep;

    function goNext() {
      removeHighlights();
      if (currentStep >= steps.length - 1) {
        // Tour done
        finishTour(tourName);
        fadeOutUI(ui);
        return;
      }

      currentStep++;
      const step = steps[currentStep];

      // Does this step require a different page?
      if (step.page) {
        const targetPath = new URL(step.page, location.origin).pathname;
        if (location.pathname !== targetPath) {
          // Navigate and resume
          setState({ name: tourName, step: currentStep, guildId });
          fadeOutUI(ui);
          window.location.href = step.page;
          return;
        }
      }

      renderStep(step, currentStep, steps.length, ui);
    }

    ui.tooltip.querySelector('#beb-tour-next').addEventListener('click', goNext);
    ui.tooltip.querySelector('#beb-tour-skip').addEventListener('click', () => {
      finishTour(tourName);
      fadeOutUI(ui);
    });
    ui.overlay.addEventListener('click', () => {
      finishTour(tourName);
      fadeOutUI(ui);
    });

    // Render current step
    setTimeout(() => renderStep(steps[currentStep], currentStep, steps.length, ui), 600);
  }

  function finishTour(tourName) {
    clearTour();
    if (tourName === 'start')      localStorage.setItem('beb_tour_done', '1');
    if (tourName === 'my-account') localStorage.setItem('beb_account_tour_done', '1');
  }

  // ── Entry point ───────────────────────────────────────────────────────────

  function init() {
    const path = location.pathname;

    // Extract guildId from URL if present
    const guildMatch = path.match(/\/guild\/(\d{17,20})/);
    const guildId = guildMatch ? guildMatch[1] : null;

    // Check for resume state (navigated from previous tour step)
    const state = getState();
    if (state) {
      const steps = resolveSteps(state.name, state.guildId || guildId);
      const step  = steps[state.step];

      // Verify we're on the right page
      if (step) {
        const targetPath = step.page ? new URL(step.page, location.origin).pathname : null;
        if (!targetPath || location.pathname === targetPath) {
          clearTour();  // clear pending state before running
          runTour(state.name, state.step, state.guildId || guildId);
          return;
        }
      }
    }

    // Auto-start: first-time guild visit → start tour
    if (guildId && /^\/guild\/\d{17,20}(\/)?$/.test(path)) {
      if (!localStorage.getItem('beb_tour_done')) {
        runTour('start', 0, guildId);
        return;
      }
    }

    // Auto-start: my-account tour (if flagged)
    if (path === '/profile') {
      if (!localStorage.getItem('beb_account_tour_done')) {
        runTour('my-account', 0, guildId);
        return;
      }
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────

  window.TravOpsTour = {
    /** Start a named tour manually (e.g. from a ? button) */
    start(tourName, guildId) {
      localStorage.removeItem('beb_tour_done');
      localStorage.removeItem('beb_account_tour_done');
      clearTour();
      runTour(tourName, 0, guildId || (location.pathname.match(/\/guild\/(\d{17,20})/) || [])[1]);
    },
    /** Resume from saved state */
    resume() { init(); },
  };

  // ── Profile page: mark tour done after viewing ────────────────────────────

  // Override restartTour from profile.html to also restart account tour
  window.restartTourFull = function () {
    localStorage.removeItem('beb_tour_done');
    localStorage.removeItem('beb_account_tour_done');
    clearTour();
    const guildMatch = location.pathname.match(/\/guild\/(\d{17,20})/);
    window.location.href = guildMatch ? `/guild/${guildMatch[1]}` : '/dashboard';
  };

  // ── Init ──────────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
