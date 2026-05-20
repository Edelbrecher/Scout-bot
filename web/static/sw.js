// Service Worker — BIG-EYE-BOT Timer Notifications
// Handles scheduled notifications even when the tab is in the background.
// Note: browsers may suspend idle service workers after ~30s of inactivity.
// The page re-syncs schedules on every load and every 20s while visible.

const scheduled = new Map(); // id → { warnTimeout, fireTimeout }

function clearSchedule(id) {
  const s = scheduled.get(id);
  if (s) {
    clearTimeout(s.warnTimeout);
    clearTimeout(s.fireTimeout);
    scheduled.delete(id);
  }
}

function scheduleReminder(r) {
  clearSchedule(r.id);

  const now  = Date.now();
  const left = r.at - now;
  if (left <= -60000) return; // more than 1 min overdue, skip

  const timeouts = {};

  // Warning 30s before
  const warnDelay = left - 30000;
  if (warnDelay > 0) {
    timeouts.warnTimeout = setTimeout(() => {
      self.registration.showNotification(`⚠️ ${r.label}`, {
        body: 'Erinnerung in 30 Sekunden!',
        icon: '/static/logo.png',
        tag:  `warn-${r.id}`,
        silent: false,
      });
    }, warnDelay);
  }

  // Fire at exact time
  const fireDelay = Math.max(0, left);
  timeouts.fireTimeout = setTimeout(() => {
    self.registration.showNotification(`🔔 ${r.label}`, {
      body:    r.rally ? 'Zum Versammlungsplatz! Klicken zum Öffnen.' : 'Zeit!',
      icon:    '/static/logo.png',
      tag:     `fire-${r.id}`,
      data:    { url: r.rallyUrl || null },
      actions: r.rallyUrl ? [{ action: 'rally', title: '⚔️ Rally Point' }] : [],
      requireInteraction: true,
      silent:  false,
    });
    scheduled.delete(r.id);
  }, fireDelay);

  scheduled.set(r.id, timeouts);
}

function scheduleFarmlist(farm) {
  clearSchedule('__farm__');
  const now  = Date.now();
  const left = farm.ends - now;
  if (left <= 0) return;

  const timeouts = {};
  const warnDelay = left - 30000;
  if (warnDelay > 0) {
    timeouts.warnTimeout = setTimeout(() => {
      self.registration.showNotification('🌾 Farmlist bald!', {
        body:   'Noch 30 Sekunden bis zur nächsten Farmliste.',
        icon:   '/static/logo.png',
        tag:    'farm-warn',
        silent: false,
      });
    }, warnDelay);
  }

  timeouts.fireTimeout = setTimeout(() => {
    self.registration.showNotification('🌾 Farmlist jetzt!', {
      body:               'Zeit, die Farmliste zu schicken!',
      icon:               '/static/logo.png',
      tag:                'farm-fire',
      requireInteraction: true,
      silent:             false,
    });
    scheduled.delete('__farm__');
  }, Math.max(0, left));

  scheduled.set('__farm__', timeouts);
}

self.addEventListener('message', event => {
  const msg = event.data;
  if (!msg) return;

  if (msg.type === 'sync_reminders') {
    // Clear removed reminders
    const incoming = new Set(msg.reminders.map(r => r.id));
    for (const id of scheduled.keys()) {
      if (id !== '__farm__' && !incoming.has(id)) clearSchedule(id);
    }
    // Schedule / re-schedule all
    for (const r of msg.reminders) {
      scheduleReminder(r);
    }
  }

  if (msg.type === 'sync_farm') {
    if (msg.active) {
      scheduleFarmlist(msg);
    } else {
      clearSchedule('__farm__');
    }
  }

  if (msg.type === 'cancel') {
    clearSchedule(msg.id);
  }
});

// Open / focus tab when notification is clicked
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url)
    || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          if (event.action === 'rally' && url) c.navigate(url);
          return c.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});

self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', e  => e.waitUntil(clients.claim()));
