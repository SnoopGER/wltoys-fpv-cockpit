/*
 * Garden Kart — Virtual Race client.
 *
 * Dev mode (?dev=1): local demo physics, renderer QA without server.
 * Live mode: renders ONLY server snapshots (D6). Input is emitted at 20 Hz;
 * nothing in the browser has authority over physics, items or scoring.
 */
import {
  RoadRenderer, buildTrack, drawCar, drawBanana,
} from './road.js';

const cfg = window.VR_CONFIG || { mode: 'dev', trackLength: 2000, user: null };
const TRACK_LEN = cfg.trackLength || 2000;

const $ = (id) => document.getElementById(id);
const canvas = $('road');
const hudFps = $('vr-fps');
const hudSpeed = $('vr-speed');
const badge = $('vr-mode');
const countdownEl = $('vr-countdown');
const posEl = $('vr-pos');
const lapEl = $('vr-lap');
const itemsEl = $('vr-items');
const flashEl = $('vr-flash');
const toastEl = $('vr-toast');
const resultsEl = $('vr-results');
const statusEl = $('vr-status');

const track = buildTrack(TRACK_LEN, 42);
const renderer = new RoadRenderer(canvas, track);

const ITEM_SLOTS = [
  { key: '1', item: 'boost', icon: '🚀', name: 'BOOST' },
  { key: '2', item: 'redshell', icon: '🐚', name: 'SHELL' },
  { key: '3', item: 'banana', icon: '🍌', name: 'BANANA' },
  { key: '4', item: 'star', icon: '⭐', name: 'STAR' },
];

/* ------------------------------------------------------------------ input */
const keys = new Set();
const pendingItems = new Set();
window.addEventListener('keydown', (e) => {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) e.preventDefault();
  const k = e.key.toLowerCase();
  if (!keys.has(k)) {
    const slot = ITEM_SLOTS.find((s) => s.key === e.key);
    if (slot) pendingItems.add(slot.item);
  }
  keys.add(k);
});
window.addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));

function readGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const p of pads) {
    if (!p) continue;
    const steer = Math.abs(p.axes[0]) > 0.15 ? p.axes[0] : 0;
    const throttle = p.buttons[7] ? p.buttons[7].value : 0;
    const brake = !!(p.buttons[6] && p.buttons[6].pressed);
    const btnItem = { 0: 'boost', 2: 'redshell', 1: 'banana', 3: 'star' };
    for (const [b, item] of Object.entries(btnItem)) {
      if (p.buttons[b] && p.buttons[b].pressed && !readGamepad._held?.[b]) {
        pendingItems.add(item);
      }
      readGamepad._held = readGamepad._held || {};
      readGamepad._held[b] = p.buttons[b] && p.buttons[b].pressed;
    }
    if (steer || throttle || brake) return { steer, throttle, brake };
  }
  return null;
}

function keyboardInput() {
  const throttle = keys.has('arrowup') || keys.has('w') ? 1 : 0;
  const brake = keys.has('arrowdown') || keys.has('s') || keys.has(' ');
  const steer = (keys.has('arrowleft') || keys.has('a') ? -1 : 0)
    + (keys.has('arrowright') || keys.has('d') ? 1 : 0);
  return { throttle, steer, brake };
}

/* ------------------------------------------------------------- dev physics */
/* (dev world is constructed near the bottom; live mode never touches it) */

/* --------------------------------------------------------------- live state */
const live = {
  socket: null,
  connected: false,
  snap: null,
  recvAt: 0,
  events: [],
  myId: (cfg.user && cfg.user.id) || null,
};

function buildItemHud() {
  itemsEl.innerHTML = '';
  for (const s of ITEM_SLOTS) {
    const div = document.createElement('div');
    div.className = 'vr-item';
    div.id = 'vri-' + s.item;
    div.innerHTML = `<span class="vr-item-key">${s.key}</span>` +
      `<span class="vr-item-icon">${s.icon}</span>`;
    itemsEl.appendChild(div);
  }
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.style.opacity = 1;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.style.opacity = 0; }, 2200);
}

function connectLive() {
  if (typeof io === 'undefined') { toast('socket.io missing'); return; }
  const socket = io({ withCredentials: true, reconnection: true });
  live.socket = socket;
  socket.on('connect', () => {
    live.connected = true;
    statusEl.textContent = '';
    socket.emit('vr:join');          // fires again on every reconnect (D21)
  });
  socket.on('disconnect', () => {
    live.connected = false;
    statusEl.textContent = 'connection lost — reconnecting…';
  });
  socket.on('vr:error', (e) => toast('virtual race: ' + e.error));
  socket.on('vr:item:ack', (e) => {
    if (!e.ok && e.error) toast('item: ' + e.error.replace(/_/g, ' '));
  });
  socket.on('vr:snapshot', (s) => {
    live.snap = s;
    live.recvAt = performance.now();
    live.events = live.events.concat(s.events || []).slice(-80);
  });
}

/* 20 Hz input cadence, mirrors the real cockpit */
function inputPump() {
  setInterval(() => {
    if (!live.socket || !live.connected) return;
    const gp = readGamepad();
    const base = gp || keyboardInput();
    const payload = {
      steer: base.steer, throttle: base.throttle, brake: !!base.brake,
      client_ts: Date.now(),
    };
    if (pendingItems.size) {
      payload.item = pendingItems.values().next().value;
      pendingItems.delete(payload.item);
    }
    live.socket.volatile.emit('vr:input', payload);
  }, 50);
}

/* extrapolate a car from the latest snapshot (never run physics as authority
   — this is display smoothing only; next snapshot corrects it) */
function exPos(car) {
  if (!live.snap) return 0;
  const dt = Math.min(0.35, (performance.now() - live.recvAt) / 1000);
  return car.total_pos + car.speed * dt;
}

/* ----------------------------------------------------------------- render */
let last = performance.now();
let fpsAcc = 0, fpsN = 0, fpsShown = 0;

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  const t = now / 1000;

  let view, sprites, traps, playerCar = null, steerVis = 0;

  if (cfg.mode === 'dev') {
    devStep(dt, t);
    view = { position: dev.pos, playerX: dev.lane };
    sprites = dev.npcs.map((n) => ({ ...n, total_pos: n.pos }))
      .filter((n) => {
        const dz = mod(n.pos - dev.pos, track.length);
        return dz > 2 && dz < 500;
      })
      .map((n) => ({ ...n, pos_for_view: n.pos }));
    traps = dev.bananas;
    steerVis = dev.steer;
  } else {
    const s = live.snap;
    const me = s && s.cars.find((c) => c.id === live.myId);
    playerCar = me || null;
    const myPos = me ? exPos(me) : 0;
    const myLane = me ? me.lane : 0;
    view = { position: myPos, playerX: myLane };
    sprites = [];
    if (s) {
      for (const c of s.cars) {
        if (c.id === live.myId) continue;
        // raw (unwrapped) gap: a rival a full lap behind must NOT appear ahead
        const dz = exPos(c) - myPos;
        if (dz > 2 && dz < 500) {
          sprites.push({ id: c.id, name: c.name, color: c.color,
                         items: c.items, pos_for_view: myPos + dz,
                         lane: c.lane });
        }
      }
      for (const n of s.npcs) {
        const dz = mod(n.pos - myPos, track.length);
        if (dz > 2 && dz < 500) {
          sprites.push({ id: n.id, name: '', color: '#5c6b8a',
                         items: [], pos_for_view: myPos + dz, lane: n.lane });
        }
      }
      traps = s.traps.map((tr) => ({ pos: myPos + mod(tr.pos - mod(myPos, TRACK_LEN), TRACK_LEN), lane: tr.lane }));
    } else {
      traps = [];
    }
    steerVis = me ? me.lane * 0 + (keyboardInput().steer || readGamepad()?.steer || 0) : 0;
    updateLiveHud(s, me, t);
  }

  renderer.drawSky();
  renderer.renderRoad(view);

  for (const b of traps || []) {
    const pr = renderer.projectWorld(b.pos, b.lane, view);
    if (pr && !pr.clipped) drawBanana(renderer.ctx, pr.x, pr.y, pr.wpx);
  }
  sprites.sort((a, b) => {
    const dza = mod(a.pos_for_view - view.position, track.length);
    const dzb = mod(b.pos_for_view - view.position, track.length);
    return dzb - dza;
  });
  for (const s of sprites) {
    const pr = renderer.projectWorld(s.pos_for_view, s.lane, view);
    if (pr && !pr.clipped) drawCar(renderer.ctx, pr.x, pr.y, pr.wpx, s);
  }

  if (cfg.mode === 'dev' || playerCar) {
    const speed = cfg.mode === 'dev' ? dev.speed : playerCar.speed;
    const bounce = Math.sin(t * 30) * (speed / 60) * 2.5;
    drawCar(renderer.ctx, renderer.w / 2, renderer.h * 0.94 + bounce,
      renderer.h * 0.16,
      { color: cfg.mode === 'dev' ? '#7cff6b' : playerCar.color,
        items: cfg.mode === 'dev' ? [] : playerCar.items, name: '' },
      { steerVis, hideTag: true });
  }

  fpsAcc += dt; fpsN++;
  if (fpsAcc >= 0.5) { fpsShown = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }
  hudFps.textContent = fpsShown + ' fps';

  window.__VR.frames++;
  requestAnimationFrame(frame);
}

/* -------------------------------------------------------------- live HUD */
function updateLiveHud(s, me, t) {
  if (!s) { hudSpeed.textContent = '--'; return; }
  hudSpeed.textContent = me ? Math.round(me.kmh) + ' km/h' : '--';

  if (s.state === 'green' && updateLiveHud._prev !== 'green') {
    updateLiveHud._goUntil = t + 1.0;
  }
  updateLiveHud._prev = s.state;
  if (s.state === 'countdown' && s.countdown_remaining != null) {
    countdownEl.style.display = 'flex';
    countdownEl.textContent = Math.ceil(s.countdown_remaining);
  } else if (t < (updateLiveHud._goUntil || 0)) {
    countdownEl.style.display = 'flex';
    countdownEl.textContent = 'GO!';
  } else {
    countdownEl.style.display = 'none';
  }

  posEl.textContent = me ? `P${me.rank || '-'}` : '';
  lapEl.textContent = me ? `LAP ${me.lap}/${s.laps}` : '';

  for (const slot of ITEM_SLOTS) {
    const el = $('vri-' + slot.item);
    if (!el) continue;
    const cd = me && me.cooldowns ? (me.cooldowns[slot.item] || 0) : 0;
    const active = me && me.items && me.items.includes(slot.item);
    el.classList.toggle('vr-item-cd', cd > 0);
    el.classList.toggle('vr-item-active', !!active);
    el.style.setProperty('--cd', cd > 0 ? Math.min(1, cd / 20) : 0);
  }

  if (me && me.incoming_shell_in != null) {
    flashEl.style.opacity = 0.25 + 0.55 * Math.abs(Math.sin(t * 12));
  } else {
    flashEl.style.opacity = 0;
  }

  if (s.state === 'finished' && s.results.length) {
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = '<h2>RACE RESULTS</h2>' + s.results.map((r) => {
      const c = s.cars.find((cc) => cc.id === r.user) || { name: r.user };
      return `<div class="vr-res-row"><b>${r.place}.</b> ${escapeHtml(c.name || r.user)}${r.dnf ? ' <i>(dnf)</i>' : ''}</div>`;
    }).join('');
  } else if (s.state !== 'finished') {
    resultsEl.style.display = 'none';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function mod(a, n) { return ((a % n) + n) % n; }

/* ------------------------------------------------- dev mode (unchanged) */
let devObj = {
  pos: 0, lane: 0, speed: 0, steer: 0,
  top: 60, accel: 15, npcs: [], bananas: [],
};
const devColors = ['#ff2d95', '#00e5ff', '#ffd319', '#7cff6b', '#ff8c42', '#b388ff'];
for (let i = 0; i < 6; i++) {
  devObj.npcs.push({
    id: 'npc' + i, name: 'BOT ' + (i + 1), color: devColors[i % 6],
    pos: 120 + i * 170 + Math.floor((i * 97) % 90),
    lane: [-0.66, 0, 0.66][i % 3], targetLane: [-0.66, 0, 0.66][i % 3],
    speed: 24 + (i * 3.7) % 16, nextChange: 3 + i * 2.4, items: [],
  });
}
devObj.bananas = [430, 905, 1560].map((p, i) => ({ pos: p, lane: [-0.5, 0.4, -0.1][i] }));
const dev = devObj;

function devStep(dt, t) {
  const inp = readGamepad() || keyboardInput();
  dev.steer = inp.steer;
  const target = inp.throttle * dev.top * (keys.has('shift') ? 1.75 : 1);
  if (inp.brake) dev.speed = Math.max(0, dev.speed - 60 * dt);
  else dev.speed += Math.min(dev.accel, Math.max(-dev.accel * 2, target - dev.speed)) * dt;
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
  hudSpeed.textContent = Math.round(dev.speed * 3.6) + ' km/h';
}

/* ------------------------------------------------------------------ boot */
if (cfg.mode === 'dev') {
  badge.textContent = 'DEV MODE';
} else if (!cfg.user) {
  statusEl.textContent = 'log in from the cockpit to race';
  badge.textContent = 'VIRTUAL RACE — LOGIN REQUIRED';
} else {
  badge.textContent = 'LIVE';
  buildItemHud();
  connectLive();
  inputPump();
}

requestAnimationFrame(frame);

// expose for headless QA asserts
window.__VR = { frames: 0, track, dev, renderer, live };
