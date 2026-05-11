let workflows = initialWorkflows || [];
let settings = initialSettings || {};
let currentRules = [];

function switchTab(tabId) {
    document.getElementById('tab-workflows').classList.add('hidden');
    document.getElementById('tab-tester').classList.add('hidden');
    document.getElementById('tab-settings').classList.add('hidden');
    
    document.getElementById('nav-workflows').classList.remove('active');
    document.getElementById('nav-tester').classList.remove('active');
    document.getElementById('nav-settings').classList.remove('active');
    
    document.getElementById('tab-' + tabId).classList.remove('hidden');
    document.getElementById('nav-' + tabId).classList.add('active');
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
            <div class="workflow-flow">
                <div class="channel-tag">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                    ${wf.source_channel || wf.source_channel_id || 'Not set'}
                </div>
                <div class="flow-arrow">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                </div>
                <div class="channel-tag">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                    ${wf.target_channel || wf.target_channel_id || 'Not set'}
                </div>
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
}

function openModal() {
    document.getElementById('workflow-modal').classList.remove('hidden');
    document.getElementById('modal-title').innerText = 'New Workflow';
    document.getElementById('flow-id').value = '';
    document.getElementById('flow-name').value = '';
    document.getElementById('flow-source').value = '';
    document.getElementById('flow-source-id').value = '';
    document.getElementById('flow-target').value = '';
    document.getElementById('flow-target-id').value = '';
    currentRules = [];
    renderRules();
}

function closeModal() {
    document.getElementById('workflow-modal').classList.add('hidden');
}

function editWorkflow(id) {
    const wf = workflows.find(w => w.id === id);
    if (!wf) return;
    
    document.getElementById('workflow-modal').classList.remove('hidden');
    document.getElementById('modal-title').innerText = 'Edit Workflow';
    document.getElementById('flow-id').value = wf.id;
    document.getElementById('flow-name').value = wf.name || '';
    document.getElementById('flow-source').value = wf.source_channel || '';
    document.getElementById('flow-source-id').value = wf.source_channel_id || '';
    document.getElementById('flow-target').value = wf.target_channel || '';
    document.getElementById('flow-target-id').value = wf.target_channel_id || '';
    
    // Convert rules array of objects
    currentRules = (wf.rules || []).map(r => ({ ...r }));
    renderRules();
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
                <input type="number" class="form-input" style="width:80px" value="${rule.time_min !== undefined ? rule.time_min : ''}" placeholder="0" onchange="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max Age (m):</label>
                <input type="number" class="form-input" style="width:80px" value="${rule.time_max !== undefined ? rule.time_max : ''}" placeholder="∞" onchange="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'extract_ca') {
            content += `<span style="color: var(--text-muted); font-size: 0.85rem;">Automatically extracts Contract Address from message.</span>`;
        } else if (rule.rule_type === 'performance') {
            // Default to '5m' if not set
            const timeframe = rule.search_text || '5m';
            content += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Timeframe:</label>
                <select class="form-input" style="width:100px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="5m" ${timeframe === '5m' ? 'selected' : ''}>5m</option>
                    <option value="1hr" ${timeframe === '1hr' ? 'selected' : ''}>1hr</option>
                    <option value="6hr" ${timeframe === '6hr' ? 'selected' : ''}>6hr</option>
                    <option value="24hr" ${timeframe === '24hr' ? 'selected' : ''}>24hr</option>
                </select>
                <label style="font-size:0.85rem; color:var(--text-muted)">Max %:</label>
                <input type="number" class="form-input" style="width:100px" placeholder="e.g. 50" value="${rule.time_max || ''}" onchange="updateRule(${index}, 'time_max', this.value)">
            `;
        } else {
            content += `<input class="form-input" placeholder="${rule.rule_type === 'filter' ? 'Word to drop message' : 'Word to find'}" value="${rule.search_text || ''}" onchange="updateRule(${index}, 'search_text', this.value)">`;
            if (rule.rule_type === 'replace') {
                content += `<input class="form-input" placeholder="Replace with..." value="${rule.replace_text || ''}" onchange="updateRule(${index}, 'replace_text', this.value)">`;
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
    const workflow = {
        name: name,
        source_channel: document.getElementById('flow-source').value,
        source_channel_id: document.getElementById('flow-source-id').value,
        target_channel: document.getElementById('flow-target').value,
        target_channel_id: document.getElementById('flow-target-id').value,
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
    const wf = workflows.find(w => w.id === id);
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
    const wf = workflows.find(w => w.id === id);
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
    const phone = document.getElementById('setting-phone').value;
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
            alert('Failed to request code: ' + result.error);
        }
    } catch (err) {
        alert('Error connecting to backend: ' + err);
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
            alert("Could not load conversations: " + result.error);
        }
    } catch (e) {
        console.error(e);
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

// Check auth status right after rendering workflows
setTimeout(checkAuthStatus, 1000);

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
        div.textContent = 'No channels found';
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
});

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

renderWorkflows();
