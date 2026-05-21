document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const portSelect = document.getElementById('port-select');
    const baudSelect = document.getElementById('baud-select');
    const btnConnect = document.getElementById('btn-connect');
    
    const statusDot = document.getElementById('status-dot');
    const printerState = document.getElementById('printer-state');
    const progressCircle = document.getElementById('progress-circle');
    const progressPercent = document.getElementById('progress-percent');
    const currentFile = document.getElementById('current-file');
    
    const btnStart = document.getElementById('btn-start');
    const btnPause = document.getElementById('btn-pause');
    const btnResume = document.getElementById('btn-resume');
    const btnCancel = document.getElementById('btn-cancel');
    
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    
    const termOutput = document.getElementById('terminal-output');
    const termInput = document.getElementById('terminal-input');
    
    const actualHotend = document.getElementById('temp-hotend-actual');
    const targetHotend = document.getElementById('temp-hotend-target');
    const actualBed = document.getElementById('temp-bed-actual');
    const targetBed = document.getElementById('temp-bed-target');
    
    const timeElapsed = document.getElementById('time-elapsed');
    const timeEta = document.getElementById('time-eta');

    const jogStepSelect = document.getElementById('jog-step');
    
    // State
    let isConnected = false;
    let wsTelemetry = null;
    let wsTerminal = null;
    let availablePorts = [];
    let portPollInterval = null;
    let hasAttemptedAutoconnect = false;
    let serverConfirmedDisconnected = false;
    
    // WebSocket Timeout Trackers (prevents overlapping reconnect loops)
    let telemetryReconnectTimeout = null;
    let terminalReconnectTimeout = null;

    // Initialization
    setupWebSockets();
    checkExistingConnection();

    // --- API Calls & Polling ---

    async function checkExistingConnection() {
        try {
            const res = await fetch('/api/state');
            const data = await res.json();
            
            updateUIState(data);
            
            if (data.state !== 'Disconnected' && data.state !== 'Error') {
                // Recover the existing connection session
                if (data.port) {
                    portSelect.innerHTML = `<option value="${data.port}">${data.port}</option>`;
                    portSelect.value = data.port;
                }
                
                // Fetch full port list in background silently
                fetch('/api/ports').then(r => r.json()).then(d => {
                    availablePorts = d.ports;
                });
            } else {
                serverConfirmedDisconnected = true;
                fetchPorts();
                startPortPolling();
            }
        } catch (e) {
            console.error("Failed to check state", e);
            fetchPorts();
            startPortPolling();
        }
    }

    async function fetchPorts() {
        if (isConnected) return; // Stop polling if connected
        try {
            const res = await fetch('/api/ports');
            const data = await res.json();
            
            // Double-check connection state after async fetch
            if (isConnected) return;
            
            if (JSON.stringify(data.ports) !== JSON.stringify(availablePorts)) {
                availablePorts = data.ports;
                const currentSelection = portSelect.value || localStorage.getItem('savedPort');
                portSelect.innerHTML = '';
                
                if (availablePorts.length === 0) {
                    portSelect.innerHTML = '<option value="">No ports found</option>';
                } else {
                    availablePorts.forEach(port => {
                        const opt = document.createElement('option');
                        opt.value = port;
                        opt.textContent = port;
                        portSelect.appendChild(opt);
                    });
                    if (currentSelection && availablePorts.includes(currentSelection)) {
                        portSelect.value = currentSelection;
                    }
                }
            }

            // Autoconnect logic
            if (!hasAttemptedAutoconnect && availablePorts.length > 0 && serverConfirmedDisconnected) {
                hasAttemptedAutoconnect = true;
                const savedPort = localStorage.getItem('savedPort');
                const savedBaudrate = localStorage.getItem('savedBaudrate');
                
                if (savedPort && availablePorts.includes(savedPort)) {
                    portSelect.value = savedPort;
                    if (savedBaudrate) baudSelect.value = savedBaudrate;
                    toggleConnection();
                }
            }
        } catch (e) {
            console.error("Failed to fetch ports", e);
        }
    }

    function startPortPolling() {
        if (portPollInterval) clearInterval(portPollInterval);
        portPollInterval = setInterval(fetchPorts, 3000);
    }

    function stopPortPolling() {
        if (portPollInterval) {
            clearInterval(portPollInterval);
            portPollInterval = null;
        }
    }

    async function toggleConnection() {
        if (isConnected) {
            await fetch('/api/disconnect', { method: 'POST' });
        } else {
            const port = portSelect.value;
            const baudrate = baudSelect.value;
            if (!port) return alert("Please select a valid COM port");
            
            btnConnect.textContent = 'Connecting...';
            btnConnect.disabled = true;
            
            try {
                const res = await fetch('/api/connect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ port, baudrate: parseInt(baudrate) })
                });
                const data = await res.json();
                
                btnConnect.disabled = false;
                if (data.success) {
                    // Save successful connection parameters
                    localStorage.setItem('savedPort', port);
                    localStorage.setItem('savedBaudrate', baudrate);
                    updateUIState(data.state);
                } else {
                    btnConnect.textContent = 'Connect';
                    alert(data.message || "Failed to connect");
                }
            } catch (e) {
                btnConnect.disabled = false;
                btnConnect.textContent = 'Connect';
                alert("Network error while connecting.");
            }
        }
    }

    // --- WebSockets with Always-On Dynamic Reconnection ---

    function setupWebSockets() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        
        connectTelemetryWS(protocol, host);
        connectTerminalWS(protocol, host);
    }

    function connectTelemetryWS(protocol, host) {
        if (wsTelemetry) {
            try { wsTelemetry.close(); } catch(e) {}
        }
        wsTelemetry = new WebSocket(`${protocol}//${host}/ws/telemetry`);
        wsTelemetry.onmessage = (event) => {
            const state = JSON.parse(event.data);
            updateUIState(state);
        };
        wsTelemetry.onclose = () => {
            console.warn('Telemetry WS closed, reconnecting in 2s...');
            clearTimeout(telemetryReconnectTimeout);
            telemetryReconnectTimeout = setTimeout(() => {
                connectTelemetryWS(protocol, host);
            }, 2000);
        };
        wsTelemetry.onerror = (e) => {
            console.error('Telemetry WS error', e);
        };
    }

    function connectTerminalWS(protocol, host) {
        if (wsTerminal) {
            try { wsTerminal.close(); } catch(e) {}
        }
        wsTerminal = new WebSocket(`${protocol}//${host}/ws/terminal`);
        wsTerminal.onmessage = (event) => {
            appendTerminal(event.data, event.data.startsWith('>'));
        };
        wsTerminal.onclose = () => {
            console.warn('Terminal WS closed, reconnecting in 2s...');
            clearTimeout(terminalReconnectTimeout);
            terminalReconnectTimeout = setTimeout(() => {
                connectTerminalWS(protocol, host);
            }, 2000);
        };
        wsTerminal.onerror = (e) => {
            console.error('Terminal WS error', e);
        };
    }
    
    function closeWebSockets() {
        // Noop since WebSockets are always-on and auto-reconnecting
    }

    // --- Helpers ---
    function formatSeconds(sec) {
        if (sec === null || sec === undefined) return "--:--:--";
        const h = Math.floor(sec / 3600).toString().padStart(2, '0');
        const m = Math.floor((sec % 3600) / 60).toString().padStart(2, '0');
        const s = (sec % 60).toString().padStart(2, '0');
        return `${h}:${m}:${s}`;
    }

    // --- Dynamic Control Safety Toggles ---
    function setManualControlsEnabled({ movement, tuning, terminal }) {
        // Axis jog buttons
        document.querySelectorAll('.btn-jog').forEach(btn => {
            btn.disabled = !movement;
        });
        
        // Homing buttons
        document.getElementById('btn-home-xy').disabled = !movement;
        document.getElementById('btn-home-z').disabled = !movement;
        
        // Temperature/Multiplier tuning inputs and action buttons
        document.querySelectorAll('.tuning-card input, .tuning-card button').forEach(el => {
            el.disabled = !tuning;
        });

        // Terminal input
        termInput.disabled = !terminal;
    }

    // --- UI Updates ---

    function updateUIState(stateData) {
        if (!stateData) return;
        
        const statusCard = document.querySelector('.status-card');
        if (statusCard) {
            statusCard.classList.remove('staged', 'printing', 'paused');
        }
        progressPercent.style.color = '';
        progressPercent.style.fontSize = '';
        progressCircle.style.stroke = '';
        
        // Dynamically compute and synchronize the connection state with the backend telemetry state
        const wasConnected = isConnected;
        isConnected = (stateData.state !== 'Disconnected' && stateData.state !== 'Error');

        if (isConnected) {
            btnConnect.textContent = 'Disconnect';
            btnConnect.classList.replace('primary', 'danger');
            stopPortPolling();
        } else {
            btnConnect.textContent = 'Connect';
            btnConnect.classList.replace('danger', 'primary');
            if (wasConnected) {
                closeWebSockets();
            }
            startPortPolling();
        }

        // Port & Baud dropdown selects
        portSelect.disabled = isConnected;
        baudSelect.disabled = isConnected;

        // State text and dot with detailed error reporting
        if (stateData.state === 'Error' && stateData.error) {
            printerState.textContent = `Error: ${stateData.error}`;
        } else {
            printerState.textContent = stateData.state;
        }
        statusDot.className = 'pulse-dot'; 
        
        // Hide/Disable all print actions by default
        btnStart.style.display = 'none';
        btnPause.style.display = 'none';
        btnResume.style.display = 'none';
        btnCancel.style.display = 'none';
        
        btnStart.disabled = true;
        btnPause.disabled = true;
        btnResume.disabled = true;
        btnCancel.disabled = true;
        
        if (stateData.state === 'Disconnected') {
            statusDot.classList.add('disconnected');
            setManualControlsEnabled({ movement: false, tuning: false, terminal: false });
        } else if (stateData.state === 'Idle') {
            statusDot.classList.add('connected');
            setManualControlsEnabled({ movement: true, tuning: true, terminal: true });
            
            // Staged file flow
            if (stateData.loaded_file) {
                if (statusCard) statusCard.classList.add('staged');
                btnStart.style.display = 'flex';
                btnStart.disabled = false;
                btnCancel.style.display = 'flex';
                btnCancel.disabled = false;
                btnCancel.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg> Unload`;
                
                progressPercent.textContent = 'READY';
                progressPercent.style.color = 'var(--success)';
                progressPercent.style.fontSize = '0.95rem';
                progressCircle.style.strokeDashoffset = 0;
                progressCircle.style.stroke = 'var(--success)';
            }
        } else if (stateData.state === 'Printing') {
            statusDot.classList.add('printing');
            if (statusCard) statusCard.classList.add('printing');
            // Live temperature and multipliers remain fully active during printing, but movement jog buttons are locked
            setManualControlsEnabled({ movement: false, tuning: true, terminal: true });
            
            btnPause.style.display = 'flex';
            btnPause.disabled = false;
            btnCancel.style.display = 'flex';
            btnCancel.disabled = false;
            btnCancel.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg> Cancel`;
        } else if (stateData.state === 'Paused') {
            statusDot.classList.add('connected');
            if (statusCard) statusCard.classList.add('paused');
            // Jogging is allowed during pause to move head safely, temperature is fully adjustable
            setManualControlsEnabled({ movement: true, tuning: true, terminal: true });
            
            btnResume.style.display = 'flex';
            btnResume.disabled = false;
            btnCancel.style.display = 'flex';
            btnCancel.disabled = false;
            btnCancel.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg> Cancel`;
        } else if (stateData.state === 'Error') {
            statusDot.classList.add('error');
            setManualControlsEnabled({ movement: true, tuning: true, terminal: true });
        }

        // Progress Circle
        if (stateData.progress !== undefined) {
            if (!(stateData.state === 'Idle' && stateData.loaded_file)) {
                const p = Math.max(0, Math.min(100, stateData.progress));
                const circumference = 251.327; // 2 * pi * 40
                const offset = circumference - (p / 100) * circumference;
                progressCircle.style.strokeDashoffset = offset;
                progressPercent.textContent = p.toFixed(0) + '%';
            }
        }
        
        // File & Analytics Labels
        if (stateData.state === 'Printing' || stateData.state === 'Paused') {
            currentFile.textContent = stateData.file || "Streaming...";
        } else if (stateData.state === 'Idle' && stateData.loaded_file) {
            currentFile.textContent = stateData.loaded_file;
        } else {
            currentFile.textContent = stateData.state === 'Disconnected' ? "Not Connected" : "Ready to Print";
        }
        
        timeElapsed.textContent = formatSeconds(stateData.elapsed_s);
        timeEta.textContent = formatSeconds(stateData.eta_s);
        
        // Temperatures showing actual and target
        if (stateData.temps) {
            actualHotend.textContent = Math.round(stateData.temps.hotend.actual);
            targetHotend.textContent = Math.round(stateData.temps.hotend.target);
            actualBed.textContent = Math.round(stateData.temps.bed.actual);
            targetBed.textContent = Math.round(stateData.temps.bed.target);
        }
    }

    function appendTerminal(text, isSent = false) {
        const div = document.createElement('div');
        div.className = `terminal-line ${isSent ? 'sent' : ''}`;
        div.textContent = text;
        termOutput.appendChild(div);
        
        while (termOutput.children.length > 150) {
            termOutput.removeChild(termOutput.firstChild);
        }
        termOutput.scrollTop = termOutput.scrollHeight;
    }

    // --- Control Event Handlers ---

    btnConnect.addEventListener('click', toggleConnection);
    
    // Dedicated Print Start execution
    btnStart.addEventListener('click', () => {
        fetch('/api/control/start', { method: 'POST' });
    });

    btnPause.addEventListener('click', () => {
        fetch('/api/control/pause', { method: 'POST' });
    });
    
    btnResume.addEventListener('click', () => {
        fetch('/api/control/resume', { method: 'POST' });
    });
    
    btnCancel.addEventListener('click', () => {
        const state = printerState.textContent;
        if (state === 'Idle') {
            // Safe unload
            fetch('/api/control/unload', { method: 'POST' });
        } else {
            // Active print cancel with prompt
            if (confirm('Cancel the current print? This will turn off heaters and home X/Y.')) {
                fetch('/api/control/cancel', { method: 'POST' });
            }
        }
    });

    // Jogging — debounced G91/G90 mode for responsive rapid clicking
    let jogTimeout = null;
    let isRelativeMode = false;

    document.querySelectorAll('.btn-jog').forEach(btn => {
        btn.addEventListener('click', () => {
            const axis = btn.dataset.axis;
            const dir = parseFloat(btn.dataset.dir);
            const step = parseFloat(jogStepSelect.value);
            const amount = dir * step;

            // Switch to relative mode only once
            if (!isRelativeMode) {
                sendCommand('G91');
                isRelativeMode = true;
            }

            // Send just the move (1 command instead of 3)
            sendCommand(`G1 ${axis}${amount} F3000`);

            // Restore absolute mode after 500ms of no clicking
            clearTimeout(jogTimeout);
            jogTimeout = setTimeout(() => {
                sendCommand('G90');
                isRelativeMode = false;
            }, 500);
        });
    });
    
    document.getElementById('btn-home-xy').addEventListener('click', () => sendCommand('G28 X Y'));
    document.getElementById('btn-home-z').addEventListener('click', () => sendCommand('G28 Z'));

    // Terminal Input
    termInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && termInput.value) {
            sendCommand(termInput.value);
            termInput.value = '';
        }
    });

    function sendCommand(cmd) {
        fetch('/api/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd})
        });
    }

    // Clean modular temperature and speed/flow rate tuning
    function setTemp(heater) {
        const input = document.getElementById(`input-${heater}`);
        const val = input.value;
        if (!val) return;
        
        const cmd = heater === 'hotend' ? `M104 S${val}` : `M140 S${val}`;
        sendCommand(cmd);
        input.value = '';
    }

    function setMultiplier(type) {
        const input = document.getElementById(`input-${type}`);
        const val = input.value;
        if (!val) return;
        
        if (type === 'speed') {
            fetch(`/api/control/speed/${val}`, { method: 'POST' });
        } else if (type === 'flow') {
            fetch(`/api/control/flow/${val}`, { method: 'POST' });
        }
        input.value = '';
    }

    // Modern event listener selectors replacing global namespaces
    document.getElementById('btn-set-hotend').addEventListener('click', () => setTemp('hotend'));
    document.getElementById('btn-set-bed').addEventListener('click', () => setTemp('bed'));
    document.getElementById('btn-set-speed').addEventListener('click', () => setMultiplier('speed'));
    document.getElementById('btn-set-flow').addEventListener('click', () => setMultiplier('flow'));

    // --- File Upload ---

    uploadZone.addEventListener('click', () => fileInput.click());
    
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('dragover');
    });
    
    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('dragover');
    });
    
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            uploadFile(e.dataTransfer.files[0]);
        }
    });
    
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            uploadFile(fileInput.files[0]);
        }
    });

    async function uploadFile(file) {
        if (!isConnected) return alert("Connect to printer first!");
        
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (!data.success) {
                alert(data.error || "Failed to stage print file");
            } else {
                const stateRes = await fetch('/api/state');
                const stateData = await stateRes.json();
                updateUIState(stateData);
            }
        } catch (e) {
            console.error(e);
            alert("Upload failed");
        }
        fileInput.value = '';
    }
});
