/* ── FPV Debug Cockpit — Frontend Logic ────────────────── */

// ── State ────────────────────────────────────────────────
let connected = false;
let streaming = false;
let logPoller = null;
let statusPoller = null;
let allLogs = [];
let motorInterval = null;   // Continuous motor command sender
let currentCommand = null;  // Currently held command
let motorSpeed = 100;       // Throttle power 0-100%
let motorSteerRange = 100;  // Steering angle 0-100%

// ── DOM Refs ─────────────────────────────────────────────
const $ = id => document.getElementById(id);
const statusDot = $('statusDot');
const statusText = $('statusText');
const statusPill = $('statusPill');
const fpsBadge = $('fpsBadge');
const videoFeed = $('videoFeed');
const videoOverlay = $('videoOverlay');
const btnConnect = $('btnConnect');
const btnDisconnect = $('btnDisconnect');
const logContainer = $('logContainer');

// ── Connection ───────────────────────────────────────────
async function doConnect() {
  addLog('SYS', 'Connecting...');
  btnConnect.disabled = true;
  setStatus('connecting', 'CONNECTING...');

  try {
    const resp = await fetch('/api/connect', { method: 'POST' });
    const data = await resp.json();

    if (data.ok) {
      connected = true;
      setStatus('connected', 'CONNECTED');
      btnConnect.disabled = true;
      btnDisconnect.disabled = false;

      // Start video stream
      videoFeed.src = '/api/stream?t=' + Date.now();
      videoFeed.classList.add('active');
      videoOverlay.classList.add('hidden');
      streaming = true;

      // Start polling
      startPolling();
      addLog('SYS', 'Connected! Video stream started.');
    } else {
      setStatus('error', 'FAILED');
      btnConnect.disabled = false;
      addLog('ERR', 'Connection failed.');
    }
  } catch (e) {
    setStatus('error', 'ERROR');
    btnConnect.disabled = false;
    addLog('ERR', 'Connection error: ' + e.message);
  }
}

async function doDisconnect() {
  stopMotor();
  try { await fetch('/api/disconnect', { method: 'POST' }); } catch (e) {}

  connected = false;
  streaming = false;
  videoFeed.src = '';
  videoFeed.classList.remove('active');
  videoOverlay.classList.remove('hidden');
  setStatus('disconnected', 'DISCONNECTED');
  btnConnect.disabled = false;
  btnDisconnect.disabled = true;
  stopPolling();
  addLog('SYS', 'Disconnected.');
}

// ── Status Polling ───────────────────────────────────────
function startPolling() {
  stopPolling();
  statusPoller = setInterval(pollStatus, 1000);
  logPoller = setInterval(pollLogs, 500);
}

function stopPolling() {
  if (statusPoller) clearInterval(statusPoller);
  if (logPoller) clearInterval(logPoller);
  statusPoller = null;
  logPoller = null;
}

async function pollStatus() {
  try {
    const resp = await fetch('/api/status');
    const s = await resp.json();

    if (s.state === 'streaming' && !streaming) {
      streaming = true;
      setStatus('streaming', 'STREAMING');
      videoOverlay.classList.add('hidden');
    }

    $('infoPackets').textContent = s.packets_received.toLocaleString();
    $('infoFrames').textContent = s.frames_assembled.toLocaleString();
    $('infoDropped').textContent = s.frames_dropped;
    $('infoIFrames').textContent = s.i_frames;
    $('infoPFrames').textContent = s.p_frames;
    $('infoLastSize').textContent = s.last_frame_size ? (s.last_frame_size / 1024).toFixed(1) + ' KB' : '--';
    $('infoBitrate').textContent = s.current_bitrate ? s.current_bitrate.toFixed(0) + ' kbps' : '--';
    $('infoBuffer').textContent = s.pending_frames;
    $('infoUptime').textContent = s.uptime ? formatTime(s.uptime) : '--';
    $('infoHB').textContent = s.heartbeat_count;

    if (s.current_fps > 0) {
      fpsBadge.textContent = s.current_fps.toFixed(1) + ' fps';
      fpsBadge.style.color = s.current_fps > 15 ? 'var(--green)' : s.current_fps > 5 ? 'var(--yellow)' : 'var(--red)';
    }
  } catch (e) {}
}

async function pollLogs() {
  try {
    const resp = await fetch('/api/logs');
    const data = await resp.json();
    if (data.logs && data.logs.length > 0) {
      for (const entry of data.logs) {
        addLog(entry.level, entry.msg, entry.ts);
      }
    }
  } catch (e) {}
}

// ── Motor Control (continuous 20Hz while held) ───────────
function startMotor(command) {
  if (!connected) return;
  if (currentCommand === command) return; // Already running this

  // Clear old interval WITHOUT sending stop (avoids brief centering between commands)
  if (motorInterval) {
    clearInterval(motorInterval);
    motorInterval = null;
  }
  currentCommand = command;

  // Send immediately, then every 50ms (20Hz)
  sendMotorCmd(command);
  motorInterval = setInterval(() => sendMotorCmd(command), 50);
}

function stopMotor() {
  if (motorInterval) {
    clearInterval(motorInterval);
    motorInterval = null;
  }
  currentCommand = null;

  // Send neutral
  if (connected) sendMotorCmd('stop');
}

async function sendMotorCmd(command) {
  try {
    await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, speed: motorSpeed, steer_range: motorSteerRange }),
    });
  } catch (e) {}
}

// ── Raw Sender ───────────────────────────────────────────
async function sendRaw() {
  const hex = $('rawHex').value.trim();
  const port = parseInt($('rawPort').value);

  if (!hex) {
    addLog('WARN', 'No hex data to send');
    return;
  }

  try {
    const resp = await fetch('/api/send_raw', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hex, port }),
    });
    const data = await resp.json();
    if (data.ok) {
      addLog('TX', `Sent ${data.sent}B raw to port ${port}`);
    } else {
      addLog('ERR', 'Raw send failed: ' + (data.error || 'unknown'));
    }
  } catch (e) {
    addLog('ERR', 'Raw send error: ' + e.message);
  }
}

// ── Keyboard Controls (WASD + arrows) ────────────────────
// Keys mapped to THROTTLE or STEER axis (not directions)
const throttleKeys = { 'w': true, 'W': true, 'ArrowUp': true,
                       's': true, 'S': true, 'ArrowDown': true };
const steerKeys    = { 'a': true, 'A': true, 'ArrowLeft': true,
                       'd': true, 'D': true, 'ArrowRight': true };
const isForwardKey = k => ['w','W','ArrowUp'].includes(k);
const isReverseKey = k => ['s','S','ArrowDown'].includes(k);
const isLeftKey    = k => ['a','A','ArrowLeft'].includes(k);
const isRightKey   = k => ['d','D','ArrowRight'].includes(k);

const activeKeys = new Set();

// Compute combined command from all held keys
function computeCommand() {
  let throttle = null; // 'forward' | 'reverse' | null
  let steer = null;    // 'left' | 'right' | null

  for (const k of activeKeys) {
    if (isForwardKey(k)) throttle = 'forward';
    else if (isReverseKey(k)) throttle = 'reverse';
    if (isLeftKey(k)) steer = 'left';
    else if (isRightKey(k)) steer = 'right';
  }

  if (!throttle && !steer) return 'stop';
  if (throttle && !steer) return throttle;
  if (!throttle && steer) return steer;
  return throttle + '_' + steer; // forward_left, forward_right, reverse_left, reverse_right
}

function updateMotorFromKeys() {
  const cmd = computeCommand();
  if (cmd === 'stop') {
    stopMotor();
  } else {
    startMotor(cmd);
  }
}

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (activeKeys.has(e.key)) return; // Prevent repeat
  const isMotor = throttleKeys[e.key] || steerKeys[e.key];
  if (!isMotor && e.key !== ' ') return;
  e.preventDefault();
  activeKeys.add(e.key);

  if (e.key === ' ') {
    stopMotor();
    activeKeys.clear();
  } else {
    updateMotorFromKeys();
  }
});

document.addEventListener('keyup', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (!activeKeys.has(e.key)) return;
  activeKeys.delete(e.key);
  if (throttleKeys[e.key] || steerKeys[e.key]) {
    e.preventDefault();
    updateMotorFromKeys();
  }
});

// ── UI Helpers ───────────────────────────────────────────
function setStatus(state, text) {
  statusText.textContent = text;
  statusDot.className = 'status-dot ' + state;
}

function addLog(level, msg, ts) {
  ts = ts || new Date().toLocaleTimeString('en-US', { hour12: false });

  const levelMap = {
    'OK': 'ok', 'INFO': 'info', 'WARN': 'warn',
    'ERROR': 'error', 'ERR': 'error', 'TX': 'tx', 'RX': 'rx', 'SYS': 'info',
  };
  const filterMap = {
    'TX': 'logFilterTx', 'RX': 'logFilterRx',
    'OK': 'logFilterInfo', 'INFO': 'logFilterInfo', 'SYS': 'logFilterInfo',
    'WARN': 'logFilterWarn', 'ERROR': 'logFilterError', 'ERR': 'logFilterError',
  };

  const cls = levelMap[level] || 'info';
  const filterId = filterMap[level] || 'logFilterInfo';
  const filterEl = $(filterId);

  const entry = document.createElement('div');
  entry.className = `log-entry log-${cls}`;
  entry.dataset.level = level;
  entry.innerHTML = `
    <span class="log-time">${ts}</span>
    <span class="log-level">${level}</span>
    <span class="log-msg">${escapeHtml(msg)}</span>
  `;

  if (filterEl && !filterEl.checked) {
    entry.style.display = 'none';
  }

  logContainer.appendChild(entry);
  allLogs.push(entry);

  while (allLogs.length > 500) {
    const old = allLogs.shift();
    old.remove();
  }

  logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLog() {
  logContainer.innerHTML = '';
  allLogs = [];
  addLog('SYS', 'Log cleared.');
}

function filterLogs() {
  const filters = {
    'TX': $('logFilterTx').checked, 'RX': $('logFilterRx').checked,
    'OK': $('logFilterInfo').checked, 'INFO': $('logFilterInfo').checked,
    'SYS': $('logFilterInfo').checked, 'WARN': $('logFilterWarn').checked,
    'ERROR': $('logFilterErr').checked, 'ERR': $('logFilterErr').checked,
  };
  for (const entry of allLogs) {
    entry.style.display = (filters[entry.dataset.level] !== false) ? '' : 'none';
  }
}

function toggleFullscreen() {
  const container = $('videoContainer');
  if (document.fullscreenElement) document.exitFullscreen();
  else container.requestFullscreen().catch(() => {});
}

function snapshot() {
  if (!videoFeed.src) return;
  const a = document.createElement('a');
  a.href = videoFeed.src;
  a.download = `fpv_snapshot_${Date.now()}.jpg`;
  a.click();
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Slider Controls ─────────────────────────────────────
function initSliders() {
  const speedSlider = $('speedSlider');
  const speedValue = $('speedValue');
  const steerSlider = $('steerSlider');
  const steerValue = $('steerValue');

  if (speedSlider) {
    speedSlider.addEventListener('input', () => {
      motorSpeed = parseInt(speedSlider.value);
      speedValue.textContent = motorSpeed + '%';
    });
  }
  if (steerSlider) {
    steerSlider.addEventListener('input', () => {
      motorSteerRange = parseInt(steerSlider.value);
      steerValue.textContent = motorSteerRange + '%';
    });
  }
}

// ── Init ─────────────────────────────────────────────────
initSliders();
addLog('SYS', '🏎️ FPV Debug Cockpit loaded.');
addLog('SYS', 'Car: WL_FPV_CAR_99613492 @ 172.16.11.1');
addLog('SYS', 'Codec: H.264 Baseline 640×360 @ 20fps');
addLog('SYS', 'Motor: Hold WASD/D-pad for continuous control (20Hz).');
