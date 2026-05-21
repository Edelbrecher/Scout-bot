/**
 * BIG-EYE-BOT Onboarding Tour
 * Only runs on /guild/{id} pages for first-time users.
 * Controlled by localStorage key 'beb_tour_done'.
 */

(function () {
  // Only run on guild pages
  if (!/^\/guild\/\d{17,20}(\/)?$/.test(location.pathname)) return;
  if (localStorage.getItem('beb_tour_done')) return;

  const lang = localStorage.getItem('beb_lang') || 'de';
  const t = (de, en) => lang === 'en' ? en : de;

  const STEPS = [
    {
      target: null, // full-screen welcome modal
      title: t('Willkommen bei BIG-EYE-BOT 👋', 'Welcome to BIG-EYE-BOT 👋'),
      body: t(
        'Spare Stunden pro Woche — der Bot übernimmt Scouting, Angriffserkennung, Farming-Analyse und Koordination vollautomatisch.',
        'Save hours every week — the bot fully automates scouting, attack detection, farming analysis, and coordination.'
      ),
      next: t('Tour starten', 'Start tour'),
    },
    {
      target: '.feature-grid, .guild-features, [data-tour="features"]',
      title: t('Alle Features auf einen Blick', 'All features at a glance'),
      body: t(
        'Hier siehst du alle verfügbaren Module deines Servers.',
        'Here you can see all available modules for your server.'
      ),
      next: t('Weiter', 'Next'),
    },
    {
      target: '[href*="/einsatz"], [data-tour="einsatz"]',
      title: t('Einsatzplanung', 'Attack Planning'),
      body: t(
        'Plane Angriffe mit automatischer Marschzeitberechnung — nie wieder Timing-Fehler.',
        'Plan attacks with automatic march time calculation — never miss timing again.'
      ),
      next: t('Weiter', 'Next'),
    },
    {
      target: '[href*="/farming"], [data-tour="farming"]',
      title: t('Farming Intel', 'Farming Intel'),
      body: t(
        'Inaktive Farmen automatisch erkennen und als Farmziele markieren.',
        'Auto-detect inactive farms and mark them as farming targets.'
      ),
      next: t('Weiter', 'Next'),
    },
    {
      target: '[href*="/timer"], [data-tour="timer"]',
      title: t('Timer', 'Timer'),
      body: t(
        'Verpasse nie wieder einen Einsatz — Echtzeit-Countdown für alle Aktionen.',
        'Never miss an operation — real-time countdown for all actions.'
      ),
      next: t('Weiter', 'Next'),
    },
    {
      target: null,
      title: t('Du sparst 3+ Stunden pro Woche! 🎉', 'You save 3+ hours per week! 🎉'),
      body: t(
        'Im Durchschnitt sparen Teams mit BIG-EYE-BOT über 3 Stunden Koordinationsaufwand pro Woche.',
        'On average, teams using BIG-EYE-BOT save over 3 hours of coordination effort per week.'
      ),
      next: t("Los geht's", "Let's go"),
      isLast: true,
    },
  ];

  let currentStep = 0;

  // ── DOM setup ─────────────────────────────────────────────────────────────

  const overlay = document.createElement('div');
  overlay.id = 'beb-tour-overlay';
  overlay.style.cssText = `
    position:fixed; inset:0; z-index:10000;
    background:rgba(0,0,0,0.75); backdrop-filter:blur(2px);
    transition:opacity 0.3s ease;
  `;

  const tooltip = document.createElement('div');
  tooltip.id = 'beb-tour-tooltip';
  tooltip.style.cssText = `
    position:fixed; z-index:10001;
    background:#1e293b; border:1px solid #334155;
    border-radius:14px; padding:1.5rem; max-width:360px; width:90vw;
    box-shadow:0 20px 60px rgba(0,0,0,0.6);
    transition:opacity 0.25s ease, transform 0.25s ease;
  `;

  tooltip.innerHTML = `
    <div id="beb-tour-title" style="font-size:1.1rem; font-weight:700; color:#f1f5f9; margin-bottom:0.5rem;"></div>
    <div id="beb-tour-body" style="font-size:0.9rem; color:#94a3b8; line-height:1.6; margin-bottom:1.25rem;"></div>
    <div style="display:flex; align-items:center; justify-content:space-between;">
      <div id="beb-tour-dots" style="display:flex; gap:6px;"></div>
      <div style="display:flex; gap:0.5rem;">
        <button id="beb-tour-skip" style="background:none; border:1px solid #334155; border-radius:8px; padding:0.4rem 0.9rem; color:#94a3b8; font-size:0.85rem; cursor:pointer;">${t('Überspringen', 'Skip')}</button>
        <button id="beb-tour-next" style="background:#3b82f6; border:none; border-radius:8px; padding:0.4rem 1rem; color:#fff; font-size:0.85rem; font-weight:600; cursor:pointer;"></button>
      </div>
    </div>
  `;

  // Spotlight element (box-shadow technique)
  const spotlight = document.createElement('div');
  spotlight.id = 'beb-tour-spotlight';
  spotlight.style.cssText = `
    position:fixed; z-index:10000; pointer-events:none;
    border-radius:8px; transition:all 0.35s cubic-bezier(.22,1,.36,1);
    box-shadow:0 0 0 9999px rgba(0,0,0,0.75);
  `;

  document.body.appendChild(overlay);
  document.body.appendChild(spotlight);
  document.body.appendChild(tooltip);

  // ── Helpers ───────────────────────────────────────────────────────────────

  function buildDots(total, current) {
    const container = document.getElementById('beb-tour-dots');
    container.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const dot = document.createElement('div');
      dot.style.cssText = `
        width:8px; height:8px; border-radius:50%;
        background: ${i === current ? '#3b82f6' : '#334155'};
        transition:background 0.2s;
      `;
      container.appendChild(dot);
    }
  }

  function positionTooltip(targetEl) {
    const tt = tooltip;
    if (!targetEl) {
      // Centered
      tt.style.top = '50%';
      tt.style.left = '50%';
      tt.style.transform = 'translate(-50%, -50%)';
      return;
    }
    const rect = targetEl.getBoundingClientRect();
    const ttH = 220; // approx
    const margin = 16;
    let top, left;

    // Prefer below
    if (rect.bottom + ttH + margin < window.innerHeight) {
      top = rect.bottom + margin;
    } else {
      top = Math.max(margin, rect.top - ttH - margin);
    }
    left = Math.min(
      Math.max(margin, rect.left),
      window.innerWidth - 360 - margin
    );

    tt.style.top = top + 'px';
    tt.style.left = left + 'px';
    tt.style.transform = 'none';
  }

  function showSpotlight(targetEl) {
    if (!targetEl) {
      spotlight.style.cssText += 'width:0;height:0;top:-999px;left:-999px;box-shadow:none;';
      return;
    }
    const rect = targetEl.getBoundingClientRect();
    const pad = 8;
    spotlight.style.top = (rect.top - pad) + 'px';
    spotlight.style.left = (rect.left - pad) + 'px';
    spotlight.style.width = (rect.width + pad * 2) + 'px';
    spotlight.style.height = (rect.height + pad * 2) + 'px';
    spotlight.style.boxShadow = '0 0 0 9999px rgba(0,0,0,0.75)';
  }

  function findTarget(selector) {
    if (!selector) return null;
    const selectors = selector.split(',').map(s => s.trim());
    for (const s of selectors) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  }

  function renderStep(index) {
    const step = STEPS[index];
    const targetEl = findTarget(step.target);

    // Fade out
    tooltip.style.opacity = '0';
    tooltip.style.transform = tooltip.style.transform.includes('translate(-50%') ? 'translate(-50%, -48%)' : 'translateY(8px)';

    setTimeout(() => {
      document.getElementById('beb-tour-title').textContent = step.title;
      document.getElementById('beb-tour-body').textContent = step.body;
      document.getElementById('beb-tour-next').textContent = step.next;
      buildDots(STEPS.length, index);
      showSpotlight(targetEl);
      positionTooltip(targetEl);

      // Show/hide overlay based on whether we have a spotlight
      overlay.style.display = targetEl ? 'none' : 'block';

      // Fade in
      tooltip.style.opacity = '1';
      tooltip.style.transform = tooltip.style.transform.includes('translate(-50%') ? 'translate(-50%, -50%)' : 'translateY(0)';

      // Scroll target into view
      if (targetEl) targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 150);
  }

  function endTour() {
    localStorage.setItem('beb_tour_done', '1');
    [overlay, spotlight, tooltip].forEach(el => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    });
  }

  // ── Event listeners ───────────────────────────────────────────────────────

  document.getElementById('beb-tour-next').addEventListener('click', () => {
    if (currentStep >= STEPS.length - 1) {
      endTour();
    } else {
      currentStep++;
      renderStep(currentStep);
    }
  });

  document.getElementById('beb-tour-skip').addEventListener('click', endTour);

  // Click outside spotlight closes tour
  overlay.addEventListener('click', endTour);

  // ── Start ─────────────────────────────────────────────────────────────────

  // Small delay so page finishes rendering
  setTimeout(() => {
    renderStep(0);
  }, 800);

})();
