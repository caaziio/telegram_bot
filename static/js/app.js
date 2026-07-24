let workflows = initialWorkflows || [];
let settings = initialSettings || {};
let currentRules = [];

function switchTab(tabId) {
    const tabs = ['workflows', 'settings', 'wallet-tracker'];
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
    // Populate the dropdown selector in Scan History card
    const scanSelect = document.getElementById('scan-test-workflow');
    if (scanSelect) {
        const currentVal = scanSelect.value;
        scanSelect.innerHTML = '<option value="">None (Show All Senders)</option>';
        workflows.forEach(wf => {
            scanSelect.innerHTML += `<option value="${wf.id}">${wf.name}</option>`;
        });
        scanSelect.value = currentVal;
    }

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
            <div style="font-size: 0.82rem; color: var(--text-muted); margin: 0.75rem 0 1rem 0; display: flex; align-items: center; gap: 6px;">
                <span>📢 Target Group:</span>
                <strong style="color: #f3f4f6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;" title="${wf.target_channel || wf.target_channel_id || 'Fallback Group'}">${wf.target_channel || (wf.target_channel_id ? `Chat ${wf.target_channel_id}` : 'Fallback Group')}</strong>
            </div>
            <div class="workflow-rules-preview" style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1.5rem; min-height: 40px; align-content: flex-start;">
                ${(wf.rules && wf.rules.length > 0) ? wf.rules.map(r => {
                    let label = '';
                    let color = 'var(--accent-color)';
                    if (r.rule_type === 'token_age') {
                        const type = r.search_text === 'migration' ? 'Migr' : 'Creat';
                        label = `⏱️ Age (${type}): ${r.time_min || 0}-${r.time_max || '∞'}m`;
                        color = '#3b82f6';
                    } else if (r.rule_type === 'market_cap') {
                        let minMC = r.time_min ? (Number(r.time_min) >= 1000000 ? (Number(r.time_min)/1000000).toFixed(1) + 'M' : Number(r.time_min) >= 1000 ? (Number(r.time_min)/1000).toFixed(1) + 'K' : r.time_min) : '0';
                        let maxMC = r.time_max ? (Number(r.time_max) >= 1000000 ? (Number(r.time_max)/1000000).toFixed(1) + 'M' : Number(r.time_max) >= 1000 ? (Number(r.time_max)/1000).toFixed(1) + 'K' : r.time_max) : '∞';
                        label = `💰 MC: $${minMC}-$${maxMC}`;
                        color = '#10b981';
                    } else if (r.rule_type === 'performance') {
                        label = `📊 Perf (${r.search_text || '5m'}): ${r.time_min || '-∞'}% to ${r.time_max || '∞'}%`;
                        color = '#f59e0b';
                    } else if (r.rule_type === 'dex_payment') {
                        let minP = (r.time_min !== undefined && r.time_min !== null && r.time_min !== '') ? r.time_min : '0';
                        let maxP = (r.time_max !== undefined && r.time_max !== null && r.time_max !== '') ? r.time_max : '∞';
                        let labelText = r.search_text ? ` (${r.search_text})` : '';
                        label = `💳 DEX Pay: $${minP}-$${maxP}${labelText}`;
                        color = '#10b981';
                    } else if (r.rule_type === 'migrated') {
                        label = `🧬 Migrated: ${r.search_text === 'no' ? 'No' : 'Yes'}`;
                        color = '#6366f1';
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

    // Populate tester dropdown if it exists
    const select = document.getElementById('tester-workflow');
    if (select) {
        select.innerHTML = workflows.map(wf => `<option value="${wf.id}">${wf.name}</option>`).join('');
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
        div.style = 'display: grid; grid-template-columns: 130px 1fr auto; gap: 1rem; align-items: center; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); padding: 10px 14px; border-radius: 8px; margin-bottom: 10px;';
        
        let badgeColor = 'var(--accent-color)';
        if (rule.rule_type === 'token_age') badgeColor = '#3b82f6';
        else if (rule.rule_type === 'market_cap') badgeColor = '#10b981';
        else if (rule.rule_type === 'dex_payment') badgeColor = '#06b6d4';
        else if (rule.rule_type === 'migrated') badgeColor = '#6366f1';
        else if (rule.rule_type === 'performance') badgeColor = '#f59e0b';
        else if (rule.rule_type === 'exclude_platform') badgeColor = '#ef4444';
        else if (rule.rule_type === 'filter') badgeColor = '#ec4899';
        
        let badgeHtml = `<div style="display: flex; align-items: center;">
            <span class="status-badge" style="background: ${badgeColor}; color: white; width: 100%; text-align: center; justify-content: center; display: inline-flex;">${rule.rule_type.toUpperCase()}</span>
        </div>`;
        
        let fieldsHtml = '<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex: 1;">';
        
        if (rule.rule_type === 'token_age') {
            const ageType = rule.search_text || 'creation';
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Type:</label>
                <select class="form-input" style="width:100px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="creation" ${ageType === 'creation' ? 'selected' : ''}>Creation</option>
                    <option value="migration" ${ageType === 'migration' ? 'selected' : ''}>Migration</option>
                </select>
                <label style="font-size:0.85rem; color:var(--text-muted)">Min (m):</label>
                <input type="number" class="form-input" style="width:75px" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" placeholder="0" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max (m):</label>
                <input type="number" class="form-input" style="width:75px" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" placeholder="∞" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'extract_ca') {
            fieldsHtml += `<span style="color: var(--text-muted); font-size: 0.85rem;">Automatically extracts Contract Address from message.</span>`;
        } else if (rule.rule_type === 'performance') {
            const timeframe = rule.search_text || '5m';
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Timeframe:</label>
                <select class="form-input" style="width:85px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="5m" ${timeframe === '5m' ? 'selected' : ''}>5m</option>
                    <option value="1hr" ${timeframe === '1hr' ? 'selected' : ''}>1hr</option>
                    <option value="6hr" ${timeframe === '6hr' ? 'selected' : ''}>6hr</option>
                    <option value="24hr" ${timeframe === '24hr' ? 'selected' : ''}>24hr</option>
                </select>
                <label style="font-size:0.85rem; color:var(--text-muted)">Min %:</label>
                <input type="number" class="form-input" style="width:75px" placeholder="-∞" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max %:</label>
                <input type="number" class="form-input" style="width:75px" placeholder="∞" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'market_cap') {
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Min ($):</label>
                <input type="number" class="form-input" style="width:85px" placeholder="0" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max ($):</label>
                <input type="number" class="form-input" style="width:85px" placeholder="∞" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" oninput="updateRule(${index}, 'time_max', this.value)">
            `;
        } else if (rule.rule_type === 'dex_payment') {
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Min ($):</label>
                <input type="number" class="form-input" style="width:75px" placeholder="0" value="${(rule.time_min !== undefined && rule.time_min !== null) ? rule.time_min : ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max ($):</label>
                <input type="number" class="form-input" style="width:75px" placeholder="∞" value="${(rule.time_max !== undefined && rule.time_max !== null) ? rule.time_max : ''}" oninput="updateRule(${index}, 'time_max', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Label:</label>
                <input class="form-input" style="width:105px" placeholder="e.g. CTO" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)">
            `;
        } else if (rule.rule_type === 'migrated') {
            const isMigrated = rule.search_text || 'yes';
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Migrated:</label>
                <select class="form-input" style="width:90px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="yes" ${isMigrated === 'yes' ? 'selected' : ''}>Yes</option>
                    <option value="no" ${isMigrated === 'no' ? 'selected' : ''}>No</option>
                </select>
            `;
        } else if (rule.rule_type === 'exclude_platform') {
            fieldsHtml += `
                <input class="form-input" style="flex: 1; max-width: 250px;" placeholder="e.g. pump.fun" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)">
            `;
        } else {
            fieldsHtml += `<input class="form-input" style="flex: 1; max-width: 250px;" placeholder="${rule.rule_type === 'filter' ? 'Word to drop message' : 'Word to find'}" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)">`;
            if (rule.rule_type === 'replace') {
                fieldsHtml += `<input class="form-input" style="flex: 1; max-width: 250px;" placeholder="Replace with..." value="${rule.replace_text || ''}" oninput="updateRule(${index}, 'replace_text', this.value)">`;
            }
        }
        fieldsHtml += '</div>';
        
        let deleteHtml = `<div style="display: flex; align-items: center; justify-content: flex-end;">
            <button type="button" class="btn-icon" style="color: #ef4444; padding: 4px;" onclick="removeRule(${index})">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
        </div>`;
        
        div.innerHTML = badgeHtml + fieldsHtml + deleteHtml;
        container.appendChild(div);
    });
}

function addRule(type) {
    const newRule = { rule_type: type };
    if (type === 'token_age') {
        newRule.search_text = 'creation';
    }
    currentRules.push(newRule);
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
    const sourceChannel = document.getElementById('flow-source').value;
    const sourceChannelId = document.getElementById('flow-source-id').value;
    const targetChannel = document.getElementById('flow-target').value;
    const targetChannelId = document.getElementById('flow-target-id').value;
    
    const workflow = {
        name: name,
        source_channel: sourceChannel,
        source_channel_id: sourceChannelId,
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
    
    // Create a copy of the workflow object without the ID and clean its rules
    const newWf = {
        name: wf.name + " (Copy)",
        source_channel: wf.source_channel,
        source_channel_id: wf.source_channel_id,
        target_channel: wf.target_channel,
        target_channel_id: wf.target_channel_id,
        rules: (wf.rules || []).map(r => {
            const ruleCopy = { ...r };
            delete ruleCopy.id;
            delete ruleCopy.workflow_id;
            return ruleCopy;
        })
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
if (document.getElementById('setting-helius-key')) {
    document.getElementById('setting-helius-key').value = settings.helius_api_key || '';
}
if (document.getElementById('setting-cto-dex-payment-address')) {
    document.getElementById('setting-cto-dex-payment-address').value = settings.cto_dex_payment_address || '';
}
if (document.getElementById('setting-wallet-poll-enabled')) {
    document.getElementById('setting-wallet-poll-enabled').value = settings.wallet_tracker_poll_enabled || 'false';
}
if (document.getElementById('setting-helius-poll-enabled')) {
    document.getElementById('setting-helius-poll-enabled').value = settings.helius_poll_enabled || 'false';
}
if (document.getElementById('setting-polling-interval')) {
    document.getElementById('setting-polling-interval').value = settings.polling_interval || '10';
}
const webhookUrlInput = document.getElementById('setting-helius-webhook-url');
if (webhookUrlInput) {
    webhookUrlInput.value = window.location.origin + '/api/helius/webhook';
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        const warningDiv = document.createElement('div');
        warningDiv.style.marginTop = '8px';
        warningDiv.style.padding = '8px 12px';
        warningDiv.style.background = 'rgba(245, 158, 11, 0.1)';
        warningDiv.style.border = '1px solid rgba(245, 158, 11, 0.3)';
        warningDiv.style.borderRadius = '6px';
        warningDiv.style.color = '#f59e0b';
        warningDiv.style.fontSize = '0.8rem';
        warningDiv.style.lineHeight = '1.4';
        warningDiv.innerHTML = '⚠️ <strong>Localhost Detected:</strong> Helius cannot send webhooks to a local URL. You must expose port 5000 using a tunnel (e.g., <code>ngrok http 5000</code>) and paste your public tunnel URL in Helius Developer Portal pointing to <code>/api/helius/webhook</code>.';
        webhookUrlInput.parentNode.appendChild(warningDiv);
    }
}

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
            div.style.display = 'flex';
            div.style.justifyContent = 'space-between';
            div.style.alignItems = 'center';
            div.style.padding = '0.5rem 1rem';
            div.style.gap = '8px';
            
            let typeBadge = '';
            if (c.type === 'User') {
                typeBadge = `<span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(59, 130, 246, 0.3); font-weight: 500;">👤 User</span>`;
            } else if (c.type === 'Group') {
                typeBadge = `<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 500;">👥 Group</span>`;
            } else {
                typeBadge = `<span style="background: rgba(139, 92, 246, 0.15); color: #a78bfa; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(139, 92, 246, 0.3); font-weight: 500;">📢 Channel</span>`;
            }
            
            div.innerHTML = `
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%;">${c.name || 'Chat ' + c.id}</span>
                ${typeBadge}
            `;
            div.onclick = () => {
                input.value = c.name || `Chat ${c.id}`;
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



// Helius Settings Functions
async function saveHeliusSettings(e) {
    if (e) e.preventDefault();
    const heliusKey = document.getElementById('setting-helius-key').value.trim();
    const paymentAddress = document.getElementById('setting-cto-dex-payment-address').value.trim();
    const walletPollEnabled = document.getElementById('setting-wallet-poll-enabled').value;
    const heliusPollEnabled = document.getElementById('setting-helius-poll-enabled').value;
    const pollingInterval = document.getElementById('setting-polling-interval').value;
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                helius_api_key: heliusKey,
                cto_dex_payment_address: paymentAddress,
                wallet_tracker_poll_enabled: walletPollEnabled,
                helius_poll_enabled: heliusPollEnabled,
                polling_interval: pollingInterval
            })
        });
        const result = await res.json();
        if (result.success) {
            settings.helius_api_key = heliusKey;
            settings.cto_dex_payment_address = paymentAddress;
            settings.wallet_tracker_poll_enabled = walletPollEnabled;
            settings.helius_poll_enabled = heliusPollEnabled;
            settings.polling_interval = pollingInterval;
            updateTrackerStatusUI();
            alert('Helius settings saved successfully!');
        } else {
            alert('Error saving Helius settings: ' + result.error);
        }
    } catch (err) {
        alert('Failed to save settings: ' + err.message);
    }
}


// Wallet Tracker functions
async function saveTrackedAddress(event) {
    if (event) event.preventDefault();
    const address = document.getElementById('tracker-monitored-address').value.trim();
    if (!address) {
        alert("Please enter a valid Solana address");
        return;
    }
    
    try {
        const res = await fetch('/api/wallet/save_tracked', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address })
        });
        const result = await res.json();
        if (result.success) {
            settings.tracked_wallet_address = address;
            document.getElementById('scan-wallet-address').value = address;
            
            if (str(settings.cto_auto_scan).toLowerCase() !== 'true') {
                alert("Tracked address saved. Make sure to click 'Start Live Scan' in the header to start monitoring!");
            } else {
                alert("Tracked address saved. Monitoring started in background!");
            }
            
            loadRealtimePayments();
            updateTrackerStatusUI();
        } else {
            alert("Error saving tracked address: " + result.error);
        }
    } catch (e) {
        alert("Failed to save tracked address: " + e.message);
    }
}

async function scanWalletHistory() {
    const address = document.getElementById('scan-wallet-address').value.trim();
    const minAmount = document.getElementById('scan-min-amount').value.trim();
    const maxAmount = document.getElementById('scan-max-amount').value.trim();
    const timeValue = document.getElementById('scan-time-value').value.trim();
    const timeUnit = document.getElementById('scan-time-unit').value;
    
    const testWorkflowSelect = document.getElementById('scan-test-workflow');
    const workflowId = testWorkflowSelect ? testWorkflowSelect.value : '';
    
    const container = document.getElementById('tracker-senders-container');
    const badge = document.getElementById('tracker-senders-count');
    
    if (!address) {
        alert("Please enter a Solana wallet address to scan");
        return;
    }
    
    container.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--text-muted);">Scanning transactions (this may take a few seconds)...</div>';
    badge.textContent = '0';
    
    try {
        const res = await fetch('/api/wallet/transactions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                address,
                min_amount: minAmount ? parseFloat(minAmount) : null,
                max_amount: maxAmount ? parseFloat(maxAmount) : null,
                time_value: timeValue ? parseFloat(timeValue) : 12,
                time_unit: timeUnit,
                workflow_id: workflowId ? parseInt(workflowId) : null
            })
        });
        
        const result = await res.json();
        if (result.success) {
            badge.textContent = result.senders.length;
            if (result.senders.length === 0) {
                container.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--text-muted);">No matching transactions found in the specified window.</div>';
                return;
            }
            
            container.innerHTML = result.senders.map((s, idx) => {
                const latestDate = new Date(s.latest_timestamp * 1000).toLocaleString();
                
                // Construct test badge if a workflow test was performed
                let testBadgeHtml = '';
                if (s.test_status) {
                    if (s.test_status === 'passed') {
                        testBadgeHtml = `
                            <div style="margin-bottom: 10px; padding: 6px 10px; background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; display: flex; align-items: center; gap: 6px; font-size: 0.78rem; color: #34d399; font-weight: 600;">
                                🟢 PASSED WORKFLOW CHECKLIST
                            </div>
                        `;
                    } else if (s.test_status === 'dropped') {
                        testBadgeHtml = `
                            <div style="margin-bottom: 10px; padding: 6px 10px; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 6px; display: flex; flex-direction: column; gap: 2px; font-size: 0.78rem; color: #f87171; font-weight: 500;">
                                <div style="font-weight: 700; color: #fca5a5; display: flex; align-items: center; gap: 6px;">
                                    🔴 DROPPED BY WORKFLOW
                                </div>
                                <div style="font-size: 0.72rem; color: #fca5a5; margin-top: 2px;">Reason: ${s.test_reason}</div>
                            </div>
                        `;
                    } else if (s.test_status === 'error') {
                        testBadgeHtml = `
                            <div style="margin-bottom: 10px; padding: 6px 10px; background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 6px; display: flex; flex-direction: column; gap: 2px; font-size: 0.78rem; color: #fbbf24; font-weight: 500;">
                                <div style="font-weight: 700;">⚠️ TEST RUN ERROR</div>
                                <div style="font-size: 0.72rem; margin-top: 2px;">${s.test_reason}</div>
                            </div>
                        `;
                    }
                }

                // Construct details of transfers
                const transfersListHtml = s.transfers.map(t => {
                    const explorerUrl = `https://solscan.io/tx/${t.signature}`;
                    const tokenDisplayName = t.token_name ? t.token_name : (t.mint === 'SOL' ? 'SOL' : t.mint.slice(0,6) + '...');
                    const amountUsdText = t.amount_usd ? `$${t.amount_usd.toFixed(2)}` : '$0.00';
                    return `
                        <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.75rem; background:rgba(255,255,255,0.02); padding: 4px 8px; border-radius:4px; margin-top:4px;">
                            <span style="color:#10b981; font-weight: 500;">+${t.amount.toFixed(4)} ${tokenDisplayName} (${amountUsdText})</span>
                            <a href="${explorerUrl}" target="_blank" style="color:#60a5fa; text-decoration:none; font-family:monospace;">${t.signature.slice(0,8)}... ↗</a>
                        </div>
                    `;
                }).join('');
                
                return `
                    <div class="result-success" style="padding: 1rem; border: 1px solid var(--border-color); border-radius: 8px; background: rgba(0,0,0,0.2); margin-bottom: 0.5rem;">
                        ${testBadgeHtml}
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                            <span style="font-weight:bold; color:white; font-size:0.9rem;">#${idx + 1} Sender</span>
                            <button onclick="openWalletTxModal('${s.address}')" class="btn-primary" style="padding: 4px 10px; font-size: 0.75rem; border-radius: 4px; box-shadow: none;">
                                Inspect Wallet ↗
                            </button>
                        </div>
                        <div style="font-size:0.78rem; font-family:monospace; color:#60a5fa; word-break:break-all; margin-bottom:8px; user-select:all;">
                            ${s.address}
                        </div>
                        <div style="font-size:0.8rem; margin-bottom:6px; display:flex; justify-content:space-between;">
                            <span>Total Sent: <strong style="color:white;">$${s.total_sent_usd.toFixed(2)}</strong></span>
                            <span style="color:var(--text-muted);">${s.tx_count} tx(s)</span>
                        </div>
                        <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:6px;">
                            Latest: ${latestDate}
                        </div>
                        
                        ${s.last_bought_token ? `
                        <div style="margin-top: 8px; margin-bottom: 8px; padding: 8px; background: rgba(139, 92, 246, 0.08); border: 1px solid rgba(139, 92, 246, 0.2); border-radius: 6px;">
                            <div style="font-size: 0.72rem; color: #a78bfa; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 4px;">
                                🪙 Last Token Bought Before Payment:
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                                <div style="display: flex; flex-direction: column; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: calc(100% - 100px);">
                                    <span style="font-weight: bold; color: white; font-size: 0.82rem;" title="${s.last_bought_token_name || ''}">${s.last_bought_token_name || 'Unknown Token'}</span>
                                    <span style="font-family: monospace; color: var(--text-muted); font-size: 0.7rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: all;" title="${s.last_bought_token}">${s.last_bought_token}</span>
                                </div>
                                <a href="https://dexscreener.com/solana/${s.last_bought_token}" target="_blank" class="btn-primary" style="padding: 3px 8px; font-size: 0.7rem; border-radius: 4px; text-decoration: none; background: #8b5cf6; color: white; flex-shrink: 0; font-weight: 600; box-shadow: none;">
                                    Dexscreener ↗
                                </a>
                            </div>
                        </div>
                        ` : `
                        <div style="margin-top: 8px; margin-bottom: 8px; padding: 6px 8px; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 6px; font-size: 0.72rem; color: var(--text-muted);">
                            🪙 Last Token Bought: None detected in recent history
                        </div>
                        `}

                        <div style="border-top:1px dashed rgba(255,255,255,0.05); padding-top:6px; margin-top:6px;">
                            <span style="font-size:0.75rem; color:var(--text-muted); font-weight:500;">Transfers:</span>
                            ${transfersListHtml}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            container.innerHTML = `<div class="result-error">Error: ${result.error}</div>`;
        }
    } catch (e) {
        container.innerHTML = `<div class="result-error">Request failed: ${e.message}</div>`;
    }
}

async function openWalletTxModal(senderAddress) {
    const modal = document.getElementById('wallet-tx-modal');
    const title = document.getElementById('wallet-tx-modal-title');
    const subtitle = document.getElementById('wallet-tx-modal-subtitle');
    const loader = document.getElementById('wallet-tx-loader');
    const tbody = document.getElementById('wallet-tx-tbody');
    
    if (!modal) return;
    
    title.textContent = "Sender Wallet Inspect (Level 2)";
    subtitle.textContent = "Address: " + senderAddress;
    tbody.innerHTML = '';
    loader.style.display = 'block';
    modal.classList.remove('hidden');
    
    try {
        const res = await fetch('/api/wallet/address_transactions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address: senderAddress })
        });
        const result = await res.json();
        
        loader.style.display = 'none';
        
        if (result.success) {
            if (result.transactions.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem; color:var(--text-muted);">No recent transactions found.</td></tr>';
                return;
            }
            
            tbody.innerHTML = result.transactions.map(tx => {
                const txDate = new Date(tx.timestamp * 1000).toLocaleString();
                const explorerUrl = `https://solscan.io/tx/${tx.signature}`;
                
                let actionBadge = '';
                if (tx.action === 'BUY') {
                    actionBadge = `<span style="color:#34d399; background:rgba(16, 185, 129, 0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem;">🟢 BUY</span>`;
                } else if (tx.action === 'SELL') {
                    actionBadge = `<span style="color:#f87171; background:rgba(239, 68, 68, 0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem;">🔴 SELL</span>`;
                } else {
                    actionBadge = `<span style="color:#9ca3af; background:rgba(156,163,175,0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem;">⚪ ${tx.action.slice(0, 10)}</span>`;
                }
                
                let tokenHtml = '-';
                let tradeBtn = '';
                if (tx.token_bought) {
                    const dexscreenerUrl = `https://dexscreener.com/solana/${tx.token_bought}`;
                    const displayName = tx.token_name ? tx.token_name : (tx.token_bought.slice(0,6) + '...' + tx.token_bought.slice(-4));
                    tokenHtml = `
                        <div style="display:flex; flex-direction:column; gap:2px;">
                            <span style="font-weight:600; color:white; font-size:0.88rem;" title="${tx.token_bought}">${displayName}</span>
                            <div style="display:flex; align-items:center; gap:6px; font-size:0.75rem;">
                                <span style="font-family:monospace; color:#60a5fa; user-select:all;" title="${tx.token_bought}">${tx.token_bought.slice(0,6)}...${tx.token_bought.slice(-4)}</span>
                                <button onclick="navigator.clipboard.writeText('${tx.token_bought}'); alert('Token Address copied!');" class="btn-icon" style="padding:2px; font-size:0.75rem;" title="Copy Address">📋</button>
                            </div>
                        </div>
                    `;
                    tradeBtn = `
                        <a href="${dexscreenerUrl}" target="_blank" style="background: linear-gradient(135deg, #10b981, #059669); color: white; text-decoration: none; font-size: 0.72rem; padding: 3px 8px; border-radius: 4px; display: inline-flex; align-items: center; font-weight: 600; transition: all 0.2s; box-shadow: 0 0 8px rgba(16,185,129,0.3);" onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
                            Trade ↗
                        </a>
                    `;
                }
                
                const amountText = tx.amount_bought > 0 ? tx.amount_bought.toLocaleString(undefined, {maximumFractionDigits: 2}) : '-';
                const costText = tx.value_exchanged || '-';
                
                return `
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding:0.75rem 0.5rem; color:var(--text-muted); font-size:0.8rem; white-space:nowrap;">
                            ${txDate}
                        </td>
                        <td style="padding:0.75rem 0.5rem; white-space:nowrap;">
                            ${actionBadge}
                        </td>
                        <td style="padding:0.75rem 0.5rem;">
                            ${tokenHtml}
                        </td>
                        <td style="padding:0.75rem 0.5rem; text-align:right; font-weight:500; color:${tx.action === 'BUY' ? '#34d399' : (tx.action === 'SELL' ? '#f87171' : 'white')};">
                            ${amountText}
                        </td>
                        <td style="padding:0.75rem 0.5rem; text-align:right; color:white; font-weight:500;">
                            ${costText}
                        </td>
                        <td style="padding:0.75rem 0.5rem; text-align:right; white-space:nowrap; display:flex; gap:6px; justify-content:flex-end; align-items:center;">
                            ${tradeBtn}
                            <a href="${explorerUrl}" target="_blank" style="background: rgba(59, 130, 246, 0.12); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); text-decoration: none; font-size: 0.72rem; padding: 2px 8px; border-radius: 4px; display: inline-flex; align-items: center; font-weight: 500; font-style: normal; transition: all 0.2s;">
                                Tx ↗
                            </a>
                        </td>
                    </tr>
                `;
            }).join('');
        } else {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2rem; color:#ef4444;">Error: ${result.error}</td></tr>`;
        }
    } catch (e) {
        loader.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding: 2rem; color:#ef4444;">Request failed: ${e.message}</td></tr>`;
    }
}

function closeWalletTxModal() {
    const modal = document.getElementById('wallet-tx-modal');
    if (modal) modal.classList.add('hidden');
}

async function loadRealtimePayments() {
    const container = document.getElementById('realtime-payments-container');
    if (!container) return;
    
    try {
        const res = await fetch('/api/wallet/payments_log');
        const result = await res.json();
        
        if (result.success) {
            if (result.payments.length === 0) {
                container.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--text-muted);">No payments tracked yet. Background monitor checks every 10s.</div>';
                return;
            }
            
            container.innerHTML = result.payments.map((p, idx) => {
                const dateStr = new Date(p.timestamp * 1000).toLocaleString();
                const explorerUrl = `https://solscan.io/tx/${p.signature}`;
                const amountUsdText = p.amount_usd ? `$${p.amount_usd.toFixed(2)}` : '$0.00';
                const tokenDisplayName = p.token_name ? p.token_name : (p.mint === 'SOL' ? 'SOL' : p.mint.slice(0,6) + '...');
                
                return `
                    <div class="result-success" style="padding: 1rem; border: 1px solid var(--border-color); border-radius: 8px; background: rgba(0,0,0,0.1); border-left: 3px solid #8b5cf6; margin-bottom: 0.5rem;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <span style="font-weight:600; color:#c084fc; font-size:0.82rem;">💳 Incoming Transfer</span>
                            <span style="font-size:0.72rem; color:var(--text-muted);">${dateStr}</span>
                        </div>
                        <div style="font-size:0.95rem; font-weight:bold; color:white; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                            <span>+${p.amount.toFixed(4)} ${tokenDisplayName}</span>
                            <span style="color:#c084fc; font-size:0.9rem;">${amountUsdText}</span>
                        </div>
                        <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:4px; display:flex; flex-direction:column; gap:4px;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span>Sender: <span style="font-family:monospace; color:#60a5fa; user-select:all;">${p.sender_address.slice(0,10)}...${p.sender_address.slice(-6)}</span></span>
                                <button onclick="openWalletTxModal('${p.sender_address}')" class="btn-primary" style="padding: 3px 8px; font-size: 0.72rem; border-radius: 4px; box-shadow: none; background: rgba(139, 92, 246, 0.2); border: 1px solid rgba(139, 92, 246, 0.4); color: #c084fc;">
                                    Inspect Wallet ↗
                                </button>
                            </div>
                            <div>Receiver: <span style="font-family:monospace; color:white;">${p.tracked_address.slice(0,10)}...${p.tracked_address.slice(-6)}</span></div>
                        </div>
                        <div style="text-align:right; font-size:0.72rem; margin-top:6px; border-top:1px dashed rgba(255,255,255,0.05); padding-top:6px;">
                            <a href="${explorerUrl}" target="_blank" style="color:#60a5fa; text-decoration:none; font-family:monospace;">View signature ${p.signature.slice(0,8)}... ↗</a>
                        </div>
                    </div>
                `;
            }).join('');
        }
    } catch (e) {
        console.error("Failed to load real-time payments", e);
    }
}

async function toggleLiveScan() {
    const currentActive = str(settings.cto_auto_scan).toLowerCase() === 'true';
    const nextActive = !currentActive;
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cto_auto_scan: nextActive ? 'true' : 'false' })
        });
        const result = await res.json();
        if (result.success) {
            settings.cto_auto_scan = nextActive ? 'true' : 'false';
            updateLiveScanButtonUI();
            updateTrackerStatusUI();
        } else {
            alert('Failed to update live scan status: ' + result.error);
        }
    } catch (err) {
        alert('Network error updating live scan: ' + err.message);
    }
}

function updateLiveScanButtonUI() {
    const btn = document.getElementById('btn-toggle-live-scan');
    if (!btn) return;
    const isActive = str(settings.cto_auto_scan).toLowerCase() === 'true';
    if (isActive) {
        btn.innerHTML = '🟢 Live Scan Active';
        btn.style.background = 'rgba(16, 185, 129, 0.15)';
        btn.style.borderColor = '#10b981';
        btn.style.color = '#10b981';
    } else {
        btn.innerHTML = '⚫ Start Live Scan';
        btn.style.background = 'rgba(255, 255, 255, 0.05)';
        btn.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        btn.style.color = 'var(--text-muted)';
    }
}

function updateTrackerStatusUI() {
    const indicator = document.getElementById('tracker-status-indicator');
    if (!indicator) return;
    const hasAddress = settings.tracked_wallet_address && settings.tracked_wallet_address.trim().length >= 32;
    const isPolling = String(settings.wallet_tracker_poll_enabled).toLowerCase() === 'true';
    const isLiveScan = String(settings.cto_auto_scan).toLowerCase() === 'true';
    if (hasAddress && (isPolling || isLiveScan)) {
        indicator.innerHTML = 'Active';
        indicator.style.background = 'rgba(16, 185, 129, 0.15)';
        indicator.style.borderColor = '#10b981';
        indicator.style.color = '#10b981';
    } else {
        indicator.innerHTML = 'Inactive';
        indicator.style.background = 'rgba(255, 255, 255, 0.05)';
        indicator.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        indicator.style.color = 'var(--text-muted)';
    }
}

function str(val) {
    return val === undefined || val === null ? '' : String(val);
}

// Initialization on load
if (document.getElementById('tracker-monitored-address')) {
    document.getElementById('tracker-monitored-address').value = settings.tracked_wallet_address || '';
}
if (document.getElementById('scan-wallet-address')) {
    document.getElementById('scan-wallet-address').value = settings.tracked_wallet_address || '';
}

// Close Level 2 modal when clicking overlay
document.addEventListener('click', function(e) {
    const modal = document.getElementById('wallet-tx-modal');
    if (modal && e.target === modal) {
        closeWalletTxModal();
    }
});

loadRealtimePayments();
setInterval(loadRealtimePayments, 15000);

renderWorkflows();
updateLiveScanButtonUI();
updateTrackerStatusUI();
