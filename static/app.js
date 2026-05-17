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

// ── Gamepad State ────────────────────────────────────────
let gamepadActive = false;
let gamepadPoller = null;
let gpBtnPrev = {};  // Previous button states for edge detection

const DEADZONE = 0.15;

// ── Gamepad UI Helpers ───────────────────────────────────
function setGamepadUI(connected, name) {
  const dot = $('gamepadDot');
  const txt = $('gamepadText');
  if (connected) {
    dot.classList.add('connected');
    txt.textContent = '🎮 ' + (name || 'Xbox Controller');
  } else {
    dot.classList.remove('connected');
    txt.textContent = '🎮 No Gamepad';
  }
}

// ── Gamepad Connect/Disconnect Events ───────────────────
window.addEventListener('gamepadconnected', (e) => {
  addLog('SYS', '🎮 Gamepad connected: ' + e.gamepad.id);
  setGamepadUI(true, e.gamepad.id.substring(0, 30));
  if (!gamepadPoller) {
    gamepadPoller = setInterval(pollGamepad, 50);
  }
});

window.addEventListener('gamepaddisconnected', (e) => {
  addLog('SYS', '🎮 Gamepad disconnected: ' + e.gamepad.id);
  setGamepadUI(false);
  // If no gamepads remain, stop polling
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const anyConnected = Array.from(pads).some(p => p !== null);
  if (!anyConnected) {
    if (gamepadPoller) { clearInterval(gamepadPoller); gamepadPoller = null; }
    gamepadActive = false;
    // Resume keyboard control if keys are held
    if (activeKeys.size > 0) updateMotorFromKeys();
  }
});

// ── Apply circular deadzone ──────────────────────────────
function applyDeadzone(x, y) {
  const mag = Math.sqrt(x * x + y * y);
  if (mag < DEADZONE) return { x: 0, y: 0 };
  // Rescale so values just outside deadzone start from 0
  const scale = (mag - DEADZONE) / (1 - DEADZONE);
  const nx = (x / mag) * scale;
  const ny = (y / mag) * scale;
  return { x: Math.max(-1, Math.min(1, nx)), y: Math.max(-1, Math.min(1, ny)) };
}

// ── Gamepad Polling Loop (20Hz, 50ms) ────────────────────
function pollGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = Array.from(pads).find(p => p !== null);
  if (!gp) return;

  // ── Axes ──
  const rawLX = gp.axes[0] || 0;  // Left stick X
  const rawLY = gp.axes[1] || 0;  // Left stick Y (up = -1)
  const rawRX = gp.axes[3] || 0;  // Right stick X (alt steering)
  // Right trigger: axis 5 (range 0..1 on Xbox, or -1..1)
  // Also check button 7 (RT) as fallback
  const rawRT = gp.axes[5] !== undefined ? gp.axes[5] : 0;

  // ── Primary stick (left) with circular deadzone ──
  const left = applyDeadzone(rawLX, rawLY);
  // ── Alternative: right stick X + right trigger ──
  const rtNorm = rawRT > 0 ? rawRT : 0;  // Trigger 0..1
  const rightSteerDead = applyDeadzone(rawRX, 0);

  // Determine which input source has more magnitude (last-input-wins by magnitude)
  const leftMag = Math.sqrt(left.x * left.x + left.y * left.y);
  const altThrottle = rtNorm;  // Right trigger as throttle
  const altSteer = Math.abs(rightSteerDead.x);

  // Use whichever has more deflection for throttle
  let throttleVal = 0;   // negative = forward, positive = reverse
  let steerVal = 0;       // negative = left, positive = right

  if (leftMag > 0.01) {
    // Left stick is active — use it for both throttle and steer
    throttleVal = left.y;  // Y: -1 = forward, +1 = reverse (inverted)
    steerVal = left.x;     // X: -1 = left, +1 = right
    gamepadActive = true;
  } else if (altThrottle > 0.01 || altSteer > 0.01) {
    // Alternative controls are active
    throttleVal = altThrottle;  // RT: 0..1 means throttle forward
    steerVal = rightSteerDead.x;
    gamepadActive = true;
  } else {
    // Stick is in deadzone — if we were active, stop
    if (gamepadActive) {
      gamepadActive = false;
      // Clear any active keyboard command tracking and stop
      activeKeys.clear();
      stopMotor();
      // Restore sliders to manual defaults
      restoreSliders();
    }
    handleGamepadButtons(gp);
    return;
  }

  gamepadActive = true;

  // ── Compute motor speed/steer from analog deflection ──
  // Speed = proportional to stick deflection (5% min, 100% max)
  const speedPct = Math.max(5, Math.round(Math.abs(throttleVal) * 100));
  const steerPct = Math.max(5, Math.round(Math.abs(steerVal) * 100));

  // Update sliders visually
  const speedSlider = $('speedSlider');
  const speedValue = $('speedValue');
  const steerSlider = $('steerSlider');
  const steerValue = $('steerValue');
  if (speedSlider) { speedSlider.value = speedPct; speedValue.textContent = speedPct + '%'; motorSpeed = speedPct; }
  if (steerSlider) { steerSlider.value = steerPct; steerValue.textContent = steerPct + '%'; motorSteerRange = steerPct; }

  // ── Determine direction ──
  const isForward = throttleVal < -DEADZONE;
  const isReverse = throttleVal > DEADZONE;
  const isLeft = steerVal < -DEADZONE;
  const isRight = steerVal > DEADZONE;

  let command = null;
  if (isForward && isLeft) command = 'forward_left';
  else if (isForward && isRight) command = 'forward_right';
  else if (isReverse && isLeft) command = 'reverse_left';
  else if (isReverse && isRight) command = 'reverse_right';
  else if (isForward) command = 'forward';
  else if (isReverse) command = 'reverse';
  else if (isLeft) command = 'left';
  else if (isRight) command = 'right';

  if (command) {
    startMotor(command);
  } else {
    stopMotor();
  }

  // ── Handle buttons ──
  handleGamepadButtons(gp);
}

function restoreSliders() {
  // Restore speed slider to 100, steer to 100 when gamepad releases
  const speedSlider = $('speedSlider');
  const speedValue = $('speedValue');
  const steerSlider = $('steerSlider');
  const steerValue = $('steerValue');
  if (speedSlider) { speedSlider.value = 100; speedValue.textContent = '100%'; motorSpeed = 100; }
  if (steerSlider) { steerSlider.value = 100; steerValue.textContent = '100%'; motorSteerRange = 100; }
}

function handleGamepadButtons(gp) {
  // Edge-detect: only fire on button-down (not held)
  for (let i = 0; i < gp.buttons.length; i++) {
    const pressed = gp.buttons[i].pressed;
    const wasDown = gpBtnPrev[i] || false;

    if (pressed && !wasDown) {
      // Button just pressed
      switch (i) {
        case 0: // A — emergency stop
          stopMotor();
          activeKeys.clear();
          if (gamepadActive) { gamepadActive = false; restoreSliders(); }
          addLog('SYS', '🎮 A: Emergency Stop');
          break;
        case 1: // B — toggle connection
          if (connected) {
            doDisconnect();
            addLog('SYS', '🎮 B: Disconnected');
          } else {
            doConnect();
            addLog('SYS', '🎮 B: Connecting...');
          }
          break;
        case 4: // LB — decrease speed
          {
            const slider = $('speedSlider');
            if (slider) {
              const val = Math.max(5, parseInt(slider.value) - 5);
              slider.value = val;
              motorSpeed = val;
              $('speedValue').textContent = val + '%';
              addLog('SYS', '🎮 LB: Speed → ' + val + '%');
            }
          }
          break;
        case 5: // RB — increase speed
          {
            const slider = $('speedSlider');
            if (slider) {
              const val = Math.min(100, parseInt(slider.value) + 5);
              slider.value = val;
              motorSpeed = val;
              $('speedValue').textContent = val + '%';
              addLog('SYS', '🎮 RB: Speed → ' + val + '%');
            }
          }
          break;
      }
    }

    gpBtnPrev[i] = pressed;
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

  // Space always stops, even during gamepad input
  if (e.key === ' ') {
    stopMotor();
    activeKeys.clear();
    gamepadActive = false;
    restoreSliders();
  } else if (!gamepadActive) {
    // Keyboard motor input only when gamepad is not actively controlling
    updateMotorFromKeys();
  }
});

document.addEventListener('keyup', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (!activeKeys.has(e.key)) return;
  activeKeys.delete(e.key);
  if (throttleKeys[e.key] || steerKeys[e.key]) {
    e.preventDefault();
    if (!gamepadActive) {
      updateMotorFromKeys();
    }
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
addLog('SYS', 'Motor: Hold WASD/D-pad/gamepad for continuous control (20Hz).');
