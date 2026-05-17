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
let lobbyState = null;
let canControl = false;
let socket = null;
const userRole = document.body.dataset.userRole || 'guest';
const userId = document.body.dataset.userId || '';

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

function isLoggedIn() { return userRole !== 'guest'; }
function isAdmin() { return userRole === 'admin'; }
function isDriverRole() { return userRole === 'admin' || userRole === 'driver'; }

function startVideoStream() {
  if (streaming) return;
  videoFeed.src = '/api/stream?t=' + Date.now();
  videoFeed.classList.add('active');
  videoOverlay.classList.add('hidden');
  streaming = true;
}

function stopVideoStream() {
  streaming = false;
  videoFeed.src = '';
  videoFeed.classList.remove('active');
  videoOverlay.classList.remove('hidden');
}

// ── Connection ───────────────────────────────────────────
async function doConnect() {
  if (!isAdmin()) return;
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

      startVideoStream();

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
  if (!isAdmin()) return;
  stopMotor();
  try { await fetch('/api/disconnect', { method: 'POST' }); } catch (e) {}

  connected = false;
  stopVideoStream();
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

    connected = ['connected', 'streaming'].includes(s.state);
    if (s.state === 'streaming' && !streaming) {
      setStatus('streaming', 'STREAMING');
      if (isLoggedIn()) startVideoStream();
    } else if (connected) {
      setStatus('connected', 'CONNECTED');
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
  if (!connected || !canControl) return;
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
  if (connected && canControl) sendMotorCmd('stop');
}

async function sendMotorCmd(command) {
  try {
    const payload = { command, speed: motorSpeed, steer_range: motorSteerRange, client_ts: Date.now() / 1000 };
    if (socket && socket.connected) {
      socket.emit('control:command', payload);
      return;
    }
    await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (e) {}
}

// ── Lights Toggle ────────────────────────────────────────
let lightsOn = false;

async function toggleLights() {
  if (!connected || !canControl) return;
  lightsOn = !lightsOn;
  try {
    const resp = await fetch('/api/lights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on: lightsOn }),
    });
    const data = await resp.json();
    if (data.ok) {
      addLog('TX', '💡 Lights ' + (lightsOn ? 'ON' : 'OFF'));
      const btn = $('btnLights');
      if (btn) btn.textContent = lightsOn ? '💡 LIGHTS ON' : '💡 LIGHTS';
    } else {
      addLog('ERR', 'Lights toggle failed: ' + (data.error || 'unknown'));
      lightsOn = !lightsOn;  // Revert on failure
    }
  } catch (e) {
    addLog('ERR', 'Lights error: ' + e.message);
    lightsOn = !lightsOn;
  }
}

// ── Lobby + Admin ───────────────────────────────────────
function userLabel(user) {
  if (!user) return '--';
  return user.display_name || user.username || user.id;
}

function renderLobby(data) {
  lobbyState = data;
  const active = data.active_driver;
  canControl = isAdmin() || (active && active.id === userId && userRole === 'driver');
  connected = Boolean(data.car_online);

  if ($('lobbyActiveDriver')) $('lobbyActiveDriver').textContent = userLabel(active);
  if ($('lobbyTimeLeft')) $('lobbyTimeLeft').textContent = formatTime(data.remaining_drive_time || 0);
  if ($('lobbyCarStatus')) {
    $('lobbyCarStatus').textContent = data.car_online ? 'online' : 'offline';
    $('lobbyCarStatus').style.color = data.car_online ? 'var(--green)' : 'var(--red)';
  }
  if ($('lobbySpectators')) $('lobbySpectators').textContent = (data.connected_spectators || []).length;

  const queueList = $('queueList');
  if (queueList) {
    queueList.innerHTML = '';
    for (const user of data.queue || []) {
      const li = document.createElement('li');
      li.textContent = userLabel(user);
      queueList.appendChild(li);
    }
    if (!queueList.children.length) {
      const li = document.createElement('li');
      li.textContent = 'empty';
      li.className = 'dim-item';
      queueList.appendChild(li);
    }
  }

  renderAdminUsers(data);

  if (data.car_online) {
    setStatus('connected', 'CONNECTED');
    if (isLoggedIn()) startVideoStream();
    if (!statusPoller || !logPoller) startPolling();
  } else if (!isAdmin()) {
    setStatus('disconnected', 'DISCONNECTED');
    stopVideoStream();
  }

  const speedLimit = $('adminSpeedLimit');
  const speedValue = $('adminSpeedValue');
  const duration = $('adminDuration');
  const durationValue = $('adminDurationValue');
  if (speedLimit && speedValue) {
    speedLimit.value = data.max_speed_percent;
    speedValue.textContent = data.max_speed_percent + '%';
  }
  if (duration && durationValue) {
    duration.value = data.session_duration;
    durationValue.textContent = data.session_duration + 's';
  }

  updateRoleUi();
}

async function refreshLobby() {
  if (!isLoggedIn()) return;
  try {
    const resp = await fetch('/api/lobby');
    if (resp.ok) renderLobby(await resp.json());
  } catch (e) {}
}

async function joinQueue() {
  if (!isDriverRole()) return;
  try {
    const resp = await fetch('/api/queue/join', { method: 'POST' });
    if (resp.ok) renderLobby(await resp.json());
  } catch (e) {}
}

async function leaveQueue() {
  if (!isDriverRole()) return;
  try {
    const resp = await fetch('/api/queue/leave', { method: 'POST' });
    if (resp.ok) renderLobby(await resp.json());
  } catch (e) {}
}

async function adminAction(action, body = {}) {
  if (!isAdmin()) return;
  stopMotor();
  try {
    const resp = await fetch('/api/admin/' + action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (resp.ok) {
      renderLobby(data);
    } else {
      addLog('WARN', 'Admin action failed: ' + (data.error || action));
    }
  } catch (e) {
    addLog('ERR', 'Admin action error: ' + e.message);
  }
}

function renderAdminUsers(data) {
  if (!isAdmin()) return;
  const list = $('adminUserList');
  if (list) {
    list.innerHTML = '';
    for (const user of data.connected_users || []) {
      const row = document.createElement('div');
      row.className = 'admin-user-row';

      const meta = document.createElement('div');
      meta.className = 'admin-user-meta';
      const tags = [];
      if (user.active) tags.push('active');
      if (user.queued) tags.push('queued');
      tags.push(user.role || 'unknown');
      meta.innerHTML = `<strong>${escapeHtml(userLabel(user))}</strong><span>${escapeHtml(user.id)} · ${escapeHtml(tags.join(' · '))} · ${user.connections || 0} conn</span>`;

      const buttons = document.createElement('div');
      buttons.className = 'admin-user-actions';
      const kick = document.createElement('button');
      kick.className = 'btn btn-small btn-blue';
      kick.textContent = 'KICK';
      kick.onclick = () => adminAction('kick_user', { user_id: user.id });
      const ban = document.createElement('button');
      ban.className = 'btn btn-small btn-red';
      ban.textContent = 'BAN';
      ban.disabled = user.role === 'admin';
      ban.onclick = () => adminAction('ban_user', { user_id: user.id });
      buttons.append(kick, ban);
      row.append(meta, buttons);
      list.appendChild(row);
    }
    if (!list.children.length) {
      const empty = document.createElement('div');
      empty.className = 'dim-item admin-empty';
      empty.textContent = 'none connected';
      list.appendChild(empty);
    }
  }

  const banList = $('adminBanList');
  if (banList) {
    banList.innerHTML = '';
    for (const id of data.banned_users || []) {
      const row = document.createElement('div');
      row.className = 'admin-user-row';
      const meta = document.createElement('div');
      meta.className = 'admin-user-meta';
      meta.innerHTML = `<strong>${escapeHtml(id)}</strong><span>banned</span>`;
      const unban = document.createElement('button');
      unban.className = 'btn btn-small btn-blue';
      unban.textContent = 'UNBAN';
      unban.onclick = () => adminAction('unban_user', { user_id: id });
      row.append(meta, unban);
      banList.appendChild(row);
    }
    if (!banList.children.length) {
      const empty = document.createElement('div');
      empty.className = 'dim-item admin-empty';
      empty.textContent = 'none banned';
      banList.appendChild(empty);
    }
  }
}

function banDiscordIdInput() {
  const input = $('adminBanId');
  const userId = input ? input.value.trim() : '';
  if (userId) adminAction('ban_user', { user_id: userId });
}

function unbanDiscordIdInput() {
  const input = $('adminBanId');
  const userId = input ? input.value.trim() : '';
  if (userId) adminAction('unban_user', { user_id: userId });
}

function setAdminSpeed() {
  const input = $('adminSpeedLimit');
  if (input) adminAction('set_max_speed', { value: parseInt(input.value) });
}

function setSessionDuration() {
  const input = $('adminDuration');
  if (input) adminAction('set_session_duration', { value: parseInt(input.value) });
}

function updateRoleUi() {
  for (const el of document.querySelectorAll('.admin-only')) {
    el.style.display = isAdmin() ? '' : 'none';
  }
  for (const el of document.querySelectorAll('.driver-only')) {
    el.style.display = isDriverRole() ? '' : 'none';
  }
  for (const el of document.querySelectorAll('.dpad-btn, #btnLights, #speedSlider, #steerSlider')) {
    el.disabled = !canControl;
  }
}

function initSocket() {
  if (!isLoggedIn() || typeof io === 'undefined') {
    refreshLobby();
    if (isLoggedIn()) setInterval(refreshLobby, 1000);
    return;
  }
  socket = io();
  socket.on('connect', () => addLog('SYS', 'Lobby socket connected.'));
  socket.on('lobby:update', renderLobby);
  socket.on('control:ack', (data) => {
    if (data && !data.ok && data.error !== 'stale_command') {
      addLog('WARN', 'Command rejected: ' + data.error);
    }
  });
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
let selectedGamepadId = 'none';  // 'none' = keyboard only, or gamepad.id string
let gpProfileCache = {};  // Cache detected profiles by gamepad.id

const DEADZONE = 0.15;

// ── Controller Profiles ──────────────────────────────────
// Auto-detect profile from gamepad.id string
function detectProfile(gp) {
  const id = (gp.id || '').toLowerCase();
  if (id.includes('mboster') || id.includes('mboo'))
    return 'moza-pedals';
  if ((id.includes('r9') || id.includes('moza') || id.includes('mozar')) && !id.includes('pedal'))
    return 'moza-wheel';
  if (id.includes('xbox') || id.includes('microsoft') || id.includes('xinput'))
    return 'xbox';
  if (id.includes('dualshock') || id.includes('dualsense') || id.includes('sony') || id.includes('playstation'))
    return 'playstation';
  if (id.includes('logitech') || id.includes('g29') || id.includes('g920') || id.includes('g923'))
    return 'moza-wheel';  // Logitech wheels behave like Moza wheel
  return 'generic';
}

// Get short label for profile
function profileLabel(profile) {
  const labels = {
    'xbox': '🎮 Xbox',
    'playstation': '🎮 PlayStation',
    'moza-pedals': '🦶 Moza Pedals',
    'moza-wheel': '🎡 Moza Wheel',
    'generic': '🎮 Gamepad',
  };
  return labels[profile] || '🎮 Gamepad';
}

// ── Gamepad Selector ─────────────────────────────────────
function refreshGamepadList() {
  const select = $('gamepadSelect');
  if (!select) return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const current = select.value;

  // Preserve selection, rebuild options
  select.innerHTML = '<option value="none">⌨️ Keyboard</option>';

  let anyConnected = false;
  let hasPedals = false;
  let hasWheel = false;

  for (let i = 0; i < pads.length; i++) {
    const gp = pads[i];
    if (!gp) continue;
    anyConnected = true;
    const profile = gpProfileCache[gp.id] || detectProfile(gp);
    gpProfileCache[gp.id] = profile;
    if (profile === 'moza-pedals') hasPedals = true;
    if (profile === 'moza-wheel') hasWheel = true;
    const label = profileLabel(profile) + ' ' + (gp.index + 1);
    const opt = document.createElement('option');
    opt.value = gp.id;
    opt.textContent = label;
    opt.title = gp.id;
    select.appendChild(opt);
  }

  // Add combined option if both pedals and wheel are present
  if (hasPedals && hasWheel) {
    const opt = document.createElement('option');
    opt.value = 'combined-moza';
    opt.textContent = '🦶🎡 Pedals + Wheel';
    opt.title = 'Moza pedals (throttle/brake) + R9 wheelbase (steering)';
    select.insertBefore(opt, select.children[1]);  // Right after Auto-detect
  }

  // Restore previous selection
  if (current && Array.from(select.options).some(o => o.value === current)) {
    select.value = current;
  }

  // Update dot color
  const dot = $('gamepadDot');
  if (anyConnected) {
    dot.classList.add('connected');
  } else {
    dot.classList.remove('connected');
  }
}

function onGamepadSelectChange() {
  const select = $('gamepadSelect');
  selectedGamepadId = select.value;
  if (selectedGamepadId === 'none') {
    addLog('SYS', '⌨️ Keyboard controls active');
    if (gamepadActive) { gamepadActive = false; stopMotor(); restoreSliders(); }
  } else {
    addLog('SYS', '🎮 Controller: ' + selectedGamepadId.substring(0, 40));
  }
}

function getSelectedGamepad() {
  if (selectedGamepadId === 'none') return null;  // Keyboard only

  const pads = navigator.getGamepads ? navigator.getGamepads() : [];

  if (selectedGamepadId === 'combined-moza') {
    const pedals = Array.from(pads).find(p => p && detectProfile(p) === 'moza-pedals');
    const wheel = Array.from(pads).find(p => p && detectProfile(p) === 'moza-wheel');
    if (!pedals && !wheel) return null;
    return { _combined: true, pedals, wheel };
  }

  return Array.from(pads).find(p => p && p.id === selectedGamepadId) || null;
}

// ── Gamepad Connect/Disconnect Events ───────────────────
window.addEventListener('gamepadconnected', (e) => {
  const profile = detectProfile(e.gamepad);
  gpProfileCache[e.gamepad.id] = profile;
  addLog('SYS', '🎮 Connected: ' + profileLabel(profile) + ' — ' + e.gamepad.id.substring(0, 50));
  refreshGamepadList();
  if (!gamepadPoller) {
    gamepadPoller = setInterval(pollGamepad, 50);
  }
});

window.addEventListener('gamepaddisconnected', (e) => {
  addLog('SYS', '🎮 Disconnected: ' + (e.gamepad.id || '').substring(0, 50));
  refreshGamepadList();
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const anyConnected = Array.from(pads).some(p => p !== null);
  if (!anyConnected) {
    if (gamepadPoller) { clearInterval(gamepadPoller); gamepadPoller = null; }
    gamepadActive = false;
    if (activeKeys.size > 0) updateMotorFromKeys();
  }
});

// ── Apply circular deadzone ──────────────────────────────
function applyDeadzone(x, y) {
  const mag = Math.sqrt(x * x + y * y);
  if (mag < DEADZONE) return { x: 0, y: 0 };
  const scale = (mag - DEADZONE) / (1 - DEADZONE);
  const nx = (x / mag) * scale;
  const ny = (y / mag) * scale;
  return { x: Math.max(-1, Math.min(1, nx)), y: Math.max(-1, Math.min(1, ny)) };
}

function applyDeadzone1D(val) {
  if (Math.abs(val) < DEADZONE) return 0;
  const sign = val > 0 ? 1 : -1;
  return sign * (Math.abs(val) - DEADZONE) / (1 - DEADZONE);
}

// ── Gamepad Polling Loop (20Hz, 50ms) ────────────────────
function pollGamepad() {
  const gp = getSelectedGamepad();
  if (!gp) {
    // Check if any gamepads exist but selected one is gone — refresh list
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    if (Array.from(pads).some(p => p !== null)) refreshGamepadList();
    return;
  }

  const profile = gp._combined ? null : (gpProfileCache[gp.id] || detectProfile(gp));

  let throttleVal = 0;  // -1 = forward, +1 = reverse
  let steerVal = 0;     // -1 = left, +1 = right
  let hasInput = false;

  // ── Combined mode: pedals + wheel simultaneously ──
  if (gp._combined) {
    // Pedals for throttle/brake
    if (gp.pedals) {
      let throttleRaw = gp.pedals.axes[0] || 0;
      let brakeRaw = gp.pedals.axes[1] || 0;
      const throttlePress = (throttleRaw + 1) / 2;
      const brakePress = (brakeRaw + 1) / 2;
      const throttle = throttlePress > DEADZONE ? throttlePress : 0;
      const brake = brakePress > DEADZONE ? brakePress : 0;
      if (throttle > 0.01) { throttleVal = -throttle; hasInput = true; }
      if (brake > 0.01) { throttleVal = brake; hasInput = true; }
    }
    // Wheel for steering
    if (gp.wheel) {
      const wheelRaw = gp.wheel.axes[0] || 0;
      steerVal = applyDeadzone1D(wheelRaw);
      if (Math.abs(steerVal) > 0.01) hasInput = true;
    }
    // Handle buttons from both devices
    if (gp.pedals) handleGamepadButtons(gp.pedals);
    if (gp.wheel) handleGamepadButtons(gp.wheel);
  } else {
    // ── Single device: profile-specific axis mapping ──
    const profile = gpProfileCache[gp.id] || detectProfile(gp);
    gpProfileCache[gp.id] = profile;

    switch (profile) {
      case 'xbox':
    case 'playstation': {
      // Left stick: Y=throttle, X=steer
      const left = applyDeadzone(gp.axes[0] || 0, gp.axes[1] || 0);
      // Right stick X as alternative steer (axis 2 = RS X, axis 3 = RS Y)
      const rightSteer = applyDeadzone1D(gp.axes[2] || 0);
      // Triggers: axis 4 = LT (brake), axis 5 = RT (accelerate)
      // Range varies by browser: -1(released)..1(pressed) OR 0..1
      // Handle both: use raw value if > 0.05 (covers 0..1 range),
      // or normalize if rest position is near -1 (covers -1..1 range)
      const rtRaw = gp.axes[5] !== undefined ? gp.axes[5] : -1;
      const ltRaw = gp.axes[4] !== undefined ? gp.axes[4] : -1;
      // Normalize: if rest is near -1, map -1..1 → 0..1. If rest is near 0, use directly.
      const rtAccel = rtRaw < -0.5 ? Math.max(0, (rtRaw + 1) / 2) : Math.max(0, rtRaw);
      const ltBrake = ltRaw < -0.5 ? Math.max(0, (ltRaw + 1) / 2) : Math.max(0, ltRaw);

      const leftMag = Math.sqrt(left.x * left.x + left.y * left.y);
      const rightMag = Math.abs(rightSteer);

      // Throttle: triggers override left stick Y when pressed
      if (rtAccel > 0.01) {
        throttleVal = -rtAccel;  // RT = forward (negative)
        hasInput = true;
      } else if (ltBrake > 0.01) {
        throttleVal = ltBrake;  // LT = reverse (positive)
        hasInput = true;
      } else if (leftMag > 0.01) {
        throttleVal = left.y;   // Left stick Y: -1=forward, +1=reverse
        hasInput = true;
      }

      // Steering: right stick X overrides left stick X when active
      if (rightMag > 0.01) {
        steerVal = rightSteer;
      } else if (leftMag > 0.01) {
        steerVal = left.x;
      }

      if (Math.abs(steerVal) > 0.01) hasInput = true;
      break;
    }

    case 'moza-pedals': {
      // MBoster pedals: axis 0 = throttle, axis 1 = brake
      // Pedals at rest = -1 (released), full press = +1
      // Some pedals: rest=1, full=-1 — we handle both
      let throttleRaw = gp.axes[0] || 0;  // Throttle pedal
      let brakeRaw = gp.axes[1] || 0;      // Brake pedal

      // Detect range: if rest position is near -1, use 0..1 mapping
      // If rest position is near +1, invert
      // We normalize to 0 = released, 1 = fully pressed
      const throttlePress = (throttleRaw + 1) / 2;  // -1..1 → 0..1
      const brakePress = (brakeRaw + 1) / 2;

      // Apply deadzone to pedal press
      const throttle = throttlePress > DEADZONE ? throttlePress : 0;
      const brake = brakePress > DEADZONE ? brakePress : 0;

      if (throttle > 0.01) {
        throttleVal = -throttle;  // Negative = forward
        hasInput = true;
      }
      if (brake > 0.01) {
        throttleVal = brake;  // Positive = reverse
        hasInput = true;
      }
      // No steering from pedals alone
      steerVal = 0;
      break;
    }

    case 'moza-wheel': {
      // R9 wheelbase: axis 0 = steering rotation (-1 = full left, 0 = center, 1 = full right)
      const wheelRaw = gp.axes[0] || 0;
      steerVal = applyDeadzone1D(wheelRaw);
      // No throttle from wheel alone
      throttleVal = 0;
      hasInput = Math.abs(steerVal) > 0.01;
      // Also check if there are button-based throttle/brake (paddle shifters etc.)
      // Buttons: typically 0=shift up, 1=shift down on wheels
      break;
    }

    default: {
      // Generic: assume axis 0=steer, axis 1=throttle (common HID layout)
      const left = applyDeadzone(gp.axes[0] || 0, gp.axes[1] || 0);
      const leftMag = Math.sqrt(left.x * left.x + left.y * left.y);
      if (leftMag > 0.01) {
        throttleVal = left.y;
        steerVal = left.x;
        hasInput = true;
      }
      break;
    }
  }

  // ── Apply input or stop ──
  if (hasInput && (Math.abs(throttleVal) > 0.01 || Math.abs(steerVal) > 0.01)) {
    gamepadActive = true;

    // Compute proportional speed/steer from deflection
    const speedPct = Math.max(5, Math.round(Math.abs(throttleVal) * 100));
    const steerPct = Math.max(5, Math.round(Math.abs(steerVal) * 100));

    // Update sliders visually
    const speedSlider = $('speedSlider');
    const speedValue = $('speedValue');
    const steerSlider = $('steerSlider');
    const steerValue = $('steerValue');
    if (speedSlider) { speedSlider.value = speedPct; speedValue.textContent = speedPct + '%'; motorSpeed = speedPct; }
    if (steerSlider) { steerSlider.value = steerPct; steerValue.textContent = steerPct + '%'; motorSteerRange = steerPct; }

    // Determine direction
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
  } else {
    // Stick in deadzone
    if (gamepadActive) {
      gamepadActive = false;
      activeKeys.clear();
      stopMotor();
      restoreSliders();
    }
    }  // end switch
  }  // end else (single device)

  // ── Apply input or stop ──
  if (hasInput && (Math.abs(throttleVal) > 0.01 || Math.abs(steerVal) > 0.01)) {
    gamepadActive = true;

    const speedPct = Math.max(5, Math.round(Math.abs(throttleVal) * 100));
    const steerPct = Math.max(5, Math.round(Math.abs(steerVal) * 100));

    const speedSlider = $('speedSlider');
    const speedValue = $('speedValue');
    const steerSlider = $('steerSlider');
    const steerValue = $('steerValue');
    if (speedSlider) { speedSlider.value = speedPct; speedValue.textContent = speedPct + '%'; motorSpeed = speedPct; }
    if (steerSlider) { steerSlider.value = steerPct; steerValue.textContent = steerPct + '%'; motorSteerRange = steerPct; }

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
  } else {
    // Stick in deadzone
    if (gamepadActive) {
      gamepadActive = false;
      activeKeys.clear();
      stopMotor();
      restoreSliders();
    }
  }

  // Handle buttons (single device only — combined handles above)
  if (!gp._combined) handleGamepadButtons(gp);
}

function restoreSliders() {
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
        case 13: // D-pad down — toggle lights
          toggleLights();
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
updateRoleUi();
initSocket();
if (isLoggedIn()) startPolling();
addLog('SYS', '🏎️ FPV Debug Cockpit loaded.');
addLog('SYS', 'Car: WL_FPV_CAR_99613492 @ 172.16.11.1');
addLog('SYS', 'Codec: H.264 Baseline 640×360 @ 20fps');
addLog('SYS', 'Motor: Hold WASD/D-pad/gamepad for continuous control (20Hz).');
