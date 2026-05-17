// Mobile FPV Dashboard JavaScript

const $ = (id) => document.getElementById(id);

// ── Guest Code Redemption ─────────────────────────────────
async function redeemCode() {
  const input = $('guestCodeInput');
  const error = $('loginError');
  if (!input) return;
  
  const code = input.value.trim();
  if (!code) {
    error.textContent = 'Please enter a drive code.';
    return;
  }
  
  try {
    const resp = await fetch('/api/redeem-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ code }),
    });
    const data = await resp.json();
    if (data.ok) {
      window.location.href = '/?_=' + Date.now();
    } else {
      error.textContent = data.error || 'Invalid code';
      input.value = '';
    }
  } catch (e) {
    error.textContent = 'Network error';
  }
}

// Allow Enter key to redeem
document.addEventListener('DOMContentLoaded', () => {
  const codeInput = $('guestCodeInput');
  if (codeInput) {
    codeInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        redeemCode();
      }
    });
  }
});

// ── Connection Controls ───────────────────────────────────
async function doConnect() {
  try {
    const resp = await fetch('/api/connect', {
      method: 'POST',
      credentials: 'include',
    });
    const data = await resp.json();
    if (data.ok) {
      updateConnectionUI(true);
      startVideoFeed();
    }
  } catch (e) {
    console.error('Connect failed:', e);
  }
}

async function doDisconnect() {
  try {
    const resp = await fetch('/api/disconnect', {
      method: 'POST',
      credentials: 'include',
    });
    const data = await resp.json();
    if (data.ok) {
      updateConnectionUI(false);
      stopVideoFeed();
    }
  } catch (e) {
    console.error('Disconnect failed:', e);
  }
}

function updateConnectionUI(connected) {
  const btnConnect = $('btnConnect');
  const btnDisconnect = $('btnDisconnect');
  const status = $('connectionStatus');
  
  if (btnConnect) btnConnect.disabled = connected;
  if (btnDisconnect) btnDisconnect.disabled = !connected;
  if (status) status.textContent = connected ? 'Connected' : 'Disconnected';
}

// ── Video Feed ────────────────────────────────────────────
let videoFeedInterval = null;

function startVideoFeed() {
  const videoFeed = $('videoFeed');
  const overlay = $('videoOverlay');
  
  if (videoFeed) {
    videoFeed.src = '/api/stream';
    if (overlay) overlay.classList.add('hidden');
  }
}

function stopVideoFeed() {
  const videoFeed = $('videoFeed');
  const overlay = $('videoOverlay');
  
  if (videoFeed) {
    videoFeed.src = '';
    if (overlay) overlay.classList.remove('hidden');
  }
}

// ── Touch Controls ────────────────────────────────────────
let currentCommand = null;
let commandInterval = null;
const COMMAND_SEND_INTERVAL = 100; // Send command every 100ms while button held

function sendCommand(command, speed = 100, steerRange = 100) {
  fetch('/api/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ command, speed, steer_range: steerRange }),
  }).catch(() => {});
}

function startCommand(command) {
  if (currentCommand === command) return;
  
  // Stop previous command
  stopCommand();
  
  currentCommand = command;
  sendCommand(command);
  
  // Continue sending while button held
  commandInterval = setInterval(() => {
    sendCommand(command);
  }, COMMAND_SEND_INTERVAL);
}

function stopCommand() {
  if (commandInterval) {
    clearInterval(commandInterval);
    commandInterval = null;
  }
  
  if (currentCommand) {
    sendCommand('stop');
    currentCommand = null;
  }
}

// ── Button Event Handlers ─────────────────────────────────
function setupTouchControls() {
  const btnThrottle = $('btnThrottle');
  const btnBrake = $('btnBrake');
  const btnSteerLeft = $('btnSteerLeft');
  const btnSteerRight = $('btnSteerRight');
  
  // Throttle (forward)
  if (btnThrottle) {
    btnThrottle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startCommand('forward');
      btnThrottle.classList.add('active');
    });
    btnThrottle.addEventListener('touchstart', (e) => {
      e.preventDefault();
      startCommand('forward');
      btnThrottle.classList.add('active');
    });
    btnThrottle.addEventListener('mouseup', () => {
      stopCommand();
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('mouseleave', () => {
      stopCommand();
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('touchend', () => {
      stopCommand();
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('touchcancel', () => {
      stopCommand();
      btnThrottle.classList.remove('active');
    });
  }
  
  // Brake (backward)
  if (btnBrake) {
    btnBrake.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startCommand('backward');
      btnBrake.classList.add('active');
    });
    btnBrake.addEventListener('touchstart', (e) => {
      e.preventDefault();
      startCommand('backward');
      btnBrake.classList.add('active');
    });
    btnBrake.addEventListener('mouseup', () => {
      stopCommand();
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('mouseleave', () => {
      stopCommand();
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('touchend', () => {
      stopCommand();
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('touchcancel', () => {
      stopCommand();
      btnBrake.classList.remove('active');
    });
  }
  
  // Steer Left
  if (btnSteerLeft) {
    btnSteerLeft.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startCommand('left');
      btnSteerLeft.classList.add('active');
    });
    btnSteerLeft.addEventListener('touchstart', (e) => {
      e.preventDefault();
      startCommand('left');
      btnSteerLeft.classList.add('active');
    });
    btnSteerLeft.addEventListener('mouseup', () => {
      stopCommand();
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('mouseleave', () => {
      stopCommand();
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('touchend', () => {
      stopCommand();
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('touchcancel', () => {
      stopCommand();
      btnSteerLeft.classList.remove('active');
    });
  }
  
  // Steer Right
  if (btnSteerRight) {
    btnSteerRight.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startCommand('right');
      btnSteerRight.classList.add('active');
    });
    btnSteerRight.addEventListener('touchstart', (e) => {
      e.preventDefault();
      startCommand('right');
      btnSteerRight.classList.add('active');
    });
    btnSteerRight.addEventListener('mouseup', () => {
      stopCommand();
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('mouseleave', () => {
      stopCommand();
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('touchend', () => {
      stopCommand();
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('touchcancel', () => {
      stopCommand();
      btnSteerRight.classList.remove('active');
    });
  }
}

// ── Status Polling ────────────────────────────────────────
let statusInterval = null;

function startStatusPolling() {
  statusInterval = setInterval(async () => {
    try {
      const resp = await fetch('/api/status', { credentials: 'include' });
      const data = await resp.json();
      if (data.ok) {
        const speedDisplay = $('speedDisplay');
        if (speedDisplay && data.motor_speed !== undefined) {
          speedDisplay.textContent = `Speed: ${data.motor_speed}%`;
        }
      }
    } catch (e) {}
  }, 1000);
}

// ── Initialization ────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const role = document.body.dataset.userRole;
  const canConnect = document.body.dataset.canConnect === 'true';
  
  if (role && canConnect) {
    setupTouchControls();
    startStatusPolling();
    
    // Auto-start video if already connected
    fetch('/api/status', { credentials: 'include' })
      .then(r => r.json())
      .then(data => {
        if (data.ok && data.car_online) {
          updateConnectionUI(true);
          startVideoFeed();
        }
      })
      .catch(() => {});
  }
});

// Prevent zoom on double tap
document.addEventListener('dblclick', (e) => {
  e.preventDefault();
}, { passive: false });

// Prevent context menu on long press
document.addEventListener('contextmenu', (e) => {
  e.preventDefault();
});
