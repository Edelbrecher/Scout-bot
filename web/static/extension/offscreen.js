// offscreen.js - Handles audio playback in offscreen document

chrome.runtime.onMessage.addListener((message) => {
  if (message.action === 'playSound') {
    if (message.soundType === 2) {
      playBong();
    } else {
      playBeep();
    }
  }
});

// Timer 1: 3 high beeps (880Hz → 440Hz)
function playBeep() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;

  const ctx = new AudioContext();
  const beepPattern = [0, 0.3, 0.6];

  beepPattern.forEach((delay) => {
    const oscillator = ctx.createOscillator();
    const gainNode = ctx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, ctx.currentTime + delay);
    oscillator.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + delay + 0.2);

    gainNode.gain.setValueAtTime(0.8, ctx.currentTime + delay);
    gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.25);

    oscillator.start(ctx.currentTime + delay);
    oscillator.stop(ctx.currentTime + delay + 0.25);
  });
}

// Timer 2: 2 low bong tones (330Hz → 220Hz, slower decay)
function playBong() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;

  const ctx = new AudioContext();
  const bongPattern = [0, 0.5];

  bongPattern.forEach((delay) => {
    const oscillator = ctx.createOscillator();
    const gainNode = ctx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(330, ctx.currentTime + delay);
    oscillator.frequency.exponentialRampToValueAtTime(220, ctx.currentTime + delay + 0.4);

    gainNode.gain.setValueAtTime(0.8, ctx.currentTime + delay);
    gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.4);

    oscillator.start(ctx.currentTime + delay);
    oscillator.stop(ctx.currentTime + delay + 0.4);
  });
}
