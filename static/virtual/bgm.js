/*
 * Virtual Race — procedural synthwave BGM (WebAudio, zero assets).
 * Deliberately NOT javascript-racer's bundled music (licensed only for
 * that project). This is an original A-minor arpeggio loop.
 */
export function createBgm() {
  let ctx = null, master = null, timer = null, step = 0, nextT = 0;
  let enabled = true;

  const BPM = 112, STEP = 60 / BPM / 4;      // 16ths

  // A minor: A C E G / F A C / D F A / E G# B (i - VI - III - v-ish)
  const bass = [
    55.00, 55.00, 110.0, 55.00, 55.00, 110.0, 82.41, 55.00,   // Am
    43.65, 43.65, 87.31, 43.65, 43.65, 87.31, 65.41, 43.65,   // F
    65.41, 65.41, 130.8, 65.41, 65.41, 130.8, 98.00, 65.41,   // C
    41.20, 41.20, 82.41, 41.20, 41.20, 82.41, 61.74, 41.20,   // E
  ];
  const lead = [
    440.0, 0, 523.25, 0, 659.25, 0, 523.25, 0,
    349.23, 0, 440.0, 0, 523.25, 0, 440.0, 0,
    523.25, 0, 659.25, 0, 783.99, 0, 659.25, 0,
    659.25, 0, 493.88, 0, 617.25, 0, 493.88, 0,
  ];

  function pluck(t, freq, dur, type, gainV, filterHz) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type; o.frequency.value = freq;
    let node = o;
    if (filterHz) {
      const f = ctx.createBiquadFilter();
      f.type = 'lowpass'; f.frequency.value = filterHz;
      node.connect(f); node = f;
    }
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gainV, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    node.connect(g); g.connect(master);
    o.start(t); o.stop(t + dur + 0.05);
  }

  function kick(t) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine';
    o.frequency.setValueAtTime(150, t);
    o.frequency.exponentialRampToValueAtTime(45, t + 0.12);
    g.gain.setValueAtTime(0.9, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
    o.connect(g); g.connect(master);
    o.start(t); o.stop(t + 0.2);
  }

  function hat(t) {
    const len = 0.04;
    const buf = ctx.createBuffer(1, ctx.sampleRate * len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const f = ctx.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 7000;
    const g = ctx.createGain(); g.gain.value = 0.12;
    src.connect(f); f.connect(g); g.connect(master);
    src.start(t);
  }

  function schedule() {
    while (nextT < ctx.currentTime + 0.12) {
      const s = step % 32;
      if (bass[s]) pluck(nextT, bass[s], 0.22, 'sawtooth', 0.16, 320);
      if (lead[s]) pluck(nextT + 0.005, lead[s], 0.30, 'square', 0.05, 2400);
      if (s % 4 === 0) kick(nextT);
      if (s % 2 === 1) hat(nextT);
      nextT += STEP;
      step++;
    }
  }

  function start() {
    if (timer) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain(); master.gain.value = enabled ? 0.5 : 0;
    const delay = ctx.createDelay(1.0);
    delay.delayTime.value = STEP * 3;
    const fb = ctx.createGain(); fb.gain.value = 0.30;
    master.connect(delay); delay.connect(fb); fb.connect(delay);
    const wet = ctx.createGain(); wet.gain.value = 0.35;
    delay.connect(wet); wet.connect(ctx.destination);
    master.connect(ctx.destination);
    nextT = ctx.currentTime + 0.1;
    timer = setInterval(schedule, 40);
  }

  function toggle() {
    enabled = !enabled;
    if (master) master.gain.setTargetAtTime(enabled ? 0.5 : 0, ctx.currentTime, 0.05);
    return enabled;
  }

  // browsers block audio until a gesture — arm on first key/pointer
  const arm = () => {
    if (!timer) start();
    window.removeEventListener('keydown', arm);
    window.removeEventListener('pointerdown', arm);
  };
  window.addEventListener('keydown', arm);
  window.addEventListener('pointerdown', arm);

  return { start, toggle, isOn: () => enabled };
}
