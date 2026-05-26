// popup.js

const CIRCUMFERENCE = 2 * Math.PI * 68;

// --- Timer 1 Tab elements ---
const timeText       = document.getElementById('timeText');
const timeLabel      = document.getElementById('timeLabel');
const ringProgress   = document.getElementById('ringProgress');
const btnStart       = document.getElementById('btnStart');
const btnStop        = document.getElementById('btnStop');
const statusText     = document.getElementById('statusText');
const inputSection   = document.getElementById('inputSection');
const inputHours     = document.getElementById('inputHours');
const inputMinutes   = document.getElementById('inputMinutes');
const inputSeconds   = document.getElementById('inputSeconds');
const loopCheckbox   = document.getElementById('loopCheckbox');

// --- Timer 2 Tab elements ---
const timeText2      = document.getElementById('timeText2');
const timeLabel2     = document.getElementById('timeLabel2');
const ringProgress2  = document.getElementById('ringProgress2');
const btnStart2      = document.getElementById('btnStart2');
const btnStop2       = document.getElementById('btnStop2');
const statusText2    = document.getElementById('statusText2');
const inputSection2  = document.getElementById('inputSection2');
const inputHours2    = document.getElementById('inputHours2');
const inputMinutes2  = document.getElementById('inputMinutes2');
const inputSeconds2  = document.getElementById('inputSeconds2');
const loopCheckbox2  = document.getElementById('loopCheckbox2');

// --- Alarm Tab elements ---
const alarmNextCountdown = document.getElementById('alarmNextCountdown');
const alarmNextTime      = document.getElementById('alarmNextTime');
const alarmStatusText    = document.getElementById('alarmStatusText');
const btnSaveAlarms      = document.getElementById('btnSaveAlarms');

let updateInterval  = null;
let updateInterval2 = null;
let alarmInterval   = null;
let currentDuration  = 0;
let currentDuration2 = 0;

ringProgress.style.strokeDasharray  = CIRCUMFERENCE;
ringProgress.style.strokeDashoffset = 0;
ringProgress2.style.strokeDasharray  = CIRCUMFERENCE;
ringProgress2.style.strokeDashoffset = 0;

// ===================== TAB SWITCHING =====================
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const contentId = 'tabContent' + tab.dataset.tab.charAt(0).toUpperCase() + tab.dataset.tab.slice(1);
    document.getElementById(contentId).classList.add('active');
  });
});

// ===================== LANGUAGE TOGGLE =====================
document.getElementById('langToggleBtn').addEventListener('click', () => {
  const newLang = currentLang === 'en' ? 'de' : 'en';
  setLanguage(newLang);
  // Re-apply dynamic status texts if timers are idle
  if (!updateInterval)  { statusText.textContent  = t('status_ready'); }
  if (!updateInterval2) { statusText2.textContent = t('status_ready'); }
  // Update time labels if idle
  const ringOff1 = parseFloat(ringProgress.style.strokeDashoffset) || 0;
  if (ringOff1 === 0) timeLabel.textContent  = t('no_timer');
  const ringOff2 = parseFloat(ringProgress2.style.strokeDashoffset) || 0;
  if (ringOff2 === 0) timeLabel2.textContent = t('no_timer');
  // Update alarm status hint if idle
  if (alarmStatusText.dataset.i18n === 'alarm_hint') {
    alarmStatusText.textContent = t('alarm_hint');
  }
});

// ===================== MESSAGING =====================
function sendMessage(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (response) => resolve(response));
  });
}

// ===================== TIMER 1 =====================
async function init() {
  const status = await sendMessage({ action: 'getStatus' });
  updateUI(status);
  if (status.running) startPolling();
}

function updateUI(status) {
  if (status.running) {
    currentDuration = status.duration;
    renderTimer(status.remaining, status.duration);
    showRunningState();
  } else {
    renderTimer(0, 0);
    showIdleState();
  }
}

function showRunningState() {
  btnStart.classList.add('hidden');
  btnStop.classList.remove('hidden');
  inputSection.classList.add('disabled');
  statusText.textContent = t('status_running');
  statusText.className = 'status-running';
}

function showIdleState() {
  btnStart.classList.remove('hidden');
  btnStop.classList.add('hidden');
  inputSection.classList.remove('disabled');
  statusText.textContent = t('status_ready');
  statusText.className = 'status-stopped';
  timeLabel.textContent = t('no_timer');
  ringProgress.classList.remove('warning', 'danger');
}

function renderTimer(remaining, duration) {
  timeText.textContent = formatTime(remaining);
  if (duration > 0) {
    const ratio = remaining / duration;
    ringProgress.style.strokeDashoffset = CIRCUMFERENCE * (1 - ratio);
    ringProgress.classList.remove('warning', 'danger');
    if (ratio <= 0.1)       ringProgress.classList.add('danger');
    else if (ratio <= 0.25) ringProgress.classList.add('warning');
    timeLabel.textContent = `${Math.round(ratio * 100)}${t('pct_remaining')}`;
  } else {
    ringProgress.style.strokeDashoffset = 0;
    timeLabel.textContent = t('no_timer');
  }
}

function startPolling() {
  if (updateInterval) clearInterval(updateInterval);
  updateInterval = setInterval(async () => {
    const status = await sendMessage({ action: 'getStatus' });
    if (!status.running) {
      clearInterval(updateInterval);
      updateInterval = null;
      showIdleState();
      renderTimer(0, 0);
    } else {
      renderTimer(status.remaining, status.duration);
    }
  }, 250);
}

btnStart.addEventListener('click', async () => {
  const h = parseInt(inputHours.value) || 0;
  const m = parseInt(inputMinutes.value) || 0;
  const s = parseInt(inputSeconds.value) || 0;
  const totalMs = (h * 3600 + m * 60 + s) * 1000;

  if (totalMs <= 0) {
    statusText.textContent = t('status_no_time');
    statusText.className = '';
    setTimeout(() => { statusText.textContent = t('status_ready'); }, 2000);
    return;
  }

  await chrome.storage.local.set({
    timerLoop: loopCheckbox.checked,
    lastHours: h, lastMinutes: m, lastSeconds: s
  });
  await sendMessage({ action: 'startTimer', duration: totalMs });
  currentDuration = totalMs;
  renderTimer(totalMs, totalMs);
  showRunningState();
  startPolling();
});

btnStop.addEventListener('click', async () => {
  if (updateInterval) { clearInterval(updateInterval); updateInterval = null; }
  await sendMessage({ action: 'stopTimer' });
  showIdleState();
  renderTimer(0, 0);
});

// ===================== TIMER 2 =====================
async function init2() {
  const status = await sendMessage({ action: 'getStatus2' });
  updateUI2(status);
  if (status.running) startPolling2();
}

function updateUI2(status) {
  if (status.running) {
    currentDuration2 = status.duration;
    renderTimer2(status.remaining, status.duration);
    showRunningState2();
  } else {
    renderTimer2(0, 0);
    showIdleState2();
  }
}

function showRunningState2() {
  btnStart2.classList.add('hidden');
  btnStop2.classList.remove('hidden');
  inputSection2.classList.add('disabled');
  statusText2.textContent = t('status_running');
  statusText2.className = 'status-running';
}

function showIdleState2() {
  btnStart2.classList.remove('hidden');
  btnStop2.classList.add('hidden');
  inputSection2.classList.remove('disabled');
  statusText2.textContent = t('status_ready');
  statusText2.className = 'status-stopped';
  timeLabel2.textContent = t('no_timer');
  ringProgress2.classList.remove('warning', 'danger');
}

function renderTimer2(remaining, duration) {
  timeText2.textContent = formatTime(remaining);
  if (duration > 0) {
    const ratio = remaining / duration;
    ringProgress2.style.strokeDashoffset = CIRCUMFERENCE * (1 - ratio);
    ringProgress2.classList.remove('warning', 'danger');
    if (ratio <= 0.1)       ringProgress2.classList.add('danger');
    else if (ratio <= 0.25) ringProgress2.classList.add('warning');
    timeLabel2.textContent = `${Math.round(ratio * 100)}${t('pct_remaining')}`;
  } else {
    ringProgress2.style.strokeDashoffset = 0;
    timeLabel2.textContent = t('no_timer');
  }
}

function startPolling2() {
  if (updateInterval2) clearInterval(updateInterval2);
  updateInterval2 = setInterval(async () => {
    const status = await sendMessage({ action: 'getStatus2' });
    if (!status.running) {
      clearInterval(updateInterval2);
      updateInterval2 = null;
      showIdleState2();
      renderTimer2(0, 0);
    } else {
      renderTimer2(status.remaining, status.duration);
    }
  }, 250);
}

btnStart2.addEventListener('click', async () => {
  const h = parseInt(inputHours2.value) || 0;
  const m = parseInt(inputMinutes2.value) || 0;
  const s = parseInt(inputSeconds2.value) || 0;
  const totalMs = (h * 3600 + m * 60 + s) * 1000;

  if (totalMs <= 0) {
    statusText2.textContent = t('status_no_time');
    statusText2.className = '';
    setTimeout(() => { statusText2.textContent = t('status_ready'); }, 2000);
    return;
  }

  await chrome.storage.local.set({
    timer2Loop: loopCheckbox2.checked,
    lastHours2: h, lastMinutes2: m, lastSeconds2: s
  });
  await sendMessage({ action: 'startTimer2', duration: totalMs });
  currentDuration2 = totalMs;
  renderTimer2(totalMs, totalMs);
  showRunningState2();
  startPolling2();
});

btnStop2.addEventListener('click', async () => {
  if (updateInterval2) { clearInterval(updateInterval2); updateInterval2 = null; }
  await sendMessage({ action: 'stopTimer2' });
  showIdleState2();
  renderTimer2(0, 0);
});

// ===================== ALARM TAB =====================

function msUntilTime(timeStr) {
  if (!timeStr) return null;
  const [hh, mm] = timeStr.split(':').map(Number);
  const now = new Date();
  const target = new Date(now);
  target.setHours(hh, mm, 0, 0);
  let diff = target - now;
  if (diff <= 0) diff += 24 * 60 * 60 * 1000;
  return diff;
}

function formatCountdown(ms) {
  if (ms === null || ms < 0) return '–';
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function saveAlarms() {
  const alarms = [];
  for (let i = 0; i < 4; i++) {
    alarms.push({
      time:    document.getElementById(`alarmTime${i}`).value,
      enabled: document.getElementById(`alarmEnabled${i}`).checked
    });
  }
  chrome.storage.local.set({ clockAlarms: alarms });
  return alarms;
}

function loadAlarms(callback) {
  chrome.storage.local.get(['clockAlarms'], (data) => {
    const alarms = data.clockAlarms || [{time:'',enabled:false},{time:'',enabled:false},{time:'',enabled:false},{time:'',enabled:false}];
    for (let i = 0; i < 4; i++) {
      document.getElementById(`alarmTime${i}`).value      = alarms[i] ? (alarms[i].time    || '') : '';
      document.getElementById(`alarmEnabled${i}`).checked = alarms[i] ? (alarms[i].enabled || false) : false;
      updateAlarmRowStyle(i, alarms[i] ? alarms[i].enabled : false);
    }
    if (callback) callback(alarms);
  });
}

function updateAlarmRowStyle(i, enabled) {
  const row = document.querySelector(`.alarm-row[data-index="${i}"]`);
  if (enabled) row.classList.add('alarm-active');
  else         row.classList.remove('alarm-active');
}

for (let i = 0; i < 4; i++) {
  document.getElementById(`alarmEnabled${i}`).addEventListener('change', (e) => {
    updateAlarmRowStyle(i, e.target.checked);
  });
}

btnSaveAlarms.addEventListener('click', () => {
  const alarms = saveAlarms();
  chrome.runtime.sendMessage({ action: 'setClockAlarms', alarms });
  alarmStatusText.dataset.i18n = '';
  alarmStatusText.textContent = t('alarm_saved');
  alarmStatusText.style.color = '#10b981';
  setTimeout(() => {
    alarmStatusText.dataset.i18n = 'alarm_hint';
    alarmStatusText.textContent = t('alarm_hint');
    alarmStatusText.style.color = '';
  }, 2500);
});

let lastFiredMinute = {};

function tickAlarms(alarms) {
  const now = new Date();
  const nowStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const minuteKey = `${now.getHours()}_${now.getMinutes()}`;

  let nextMs = null;
  let nextTimeStr = null;

  for (let i = 0; i < 4; i++) {
    const a = alarms[i];
    const miniEl = document.getElementById(`alarmCountdown${i}`);

    if (!a || !a.enabled || !a.time) {
      miniEl.textContent = '–';
      continue;
    }

    const ms = msUntilTime(a.time);
    miniEl.textContent = formatCountdown(ms);

    if (a.time === nowStr && lastFiredMinute[i] !== minuteKey) {
      lastFiredMinute[i] = minuteKey;
      const row = document.querySelector(`.alarm-row[data-index="${i}"]`);
      row.classList.add('fired');
      setTimeout(() => row.classList.remove('fired'), 1500);
      chrome.runtime.sendMessage({ action: 'playAlarmSound' });
    }

    if (nextMs === null || ms < nextMs) {
      nextMs = ms;
      nextTimeStr = a.time;
    }
  }

  if (nextMs !== null) {
    alarmNextCountdown.textContent = formatCountdown(nextMs);
    alarmNextTime.textContent = `${t('alarm_next_prefix')} ${nextTimeStr}${t('alarm_oclock')}`;
  } else {
    alarmNextCountdown.textContent = '--:--:--';
    alarmNextTime.textContent = t('alarm_no_active');
  }
}

function startAlarmTick() {
  if (alarmInterval) clearInterval(alarmInterval);
  loadAlarms((alarms) => {
    tickAlarms(alarms);
    alarmInterval = setInterval(() => {
      const current = [];
      for (let i = 0; i < 4; i++) {
        current.push({
          time:    document.getElementById(`alarmTime${i}`).value,
          enabled: document.getElementById(`alarmEnabled${i}`).checked
        });
      }
      tickAlarms(current);
    }, 1000);
  });
}

// ===================== HELPERS =====================
function formatTime(ms) {
  if (ms <= 0) return '00:00';
  const totalSec = Math.ceil(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function pad(n) { return String(n).padStart(2, '0'); }

// ===================== STARTUP =====================
chrome.storage.local.get([
  'timerLoop', 'lastHours', 'lastMinutes', 'lastSeconds',
  'timer2Loop', 'lastHours2', 'lastMinutes2', 'lastSeconds2'
], (data) => {
  if (data.timerLoop !== undefined)    loopCheckbox.checked   = data.timerLoop;
  if (data.lastHours !== undefined)    inputHours.value       = data.lastHours;
  if (data.lastMinutes !== undefined)  inputMinutes.value     = data.lastMinutes;
  if (data.lastSeconds !== undefined)  inputSeconds.value     = data.lastSeconds;

  if (data.timer2Loop !== undefined)   loopCheckbox2.checked  = data.timer2Loop;
  if (data.lastHours2 !== undefined)   inputHours2.value      = data.lastHours2;
  if (data.lastMinutes2 !== undefined) inputMinutes2.value    = data.lastMinutes2;
  if (data.lastSeconds2 !== undefined) inputSeconds2.value    = data.lastSeconds2;
});

// Init i18n first, then rest
initI18n(() => {
  init();
  init2();
  startAlarmTick();
});

// ===================== BLUEPRINTS =====================
const TRAVOPS_BASE = 'https://travops.online';

const bpList       = document.getElementById('bpList');
const bpLoading    = document.getElementById('bpLoading');
const bpLoginHint  = document.getElementById('bpLoginHint');
const bpEmpty      = document.getElementById('bpEmpty');
const btnBpRefresh = document.getElementById('btnBpRefresh');

let bpData = [];        // [{id, guild_id, template_name, steps, ...}]
let bpOpenCard = null;  // currently expanded blueprint id

// Load blueprints from TravOps API
async function loadBlueprints() {
  bpLoading.classList.remove('hidden');
  bpLoginHint.classList.add('hidden');
  bpEmpty.classList.add('hidden');
  bpList.innerHTML = '';

  try {
    const resp = await fetch(TRAVOPS_BASE + '/api/my-blueprints', { credentials: 'include' });
    if (resp.status === 401) {
      bpLoading.classList.add('hidden');
      bpLoginHint.classList.remove('hidden');
      return;
    }
    const data = await resp.json();
    bpData = data.blueprints || [];
    bpLoading.classList.add('hidden');
    if (!bpData.length) {
      bpEmpty.classList.remove('hidden');
      return;
    }
    renderBlueprints();
  } catch (e) {
    bpLoading.classList.add('hidden');
    bpLoginHint.classList.remove('hidden');
  }
}

function renderBlueprints() {
  bpList.innerHTML = '';
  bpData.forEach(bp => {
    const done  = bp.steps.filter(s => s.completed).length;
    const total = bp.steps.length;
    const pct   = total > 0 ? Math.round((done / total) * 100) : 0;
    const isOpen = bpOpenCard === bp.id;

    const card = document.createElement('div');
    card.className = 'bp-card' + (isOpen ? ' open' : '');
    card.dataset.bpId = bp.id;

    card.innerHTML = `
      <div class="bp-card-header">
        <div style="flex:1;min-width:0;">
          <div class="bp-card-title">${esc(bp.village_name || bp.player_name)}</div>
          <div class="bp-card-sub">${esc(bp.template_name)} · ${esc(bp.guild_name)}</div>
        </div>
        <div style="display:flex;align-items:center;gap:0.35rem;flex-shrink:0;">
          <span style="font-size:0.7rem;color:#6b7280;">${done}/${total}</span>
          <span class="bp-chevron">▶</span>
        </div>
      </div>
      <div class="bp-progress-bar"><div class="bp-progress-fill" style="width:${pct}%"></div></div>
      <div class="bp-progress-label">${pct}% abgehakt</div>
      <div class="bp-steps ${isOpen ? 'open' : ''}" id="bpSteps-${bp.id}">
        ${bp.steps.map(s => renderStep(s, bp)).join('')}
      </div>
    `;

    // Toggle expand/collapse on header click
    card.querySelector('.bp-card-header').addEventListener('click', () => {
      bpOpenCard = (bpOpenCard === bp.id) ? null : bp.id;
      renderBlueprints();
    });

    // Step toggle clicks
    card.querySelectorAll('.bp-step').forEach(stepEl => {
      stepEl.addEventListener('click', async (e) => {
        e.stopPropagation();
        const stepId     = parseInt(stepEl.dataset.stepId);
        const blueprintId = bp.id;
        // Optimistic toggle
        const step = bp.steps.find(s => s.id === stepId);
        if (step) step.completed = !step.completed;
        renderBlueprints();
        // Persist via API
        try {
          await fetch(TRAVOPS_BASE + '/api/blueprint-step/toggle', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ blueprint_id: blueprintId, step_id: stepId, guild_id: bp.guild_id }),
          });
        } catch(e) {
          // Revert on error
          if (step) step.completed = !step.completed;
          renderBlueprints();
        }
      });
    });

    bpList.appendChild(card);
  });
}

function renderStep(s, bp) {
  const done = s.completed;
  const typeIcon = { build: '🏗️', research: '🔬', recruit: '⚔️', note: '📝' }[s.step_type] || '•';
  return `
    <div class="bp-step ${done ? 'done' : ''}" data-step-id="${s.id}">
      <div class="bp-step-check">${done ? '✓' : ''}</div>
      <div>
        <div class="bp-step-title">${typeIcon} ${esc(s.title)}</div>
        ${s.description ? `<div class="bp-step-desc">${esc(s.description)}</div>` : ''}
      </div>
    </div>
  `;
}

function esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Load blueprints when tab is opened
document.getElementById('tabBlueprint').addEventListener('click', () => {
  if (!bpData.length) loadBlueprints();
});
btnBpRefresh.addEventListener('click', () => loadBlueprints());

// Auto-load if blueprint tab is somehow active on open
if (document.getElementById('tabBlueprint').classList.contains('active')) {
  loadBlueprints();
}
