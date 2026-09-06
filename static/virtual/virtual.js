/*
 * Garden Kart — Virtual Race client (P1: standalone dev mode).
 * Dev mode runs a local demo physics so the renderer can be QA'd
 * without the server. Live mode (P3) renders ONLY server snapshots (D6).
 */
import {
  RoadRenderer, buildTrack, drawCar, drawBanana,
  SEG_LEN, ROAD_HALF_WIDTH,
} from './road.js';

const cfg = window.VR_CONFIG || { mode: 'dev', trackLength: 2000, user: null };

const canvas = document.getElementById('road');
const hudFps = document.getElementById('vr-fps');
const hudSpeed = document.getElementById('vr-speed');
const badge = document.getElementById('vr-mode');

const track = buildTrack(cfg.trackLength || 2000, 42);

if (cfg.mode === 'dev') badge.textContent = 'DEV MODE';

/* ------------------------------------------------------------------ input */
const keys = new Set();
window.addEventListener('keydown', (e) => {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) e.preventDefault();
  keys.add(e.key.toLowerCase());
});
window.addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));

const PALETTE = ['#ff2d95', '#00e5ff', '#ffd319', '#7cff6b', '#ff8c42', '#b388ff', '#ff5252', '#40c4ff'];

/* ------------------------------------------------------------- dev physics */
const dev = {
  pos: 0, lane: 0, speed: 0, steer: 0,
  top: 60, accel: 15,
  npcs: [],
  bananas: [],
};
for (let i = 0; i < 6; i++) {
  dev.npcs.push({
    id: 'npc' + i,
    name: 'BOT ' + (i + 1),
    color: PALETTE[i % PALETTE.length],
    pos: 120 + i * 170 + Math.floor((i * 97) % 90),
    lane: [-0.66, 0, 0.66][i % 3],
    targetLane: [-0.66, 0, 0.66][i % 3],
    speed: 24 + (i * 3.7) % 16,
    nextChange: 3 + i * 2.4,
    items: [],
  });
}
dev.bananas = [430, 905, 1560].map((p, i) => ({ pos: p, lane: [-0.5, 0.4, -0.1][i] }));

function devStep(dt, t) {
  const throttle = keys.has('arrowup') || keys.has('w') ? 1 : 0;
  const brake = keys.has('arrowdown') || keys.has('s') || keys.has(' ') ? 1 : 0;
  dev.steer = (keys.has('arrowleft') || keys.has('a') ? -1 : 0) + (keys.has('arrowright') || keys.has('d') ? 1 : 0);

  const targetSpeed = throttle * dev.top * (1 + (keys.has('shift') ? 0.75 : 0));
  if (brake) dev.speed = Math.max(0, dev.speed - 60 * dt);
  else dev.speed += Math.min(dev.accel, Math.max(-dev.accel * 2, targetSpeed - dev.speed)) * dt *
    (dev.speed < targetSpeed ? 1 : 1);
  dev.speed = Math.max(0, Math.min(dev.speed, dev.top * 1.8));

  dev.lane = Math.max(-1, Math.min(1, dev.lane + dev.steer * 1.4 * dt));
  dev.pos = (dev.pos + dev.speed * dt) % track.length;

  for (const n of dev.npcs) {
    if (t > n.nextChange) {
      const lanes = [-0.66, 0, 0.66];
      n.targetLane = lanes[Math.floor(Math.random() * 3)];
      n.nextChange = t + 4 + Math.random() * 6;
    }
    const d = n.targetLane - n.lane;
    n.lane += Math.sign(d) * Math.min(Math.abs(d), 0.5 * dt);
    n.pos = (n.pos + n.speed * dt) % track.length;
  }
}

/* ----------------------------------------------------------------- render */
const renderer = new RoadRenderer(canvas, track);
let last = performance.now();
let fpsAcc = 0, fpsN = 0, fpsShown = 0;

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  const t = now / 1000;

  if (cfg.mode === 'dev') devStep(dt, t);

  const view = { position: cfg.mode === 'dev' ? dev.pos : 0, playerX: cfg.mode === 'dev' ? dev.lane : 0 };

  renderer.drawSky();
  renderer.renderRoad(view);

  // bananas
  for (const b of (cfg.mode === 'dev' ? dev.bananas : [])) {
    const pr = renderer.projectWorld(b.pos, b.lane, view);
    if (pr && !pr.clipped) drawBanana(renderer.ctx, pr.x, pr.y, pr.wpx);
  }

  // remote cars + NPCs, far -> near
  const sprites = [];
  if (cfg.mode === 'dev') {
    for (const n of dev.npcs) {
      const dz = ((n.pos - dev.pos) % track.length + track.length) % track.length;
      if (dz > 2 && dz < 500) sprites.push({ dz, car: n, lane: n.lane, pos: n.pos });
    }
  }
  sprites.sort((a, b) => b.dz - a.dz);
  for (const s of sprites) {
    const pr = renderer.projectWorld(s.pos, s.lane, view);
    if (pr && !pr.clipped) drawCar(renderer.ctx, pr.x, pr.y, pr.wpx, s.car);
  }

  // player car: fixed at bottom, bounce with speed
  if (cfg.mode === 'dev') {
    const bounce = Math.sin(t * 30) * (dev.speed / dev.top) * 2.5;
    drawCar(renderer.ctx, renderer.w / 2, renderer.h * 0.94 + bounce,
      renderer.h * 0.16, { color: '#7cff6b', name: '' }, { steerVis: dev.steer });
  }

  // HUD
  fpsAcc += dt; fpsN++;
  if (fpsAcc >= 0.5) { fpsShown = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }
  hudFps.textContent = fpsShown + ' fps';
  hudSpeed.textContent = cfg.mode === 'dev'
    ? Math.round(dev.speed * 3.6) + ' km/h' : '--';

  window.__VR.frames++;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// expose for headless QA asserts
window.__VR = { frames: 0, track, dev, renderer };
