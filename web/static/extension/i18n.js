// i18n.js — TravOps Timer translations

const TRANSLATIONS = {
  en: {
    tab_timer1:          '⏱ Timer 1',
    tab_timer2:          '⏱ Timer 2',
    tab_alarm:           '🔔 Alarms',
    no_timer:            'No Timer',
    set_timer:           'Set Timer',
    label_h:             'h',
    label_min:           'min',
    label_sec:           's',
    loop_label:          'Loop (auto repeat)',
    btn_start:           'Start',
    btn_stop:            'Stop',
    status_ready:        'Ready',
    status_running:      '⏳ Timer running in background...',
    status_no_time:      '⚠️ Please enter a time!',
    pct_remaining:       '% remaining',
    alarm_next_label:    'Next alarm in',
    alarm_no_active:     'No active alarms',
    alarm_next_prefix:   'Next alarm:',
    alarm_slot:          'Alarm',
    alarm_save:          '💾 Save & Activate',
    alarm_hint:          'Enter times and save',
    alarm_saved:         '✅ Alarms saved & active!',
    alarm_oclock:        '',
  },
  de: {
    tab_timer1:          '⏱ Timer 1',
    tab_timer2:          '⏱ Timer 2',
    tab_alarm:           '🔔 Uhrzeiten',
    no_timer:            'Kein Timer',
    set_timer:           'Timer einstellen',
    label_h:             'Std',
    label_min:           'Min',
    label_sec:           'Sek',
    loop_label:          'Loop (automatisch wiederholen)',
    btn_start:           'Starten',
    btn_stop:            'Stoppen',
    status_ready:        'Bereit',
    status_running:      '⏳ Timer läuft im Hintergrund...',
    status_no_time:      '⚠️ Bitte eine Zeit eingeben!',
    pct_remaining:       '% verbleibend',
    alarm_next_label:    'Nächster Alarm in',
    alarm_no_active:     'Keine aktiven Alarme',
    alarm_next_prefix:   'Nächster Alarm:',
    alarm_slot:          'Alarm',
    alarm_save:          '💾 Speichern & Aktivieren',
    alarm_hint:          'Uhrzeiten eintragen und speichern',
    alarm_saved:         '✅ Alarme gespeichert & aktiv!',
    alarm_oclock:        ' Uhr',
  }
};

let currentLang = 'en';

function t(key) {
  return (TRANSLATIONS[currentLang] || TRANSLATIONS['en'])[key] || key;
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    const val = t(key);
    if (el.tagName === 'INPUT' && el.type === 'text') {
      el.placeholder = val;
    } else {
      el.textContent = val;
    }
  });

  // Update button inner HTML (they have icons + text)
  ['btnStart', 'btnStop', 'btnStart2', 'btnStop2'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    const icon = btn.querySelector('.btn-icon');
    const iconHtml = icon ? icon.outerHTML + ' ' : '';
    const key = id.startsWith('btnStart') ? 'btn_start' : 'btn_stop';
    btn.innerHTML = iconHtml + t(key);
    if (icon) btn.querySelector('.btn-icon'); // preserve icon
  });

  // Alarm slot labels
  for (let i = 0; i < 4; i++) {
    const el = document.querySelector(`.alarm-row[data-index="${i}"] .alarm-slot-label`);
    if (el) el.textContent = `${t('alarm_slot')} ${i + 1}`;
  }

  // Language toggle button label
  const langBtn = document.getElementById('langToggleBtn');
  if (langBtn) langBtn.textContent = currentLang === 'en' ? '🌐 DE' : '🌐 EN';
}

function setLanguage(lang, save = true) {
  currentLang = lang;
  applyTranslations();
  if (save) chrome.storage.local.set({ uiLang: lang });
}

function initI18n(callback) {
  chrome.storage.local.get(['uiLang'], (data) => {
    currentLang = data.uiLang || 'en';
    applyTranslations();
    if (callback) callback();
  });
}
