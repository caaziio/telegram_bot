let workflows = initialWorkflows || [];
let settings = initialSettings || {};
let currentRules = [];

function switchTab(tabId) {
    const tabs = ['workflows', 'tester', 'settings', 'cto'];
    tabs.forEach(t => {
        const tabEl = document.getElementById('tab-' + t);
        const navEl = document.getElementById('nav-' + t);
        if (tabEl) tabEl.classList.add('hidden');
        if (navEl) navEl.classList.remove('active');
    });
    
    const targetTab = document.getElementById('tab-' + tabId);
    const targetNav = document.getElementById('nav-' + tabId);
    if (targetTab) targetTab.classList.remove('hidden');
    if (targetNav) targetNav.classList.add('active');
}

function renderWorkflows() {
    const grid = document.getElementById('workflows-grid');
    grid.innerHTML = '';
    
    workflows.forEach(wf => {
        const card = document.createElement('div');
        card.className = 'workflow-card';
        card.innerHTML = `
            <div class="workflow-header">
                <div class="workflow-title">${wf.name || 'Unnamed Flow'}</div>
                <div class="status-badge" style="${!wf.is_active ? 'background: rgba(239, 68, 68, 0.1); color: #ef4444;' : ''}">
                    ${wf.is_active ? 'Active' : 'Paused'}
                </div>
            </div>
            <div class="workflow-rules-preview" style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1.5rem; min-height: 40px; align-content: flex-start;">
                ${(wf.rules && wf.rules.length > 0) ? wf.rules.map(r => {
                    let label = '';
                    let color = 'var(--accent-color)';
                    if (r.rule_type === 'token_age') {
                        label = `⏱️ Age: ${r.time_min || 0}-${r.time_max || '∞'}m`;
                        color = '#3b82f6';
                    } else if (r.rule_type === 'market_cap') {
                        let minMC = r.time_min ? (Number(r.time_min) >= 1000000 ? (Number(r.time_min)/1000000).toFixed(1) + 'M' : Number(r.time_min) >= 1000 ? (Number(r.time_min)/1000).toFixed(1) + 'K' : r.time_min) : '0';
                        let maxMC = r.time_max ? (Number(r.time_max) >= 1000000 ? (Number(r.time_max)/1000000).toFixed(1) + 'M' : Number(r.time_max) >= 1000 ? (Number(r.time_max)/1000).toFixed(1) + 'K' : r.time_max) : '∞';
                        label = `💰 MC: $${minMC}-$${maxMC}`;
                        color = '#10b981';
                    } else if (r.rule_type === 'performance') {
                        label = `📊 Perf (${r.search_text || '5m'}): ${r.time_min || '-∞'}% to ${r.time_max || '∞'}%`;
                        color = '#f59e0b';
                    } else if (r.rule_type === 'exclude_platform') {
                        label = `🚫 Exclude: ${r.search_text}`;
                        color = '#ef4444';
                    } else if (r.rule_type === 'filter') {
                        label = `🔍 Drop: "${r.search_text}"`;
                        color = '#ec4899';
                    } else if (r.rule_type === 'replace') {
                        label = `🔄 Replace: "${r.search_text}" -> "${r.replace_text}"`;
                        color = '#8b5cf6';
                    } else if (r.rule_type === 'append') {
                        label = `➕ Append: "${r.replace_text}"`;
                        color = '#06b6d4';
                    } else {
                        label = r.rule_type.toUpperCase();
                    }
                    return `<span style="background: rgba(255,255,255,0.03); border: 1px solid ${color}; color: #f3f4f6; font-size: 0.78rem; padding: 4px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; font-weight: 500;">${label}</span>`;
                }).join('') : '<span style="color: var(--text-muted); font-size: 0.85rem; font-style: italic;">No active rules configured</span>'}
            </div>
            <div class="workflow-footer">
                <div class="rules-count">${(wf.rules || []).length} Active Rules/Filters</div>
                <div style="display: flex; gap: 8px;">
                    <button class="btn-icon" onclick="duplicateWorkflow(${wf.id})" title="Duplicate Workflow">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                    <button class="btn-icon" onclick="editWorkflow(${wf.id})">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                    </button>
                    <button class="btn-icon" onclick="toggleWorkflow(${wf.id})">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path><line x1="12" y1="2" x2="12" y2="12"></line></svg>
                    </button>
                    <button class="btn-icon" onclick="deleteWorkflow(${wf.id})" style="color: #ef4444;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });

    // Populate tester dropdown
    const select = document.getElementById('tester-workflow');
    select.innerHTML = workflows.map(wf => `<option value="${wf.id}">${wf.name}</option>`).join('');
    
    // Populate CTO scanner workflow dropdown
    const ctoSelect = document.getElementById('cto-workflow-id');
    if (ctoSelect) {
        const defaultOption = `<option value="active_all" style="background: var(--bg-dark); color: white;">Evaluate All Active Workflows</option>`;
        const noFilterOption = `<option value="" style="background: var(--bg-dark); color: white;">No Filter (Send directly to target below)</option>`;
        const options = workflows.map(wf => `<option value="${wf.id}" style="background: var(--bg-dark); color: white;">${wf.name}</option>`).join('');
        ctoSelect.innerHTML = defaultOption + noFilterOption + options;
        
        // Restore selection if it exists, otherwise default to active_all
        if (settings.cto_workflow_id) {
            ctoSelect.value = settings.cto_workflow_id;
        } else {
            ctoSelect.value = "active_all";
        }
    }
}

function openModal() {
    document.getElementById('workflow-modal').classList.remove('hidden');
    document.getElementById('modal-title').innerText = 'New Workflow';
    document.getElementById('flow-id').value = '';
    document.getElementById('flow-name').value = '';
    
    const sourceEl = document.getElementById('flow-source');
    if (sourceEl) sourceEl.value = '';
    const sourceIdEl = document.getElementById('flow-source-id');
    if (sourceIdEl) sourceIdEl.value = '';
    
    const targetEl = document.getElementById('flow-target');
    if (targetEl) targetEl.value = '';
    const targetIdEl = document.getElementById('flow-target-id');
    if (targetIdEl) targetIdEl.value = '';
    
    currentRules = [];
    renderRules();
    fetchChannelsList(); // Refresh channels when opening modal
}

function closeModal() {
    document.getElementById('workflow-modal').classList.add('hidden');
}

function editWorkflow(id) {
    const wf = workflows.find(w => w.id == id);
    if (!wf) return;
    
    document.getElementById('workflow-modal').classList.remove('hidden');
    document.getElementById('modal-title').innerText = 'Edit Workflow';
    document.getElementById('flow-id').value = wf.id;
    document.getElementById('flow-name').value = wf.name || '';
    
    const sourceEl = document.getElementById('flow-source');
    if (sourceEl) sourceEl.value = wf.source_channel || '';
    const sourceIdEl = document.getElementById('flow-source-id');
    if (sourceIdEl) sourceIdEl.value = wf.source_channel_id || '';
    
    const targetEl = document.getElementById('flow-target');
    if (targetEl) targetEl.value = wf.target_channel || '';
    const targetIdEl = document.getElementById('flow-target-id');
    if (targetIdEl) targetIdEl.value = wf.target_channel_id || '';
    
    // Convert rules array of objects
    currentRules = (wf.rules || []).map(r => ({ ...r }));
    renderRules();
    fetchChannelsList(); // Refresh channels when editing
}

function renderRules() {
    const container = document.getElementById('rules-container');
    container.innerHTML = '';
    currentRules.forEach((rule, index) => {
        const div = document.createElement('div');
        div.className = 'rule-row';
        
        let content = `<span class="status-badge" style="background: var(--accent-color); color: white">${rule.rule_type.toUpperCase()}</span>`;
        
        if (rule.rule_type === 'token_age') {
            content += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Min Age (m):</label>
                <input type="number" class="form-input" style="width:80px" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" placeholder="0" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max Age (m):</label>
                <input type="number" class="form-input" style="width:80px" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" placeholder="∞" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'extract_ca') {
            content += `<span style="color: var(--text-muted); font-size: 0.85rem;">Automatically extracts Contract Address from message.</span>`;
        } else if (rule.rule_type === 'performance') {
            // Default to '5m' if not set
            const timeframe = rule.search_text || '5m';
            content += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Timeframe:</label>
                <select class="form-input" style="width:90px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="5m" ${timeframe === '5m' ? 'selected' : ''}>5m</option>
                    <option value="1hr" ${timeframe === '1hr' ? 'selected' : ''}>1hr</option>
                    <option value="6hr" ${timeframe === '6hr' ? 'selected' : ''}>6hr</option>
                    <option value="24hr" ${timeframe === '24hr' ? 'selected' : ''}>24hr</option>
                </select>
                <label style="font-size:0.85rem; color:var(--text-muted)">Min %:</label>
                <input type="number" class="form-input" style="width:80px" placeholder="-∞" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max %:</label>
                <input type="number" class="form-input" style="width:80px" placeholder="∞" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'market_cap') {
            content += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Min MC ($):</label>
                <input type="number" class="form-input" style="width:100px" placeholder="0" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max MC ($):</label>
                <input type="number" class="form-input" style="width:100px" placeholder="∞" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'exclude_platform') {
            content += `
                <input class="form-input" placeholder="e.g. pump.fun" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)">
            `;
        } else {
            content += `<input class="form-input" placeholder="${rule.rule_type === 'filter' ? 'Word to drop message' : 'Word to find'}" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)">`;
            if (rule.rule_type === 'replace') {
                content += `<input class="form-input" placeholder="Replace with..." value="${rule.replace_text || ''}" oninput="updateRule(${index}, 'replace_text', this.value)">`;
            }
        }
        
        content += `<button type="button" class="btn-icon" style="color: #ef4444" onclick="removeRule(${index})">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
        </button>`;
        
        div.innerHTML = content;
        container.appendChild(div);
    });
}

function addRule(type) {
    currentRules.push({ rule_type: type });
    renderRules();
}

function updateRule(index, key, value) {
    currentRules[index][key] = value;
}

function removeRule(index) {
    currentRules.splice(index, 1);
    renderRules();
}

async function saveWorkflow(e) {
    e.preventDefault();
    
    const name = document.getElementById('flow-name').value;
    if (!name.trim()) {
        alert("Please enter a Workflow Name.");
        return;
    }

    const id = document.getElementById('flow-id').value;
    const targetChannel = document.getElementById('flow-target').value;
    const targetChannelId = document.getElementById('flow-target-id').value;
    
    const workflow = {
        name: name,
        source_channel: "",
        source_channel_id: "",
        target_channel: targetChannel,
        target_channel_id: targetChannelId,
        rules: currentRules
    };
    
    const method = id ? 'PUT' : 'POST';
    const url = id ? '/api/workflows/' + id : '/api/workflows';
    
    try {
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(workflow)
        });
        
        if (!res.ok) {
            throw new Error(`Server returned ${res.status}`);
        }
        
        const result = await res.json();
        
        if (result.success) {
            if (!id) {
                workflow.id = result.id;
                workflow.is_active = 1;
                workflows.push(workflow);
            } else {
                const idx = workflows.findIndex(w => w.id == id);
                workflows[idx] = { ...workflows[idx], ...workflow };
            }
            renderWorkflows();
            closeModal();
        } else {
            alert('Error saving workflow: ' + (result.error || 'Unknown error'));
        }
    } catch (err) {
        alert('Failed to connect to server. Check if the bot is running. Error: ' + err.message);
        console.error(err);
    }
}

async function toggleWorkflow(id) {
    const wf = workflows.find(w => w.id == id);
    if (!wf) return;
    
    const res = await fetch('/api/workflows/' + id + '/toggle', { method: 'POST' });
    const result = await res.json();
    if (result.success) {
        wf.is_active = result.is_active;
        renderWorkflows();
    }
}

async function deleteWorkflow(id) {
    if (!id) {
        alert("Workflow ID is missing. Try refreshing the page.");
        return;
    }
    
    if(!confirm('Are you sure you want to delete this workflow?')) return;
    
    try {
        const res = await fetch('/api/workflows/' + id, { method: 'DELETE' });
        if (!res.ok) throw new Error("Server error " + res.status);
        
        const result = await res.json();
        if (result.success) {
            workflows = workflows.filter(w => w.id != id);
            renderWorkflows();
        } else {
            alert('Error deleting workflow: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Failed to delete workflow: ' + e.message);
        console.error(e);
    }
}

async function duplicateWorkflow(id) {
    const wf = workflows.find(w => w.id == id);
    if (!wf) return;
    
    // Create a copy of the workflow object without the ID
    const newWf = {
        name: wf.name + " (Copy)",
        source_channel: wf.source_channel,
        source_channel_id: wf.source_channel_id,
        target_channel: wf.target_channel,
        target_channel_id: wf.target_channel_id,
        rules: (wf.rules || []).map(r => ({ ...r }))
    };
    
    try {
        const res = await fetch('/api/workflows', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newWf)
        });
        
        if (!res.ok) throw new Error("Server error " + res.status);
        
        const result = await res.json();
        if (result.success) {
            newWf.id = result.id;
            newWf.is_active = 1;
            workflows.push(newWf);
            renderWorkflows();
        } else {
            alert('Error duplicating workflow: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Failed to duplicate workflow: ' + e.message);
        console.error(e);
    }
}

async function saveSettings(e) {
    if (e) e.preventDefault();
    const phone = document.getElementById('setting-phone').value.trim();
    
    if (phone && !phone.startsWith('+')) {
        alert("Phone number must start with '+' followed by country code (e.g., +1234567890).");
        return;
    }

    const newSettings = {
        api_id: document.getElementById('setting-api-id').value,
        api_hash: document.getElementById('setting-api-hash').value,
        phone: phone,
    };
    
    await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings)
    });
    
    alert('Settings saved! Requesting Telegram verification code...');
    
    try {
        const res = await fetch('/api/telegram/send_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone })
        });
        const result = await res.json();
        
        if (result.success) {
            document.getElementById('auth-section').style.display = 'block';
        } else {
            let msg = result.error;
            if (msg.includes("Telegram restricted code requests")) {
                msg += "\n\nTip: Telegram often sends the code to your other active sessions (Phone/Desktop app) first. Check your Telegram chat!";
            }
            alert('Failed to request code: ' + msg);
        }
    } catch (err) {
        alert('Error connecting to backend: ' + err);
    }
}

async function resetConnection() {
    if (!confirm("This will delete your local session and disconnect the bot. You will need to log in again. Continue?")) return;
    
    try {
        const res = await fetch('/api/telegram/reset', { method: 'POST' });
        const result = await res.json();
        if (result.success) {
            alert("Session reset! Please refresh the page and try connecting again.");
            window.location.reload();
        } else {
            alert("Reset failed: " + result.error);
        }
    } catch (e) {
        alert("Reset error: " + e);
    }
}

async function verifyCode() {
    const phone = document.getElementById('setting-phone').value;
    const code = document.getElementById('tg-code').value;
    
    if (!code) {
        alert("Please enter the code.");
        return;
    }
    
    try {
        const res = await fetch('/api/telegram/verify_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone, code })
        });
        const result = await res.json();
        
        if (result.success) {
            alert('Connected successfully!');
            document.getElementById('auth-section').style.display = 'none';
            document.getElementById('dialogs-section').style.display = 'block';
            loadDialogs();
            fetchChannelsList();
        } else {
            alert('Verification failed: ' + result.error);
        }
    } catch (e) {
        alert('Error verifying code: ' + e);
    }
}

async function loadDialogs() {
    try {
        const res = await fetch('/api/telegram/dialogs');
        const result = await res.json();
        
        if (result.success) {
            const list = document.getElementById('dialogs-list');
            list.innerHTML = '';
            
            if (result.dialogs.length === 0) {
                list.innerHTML = '<li style="padding: 1rem; color: #9ca3af;">No conversations found.</li>';
                return;
            }
            
            result.dialogs.forEach(d => {
                const li = document.createElement('li');
                li.style.padding = '0.75rem 1rem';
                li.style.borderBottom = '1px solid var(--border-color)';
                li.style.display = 'flex';
                li.style.justifyContent = 'space-between';
                li.style.alignItems = 'center';
                
                li.innerHTML = `
                    <strong style="color: white; font-weight: 500;">${d.name || 'Unknown'}</strong> 
                    <span style="color:#9ca3af; font-size:0.85rem; font-family: monospace;">${d.id}</span>
                `;
                list.appendChild(li);
            });
            document.getElementById('dialogs-section').style.display = 'block';
        } else {
            console.warn("Could not load conversations (network retry): " + result.error);
        }
    } catch (e) {
        console.error("Error loading conversations", e);
    }
}

async function checkAuthStatus() {
    try {
        const res = await fetch('/api/telegram/status');
        const result = await res.json();
        if (result.authorized) {
            document.getElementById('dialogs-section').style.display = 'block';
            loadDialogs();
            fetchChannelsList();
        } else {
            document.getElementById('dialogs-section').style.display = 'none';
        }
    } catch (e) {
        console.log("Could not check auth status", e);
    }
}

// Check auth status right after rendering workflows and then periodically
setTimeout(checkAuthStatus, 1000);
setInterval(checkAuthStatus, 15000); // Re-check every 15s to keep UI updated

async function runTester() {
    const workflowId = document.getElementById('tester-workflow').value;
    const text = document.getElementById('tester-input').value;
    const container = document.getElementById('tester-result-container');
    
    if (!workflowId) {
        container.innerHTML = 'Please select a workflow.';
        return;
    }
    
    container.innerHTML = 'Processing...';
    
    const res = await fetch('/api/tester', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_id: workflowId, text })
    });
    
    const result = await res.json();
    
    if (result.dropped) {
        container.innerHTML = `
            <div class="result-error">
                <div class="result-header">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>
                    Message Dropped
                </div>
                ${result.reason || 'Dropped by filters'}
            </div>
        `;
    } else {
        container.innerHTML = `
            <div class="result-success">
                <div class="result-header" style="color: #6ee7b7;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                    Message Forwarded Successfully
                </div>
                <div style="font-family: monospace; font-size: 0.85rem; white-space: pre-wrap; color: white; background: rgba(0,0,0,0.3); padding: 1rem; border-radius: 6px;">
                    ${result.text}
                </div>
            </div>
        `;
    }
}

// Initialize settings form
document.getElementById('setting-api-id').value = settings.api_id || '';
document.getElementById('setting-api-hash').value = settings.api_hash || '';
document.getElementById('setting-phone').value = settings.phone || '';

let availableChannels = [];

async function fetchChannelsList() {
    try {
        const res = await fetch('/api/telegram/channels');
        const result = await res.json();
        if (result.success) {
            availableChannels = result.channels;
        }
    } catch (e) {
        console.error("Failed to fetch channels list", e);
    }
}

function handleChannelSearch(type) {
    const input = document.getElementById(`flow-${type}`);
    const dropdown = document.getElementById(`${type}-dropdown`);
    const idInput = document.getElementById(`flow-${type}-id`);
    
    if (!input || !dropdown) return;
    
    const val = input.value.toLowerCase();
    
    let matches = availableChannels;
    if (val) {
        matches = availableChannels.filter(c => (c.name || '').toLowerCase().includes(val) || (c.username || '').toLowerCase().includes(val));
    }
    
    dropdown.innerHTML = '';
    
    if (matches.length > 0) {
        // Show max 50 to prevent huge DOM
        matches.slice(0, 50).forEach(c => {
            const div = document.createElement('div');
            div.className = 'dropdown-item';
            div.textContent = c.name || `Channel ${c.id}`;
            div.onclick = () => {
                input.value = c.name || `Channel ${c.id}`;
                idInput.value = c.id;
                dropdown.classList.add('hidden');
            };
            dropdown.appendChild(div);
        });
        dropdown.classList.remove('hidden');
    } else {
        const div = document.createElement('div');
        div.className = 'dropdown-item';
        // Show a more helpful message if no channels are found
        if (availableChannels.length === 0) {
            div.textContent = 'No channels found. Is the bot connected?';
        } else {
            div.textContent = 'No matches found';
        }
        div.style.color = '#9ca3af';
        dropdown.appendChild(div);
        dropdown.classList.remove('hidden');
    }
}

// Close dropdowns when clicking outside
document.addEventListener('click', function(e) {
    if (!e.target.closest('#flow-source') && !e.target.closest('#source-dropdown')) {
        const d = document.getElementById('source-dropdown');
        if (d) d.classList.add('hidden');
    }
    if (!e.target.closest('#flow-target') && !e.target.closest('#target-dropdown')) {
        const d = document.getElementById('target-dropdown');
        if (d) d.classList.add('hidden');
    }
    if (!e.target.closest('#flow-cto') && !e.target.closest('#cto-dropdown')) {
        const d = document.getElementById('cto-dropdown');
        if (d) d.classList.add('hidden');
    }
});

async function runCtoScanner() {
    const targetId = document.getElementById('flow-cto-id').value;
    const btn = document.getElementById('btn-run-cto');
    const container = document.getElementById('cto-result-container');
    const ledgerContainer = document.getElementById('cto-ledger-container');
    
    btn.disabled = true;
    btn.innerHTML = 'Scanning (this may take a moment)...';
    container.innerHTML = '<div style="text-align:center; padding: 2rem;">Waiting for scan to complete...</div>';
    ledgerContainer.innerHTML = '<div style="text-align:center; padding: 2rem;">Fetching CTO data from Dexscreener...</div>';
    
    const workflowId = document.getElementById('cto-workflow-id').value;
    const testMode = document.getElementById('cto-test-mode').checked;
    
    try {
        const res = await fetch('/api/cto/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                target_channel: targetId,
                workflow_id: workflowId,
                test_mode: testMode
            })
        });
        
        const result = await res.json();
        
        if (result.success) {
            const scanTime = new Date().toLocaleString();
            
            const setBadge = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.textContent = val;
            };
            
            if (result.results && result.results.length > 0) {
                // Update ledger count badge safely
                setBadge('cto-ledger-count', result.results.length);
                
                // Populate Scanned Tokens Ledger
                ledgerContainer.innerHTML = result.results.map((t, idx) => {
                    let badgeColor = '';
                    let badgeBg = '';
                    let statusLabel = '';
                    let reasonColor = '#ef4444';
                    
                    if (t.status === 'passed') {
                        badgeColor = '#10b981';
                        badgeBg = 'rgba(16, 185, 129, 0.1)';
                        statusLabel = '✅ Passed';
                        reasonColor = '#10b981';
                    } else if (t.status === 'duplicate') {
                        badgeColor = '#f59e0b';
                        badgeBg = 'rgba(245, 158, 11, 0.1)';
                        statusLabel = '⚠️ Duplicate';
                        reasonColor = '#f59e0b';
                    } else { // dropped
                        badgeColor = '#ef4444';
                        badgeBg = 'rgba(239, 68, 68, 0.1)';
                        statusLabel = '❌ Dropped';
                        reasonColor = '#ef4444';
                    }
                    
                    return `
                        <div class="result-success" style="padding: 1rem; margin-bottom: 0.5rem; background: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                                <span style="font-weight: bold; color: white; font-size: 0.95rem;">#${idx + 1} ${(t.name || 'Unknown').toUpperCase()}</span>
                                <span style="font-size: 0.78rem; font-weight: 600; padding: 2px 6px; border-radius: 4px; color: ${badgeColor}; background: ${badgeBg}; border: 1px solid ${badgeColor}">${statusLabel}</span>
                            </div>
                            
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">
                                📅 <span>Scanned: ${scanTime}</span>
                            </div>
                            
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px; word-break: break-all;">
                                🧬 <span style="font-family: monospace; user-select: all; color: #f3f4f6;">${t.ca}</span>
                            </div>
                            
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 0.8rem; margin-bottom: 8px;">
                                <div>💰 MC: <span style="color: white; font-weight: 500;">${t.market_cap}</span></div>
                                <div>⏱️ Age: <span style="color: white; font-weight: 500;">${t.age}</span></div>
                                <div>🌐 Platform: <span style="color: white; font-weight: 500;">${t.platform}</span></div>
                            </div>
                            
                            <div style="display: flex; gap: 10px; font-size: 0.75rem; background: rgba(255,255,255,0.02); padding: 4px 8px; border-radius: 4px; justify-content: space-between; align-items: center;">
                                <span style="color: #10b981;">5m: ${t.perf_5m > 0 ? '+' : ''}${t.perf_5m}%</span>
                                <span style="color: #3b82f6;">1h: ${t.perf_1h > 0 ? '+' : ''}${t.perf_1h}%</span>
                                <span style="color: #f59e0b;">6h: ${t.perf_6h > 0 ? '+' : ''}${t.perf_6h}%</span>
                                <span style="color: #ef4444;">24h: ${t.perf_24h > 0 ? '+' : ''}${t.perf_24h}%</span>
                            </div>
                            
                            <div style="font-size: 0.78rem; color: ${reasonColor}; margin-top: 8px; font-style: italic; border-top: 1px dashed rgba(255,255,255,0.05); padding-top: 6px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <span>${t.reason}</span>
                                ${t.dex_url ? `
                                    <a href="${t.dex_url}" target="_blank" style="background: rgba(59, 130, 246, 0.12); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); text-decoration: none; font-size: 0.72rem; padding: 2px 8px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight: 500; font-style: normal; transition: all 0.2s;" onmouseover="this.style.background='rgba(59, 130, 246, 0.25)'" onmouseout="this.style.background='rgba(59, 130, 246, 0.12)'">
                                        📊 Chart ↗
                                    </a>
                                ` : ''}
                            </div>
                        </div>
                    `;
                }).join('');
                
                // Populate Forwarded Signals (show only passed tokens)
                const passedTokens = result.results.filter(t => t.status === 'passed');
                // Update forwarded count badge safely
                setBadge('cto-result-count', passedTokens.length);
                
                if (passedTokens.length > 0) {
                    container.innerHTML = passedTokens.map((t, idx) => `
                        <div class="result-success" style="padding: 1rem; margin-bottom: 0.5rem; background: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px;">
                            <div style="font-size: 0.8rem; font-weight: bold; color: #10b981; margin-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 4px; display: flex; justify-content: space-between; align-items: center;">
                                <span>Signal #${idx + 1}</span>
                                <span style="color: var(--text-muted); font-weight: normal; font-size: 0.72rem;">📅 Sent: ${scanTime}</span>
                            </div>
                            <div style="font-family: monospace; font-size: 0.82rem; white-space: pre-wrap; color: white; line-height: 1.4;">${t.formatted_message}</div>
                        </div>
                    `).join('');
                } else {
                    container.innerHTML = '<div style="text-align:center; padding: 2rem;">No tokens passed the current rules & duplicate checks.</div>';
                }
                
                if (targetId && passedTokens.length > 0) {
                    alert(`Successfully scanned and forwarded ${passedTokens.length} tokens to target channel!`);
                }
            } else {
                setBadge('cto-ledger-count', '0');
                setBadge('cto-result-count', '0');
                ledgerContainer.innerHTML = '<div style="text-align:center; padding: 2rem;">No tokens returned by Dexscreener.</div>';
                container.innerHTML = '<div style="text-align:center; padding: 2rem;">No active tokens found matching criteria.</div>';
            }
        } else {
            const setBadge = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.textContent = val;
            };
            setBadge('cto-ledger-count', '0');
            setBadge('cto-result-count', '0');
            container.innerHTML = `<div class="result-error">Error: ${result.error}</div>`;
            ledgerContainer.innerHTML = `<div class="result-error">Error: ${result.error}</div>`;
        }
    } catch (e) {
        container.innerHTML = `<div class="result-error">Request failed: ${e.message}</div>`;
        ledgerContainer.innerHTML = `<div class="result-error">Request failed: ${e.message}</div>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Run Scanner';
    }
}

async function logoutTelegram() {
    if (!confirm("Are you sure you want to log out of Telegram? You will need to request a new code to connect again.")) return;
    
    try {
        const res = await fetch('/api/telegram/logout', { method: 'POST' });
        const result = await res.json();
        if (result.success) {
            alert("Logged out successfully.");
            document.getElementById('dialogs-section').style.display = 'none';
            document.getElementById('auth-section').style.display = 'none';
            availableChannels = [];
        } else {
            alert("Logout failed: " + result.error);
        }
    } catch (e) {
        alert("Logout error: " + e);
    }
}

async function saveCtoSettings() {
    const targetId = document.getElementById('flow-cto-id').value;
    const isAuto = document.getElementById('cto-auto-scan').checked;
    const workflowId = document.getElementById('cto-workflow-id').value;
    const testMode = document.getElementById('cto-test-mode').checked;
    
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                cto_target_channel: targetId,
                cto_auto_scan: isAuto ? 'true' : 'false',
                cto_workflow_id: workflowId,
                cto_test_mode: testMode ? 'true' : 'false'
            })
        });
        
        // Update local settings object so dropdown restoration works without reload
        settings.cto_target_channel = targetId;
        settings.cto_auto_scan = isAuto ? 'true' : 'false';
        settings.cto_workflow_id = workflowId;
        settings.cto_test_mode = testMode ? 'true' : 'false';
    } catch (e) {
        console.error("Failed to save CTO settings", e);
    }
}

// Initialize CTO settings
document.getElementById('flow-cto-id').value = settings.cto_target_channel || '';
document.getElementById('cto-auto-scan').checked = settings.cto_auto_scan === 'true';
document.getElementById('cto-test-mode').checked = settings.cto_test_mode === 'true';

renderWorkflows();
