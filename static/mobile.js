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
// Track pressed buttons individually (like desktop keyboard)
const activeButtons = new Set(); // 'forward', 'reverse', 'left', 'right'
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

// Compute combined command from all pressed buttons
function computeMobileCommand() {
  let throttle = null; // 'forward' | 'reverse' | null
  let steer = null;    // 'left' | 'right' | null

  if (activeButtons.has('forward')) throttle = 'forward';
  else if (activeButtons.has('reverse')) throttle = 'reverse';
  if (activeButtons.has('left')) steer = 'left';
  else if (activeButtons.has('right')) steer = 'right';

  if (!throttle && !steer) return 'stop';
  if (throttle && !steer) return throttle;
  if (!throttle && steer) return steer;
  return throttle + '_' + steer; // forward_left, forward_right, reverse_left, reverse_right
}

function updateMobileMotor() {
  const cmd = computeMobileCommand();
  sendCommand(cmd);

  // Start continuous sending if any button pressed
  if (cmd !== 'stop' && !commandInterval) {
    commandInterval = setInterval(() => {
      sendCommand(computeMobileCommand());
    }, COMMAND_SEND_INTERVAL);
  } else if (cmd === 'stop' && commandInterval) {
    clearInterval(commandInterval);
    commandInterval = null;
  }
}

function pressButton(btn) {
  activeButtons.add(btn);
  updateMobileMotor();
}

function releaseButton(btn) {
  activeButtons.delete(btn);
  updateMobileMotor();
}

function releaseAllButtons() {
  activeButtons.clear();
  if (commandInterval) {
    clearInterval(commandInterval);
    commandInterval = null;
  }
  sendCommand('stop');
}

// REVIEW WEB-3 (2026-09-06): focus lost -> buttonup goes to another window
// and a held touch button would drive the car forever.
window.addEventListener('blur', () => { if (activeButtons.size) releaseAllButtons(); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden && activeButtons.size) releaseAllButtons();
});

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
      pressButton('forward');
      btnThrottle.classList.add('active');
    });
    btnThrottle.addEventListener('touchstart', (e) => {
      e.preventDefault();
      pressButton('forward');
      btnThrottle.classList.add('active');
    });
    btnThrottle.addEventListener('mouseup', () => {
      releaseButton('forward');
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('mouseleave', () => {
      releaseButton('forward');
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('touchend', () => {
      releaseButton('forward');
      btnThrottle.classList.remove('active');
    });
    btnThrottle.addEventListener('touchcancel', () => {
      releaseButton('forward');
      btnThrottle.classList.remove('active');
    });
  }
  
  // Brake (reverse)
  if (btnBrake) {
    btnBrake.addEventListener('mousedown', (e) => {
      e.preventDefault();
      pressButton('reverse');
      btnBrake.classList.add('active');
    });
    btnBrake.addEventListener('touchstart', (e) => {
      e.preventDefault();
      pressButton('reverse');
      btnBrake.classList.add('active');
    });
    btnBrake.addEventListener('mouseup', () => {
      releaseButton('reverse');
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('mouseleave', () => {
      releaseButton('reverse');
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('touchend', () => {
      releaseButton('reverse');
      btnBrake.classList.remove('active');
    });
    btnBrake.addEventListener('touchcancel', () => {
      releaseButton('reverse');
      btnBrake.classList.remove('active');
    });
  }
  
  // Steer Left
  if (btnSteerLeft) {
    btnSteerLeft.addEventListener('mousedown', (e) => {
      e.preventDefault();
      pressButton('left');
      btnSteerLeft.classList.add('active');
    });
    btnSteerLeft.addEventListener('touchstart', (e) => {
      e.preventDefault();
      pressButton('left');
      btnSteerLeft.classList.add('active');
    });
    btnSteerLeft.addEventListener('mouseup', () => {
      releaseButton('left');
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('mouseleave', () => {
      releaseButton('left');
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('touchend', () => {
      releaseButton('left');
      btnSteerLeft.classList.remove('active');
    });
    btnSteerLeft.addEventListener('touchcancel', () => {
      releaseButton('left');
      btnSteerLeft.classList.remove('active');
    });
  }
  
  // Steer Right
  if (btnSteerRight) {
    btnSteerRight.addEventListener('mousedown', (e) => {
      e.preventDefault();
      pressButton('right');
      btnSteerRight.classList.add('active');
    });
    btnSteerRight.addEventListener('touchstart', (e) => {
      e.preventDefault();
      pressButton('right');
      btnSteerRight.classList.add('active');
    });
    btnSteerRight.addEventListener('mouseup', () => {
      releaseButton('right');
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('mouseleave', () => {
      releaseButton('right');
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('touchend', () => {
      releaseButton('right');
      btnSteerRight.classList.remove('active');
    });
    btnSteerRight.addEventListener('touchcancel', () => {
      releaseButton('right');
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
