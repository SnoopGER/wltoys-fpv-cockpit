/*
 * Virtual Race — pseudo-3D road renderer.
 *
 * Road/projection model forked from the architecture of
 * "Javascript Pseudo 3D Racer" by Jake Gordon
 * (https://github.com/jakesgordon/javascript-racer, MIT License).
 * Same segment/curve/hill projection model with credit; implementation
 * written for Garden Kart Virtual Race (units in meters).
 *
 * D6: all motion comes from the server snapshot. This module draws,
 * it does not decide.
 */

export const SEG_LEN = 5;            // meters per road segment
export const ROAD_HALF_WIDTH = 7;    // meters (arcade-wide lane set)
export const CAM_HEIGHT = 3.2;       // meters above road
export const FOV_DEG = 100;
export const DRAW_SEGMENTS = 110;    // ~550 m draw distance
export const CAR_LEN = 1.6;          // meters, sprite scale reference

export const CAM_DEPTH = 1 / Math.tan(((FOV_DEG / 2) * Math.PI) / 180);

/* Deterministic LCG so every client builds the SAME track. */
function makeRng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/* Build a looping track of >= trackLength meters: curves + hills,
   closed by a straight + a section easing back to y=0 for a seamless loop. */
export function buildTrack(trackLength, seed = 42) {
  const rng = makeRng(seed);
  const segs = [];
  let y = 0;
  const ease = (t) => (1 - Math.cos(Math.PI * t)) / 2;
  const push = (n, curve, hillH) => {
    const y0 = y;
    for (let i = 0; i < n; i++) {
      y = y0 + hillH * ease((i + 1) / n);
      segs.push({ index: segs.length, curve, y });
    }
  };
  while (segs.length * SEG_LEN < trackLength) {
    const kind = rng();
    const len = 14 + Math.floor(rng() * 22);
    // curve units are per-segment-screen-space (see note in RoadRenderer):
    // jakesgordon's 6 @ segLen=200 ~= 0.15 @ segLen=5
    if (kind < 0.3) push(len, 0, 0);                                    // straight
    else if (kind < 0.55) push(len, 0, (rng() - 0.5) * 16);             // hill (±8 m)
    else if (kind < 0.8) push(len, (rng() - 0.5) * 0.16, (rng() - 0.5) * 4); // gentle curve
    else push(len, (rng() - 0.5) * 0.30, (rng() - 0.5) * 3);            // hard curve
  }
  // long straight + ease back to y=0 so the wrap is seamless
  push(24, 0, 0);
  const closeFrom = y;
  const tail = 16;
  for (let i = 0; i < tail; i++) {
    y = closeFrom * (1 - ease((i + 1) / tail));
    segs.push({ index: segs.length, curve: 0, y });
  }
  return {
    segments: segs,
    length: segs.length * SEG_LEN,
    segAt: (pos) => segs[Math.floor(mod(pos, segs.length * SEG_LEN) / SEG_LEN)],
  };
}

const PAL = {
  skyTop: '#12061f',
  skyMid: '#3b0f5e',
  ground: '#0b0416',
  groundAlt: '#0d0519',
  road: ['#191327', '#1c1530'],
  rumble: ['#00e5ff', '#ff2d95'],
  lane: '#ffd319',
};

function projectY(worldY, camY, relZ, h) {
  const scale = CAM_DEPTH / relZ;
  return Math.round(h / 2 - (scale * (worldY - camY) * h) / 2);
}

export class RoadRenderer {
  constructor(canvas, track) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.track = track;
    this.proj = new Array(DRAW_SEGMENTS + 1).fill(0).map(() => ({ x: 0, y: 0, w: 0, clip: 0 }));
    this.base = 0;
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = Math.max(16, Math.floor(this.canvas.clientWidth * dpr));
    this.h = Math.max(16, Math.floor(this.canvas.clientHeight * dpr));
    this.canvas.width = this.w;
    this.canvas.height = this.h;
  }

  drawSky() {
    const { ctx, w, h } = this;
    const horizon = this.horizonY();
    const g = ctx.createLinearGradient(0, 0, 0, horizon);
    g.addColorStop(0, PAL.skyTop);
    g.addColorStop(1, PAL.skyMid);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, horizon + 1);

    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    for (let i = 0; i < 60; i++) {
      ctx.fillRect((i * 137.5) % w, (i * 61.3) % (horizon * 0.7), 2, 2);
    }

    // neon sun with retro bands
    const r = h * 0.16, sx = w / 2, sy = horizon - r * 0.25;
    const sg = ctx.createLinearGradient(0, sy - r, 0, sy + r);
    sg.addColorStop(0, '#ffd319');
    sg.addColorStop(1, '#ff2d95');
    ctx.save();
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = sg;
    ctx.fillRect(sx - r, sy - r, r * 2, r * 2);
    ctx.fillStyle = PAL.skyMid;
    for (let i = 0; i < 6; i++) {
      ctx.fillRect(sx - r, sy - r * 0.15 + i * (r * 0.28), r * 2, 2 + i * 1.6);
    }
    ctx.restore();

    ctx.fillStyle = PAL.ground;
    ctx.fillRect(0, horizon, w, h - horizon);
  }

  horizonY() {
    // recompute from current view each frame? cheap approximation:
    return this._horizon || this.h * 0.45;
  }

  /* view = { position (m along track), playerX (-1..1 lane units) } */
  renderRoad(view) {
    const { ctx, w, h } = this;
    const segs = this.track.segments;
    const n = segs.length;
    const trackLen = this.track.length;
    const base = Math.floor(view.position / SEG_LEN) % n;
    const basePercent = (view.position % SEG_LEN) / SEG_LEN;
    const playerY = segs[base].y + (segs[(base + 1) % n].y - segs[base].y) * basePercent;
    const camY = playerY + CAM_HEIGHT;

    let x = 0;
    let dx = -(segs[base].curve * basePercent);
    let maxy = h;
    this.base = base;
    const offset = view.position % SEG_LEN;

    for (let i = 0; i <= DRAW_SEGMENTS; i++) {
      const seg = segs[(base + i) % n];
      // distance of this segment's start/end from the camera (jakesgordon
      // model: p1 may be negative for the base segment, then it's skipped)
      const z1 = i * SEG_LEN - offset;
      const z2 = z1 + SEG_LEN;

      // store projection for sprites + use for road
      const p = this.proj[i];
      p.z1 = z1;
      p.scale1 = CAM_DEPTH / Math.max(z1, 0.1);
      p.scale2 = CAM_DEPTH / Math.max(z2, 0.1);
      p.x1 = Math.round(w / 2 + (p.scale1 * (x - view.playerX * ROAD_HALF_WIDTH) * w) / 2);
      p.x2 = Math.round(w / 2 + (p.scale2 * (x + dx - view.playerX * ROAD_HALF_WIDTH) * w) / 2);
      p.y1 = projectY(seg.y, camY, Math.max(z1, 0.1), h);
      p.y2 = projectY(segs[(base + i + 1) % n].y, camY, Math.max(z2, 0.1), h);
      p.w1 = (p.scale1 * ROAD_HALF_WIDTH * w) / 2;
      p.w2 = (p.scale2 * ROAD_HALF_WIDTH * w) / 2;
      p.clip = maxy;
      p.seg = seg;

      x += dx;
      dx += seg.curve;

      if (z1 <= CAM_DEPTH || p.y2 >= p.y1 || p.y2 >= maxy) continue;

      const alt = Math.floor(seg.index / 3) % 2;
      // grass band
      ctx.fillStyle = alt ? PAL.ground : PAL.groundAlt;
      ctx.fillRect(0, p.y2, w, p.y1 - p.y2 + 1);
      // rumble strips
      quad(ctx, p.x1 - p.w1 * 1.14, p.y1, p.x1 - p.w1, p.y1, p.x2 - p.w2, p.y2, p.x2 - p.w2 * 1.14, p.y2, PAL.rumble[alt]);
      quad(ctx, p.x1 + p.w1 * 1.14, p.y1, p.x1 + p.w1, p.y1, p.x2 + p.w2, p.y2, p.x2 + p.w2 * 1.14, p.y2, PAL.rumble[alt]);
      // asphalt
      quad(ctx, p.x1 - p.w1, p.y1, p.x1 + p.w1, p.y1, p.x2 + p.w2, p.y2, p.x2 - p.w2, p.y2, PAL.road[alt]);
      // lane markers every other group
      if (alt) {
        for (const lx of [-1 / 3, 1 / 3]) {
          quad(ctx,
            p.x1 + p.w1 * lx - p.w1 * 0.015, p.y1,
            p.x1 + p.w1 * lx + p.w1 * 0.015, p.y1,
            p.x2 + p.w2 * lx + p.w2 * 0.015, p.y2,
            p.x2 + p.w2 * lx - p.w2 * 0.015, p.y2,
            PAL.lane);
        }
      }
      // distance fog
      const fog = i / DRAW_SEGMENTS;
      if (fog > 0.18) {
        ctx.fillStyle = `rgba(18,6,31,${Math.min(0.72, (fog - 0.18) * 0.9)})`;
        ctx.fillRect(0, p.y2, w, p.y1 - p.y2 + 1);
      }
      maxy = p.y2;
    }
    this._horizon = this.proj[Math.min(DRAW_SEGMENTS, 40)].y2 || this._horizon;
  }

  /* Project a world point to screen using the road pass projections.
     pos = meters along track, lane = -1..1. Null if behind/too far/clipped. */
  projectWorld(pos, lane, view) {
    const dz = mod(pos - view.position, this.track.length);
    if (dz <= 2 || dz >= (DRAW_SEGMENTS - 1) * SEG_LEN) return null;
    const offset = view.position % SEG_LEN;
    const i = Math.floor((dz + offset) / SEG_LEN);
    const f = ((dz + offset) % SEG_LEN) / SEG_LEN;
    const a = this.proj[i];
    if (!a || !a.seg) return null;
    const x = lerp(a.x1, a.x2, f) + lane * lerp(a.w1, a.w2, f);
    const y = lerp(a.y1, a.y2, f);
    const wpx = lerp(a.w1, a.w2, f);
    return { x, y, wpx, clipped: y > a.clip };
  }
}

function quad(ctx, x1, y1, x2, y2, x3, y3, x4, y4, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.lineTo(x3, y3);
  ctx.lineTo(x4, y4);
  ctx.closePath();
  ctx.fill();
}

function lerp(a, b, t) { return a + (b - a) * t; }
function mod(a, n) { return ((a % n) + n) % n; }

/* Vector car sprite — no bitmaps, zero asset deps. */
export function drawCar(ctx, x, y, wpx, car, opts = {}) {
  const w = Math.max(8, wpx * 0.62);
  const h = w * 0.55;
  ctx.save();
  ctx.translate(x, y);
  if (opts.steerVis) {
    ctx.translate(opts.steerVis * w * 0.06, 0);
    ctx.rotate(opts.steerVis * 0.05);
  }
  ctx.fillStyle = 'rgba(0,0,0,0.45)';
  ctx.beginPath();
  ctx.ellipse(0, 0, w * 0.55, h * 0.16, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = car.color || '#888';
  roundRect(ctx, -w / 2, -h, w, h * 0.78, w * 0.12);
  ctx.fill();
  ctx.fillStyle = 'rgba(10,6,24,0.85)';
  roundRect(ctx, -w * 0.32, -h * 0.95, w * 0.64, h * 0.42, w * 0.07);
  ctx.fill();
  ctx.fillStyle = '#ff3b3b';
  ctx.fillRect(-w / 2 + w * 0.06, -h * 0.36, w * 0.16, h * 0.1);
  ctx.fillRect(w / 2 - w * 0.22, -h * 0.36, w * 0.16, h * 0.1);
  if (car.items && car.items.includes('star')) {
    ctx.strokeStyle = `rgba(255,211,25,${0.5 + 0.4 * Math.sin(Date.now() / 90)})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.ellipse(0, -h * 0.5, w * 0.66, h * 0.8, 0, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (car.name && !opts.hideTag) {
    ctx.font = `${Math.max(10, Math.min(15, w * 0.2))}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    const tw = ctx.measureText(car.name).width;
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    ctx.fillRect(-tw / 2 - 4, -h * 1.6, tw + 8, 16);
    ctx.fillStyle = '#fff';
    ctx.fillText(car.name, 0, -h * 1.6 + 12);
  }
  ctx.restore();
}

export function drawBanana(ctx, x, y, wpx) {
  const w = Math.max(5, wpx * 0.16);
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = '#ffd319';
  ctx.beginPath();
  ctx.ellipse(0, -w * 0.25, w * 0.6, w * 0.22, -0.45, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
