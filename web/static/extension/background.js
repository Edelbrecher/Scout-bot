// background.js - Service Worker

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({
    timerRunning: false,
    timerDuration: 0,
    timerRemaining: 0,
    timerStartedAt: null,
    timerLoop: true,
    timer2Running: false,
    timer2Duration: 0,
    timer2Remaining: 0,
    timer2StartedAt: null,
    timer2Loop: true,
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
  } else if (message.action === 'startTimer2') {
    startTimer2(message.duration);
    sendResponse({ success: true });
  } else if (message.action === 'stopTimer2') {
    stopTimer2();
    sendResponse({ success: true });
  } else if (message.action === 'getStatus2') {
    getTimerStatus2().then(status => sendResponse(status));
    return true;
  } else if (message.action === 'setClockAlarms') {
    setClockAlarms(message.alarms);
    sendResponse({ success: true });
  } else if (message.action === 'playAlarmSound') {
    triggerSound(1);
    sendResponse({ success: true });
  }
  return true;
});

// Listen for alarms (chrome.alarms API)
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'timerAlarm1') {
    await triggerSound(1);
    const data = await chrome.storage.local.get(['timerDuration', 'timerRunning', 'timerLoop']);
    if (data.timerRunning && data.timerLoop) {
      const now = Date.now();
      await chrome.storage.local.set({ timerRemaining: data.timerDuration, timerStartedAt: now });
      chrome.alarms.create('timerAlarm1', { delayInMinutes: data.timerDuration / 60000 });
    } else {
      await chrome.storage.local.set({ timerRunning: false });
    }
  }

  if (alarm.name === 'timerAlarm2') {
    await triggerSound(2);
    const data = await chrome.storage.local.get(['timer2Duration', 'timer2Running', 'timer2Loop']);
    if (data.timer2Running && data.timer2Loop) {
      const now = Date.now();
      await chrome.storage.local.set({ timer2Remaining: data.timer2Duration, timer2StartedAt: now });
      chrome.alarms.create('timerAlarm2', { delayInMinutes: data.timer2Duration / 60000 });
    } else {
      await chrome.storage.local.set({ timer2Running: false });
    }
  }

  // Clock alarm fired
  if (alarm.name.startsWith('clockAlarm_')) {
    await triggerSound(1);
    chrome.alarms.create(alarm.name, { delayInMinutes: 24 * 60 });
  }
});

// ===================== COUNTDOWN TIMER 1 =====================
async function startTimer(durationMs) {
  await chrome.alarms.clear('timerAlarm1');
  const now = Date.now();
  await chrome.storage.local.set({
    timerRunning: true,
    timerDuration: durationMs,
    timerRemaining: durationMs,
    timerStartedAt: now
  });
  chrome.alarms.create('timerAlarm1', { delayInMinutes: durationMs / 60000 });
}

async function stopTimer() {
  await chrome.alarms.clear('timerAlarm1');
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

// ===================== COUNTDOWN TIMER 2 =====================
async function startTimer2(durationMs) {
  await chrome.alarms.clear('timerAlarm2');
  const now = Date.now();
  await chrome.storage.local.set({
    timer2Running: true,
    timer2Duration: durationMs,
    timer2Remaining: durationMs,
    timer2StartedAt: now
  });
  chrome.alarms.create('timerAlarm2', { delayInMinutes: durationMs / 60000 });
}

async function stopTimer2() {
  await chrome.alarms.clear('timerAlarm2');
  await chrome.storage.local.set({ timer2Running: false, timer2Remaining: 0, timer2StartedAt: null });
}

async function getTimerStatus2() {
  const data = await chrome.storage.local.get(['timer2Running', 'timer2Duration', 'timer2Remaining', 'timer2StartedAt']);
  if (data.timer2Running && data.timer2StartedAt) {
    const elapsed = Date.now() - data.timer2StartedAt;
    const remaining = Math.max(0, data.timer2Duration - elapsed);
    return { running: true, duration: data.timer2Duration, remaining };
  }
  return { running: false, duration: data.timer2Duration || 0, remaining: 0 };
}

// ===================== CLOCK ALARMS =====================
async function setClockAlarms(alarms) {
  await chrome.storage.local.set({ clockAlarms: alarms });

  for (let i = 0; i < 4; i++) {
    await chrome.alarms.clear(`clockAlarm_${i}`);
  }

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
async function triggerSound(soundType) {
  try {
    const existingContexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    if (existingContexts.length === 0) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: ['AUDIO_PLAYBACK'],
        justification: 'Timer/Alarm - Benachrichtigungston abspielen'
      });
    }
    chrome.runtime.sendMessage({ action: 'playSound', soundType: soundType || 1 });
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
