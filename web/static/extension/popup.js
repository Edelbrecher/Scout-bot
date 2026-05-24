// popup.js

const CIRCUMFERENCE = 2 * Math.PI * 52;

// --- Timer Tab elements ---
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

// --- Alarm Tab elements ---
const alarmNextCountdown = document.getElementById('alarmNextCountdown');
const alarmNextTime      = document.getElementById('alarmNextTime');
const alarmStatusText    = document.getElementById('alarmStatusText');
const btnSaveAlarms      = document.getElementById('btnSaveAlarms');

let updateInterval  = null;
let alarmInterval   = null;
let currentDuration = 0;

ringProgress.style.strokeDasharray = CIRCUMFERENCE;
ringProgress.style.strokeDashoffset = 0;

// ===================== TAB SWITCHING =====================
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tabContent' + capitalize(tab.dataset.tab)).classList.add('active');
  });
});

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ===================== MESSAGING =====================
function sendMessage(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (response) => resolve(response));
  });
}

// ===================== COUNTDOWN TIMER =====================
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
  statusText.textContent = '⏳ Timer läuft im Hintergrund...';
  statusText.className = 'status-running';
}

function showIdleState() {
  btnStart.classList.remove('hidden');
  btnStop.classList.add('hidden');
  inputSection.classList.remove('disabled');
  statusText.textContent = 'Bereit';
  statusText.className = 'status-stopped';
  timeLabel.textContent = 'Kein Timer';
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
    timeLabel.textContent = `${Math.round(ratio * 100)}% verbleibend`;
  } else {
    ringProgress.style.strokeDashoffset = 0;
    timeLabel.textContent = 'Kein Timer';
  }
}

function formatTime(ms) {
  if (ms <= 0) return '00:00';
  const totalSec = Math.ceil(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function pad(n) { return String(n).padStart(2, '0'); }

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
    statusText.textContent = '⚠️ Bitte eine Zeit eingeben!';
    statusText.className = '';
    setTimeout(() => { statusText.textContent = 'Bereit'; }, 2000);
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

// ===================== ALARM TAB =====================

// Returns ms until next occurrence of "HH:MM" today or tomorrow
function msUntilTime(timeStr) {
  if (!timeStr) return null;
  const [hh, mm] = timeStr.split(':').map(Number);
  const now = new Date();
  const target = new Date(now);
  target.setHours(hh, mm, 0, 0);
  let diff = target - now;
  if (diff <= 0) diff += 24 * 60 * 60 * 1000; // next day
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
      document.getElementById(`alarmTime${i}`).value    = alarms[i].time    || '';
      document.getElementById(`alarmEnabled${i}`).checked = alarms[i].enabled || false;
      updateAlarmRowStyle(i, alarms[i].enabled);
    }
    if (callback) callback(alarms);
  });
}

function updateAlarmRowStyle(i, enabled) {
  const row = document.querySelector(`.alarm-row[data-index="${i}"]`);
  if (enabled) row.classList.add('alarm-active');
  else         row.classList.remove('alarm-active');
}

// Toggle style live when checkbox changes
for (let i = 0; i < 4; i++) {
  document.getElementById(`alarmEnabled${i}`).addEventListener('change', (e) => {
    updateAlarmRowStyle(i, e.target.checked);
  });
}

btnSaveAlarms.addEventListener('click', () => {
  const alarms = saveAlarms();
  // Send to background
  chrome.runtime.sendMessage({ action: 'setClockAlarms', alarms });
  alarmStatusText.textContent = '✅ Alarme gespeichert & aktiv!';
  alarmStatusText.style.color = '#10b981';
  setTimeout(() => {
    alarmStatusText.textContent = 'Uhrzeiten eintragen und speichern';
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

    if (!a.enabled || !a.time) {
      miniEl.textContent = '–';
      continue;
    }

    const ms = msUntilTime(a.time);
    miniEl.textContent = formatCountdown(ms);

    // Fire check: exact minute match and not already fired this minute
    if (a.time === nowStr && lastFiredMinute[i] !== minuteKey) {
      lastFiredMinute[i] = minuteKey;
      // Flash row
      const row = document.querySelector(`.alarm-row[data-index="${i}"]`);
      row.classList.add('fired');
      setTimeout(() => row.classList.remove('fired'), 1500);
      // Play sound via background
      chrome.runtime.sendMessage({ action: 'playAlarmSound' });
    }

    // Track nearest alarm for the top display
    if (nextMs === null || ms < nextMs) {
      nextMs = ms;
      nextTimeStr = a.time;
    }
  }

  // Update top countdown
  if (nextMs !== null) {
    alarmNextCountdown.textContent = formatCountdown(nextMs);
    alarmNextTime.textContent = `Nächster Alarm: ${nextTimeStr} Uhr`;
  } else {
    alarmNextCountdown.textContent = '--:--:--';
    alarmNextTime.textContent = 'Keine aktiven Alarme';
  }
}

function startAlarmTick() {
  if (alarmInterval) clearInterval(alarmInterval);
  loadAlarms((alarms) => {
    tickAlarms(alarms);
    alarmInterval = setInterval(() => {
      // Always read from DOM (user might have changed without saving yet)
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

// ===================== STARTUP =====================
chrome.storage.local.get(['timerLoop', 'lastHours', 'lastMinutes', 'lastSeconds'], (data) => {
  if (data.timerLoop !== undefined)   loopCheckbox.checked  = data.timerLoop;
  if (data.lastHours !== undefined)   inputHours.value      = data.lastHours;
  if (data.lastMinutes !== undefined) inputMinutes.value    = data.lastMinutes;
  if (data.lastSeconds !== undefined) inputSeconds.value    = data.lastSeconds;
});

init();
startAlarmTick();

