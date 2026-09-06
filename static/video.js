// FPV WebCodecs video path (Phase 3): raw H.264 over /ws/video -> <canvas>.
//
// Zero-build ES module. Adapts to the existing MJPEG plumbing by wrapping
// window.startVideoStream/stopVideoStream: if the browser supports WebCodecs
// and the token handshake works, frames render on a canvas overlaid on
// #videoFeed; otherwise (HEVC car, no WebCodecs, WS failure, stream stall)
// the original MJPEG <img> path takes over untouched. Never a dead screen.
//
// Exposes window.FPVVideo = { mode, stats, snapshot }.

(function () {
  'use strict';

  const H264_CONFIGS = [
    'avc1.640015', // High, lvl 2.1
    'avc1.4d001f', // Main, lvl 3.1
    'avc1.42001f', // Baseline, lvl 3.1
  ];

  const state = {
    mode: 'idle', // idle | mjpeg | webcodecs
    ws: null,
    decoder: null,
    canvas: null,
    ctx: null,
    configIdx: 0,
    configured: false,
    framesDecoded: 0,
    framesDropped: 0,
    lastFrameAt: 0,
    lastRenderAt: 0,
    wantStream: false,
    restartTimer: null,
    stallTimer: null,
    generation: 0,
  };

  function el(id) { return document.getElementById(id); }

  function supportedCodec() {
    if (!(window.VideoDecoder && window.EncodedVideoChunk && window.VideoFrame)) return null;
    if (navigator.userAgent && /Firefox/.test(navigator.userAgent)) {
      // Firefox ships WebCodecs but not H.264 decode on most builds — MJPEG is safer.
      return null;
    }
    return 'h264';
  }

  function ensureCanvas() {
    if (state.canvas) return state.canvas;
    const container = el('videoContainer');
    if (!container) return null;
    const c = document.createElement('canvas');
    c.id = 'fpvCanvas';
    c.style.position = 'absolute';
    c.style.inset = '0';
    c.style.width = '100%';
    c.style.height = '100%';
    c.style.objectFit = 'contain';
    c.style.zIndex = '2';
    c.style.display = 'none';
    container.appendChild(c);
    state.canvas = c;
    state.ctx = c.getContext('2d');
    return c;
  }

  function showCanvas(on) {
    const c = ensureCanvas();
    const img = el('videoFeed');
    if (c) c.style.display = on ? 'block' : 'none';
    if (img) img.classList[on ? 'remove' : 'add']('active');
  }

  function stopWebCodecs() {
    state.generation++;
    if (state.restartTimer) { clearTimeout(state.restartTimer); state.restartTimer = null; }
    if (state.stallTimer) { clearInterval(state.stallTimer); state.stallTimer = null; }
    try { if (state.ws) state.ws.close(); } catch (e) {}
    state.ws = null;
    try { if (state.decoder && state.decoder.state !== 'closed') state.decoder.close(); } catch (e) {}
    state.decoder = null;
    state.configured = false;
  }

  function fallbackToMjpeg(reason) {
    if (state.mode === 'mjpeg') return;
    const wasMode = state.mode;
    stopWebCodecs();
    state.mode = 'mjpeg';
    showCanvas(false);
    try {
      // REVIEW WEB-1 (2026-09-06): the wrapper set streaming=true when it
      // started WebCodecs; the original startVideoStream would then
      // early-return and the fallback would be a permanently blank screen.
      if (window.setStreaming) window.setStreaming(false);
      if (window.FPVVideo && typeof window.__startVideoStreamOriginal === 'function') {
        window.__startVideoStreamOriginal();
      }
    } catch (e) {}
    if (window.addLog) {
      window.addLog('VIDEO', 'WebCodecs unavailable (' + reason + ') — using MJPEG fallback' +
                    (wasMode === 'webcodecs' ? ', stream recovered' : ''));
    }
  }

  function watchdogStall() {
    // if frames stop for >4s while we believe we're streaming, recover
    if (state.mode !== 'webcodecs') return;
    const now = performance.now();
    if (state.lastFrameAt && now - state.lastFrameAt > 4000) {
      if (window.addLog) window.addLog('WARN', 'WebCodecs stream stalled — resyncing');
      // one internal retry, then MJPEG
      if (state.framesDecoded > 0 && !state._stallRetried) {
        state._stallRetried = true;
        restartWs();
      } else {
        fallbackToMjpeg('stall');
      }
    }
  }

  function restartWs() {
    stopWebCodecsKeepMode();
    // REVIEW WEB-4 (2026-09-06): capture gen AFTER the generation bump —
    // capturing before made the new socket's staleness guard discard every
    // frame forever after the first decode desync.
    const gen = state.generation;
    if (state.wantStream) {
      if (state.restartTimer) clearTimeout(state.restartTimer);
      state.restartTimer = setTimeout(() => {
        if (state.wantStream && gen === state.generation) startWs(gen);
      }, 500);
    }
  }

  function stopWebCodecsKeepMode() {
    state.generation++;
    if (state.restartTimer) { clearTimeout(state.restartTimer); state.restartTimer = null; }
    if (state.stallTimer) { clearInterval(state.stallTimer); state.stallTimer = null; }
    try { if (state.ws) state.ws.close(); } catch (e) {}
    state.ws = null;
    try { if (state.decoder && state.decoder.state !== 'closed') state.decoder.close(); } catch (e) {}
    state.decoder = null;
    state.configured = false;
  }

  function renderFrame(frame) {
    const c = state.canvas;
    if (!c) { frame.close(); return; }
    if (c.width !== frame.displayWidth || c.height !== frame.displayHeight) {
      c.width = frame.displayWidth;
      c.height = frame.displayHeight;
    }
    state.ctx.drawImage(frame, 0, 0);
    state.lastRenderAt = performance.now();
    state._stallRetried = false;
    frame.close();
  }

  async function configureDecoder(desc, codec) {
    for (let i = state.configIdx; i < H264_CONFIGS.length; i++) {
      try {
        await state.decoder.configure(Object.assign({ codec: H264_CONFIGS[i] }, desc));
        state.configIdx = i;
        state.configured = true;
        if (window.addLog) window.addLog('VIDEO', 'WebCodecs decoder live (' + H264_CONFIGS[i] + ')');
        return true;
      } catch (e) { /* try next profile */ }
    }
    fallbackToMjpeg('no usable H.264 profile');
    return false;
  }

  function onBitstreamFrame(chunk, isKey) {
    try {
      state.decoder.decode(new EncodedVideoChunk(
        { type: isKey ? 'key' : 'delta', timestamp: chunk.timeUs, data: chunk.data }));
    } catch (e) {
      // decoder desynced — request resync from server backlog
      restartWs();
    }
  }

  function parseAnnexB(data) {
    // split one WS message (may hold one frame) -> treat whole message as a
    // unit; car frames arrive as complete Annex-B access units already.
    let isKey = false;
    let i = 0;
    while (i < data.length - 4) {
      let nalOff = -1;
      if (data[i] === 0 && data[i + 1] === 0) {
        if (data[i + 2] === 1) { nalOff = i + 3; }
        else if (data[i + 2] === 0 && data[i + 3] === 1) { nalOff = i + 4; }
      }
      if (nalOff > 0) {
        const t = data[nalOff] & 0x1f;
        if (t === 5 || t === 7) isKey = true;
        i = nalOff;
      } else {
        i++;
      }
    }
    return { isKey };
  }

  async function startWs(gen) {
    let tok;
    try {
      const url = (window.fpvApiUrl ? window.fpvApiUrl('/api/video-token') : '/api/video-token?car=car1');
      const resp = await fetch(url, { credentials: 'include' });
      if (!resp.ok) return fallbackToMjpeg('token http ' + resp.status);
      tok = await resp.json();
    } catch (e) {
      return fallbackToMjpeg('token fetch failed');
    }
    if (tok.codec && tok.codec !== 'h264') return fallbackToMjpeg('codec ' + tok.codec);

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const carQ = (window.fpvApiUrl ? window.fpvApiUrl('/x').split('car=')[1] : 'car1');
    let ws;
    try {
      ws = new WebSocket(proto + '//' + location.host + '/ws/video/' +
        encodeURIComponent(carQ || 'car1') + '?token=' + encodeURIComponent(tok.token));
    } catch (e) {
      return fallbackToMjpeg('ws construct failed');
    }
    ws.binaryType = 'arraybuffer';
    state.ws = ws;

    let firstData = false;
    ws.onmessage = (ev) => {
      if (gen !== state.generation) return;
      if (typeof ev.data === 'string') {
        if (ev.data.indexOf('fpv-meta:') === 0) return; // codec meta
        if (ev.data === 'fpv-auth-error' || ev.data === 'fpv-auth-revoked') {
          return fallbackToMjpeg(ev.data);
        }
        return;
      }
      const data = new Uint8Array(ev.data);
      if (!firstData) {
        firstData = true;
        if (state.decoder && state.decoder.state === 'closed') {
          initDecoder();
        }
      }
      state.lastFrameAt = performance.now();
      if (!state.decoder) initDecoder();
      if (!state.configured) {
        // buffer until decoder configured: configure happens async on first key
        if (!state._pending) state._pending = [];
        state._pending.push(data);
        const { isKey } = parseAnnexB(data);
        if (isKey && state.decoder && state.decoder.state !== 'closed') {
          configureDecoder({}, 'h264').then((ok) => {
            if (!ok) return;
            const pend = state._pending || [];
            state._pending = [];
            for (const p of pend) {
              const k = parseAnnexB(p);
              onBitstreamFrame({ data: p, timeUs: 0 }, k.isKey);
            }
          });
        }
        return;
      }
      const { isKey } = parseAnnexB(data);
      state.framesDecoded++;
      onBitstreamFrame({ data: data, timeUs: 0 }, isKey);
    };
    ws.onclose = () => {
      if (gen !== state.generation) return;
      if (state.wantStream && state.mode === 'webcodecs') {
        state.restartTimer = setTimeout(() => {
          if (state.wantStream && gen !== state.generation) return;
          if (state.wantStream) startWs(state.generation);
        }, 1000);
      }
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  function initDecoder() {
    state.decoder = new VideoDecoder({
      output: (frame) => {
        if (gen_ok()) renderFrame(frame); else frame.close();
      },
      error: (e) => {
        if (window.addLog) window.addLog('WARN', 'WebCodecs decoder error: ' + e.message);
        state.configured = false;
        state.configIdx++;
        if (state.configIdx >= H264_CONFIGS.length) {
          fallbackToMjpeg('decoder errors');
        } else {
          restartWs();
        }
      },
    });
  }

  function gen_ok() { return state.mode === 'webcodecs'; }

  function apiUrlBase() {
    return (window.location.origin || '');
  }

  // ---- public API ----
  window.FPVVideo = {
    get mode() { return state.mode; },
    stats() {
      return {
        mode: state.mode,
        framesDecoded: state.framesDecoded,
        lastFrameAgeMs: state.lastFrameAt ? Math.round(performance.now() - state.lastFrameAt) : null,
        renderLatencyMs: (state.lastFrameAt && state.lastRenderAt)
          ? Math.round(state.lastRenderAt - state.lastFrameAt) : null,
      };
    },
    snapshot() {
      return (state.mode === 'webcodecs' && state.canvas) ? state.canvas.toDataURL('image/jpeg', 0.9) : null;
    },
    start() {
      if (state.mode === 'webcodecs') return;
      const codec = supportedCodec();
      if (!codec) { return fallbackToMjpeg('no WebCodecs'); }
      state.wantStream = true;
      state.generation++;
      state.mode = 'webcodecs';
      state.configIdx = 0;
      ensureCanvas();
      showCanvas(true);
      initDecoder();
      startWs(state.generation);
      if (state.stallTimer) clearInterval(state.stallTimer);
      state.stallTimer = setInterval(watchdogStall, 2000);
      if (window.addLog) window.addLog('VIDEO', 'WebCodecs path starting (raw H.264 over websocket)');
    },
    stop() {
      state.wantStream = false;
      stopWebCodecs();
      state.mode = 'idle';
      showCanvas(false);
    },
  };

  // ---- adapt the existing MJPEG plumbing ----
  // Wrap startVideoStream/stopVideoStream defined by app.js (loaded as a
  // classic script BEFORE this module executes).
  function adapt() {
    if (typeof window.startVideoStream !== 'function') {
      return setTimeout(adapt, 50);
    }
    const origStart = window.startVideoStream;
    const origStop = window.stopVideoStream;
    window.__startVideoStreamOriginal = origStart;
    window.startVideoStream = function () {
      if (supportedCodec()) {
        window.FPVVideo.start();
        if (window.setStreaming) window.setStreaming(true);
        const ov = el('videoOverlay');
        if (ov) ov.classList.add('hidden');
      } else {
        origStart();
      }
    };
    window.stopVideoStream = function () {
      window.FPVVideo.stop();
      origStop();
    };
  }
  adapt();
})();
