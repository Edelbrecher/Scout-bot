// background.js - Service Worker

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({
    timerRunning: false,
    timerDuration: 0,
    timerRemaining: 0,
    timerStartedAt: null,
    timerLoop: true,
    clockAlarms: []
  });
});

// Listen for messages from popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'startTimer') {
    startTimer(message.duration);
    sendResponse({ success: true });
  } else if (message.action === 'stopTimer') {
    stopTimer();
    sendResponse({ success: true });
  } else if (message.action === 'getStatus') {
    getTimerStatus().then(status => sendResponse(status));
    return true;
  } else if (message.action === 'setClockAlarms') {
    setClockAlarms(message.alarms);
    sendResponse({ success: true });
  } else if (message.action === 'playAlarmSound') {
    triggerSound();
    sendResponse({ success: true });
  }
  return true;
});

// Listen for alarms (chrome.alarms API)
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'timerAlarm') {
    await triggerSound();
    const data = await chrome.storage.local.get(['timerDuration', 'timerRunning', 'timerLoop']);
    if (data.timerRunning && data.timerLoop) {
      const now = Date.now();
      await chrome.storage.local.set({ timerRemaining: data.timerDuration, timerStartedAt: now });
      chrome.alarms.create('timerAlarm', { delayInMinutes: data.timerDuration / 60000 });
    } else {
      await chrome.storage.local.set({ timerRunning: false });
    }
  }

  // Clock alarm fired
  if (alarm.name.startsWith('clockAlarm_')) {
    await triggerSound();
    // Re-schedule for next day (24h later)
    chrome.alarms.create(alarm.name, { delayInMinutes: 24 * 60 });
  }
});

// ===================== COUNTDOWN TIMER =====================
async function startTimer(durationMs) {
  await chrome.alarms.clear('timerAlarm');
  const now = Date.now();
  await chrome.storage.local.set({
    timerRunning: true,
    timerDuration: durationMs,
    timerRemaining: durationMs,
    timerStartedAt: now
  });
  chrome.alarms.create('timerAlarm', { delayInMinutes: durationMs / 60000 });
}

async function stopTimer() {
  await chrome.alarms.clear('timerAlarm');
  await chrome.storage.local.set({ timerRunning: false, timerRemaining: 0, timerStartedAt: null });
}

async function getTimerStatus() {
  const data = await chrome.storage.local.get(['timerRunning', 'timerDuration', 'timerRemaining', 'timerStartedAt']);
  if (data.timerRunning && data.timerStartedAt) {
    const elapsed = Date.now() - data.timerStartedAt;
    const remaining = Math.max(0, data.timerDuration - elapsed);
    return { running: true, duration: data.timerDuration, remaining };
  }
  return { running: false, duration: data.timerDuration || 0, remaining: 0 };
}

// ===================== CLOCK ALARMS =====================
async function setClockAlarms(alarms) {
  await chrome.storage.local.set({ clockAlarms: alarms });

  // Clear all existing clock alarms
  for (let i = 0; i < 4; i++) {
    await chrome.alarms.clear(`clockAlarm_${i}`);
  }

  // Schedule enabled alarms
  for (let i = 0; i < alarms.length; i++) {
    const a = alarms[i];
    if (!a.enabled || !a.time) continue;

    const delayMs = msUntilTime(a.time);
    chrome.alarms.create(`clockAlarm_${i}`, { delayInMinutes: delayMs / 60000 });
  }
}

function msUntilTime(timeStr) {
  const [hh, mm] = timeStr.split(':').map(Number);
  const now = new Date();
  const target = new Date(now);
  target.setHours(hh, mm, 0, 0);
  let diff = target - now;
  if (diff <= 0) diff += 24 * 60 * 60 * 1000;
  return diff;
}

// ===================== SOUND =====================
async function triggerSound() {
  try {
    const existingContexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    if (existingContexts.length === 0) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: ['AUDIO_PLAYBACK'],
        justification: 'Timer/Alarm - Benachrichtigungston abspielen'
      });
    }
    chrome.runtime.sendMessage({ action: 'playSound' });
  } catch (e) {
    console.error('Sound error:', e);
  }
}

// Restore clock alarms on service worker restart
chrome.storage.local.get(['clockAlarms'], async (data) => {
  if (!data.clockAlarms || data.clockAlarms.length === 0) return;
  for (let i = 0; i < data.clockAlarms.length; i++) {
    const a = data.clockAlarms[i];
    if (!a.enabled || !a.time) continue;
    const existing = await chrome.alarms.get(`clockAlarm_${i}`);
    if (!existing) {
      const delayMs = msUntilTime(a.time);
      chrome.alarms.create(`clockAlarm_${i}`, { delayInMinutes: delayMs / 60000 });
    }
  }
});
