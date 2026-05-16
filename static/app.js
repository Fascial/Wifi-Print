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
    
    // State
    let isConnected = false;
    let wsTelemetry = null;
    let wsTerminal = null;
    let availablePorts = [];
    let portPollInterval = null;

    // Initialization
    fetchPorts();
    startPortPolling();

    // --- API Calls & Polling ---

    async function fetchPorts() {
        if (isConnected) return; // Stop polling if connected
        try {
            const res = await fetch('/api/ports');
            const data = await res.json();
            
            if (JSON.stringify(data.ports) !== JSON.stringify(availablePorts)) {
                availablePorts = data.ports;
                const currentSelection = portSelect.value;
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
                    if (availablePorts.includes(currentSelection)) {
                        portSelect.value = currentSelection;
                    }
                }
            }
        } catch (e) {
            console.error("Failed to fetch ports", e);
        }
    }

    function startPortPolling() {
        if (portPollInterval) clearInterval(portPollInterval);
        portPollInterval = setInterval(fetchPorts, 2000);
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
            isConnected = false;
            btnConnect.textContent = 'Connect';
            btnConnect.classList.replace('danger', 'primary');
            updateUIState({state: 'Disconnected'});
            closeWebSockets();
            startPortPolling();
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
                    isConnected = true;
                    btnConnect.textContent = 'Disconnect';
                    btnConnect.classList.replace('primary', 'danger');
                    stopPortPolling();
                    setupWebSockets();
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

    // --- WebSockets ---

    function setupWebSockets() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        
        wsTelemetry = new WebSocket(`${protocol}//${host}/ws/telemetry`);
        wsTelemetry.onmessage = (event) => {
            const state = JSON.parse(event.data);
            updateUIState(state);
        };
        
        wsTerminal = new WebSocket(`${protocol}//${host}/ws/terminal`);
        wsTerminal.onmessage = (event) => {
            appendTerminal(event.data, event.data.startsWith('>'));
        };
    }
    
    function closeWebSockets() {
        if (wsTelemetry) wsTelemetry.close();
        if (wsTerminal) wsTerminal.close();
    }

    // --- Helpers ---
    function formatSeconds(sec) {
        if (sec === null || sec === undefined) return "--:--:--";
        const h = Math.floor(sec / 3600).toString().padStart(2, '0');
        const m = Math.floor((sec % 3600) / 60).toString().padStart(2, '0');
        const s = (sec % 60).toString().padStart(2, '0');
        return `${h}:${m}:${s}`;
    }

    // --- UI Updates ---

    function updateUIState(stateData) {
        if (!stateData) return;
        
        // State text and dot
        printerState.textContent = stateData.state;
        statusDot.className = 'pulse-dot'; 
        
        btnPause.disabled = true;
        btnPause.style.display = 'flex';
        btnResume.style.display = 'none';
        btnCancel.disabled = true;
        
        if (stateData.state === 'Printing') {
            statusDot.classList.add('printing');
            btnPause.disabled = false;
            btnCancel.disabled = false;
        } else if (stateData.state === 'Paused') {
            statusDot.classList.add('connected');
            btnPause.style.display = 'none';
            btnResume.style.display = 'flex';
            btnResume.disabled = false;
            btnCancel.disabled = false;
        } else if (stateData.state === 'Idle') {
            statusDot.classList.add('connected');
        }

        // Progress Circle
        if (stateData.progress !== undefined) {
            const p = Math.max(0, Math.min(100, stateData.progress));
            const circumference = 251.327; // 2 * pi * 40
            const offset = circumference - (p / 100) * circumference;
            progressCircle.style.strokeDashoffset = offset;
            progressPercent.textContent = p.toFixed(0) + '%';
        }
        
        // File & Analytics
        if (stateData.file) {
            currentFile.textContent = stateData.file;
        } else {
            currentFile.textContent = stateData.state === 'Disconnected' ? "Not Connected" : "Ready to Print";
        }
        
        timeElapsed.textContent = formatSeconds(stateData.elapsed_s);
        timeEta.textContent = formatSeconds(stateData.eta_s);
        
        // Temperatures
        if (stateData.temps) {
            actualHotend.textContent = stateData.temps.hotend.actual;
            actualBed.textContent = stateData.temps.bed.actual;
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

    // --- Control Handlers ---

    btnConnect.addEventListener('click', toggleConnection);

    btnPause.addEventListener('click', () => fetch('/api/control/pause', {method:'POST'}));
    btnResume.addEventListener('click', () => fetch('/api/control/resume', {method:'POST'}));
    btnCancel.addEventListener('click', () => fetch('/api/control/cancel', {method:'POST'}));

    // Jogging
    document.querySelectorAll('.btn-jog').forEach(btn => {
        btn.addEventListener('click', () => {
            const axis = btn.dataset.axis;
            const amount = btn.dataset.amount;
            const cmd = `G91\nG1 ${axis}${amount} F3000\nG90`;
            sendCommand(cmd);
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

    // Exposed to window for onclick handlers
    window.setTemp = (heater) => {
        const input = document.getElementById(`input-${heater}`);
        const val = input.value;
        if (!val) return;
        
        let cmd = '';
        if (heater === 'hotend') {
            cmd = `M104 S${val}`;
            targetHotend.textContent = val;
        } else {
            cmd = `M140 S${val}`;
            targetBed.textContent = val;
        }
        sendCommand(cmd);
        input.value = '';
    };

    window.setMultiplier = (type) => {
        const input = document.getElementById(`input-${type}`);
        const val = input.value;
        if (!val) return;
        
        if (type === 'speed') {
            fetch(`/api/control/speed/${val}`, {method: 'POST'});
        } else if (type === 'flow') {
            fetch(`/api/control/flow/${val}`, {method: 'POST'});
        }
        input.value = '';
    }

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
                alert(data.error || "Failed to start print");
            }
        } catch (e) {
            console.error(e);
            alert("Upload failed");
        }
        fileInput.value = '';
    }
});
