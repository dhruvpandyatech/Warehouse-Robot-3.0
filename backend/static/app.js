// Global variables
let socket = null;
let currentWsUrl = '';

// Helper to scale robot metrics (0.0 to 2.0) to SVG coordinates (0 to 200)
function coordsToSvg(x, y) {
    const scale = 100;
    const svgX = Math.max(0, Math.min(200, x * scale));
    const svgY = Math.max(0, Math.min(200, 200 - (y * scale)));
    return { x: svgX, y: svgY };
}

function formatFloat(val) {
    return Number(val).toFixed(2);
}

// Compute API and WS URLs dynamically based on user input URL
function getTargetUrls() {
    let inputVal = document.getElementById('apiUrl').value.trim();
    
    // Default to current origin if empty
    if (!inputVal) {
        inputVal = window.location.origin;
    }
    
    // Clean trailing slash
    const httpBase = inputVal.replace(/\/$/, '');
    
    // Convert http(s) to ws(s)
    let wsBase = httpBase.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
    
    return {
        http: httpBase,
        ws: wsBase,
        wsFeed: `${wsBase}/api/ws`,
        videoFeed: `${httpBase}/api/video_feed`
    };
}

// WebSocket Connection Setup
function connectWebSocket() {
    const targets = getTargetUrls();
    
    // If already connected to the same socket URL, do nothing
    if (socket && currentWsUrl === targets.wsFeed && socket.readyState === WebSocket.OPEN) {
        return;
    }
    
    // Close existing socket
    if (socket) {
        socket.onclose = null;
        socket.close();
    }
    
    currentWsUrl = targets.wsFeed;
    appendSystemLog(`[SYSTEM] Connecting to server WebSocket at ${targets.wsFeed}...`);
    
    // Set video feed source dynamically
    document.getElementById('videoFeed').src = targets.videoFeed;
    
    try {
        socket = new WebSocket(targets.wsFeed);
    } catch (e) {
        appendSystemLog(`[ERROR] Failed to create WebSocket connection: ${e.message}`, 'error');
        return;
    }

    socket.onopen = () => {
        const statusEl = document.getElementById('serverStatus');
        statusEl.innerText = 'WS ONLINE';
        statusEl.classList.add('online');
        appendSystemLog('[SYSTEM] WebSocket successfully connected. Standing by.');
    };

    socket.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error('Error decoding WebSocket JSON:', e);
        }
    };

    socket.onclose = () => {
        const statusEl = document.getElementById('serverStatus');
        statusEl.innerText = 'WS OFFLINE';
        statusEl.classList.remove('online');
        
        // Reset robot status
        updateRobotConnectionUI('disconnected');
        
        appendSystemLog('[SYSTEM] WebSocket disconnected. Retrying in 3 seconds...', 'warn');
        setTimeout(connectWebSocket, 3000);
    };

    socket.onerror = (err) => {
        console.error('WebSocket Error:', err);
    };
}

// Handle incoming messages
function handleWsMessage(msg) {
    switch (msg.type) {
        case 'status':
            updateMissionStatusUI(msg.data);
            break;
        case 'robot_status':
            updateRobotConnectionUI(msg.data);
            break;
        case 'target':
            document.getElementById('overlayTarget').innerText = `TARGET: ${msg.data}`;
            break;
        case 'state':
            updateRobotStateUI(msg.data);
            break;
        case 'telemetry':
            updateTelemetryUI(msg.data);
            break;
        case 'log':
            appendRobotLog(msg.data);
            break;
    }
}

// Update connection status badge for Jetson Nano Agent
function updateRobotConnectionUI(status) {
    const badge = document.getElementById('robotConnection');
    if (status === 'connected') {
        badge.innerText = 'ROBOT: CONNECTED';
        badge.className = 'indicator-badge status-connected';
    } else {
        badge.innerText = 'ROBOT: DISCONNECTED';
        badge.className = 'indicator-badge status-disconnected';
        
        // Disable Start button if no robot is connected
        updateMissionStatusUI('idle');
    }
}

// Update UI buttons based on mission run state
function updateMissionStatusUI(status) {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const targetInput = document.getElementById('targetQr');
    const mockInput = document.getElementById('mockMode');
    
    const robotConnected = document.getElementById('robotConnection').classList.contains('status-connected');

    if (status === 'running') {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        targetInput.disabled = true;
        mockInput.disabled = true;
    } else {
        // Only enable Start if robot is actually connected
        startBtn.disabled = !robotConnected;
        stopBtn.disabled = true;
        targetInput.disabled = false;
        mockInput.disabled = false;
        
        if (status === 'idle') {
            document.getElementById('overlayTarget').innerText = 'NO ACTIVE MISSION';
        }
    }
}

// Update Robot State HUD
function updateRobotStateUI(state) {
    const stateEl = document.getElementById('robotState');
    stateEl.innerText = state;
    
    stateEl.style.textShadow = '';
    stateEl.style.color = '';

    if (state === 'NAVIGATING' || state === 'PLAN_PATH') {
        stateEl.style.color = 'var(--primary)';
        stateEl.style.textShadow = '0 0 8px var(--primary-glow)';
    } else if (state === 'TARGET_FOUND' || state === 'MISSION_COMPLETE') {
        stateEl.style.color = 'var(--success)';
        stateEl.style.textShadow = '0 0 8px var(--success-glow)';
    } else if (state === 'ERROR') {
        stateEl.style.color = 'var(--danger)';
        stateEl.style.textShadow = '0 0 8px var(--danger-glow)';
    } else {
        stateEl.style.color = 'var(--warning)';
    }
}

// Update Robot telemetry coordinates and paths on SVG map
let pathCoordinates = [];

function updateTelemetryUI(tele) {
    document.getElementById('robotPos').innerText = `${formatFloat(tele.x)}, ${formatFloat(tele.y)}`;
    document.getElementById('robotHeading').innerText = `${formatFloat(tele.heading)}`;
    document.getElementById('robotVel').innerText = `L: ${formatFloat(tele.linear_velocity)} | A: ${formatFloat(tele.angular_velocity)}`;

    const svgPt = coordsToSvg(tele.x, tele.y);
    
    const marker = document.getElementById('robotMarker');
    marker.setAttribute('transform', `translate(${svgPt.x}, ${svgPt.y})`);
    
    const deg = -tele.heading * (180 / Math.PI);
    document.getElementById('robotDirection').setAttribute('transform', `rotate(${deg})`);

    if (pathCoordinates.length === 0 || 
        Math.hypot(pathCoordinates[pathCoordinates.length - 1].x - svgPt.x, pathCoordinates[pathCoordinates.length - 1].y - svgPt.y) > 2) {
        
        pathCoordinates.push(svgPt);
        const pathStr = pathCoordinates.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
        document.getElementById('robotPath').setAttribute('d', pathStr);
    }
}

// Append formatted log messages to simulated Console
function appendRobotLog(rawMsg) {
    const consoleEl = document.getElementById('terminalConsole');
    const row = document.createElement('div');
    row.classList.add('log-row');

    if (rawMsg.includes('[ERROR]')) {
        row.classList.add('error-row');
    } else if (rawMsg.includes('[WARNING]')) {
        row.classList.add('warn-row');
    } else {
        row.classList.add('info-row');
    }

    row.innerText = rawMsg;
    consoleEl.appendChild(row);
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

// Append system helper alerts to simulated Console
function appendSystemLog(msg, type = 'info') {
    const consoleEl = document.getElementById('terminalConsole');
    const row = document.createElement('div');
    row.classList.add('log-row');
    
    const timeStr = new Date().toTimeString().split(' ')[0];
    
    if (type === 'warn') {
        row.classList.add('warn-row');
        row.innerText = `[${timeStr}] ${msg}`;
    } else if (type === 'error') {
        row.classList.add('error-row');
        row.innerText = `[${timeStr}] ${msg}`;
    } else {
        row.classList.add('info-row');
        row.innerText = `[${timeStr}] ${msg}`;
    }
    
    consoleEl.appendChild(row);
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

// API Controls
async function startMission(targetQr, mockMode) {
    const targets = getTargetUrls();
    try {
        const response = await fetch(`${targets.http}/api/mission/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_qr: targetQr, mock_mode: mockMode })
        });
        const data = await response.json();
        if (data.status === 'success') {
            appendSystemLog('[SYSTEM] Cloud start command sent.');
            pathCoordinates = [];
            document.getElementById('robotPath').setAttribute('d', 'M 0 200');
        } else {
            appendSystemLog(`[ERROR] Failed to start mission: ${data.message}`, 'error');
        }
    } catch (err) {
        appendSystemLog(`[ERROR] Connect failed: ${err.message}`, 'error');
    }
}

async function stopMission() {
    const targets = getTargetUrls();
    try {
        const response = await fetch(`${targets.http}/api/mission/stop`, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            appendSystemLog('[SYSTEM] Cloud stop command sent.');
        } else {
            appendSystemLog(`[ERROR] Failed to stop mission: ${data.message}`, 'error');
        }
    } catch (err) {
        appendSystemLog(`[ERROR] Connect failed: ${err.message}`, 'error');
    }
}

// Load persisted API URL on start
const savedUrl = localStorage.getItem('robotApiUrl');
if (savedUrl) {
    document.getElementById('apiUrl').value = savedUrl;
} else {
    document.getElementById('apiUrl').value = window.location.origin;
}

// Event Bindings
document.getElementById('apiUrl').addEventListener('change', (e) => {
    const urlVal = e.target.value.trim();
    localStorage.setItem('robotApiUrl', urlVal);
    appendSystemLog(`[SYSTEM] Target backend URL updated to: ${urlVal}`);
    connectWebSocket();
});

document.getElementById('missionForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const qrVal = document.getElementById('targetQr').value.trim();
    const isMock = document.getElementById('mockMode').checked;
    
    if (!qrVal) {
        alert('Please enter a target QR code.');
        return;
    }
    
    startMission(qrVal, isMock);
});

document.getElementById('stopBtn').addEventListener('click', () => {
    stopMission();
});

document.getElementById('clearLogsBtn').addEventListener('click', () => {
    const consoleEl = document.getElementById('terminalConsole');
    consoleEl.innerHTML = '';
    appendSystemLog('[SYSTEM] Logs cleared.');
});

// Run connection routine
connectWebSocket();
