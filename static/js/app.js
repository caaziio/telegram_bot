let workflows = initialWorkflows || [];
let settings = initialSettings || {};
let currentRules = [];
let currentTokenDataPage = 1;
let tokenDataSearchTimer = null;
let tokenDataAutoRefreshInterval = null;
let currentTgModalRawMessage = '';
let tokenNameRuleIndex = null;
let tokenNameDraft = { exact: [], contains: [] };
let dexValidatedRuntime = null;
let dexValidatedStatusTimer = null;
let dexValidatedCountdownTimer = null;
let dexValidatedRenderedResults = [];
let currentDexValidatedDataPage = 1;
let dexValidatedDataSearchTimer = null;
let dexValidatedDataAutoRefreshInterval = null;
let lastSeenDexValidatedRecordId = 0;

const FLOW_GROUP_COLORS = [
    { key: 'cyan', name: 'Cyan', hex: '#06b6d4', text: '#67e8f9', bg: 'rgba(6, 182, 212, 0.16)', border: 'rgba(6, 182, 212, 0.45)', glow: 'rgba(6, 182, 212, 0.35)' },
    { key: 'emerald', name: 'Emerald', hex: '#10b981', text: '#6ee7b7', bg: 'rgba(16, 185, 129, 0.16)', border: 'rgba(16, 185, 129, 0.45)', glow: 'rgba(16, 185, 129, 0.35)' },
    { key: 'blue', name: 'Blue', hex: '#3b82f6', text: '#93c5fd', bg: 'rgba(59, 130, 246, 0.16)', border: 'rgba(59, 130, 246, 0.45)', glow: 'rgba(59, 130, 246, 0.35)' },
    { key: 'amber', name: 'Amber', hex: '#f59e0b', text: '#fcd34d', bg: 'rgba(245, 158, 11, 0.16)', border: 'rgba(245, 158, 11, 0.45)', glow: 'rgba(245, 158, 11, 0.35)' },
    { key: 'rose', name: 'Rose', hex: '#f43f5e', text: '#fda4af', bg: 'rgba(244, 63, 94, 0.16)', border: 'rgba(244, 63, 94, 0.45)', glow: 'rgba(244, 63, 94, 0.35)' },
    { key: 'purple', name: 'Purple', hex: '#8b5cf6', text: '#c4b5fd', bg: 'rgba(139, 92, 246, 0.16)', border: 'rgba(139, 92, 246, 0.45)', glow: 'rgba(139, 92, 246, 0.35)' },
    { key: 'indigo', name: 'Indigo', hex: '#6366f1', text: '#a5b4fc', bg: 'rgba(99, 102, 241, 0.16)', border: 'rgba(99, 102, 241, 0.45)', glow: 'rgba(99, 102, 241, 0.35)' }
];

function getGroupTheme(colorKey) {
    return FLOW_GROUP_COLORS.find(c => c.key === colorKey) || FLOW_GROUP_COLORS[0];
}

function showAppToast(message, type = 'info', duration = 3000) {
    let container = document.getElementById('app-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'app-toast-container';
        container.className = 'app-toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `app-toast app-toast--${type}`;
    const icon = type === 'success' 
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>' 
        : type === 'error' 
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' 
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
    toast.innerHTML = `
        <span class="app-toast-icon">${icon}</span>
        <span class="app-toast-message">${escapeHtml(message)}</span>
        <button type="button" class="app-toast-close" onclick="this.parentElement.remove()" aria-label="Close notification"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('app-toast--exiting');
        setTimeout(() => toast.remove(), 260);
    }, duration);
}

function parseFlowGroups(raw) {
    try {
        const parsed = typeof raw === 'string' ? JSON.parse(raw) : (raw || {});
        let groups = Array.isArray(parsed.groups) ? parsed.groups.filter(g => g && g.id && g.name).map(g => ({
            id: String(g.id),
            name: String(g.name),
            color: g.color || 'cyan',
            parent_id: g.parent_id ? String(g.parent_id) : null,
            badge: g.badge ? String(g.badge) : null,
            is_expanded: g.is_expanded !== undefined ? Boolean(g.is_expanded) : true
        })) : [];

        if (groups.length === 0) {
            groups = [
                { id: 'grouped', name: 'Grouped', color: 'cyan' },
                { id: 'crypto-workflows', name: 'Crypto Workflows', color: 'cyan', is_expanded: true },
                { id: 'arbitrage', name: 'Arbitrage', color: 'cyan', parent_id: 'crypto-workflows' },
                { id: 'scalp-bots', name: 'Scalp Bots', color: 'cyan', parent_id: 'crypto-workflows' },
                { id: 'indicators', name: 'Indicators', color: 'cyan', badge: '34' },
                { id: 'grid-trades', name: 'Grid Trades', color: 'cyan', badge: '22' }
            ];
        }

        return {
            groups: groups,
            assignments: parsed.assignments && typeof parsed.assignments === 'object' ? parsed.assignments : {}
        };
    } catch (_) {
        return {
            groups: [
                { id: 'grouped', name: 'Grouped', color: 'cyan' },
                { id: 'crypto-workflows', name: 'Crypto Workflows', color: 'cyan', is_expanded: true },
                { id: 'arbitrage', name: 'Arbitrage', color: 'cyan', parent_id: 'crypto-workflows' },
                { id: 'scalp-bots', name: 'Scalp Bots', color: 'cyan', parent_id: 'crypto-workflows' },
                { id: 'indicators', name: 'Indicators', color: 'cyan', badge: '34' },
                { id: 'grid-trades', name: 'Grid Trades', color: 'cyan', badge: '22' }
            ],
            assignments: {}
        };
    }
}

let flowGroups = parseFlowGroups(settings.workflow_groups);
let collapsedFlowGroups = new Set();
try {
    collapsedFlowGroups = new Set(JSON.parse(localStorage.getItem('teleflow_collapsed_flow_groups') || '[]').map(String));
} catch (_) {}
let activeFlowGroupView = 'crypto-workflows';
try { activeFlowGroupView = localStorage.getItem('teleflow_active_flow_group') || 'crypto-workflows'; } catch (_) {}
let expandedWorkflowCards = new Set();
let expandedGroupDrawers = new Set();
let selectedQuickGroupColor = 'cyan';
let selectedModalGroupColor = 'cyan';
let activeAssignGroupId = null;
let pendingDeleteGroupId = null;

const TOKEN_NAME_FILTER_PREFIX = 'token_name_filter:';

function parseTokenNameFilter(value) {
    if (typeof value !== 'string' || !value.startsWith(TOKEN_NAME_FILTER_PREFIX)) return null;
    try {
        const parsed = JSON.parse(value.slice(TOKEN_NAME_FILTER_PREFIX.length));
        return {
            exact: Array.isArray(parsed.exact) ? parsed.exact.map(String).filter(Boolean) : [],
            contains: Array.isArray(parsed.contains) ? parsed.contains.map(String).filter(Boolean) : []
        };
    } catch (_) {
        return null;
    }
}

function encodeTokenNameFilter(filter) {
    return TOKEN_NAME_FILTER_PREFIX + JSON.stringify(filter);
}

function tokenNameFilterSummary(rule) {
    const filter = parseTokenNameFilter(rule.search_text);
    if (!filter) return rule.search_text || 'Legacy regex';
    return `${filter.exact.length} exact, ${filter.contains.length} contains`;
}

function tokenNameFilterCounts(rule) {
    const filter = parseTokenNameFilter(rule.search_text);
    return filter || { exact: [], contains: [] };
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

async function readJsonResponse(response, endpointName) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
        throw new Error(`${endpointName} is not available on the running server. Restart or redeploy the app to load the new backend route.`);
    }
    return response.json();
}

const VALID_PAGE_TABS = ['dashboard', 'workflows', 'dex-validated', 'dex-validated-data', 'settings', 'wallet-tracker', 'token-data'];

function getTabFromPathname(pathname) {
    const clean = String(pathname || '').replace(/^\/+|\/+$/g, '').toLowerCase();
    if (!clean || clean === 'dashboard') return 'dashboard';
    return VALID_PAGE_TABS.includes(clean) ? clean : null;
}

function navigateSidebarTab(event, tabId) {
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)) {
        return true;
    }
    if (event) event.preventDefault();
    switchTab(tabId);
    return false;
}

function switchTab(tabId, options = {}) {
    if (!VALID_PAGE_TABS.includes(tabId)) {
        tabId = 'dashboard';
    }

    VALID_PAGE_TABS.forEach(t => {
        const tabEl = document.getElementById('tab-' + t);
        const navEl = document.getElementById('nav-' + t);
        const railEl = document.getElementById('rail-nav-' + t);
        if (tabEl) tabEl.classList.add('hidden');
        if (navEl) navEl.classList.remove('active');
        if (railEl) railEl.classList.remove('active');
    });
    
    const targetTab = document.getElementById('tab-' + tabId);
    const targetNav = document.getElementById('nav-' + tabId);
    const targetRail = document.getElementById('rail-nav-' + tabId);
    if (targetTab) targetTab.classList.remove('hidden');
    if (targetNav) targetNav.classList.add('active');
    if (targetRail) targetRail.classList.add('active');

    const targetPath = '/' + tabId;
    if (!options.skipPushState && window.location.pathname !== targetPath) {
        try {
            window.history.pushState({ tabId }, '', targetPath);
        } catch (_) {}
    }

    if (tabId === 'dashboard') {
        loadDashboardOverview(false);
    }

    if (tabId === 'workflows') {
        renderGrokFoldersTree();
        renderWorkflows();
    }

    if (tabId === 'token-data') {
        const hasCache = renderTokenDataFromCache();
        loadTokenWalletData(1, !hasCache);
    }

    if (tabId === 'dex-validated') {
        loadDexValidatedLiveResults();
    }

    if (tabId === 'dex-validated-data') {
        loadDexValidatedTableData(1, true);
    }
}

window.addEventListener('popstate', () => {
    const tabFromUrl = getTabFromPathname(window.location.pathname) || 'dashboard';
    switchTab(tabFromUrl, { skipPushState: true });
});

function toggleAkoueSidebar(forceState) {
    const sidebar = document.getElementById('akoue-sidebar') || document.querySelector('.akoue-sidebar') || document.querySelector('.grok-sidebar');
    if (!sidebar) return;
    if (typeof forceState === 'boolean') {
        sidebar.classList.toggle('collapsed', !forceState);
    } else {
        sidebar.classList.toggle('collapsed');
    }
    const isCollapsed = sidebar.classList.contains('collapsed');
    try {
        localStorage.setItem('akoue_sidebar_collapsed', isCollapsed ? 'true' : 'false');
    } catch (_) {}
    const toggleBtn = document.getElementById('akoue-sidebar-toggle');
    if (toggleBtn) {
        toggleBtn.title = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
    }
}

// Backward-compatibility aliases
function toggleGrokDrawer(forceState) {
    toggleAkoueSidebar(forceState);
}

function toggleGrokRailExpand(forceState) {
    toggleAkoueSidebar(forceState);
}

function handleWorkflowsNavClick(event) {
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)) {
        return true;
    }
    if (event) event.preventDefault();
    const section = document.querySelector('.akoue-workflows-section');
    const sidebar = document.getElementById('akoue-sidebar');
    
    // Switch tab to workflows
    switchTab('workflows');
    
    // If sidebar is collapsed into icon rail, expanding it reveals the dropdown
    if (sidebar && sidebar.classList.contains('collapsed')) {
        toggleAkoueSidebar(true);
        if (section) section.classList.add('open');
        try { localStorage.setItem('akoue_workflows_dropdown_open', 'true'); } catch (_) {}
        return false;
    }
    
    // Toggle dropdown open/closed
    if (section) {
        section.classList.toggle('open');
        const isOpen = section.classList.contains('open');
        try {
            localStorage.setItem('akoue_workflows_dropdown_open', isOpen ? 'true' : 'false');
        } catch (_) {}
    }
    return false;
}

function handleGrokSearch(query) {
    renderGrokFoldersTree();
    const wfSearch = document.getElementById('workflow-search');
    if (wfSearch) {
        wfSearch.value = query;
        renderWorkflows();
    }
}

function toggleNotificationsModal() {
    const isLive = String(settings.cto_auto_scan).toLowerCase() === 'true';
    showAppToast(isLive ? '● Live Scan is actively monitoring Solana signals.' : 'Live Scan is currently paused.', isLive ? 'success' : 'info');
}

function renderGrokFoldersTree() {
    const treeEl = document.getElementById('grok-folders-tree');
    if (!treeEl) return;

    if (!flowGroups.groups || flowGroups.groups.length === 0) {
        flowGroups.groups = [
            { id: 'narratives', name: 'Narratives', color: 'cyan' }
        ];
    }

    const searchQuery = String(document.getElementById('grok-folder-search')?.value || '').trim().toLowerCase();
    const parentGroups = flowGroups.groups.filter(g => !g.parent_id);
    const childrenMap = {};
    flowGroups.groups.forEach(g => {
        if (g.parent_id) {
            if (!childrenMap[g.parent_id]) childrenMap[g.parent_id] = [];
            childrenMap[g.parent_id].push(g);
        }
    });

    let html = '';

    // "All flows" option at the top of the tree
    const isAllActive = activeFlowGroupView === 'all' || !activeFlowGroupView;
    const totalFlowsCount = workflows ? workflows.length : 0;
    if (!searchQuery || 'all flows'.includes(searchQuery)) {
        html += `
            <div class="grok-folder-item ${isAllActive ? 'active' : ''}" 
                 data-group-id="all"
                 onclick="selectFlowGroup('all')">
                <span class="grok-folder-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="2" y="3" width="20" height="14" rx="2"></rect>
                        <line x1="8" y1="21" x2="16" y2="21"></line>
                        <line x1="12" y1="17" x2="12" y2="21"></line>
                    </svg>
                </span>
                <span class="grok-folder-name">All flows</span>
                <span class="grok-folder-badge">${totalFlowsCount > 0 ? totalFlowsCount : ''}</span>
            </div>
        `;
    }

    parentGroups.forEach(group => {
        const children = childrenMap[group.id] || [];
        const hasChildren = children.length > 0;
        const isParentActive = activeFlowGroupView === group.id;

        const assignedCount = workflows.filter(wf => {
            const assigned = String(flowGroups.assignments[String(wf.id)] || '');
            return assigned === group.id;
        }).length;

        const badgeValue = group.badge || (assignedCount > 0 ? String(assignedCount) : '');

        if (searchQuery && !group.name.toLowerCase().includes(searchQuery) && !children.some(c => c.name.toLowerCase().includes(searchQuery))) {
            return;
        }

        html += `
            <div class="grok-folder-item ${isParentActive ? 'active' : ''}" 
                 data-group-id="${escapeHtml(group.id)}"
                 onclick="selectFlowGroup('${escapeHtml(group.id)}')"
                 ondragover="handleFolderDragOver(event, this)" 
                 ondragleave="handleFolderDragLeave(event, this)" 
                 ondrop="dropWorkflowOnFolder(event, '${escapeHtml(group.id)}', this)">
                <span class="grok-folder-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="${isParentActive ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"></path>
                    </svg>
                </span>
                <span class="grok-folder-name">${escapeHtml(group.name)}</span>
                ${badgeValue ? `<span class="grok-folder-badge">${escapeHtml(badgeValue)}</span>` : ''}
            </div>
        `;

        if (hasChildren && (isParentActive || group.is_expanded || searchQuery || children.some(c => c.id === activeFlowGroupView))) {
            html += `<div class="grok-folder-subitems">`;
            children.forEach(child => {
                const isChildActive = activeFlowGroupView === child.id;
                const childCount = workflows.filter(wf => {
                    const assigned = String(flowGroups.assignments[String(wf.id)] || '');
                    return assigned === child.id;
                }).length;
                const childBadge = child.badge || (childCount > 0 ? String(childCount) : '');

                html += `
                    <div class="grok-subfolder-item ${isChildActive ? 'active' : ''}"
                         data-group-id="${escapeHtml(child.id)}"
                         onclick="event.stopPropagation(); selectFlowGroup('${escapeHtml(child.id)}')"
                         ondragover="handleFolderDragOver(event, this)" 
                         ondragleave="handleFolderDragLeave(event, this)" 
                         ondrop="dropWorkflowOnFolder(event, '${escapeHtml(child.id)}', this)">
                        <span class="grok-folder-icon">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="${isChildActive ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"></path>
                            </svg>
                        </span>
                        <span class="grok-folder-name">${escapeHtml(child.name)}</span>
                        ${childBadge ? `<span class="grok-folder-badge">${escapeHtml(childBadge)}</span>` : ''}
                    </div>
                `;
            });
            html += `</div>`;
        }
    });

    treeEl.innerHTML = html;
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

    populateTokenWorkflowPresets();

    const dexWorkflow = document.getElementById('dex-validated-workflow');
    if (dexWorkflow) {
        const selected = settings.dex_validated_workflow_id || 'active_all';
        dexWorkflow.innerHTML = '<option value="active_all">All active workflows</option>';
        workflows.forEach(wf => {
            if (wf.is_active) dexWorkflow.innerHTML += `<option value="${wf.id}">${escapeHtml(wf.name || 'Unnamed Flow')}</option>`;
        });
        dexWorkflow.value = Array.from(dexWorkflow.options).some(o => o.value === String(selected)) ? String(selected) : 'active_all';
    }

    const grid = document.getElementById('workflows-grid');
    grid.innerHTML = '';
    grid.style.display = 'block';

    renderGrokFoldersTree();
    const countEl = document.getElementById('akoue-workflows-count');
    if (countEl && workflows) {
        countEl.textContent = String(workflows.length);
    }

    const validGroupIds = new Set(flowGroups.groups.map(group => group.id));
    const allSections = [
        ...flowGroups.groups,
        { id: 'ungrouped', name: 'Ungrouped' }
    ];
    if (activeFlowGroupView !== 'all' && !allSections.some(group => group.id === activeFlowGroupView)) activeFlowGroupView = 'all';
    const selectedGroup = activeFlowGroupView;
    const statusFilter = document.getElementById('workflow-status-filter')?.value || 'all';
    const search = String(document.getElementById('workflow-search')?.value || '').trim().toLowerCase();
    
    const visibleWorkflows = workflows.filter(wf => {
        const assigned = String(flowGroups.assignments[String(wf.id)] || '');
        const resolvedGroup = validGroupIds.has(assigned) ? assigned : 'ungrouped';
        if (selectedGroup !== 'all' && resolvedGroup !== selectedGroup) {
            const childGroups = flowGroups.groups.filter(g => g.parent_id === selectedGroup).map(g => g.id);
            if (!childGroups.includes(resolvedGroup)) return false;
        }
        if (statusFilter === 'active' && !wf.is_active) return false;
        if (statusFilter === 'paused' && wf.is_active) return false;
        if (search) {
            const haystack = [wf.name, wf.target_channel, wf.target_channel_id, ...(wf.rules || []).flatMap(rule => [rule.rule_type, rule.search_text, rule.replace_text])].join(' ').toLowerCase();
            if (!haystack.includes(search)) return false;
        }
        return true;
    });

    const selectedMeta = allSections.find(group => group.id === selectedGroup) || { id: selectedGroup, name: 'Workflows' };
    const isCustomGroup = selectedGroup !== 'all' && selectedGroup !== 'ungrouped';
    const activeTheme = isCustomGroup ? getGroupTheme(selectedMeta?.color) : null;

    grid.innerHTML = `
        <div class="flow-list-heading" style="margin-bottom: 1.5rem;">
            <div>
                <div style="display:flex;align-items:center;gap:10px;">
                    ${activeTheme ? `<span style="width:12px;height:12px;border-radius:50%;background:${activeTheme.hex};box-shadow:0 0 10px ${activeTheme.hex};flex-shrink:0;"></span>` : ''}
                    <h2>${escapeHtml(selectedMeta?.name || 'All flows')}</h2>
                    ${isCustomGroup ? `<button type="button" class="btn-icon" style="padding:2px 6px;margin-left:4px;" onclick="openFlowGroupEditor('${escapeHtml(selectedGroup)}')" title="Edit group"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>` : ''}
                </div>
                <p style="margin-top:4px;">${visibleWorkflows.length} shown · ${visibleWorkflows.filter(wf => wf.is_active).length} active</p>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <button type="button" class="flow-group-action" onclick="openModalForGroup('${selectedGroup === 'all' ? 'ungrouped' : escapeHtml(selectedGroup)}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>New flow</button>
                ${isCustomGroup ? `
                    <button type="button" class="flow-group-action" onclick="openAssignFlowsModal('${escapeHtml(selectedGroup)}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>Assign flows</button>
                    <button type="button" class="flow-group-action" onclick="openFlowGroupEditor('${escapeHtml(selectedGroup)}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>Group settings</button>
                    <button type="button" class="flow-group-action" style="color:#f87171;" onclick="promptDeleteFlowGroup('${escapeHtml(selectedGroup)}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>Delete</button>
                ` : ''}
            </div>
        </div>
        <div class="workflows-grid flow-group-grid" id="active-flow-grid"></div>
    `;

    const activeGrid = document.getElementById('active-flow-grid');
    if (!visibleWorkflows.length) {
        activeGrid.innerHTML = `<div class="workflow-empty-state"><strong>${search || statusFilter !== 'all' ? 'No matching flows' : 'This folder is empty'}</strong><div style="margin-top:7px;">${search || statusFilter !== 'all' ? 'Try clearing the search or status filter.' : 'Create a flow or drag one into this folder from the sidebar.'}</div>${search || statusFilter !== 'all' ? '<button type="button" class="btn-primary" style="margin:18px auto 0;" onclick="clearWorkflowFilters()">Clear filters</button>' : ''}</div>`;
    }
    
    visibleWorkflows.forEach(wf => {
        const card = document.createElement('div');
        const isExpanded = expandedWorkflowCards.has(String(wf.id));
        card.className = `workflow-card workflow-card--compact${isExpanded ? ' workflow-card--expanded' : ''}`;
        card.draggable = true;
        card.dataset.workflowId = String(wf.id);
        card.ondragstart = event => startWorkflowDrag(event, wf.id, card);
        card.ondragend = () => card.classList.remove('workflow-dragging');
        card.onclick = event => toggleWorkflowCard(event, wf.id);
        card.innerHTML = `
            <div class="workflow-header">
                <div style="display:flex;align-items:center;gap:10px;min-width:0;">
                    <span class="workflow-card-chevron"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transform:${isExpanded ? 'rotate(90deg)' : 'rotate(0deg)'};transition:transform 0.15s ease;"><polyline points="9 18 15 12 9 6"/></svg></span>
                    <div class="workflow-title">${wf.name || 'Unnamed Flow'}</div>
                </div>
                <div style="display:flex;align-items:center;gap:10px;">
                    <span class="workflow-compact-rule-count">${(wf.rules || []).length} rule${(wf.rules || []).length === 1 ? '' : 's'}</span>
                    <div class="status-badge ${wf.is_active ? 'status-active' : 'status-paused'}">
                        ${wf.is_active ? 'Active' : 'Paused'}
                    </div>
                </div>
            </div>
            <div class="workflow-compact-target">
                <span style="display:inline-flex;align-items:center;gap:4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>Target Group:</span>
                <strong style="color: #f3f4f6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;" title="${wf.target_channel || wf.target_channel_id || 'Fallback Group'}">${wf.target_channel || (wf.target_channel_id ? `Chat ${wf.target_channel_id}` : 'Fallback Group')}</strong>
            </div>
            ${isExpanded ? `<div class="workflow-card-details"><div class="workflow-rules-preview" style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1.5rem; min-height: 40px; align-content: flex-start;">
                ${(wf.rules && wf.rules.length > 0) ? wf.rules.map(r => {
                    let label = '';
                    let iconSvg = '';
                    let color = 'var(--accent-color)';
                    if (r.rule_type === 'token_age') {
                        const type = r.search_text === 'migration' ? 'Migr' : 'Creat';
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
                        label = `Age (${type}): ${r.time_min || 0}-${r.time_max || '∞'}m`;
                        color = '#3b82f6';
                    } else if (r.rule_type === 'market_cap') {
                        let minMC = r.time_min ? (Number(r.time_min) >= 1000000 ? (Number(r.time_min)/1000000).toFixed(1) + 'M' : Number(r.time_min) >= 1000 ? (Number(r.time_min)/1000).toFixed(1) + 'K' : r.time_min) : '0';
                        let maxMC = r.time_max ? (Number(r.time_max) >= 1000000 ? (Number(r.time_max)/1000000).toFixed(1) + 'M' : Number(r.time_max) >= 1000 ? (Number(r.time_max)/1000).toFixed(1) + 'K' : r.time_max) : '∞';
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';
                        label = `MC: $${minMC}-$${maxMC}`;
                        color = '#10b981';
                    } else if (r.rule_type === 'performance') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>';
                        label = `Perf (${r.search_text || '5m'}): ${r.time_min || '-∞'}% to ${r.time_max || '∞'}%`;
                        color = '#f59e0b';
                    } else if (r.rule_type === 'liquidity') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/></svg>';
                        label = `Liquidity: $${r.time_min || 0}-$${r.time_max || '∞'}`;
                        color = '#14b8a6';
                    } else if (r.rule_type === 'volume') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>';
                        label = `Volume (${r.search_text || '24h'}): $${r.time_min || 0}-$${r.time_max || '∞'}`;
                        color = '#0ea5e9';
                    } else if (r.rule_type === 'dex_payment') {
                        let minP = (r.time_min !== undefined && r.time_min !== null && r.time_min !== '') ? r.time_min : '0';
                        let maxP = (r.time_max !== undefined && r.time_max !== null && r.time_max !== '') ? r.time_max : '∞';
                        let labelText = r.search_text ? ` (${r.search_text})` : '';
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>';
                        label = `DEX Pay: $${minP}-$${maxP}${labelText}`;
                        color = '#10b981';
                    } else if (r.rule_type === 'category') {
                        const labels = { boost: 'Boost', cto: 'CTO', dex_profile: 'DEX Profile', dex_ad: 'DEX Ad' };
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
                        label = `Category: ${labels[r.search_text] || r.search_text || 'Boost'}`;
                        color = '#8b5cf6';
                    } else if (r.rule_type === 'migrated') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/></svg>';
                        label = `Migrated: ${r.search_text === 'no' ? 'No' : 'Yes'}`;
                        color = '#06b6d4';
                    } else if (r.rule_type === 'show_wallet') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7h-7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2Z"/><path d="M4 17V5a2 2 0 0 1 2-2h12"/></svg>';
                        label = `Wallet: ${r.search_text === 'no' ? 'No' : 'Yes'}`;
                        color = '#0ea5e9';
                    } else if (r.rule_type === 'exclude_platform') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>';
                        label = `Exclude: ${r.search_text}`;
                        color = '#ef4444';
                    } else if (r.rule_type === 'token_name') {
                        const mode = (r.replace_text || 'include').toLowerCase() === 'exclude' ? 'Excl' : 'Incl';
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
                        label = `Name (${mode}): ${tokenNameFilterSummary(r)}`;
                        color = '#ec4899';
                    } else if (r.rule_type === 'filter') {
                        const mode = (r.replace_text || 'include').toLowerCase() === 'exclude' ? 'Drop' : 'Pass';
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
                        label = `Words (${mode}): "${r.search_text || ''}"`;
                        color = '#f43f5e';
                    } else if (r.rule_type === 'replace') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
                        label = `Replace: "${r.search_text}" -> "${r.replace_text}"`;
                        color = '#0284c7';
                    } else if (r.rule_type === 'append') {
                        iconSvg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
                        label = `Append: "${r.replace_text}"`;
                        color = '#06b6d4';
                    } else {
                        label = r.rule_type.toUpperCase();
                    }
                    return `<span style="background: rgba(255,255,255,0.03); border: 1px solid ${color}; color: #f3f4f6; font-size: 0.78rem; padding: 4px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 5px; font-weight: 500;">${iconSvg}${label}</span>`;
                }).join('') : '<span style="color: var(--text-muted); font-size: 0.85rem; font-style: italic;">No active rules configured</span>'}
            </div>
            <div class="workflow-footer">
                <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                    <div class="rules-count">${(wf.rules || []).length} Rules</div>
                    ${(() => {
                        const curGroupId = String(flowGroups.assignments[String(wf.id)] || '');
                        const curGroup = flowGroups.groups.find(g => g.id === curGroupId);
                        const curTheme = curGroup ? getGroupTheme(curGroup.color) : null;
                        const style = curTheme ? `border-color:${curTheme.border};background:${curTheme.bg};color:${curTheme.text};font-weight:600;` : '';
                        return `<select class="workflow-quick-move" style="${style}" title="Move to group" onchange="moveWorkflowToGroup(${wf.id}, this.value)">
                            <option value="" ${!validGroupIds.has(curGroupId) ? 'selected' : ''}>Ungrouped</option>
                            ${flowGroups.groups.map(group => `<option value="${escapeHtml(group.id)}" ${curGroupId === group.id ? 'selected' : ''}>${escapeHtml(group.name)}</option>`).join('')}
                        </select>`;
                    })()}
                </div>
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
            </div>` : '<div class="workflow-card-open-hint">Click to view filters and controls</div>'}
        `;
        activeGrid.appendChild(card);
    });

}

function toggleWorkflowCard(event, workflowId) {
    if (event.target.closest('button, select, input, a')) return;
    const key = String(workflowId);
    if (expandedWorkflowCards.has(key)) expandedWorkflowCards.delete(key);
    else expandedWorkflowCards.add(key);
    renderWorkflows();
}

function toggleFlowGroupSection(button) {
    const section = button.closest('.flow-group-section');
    const groupGrid = section?.querySelector('.flow-group-grid');
    const chevron = button.querySelector('.flow-group-chevron');
    if (!groupGrid) return;
    groupGrid.classList.toggle('hidden');
    const groupId = String(section.dataset.groupId || '');
    if (groupGrid.classList.contains('hidden')) collapsedFlowGroups.add(groupId);
    else collapsedFlowGroups.delete(groupId);
    if (chevron) {
        const isHidden = groupGrid.classList.contains('hidden');
        chevron.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transform:${isHidden ? 'rotate(0deg)' : 'rotate(90deg)'};transition:transform 0.15s ease;"><polyline points="9 18 15 12 9 6"/></svg>`;
    }
    try { localStorage.setItem('teleflow_collapsed_flow_groups', JSON.stringify([...collapsedFlowGroups])); } catch (_) {}
}

function setAllFlowGroupsCollapsed(collapsed) {
    const ids = [...flowGroups.groups.map(group => group.id), 'ungrouped'];
    collapsedFlowGroups = collapsed ? new Set(ids) : new Set();
    try { localStorage.setItem('teleflow_collapsed_flow_groups', JSON.stringify([...collapsedFlowGroups])); } catch (_) {}
    renderWorkflows();
}

function clearWorkflowFilters() {
    const search = document.getElementById('workflow-search');
    const status = document.getElementById('workflow-status-filter');
    if (search) search.value = '';
    if (status) status.value = 'all';
    renderWorkflows();
}

function selectFlowGroup(groupId) {
    activeFlowGroupView = String(groupId || 'all');
    try { localStorage.setItem('teleflow_active_flow_group', activeFlowGroupView); } catch (_) {}
    renderWorkflows();
}

function openModalForGroup(groupId) {
    openModal();
    renderFlowGroupOptions(groupId === 'ungrouped' ? '' : groupId);
}

function openFlowGroupEditor(groupId) {
    openFlowGroupsModal();
    setTimeout(() => document.getElementById(`flow-group-name-${groupId}`)?.focus(), 0);
}

function startWorkflowDrag(event, workflowId, card) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(workflowId));
    card.classList.add('workflow-dragging');
}

function handleFlowGroupDragOver(event, section) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    section.classList.add('drag-over');
}

function handleFlowGroupDragLeave(event, section) {
    if (!section.contains(event.relatedTarget)) section.classList.remove('drag-over');
}

async function dropWorkflowIntoGroup(event, groupId, section) {
    event.preventDefault();
    section.classList.remove('drag-over');
    const workflowId = event.dataTransfer.getData('text/plain');
    if (workflowId) await moveWorkflowToGroup(workflowId, groupId === 'ungrouped' ? '' : groupId);
}

function handleFolderDragOver(event, item) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    item.classList.add('drag-over');
}

function handleFolderDragLeave(event, item) {
    if (!item.contains(event.relatedTarget)) item.classList.remove('drag-over');
}

async function dropWorkflowOnFolder(event, groupId, item) {
    event.preventDefault();
    event.stopPropagation();
    item.classList.remove('drag-over');
    const workflowId = event.dataTransfer.getData('text/plain');
    if (!workflowId || groupId === 'all') return;
    await moveWorkflowToGroup(workflowId, groupId === 'ungrouped' ? '' : groupId);
}

async function moveWorkflowToGroup(workflowId, groupId) {
    const key = String(workflowId);
    const previous = flowGroups.assignments[key];
    const wf = workflows.find(w => String(w.id) === key);
    const targetGroup = flowGroups.groups.find(g => g.id === String(groupId));
    const targetName = targetGroup ? targetGroup.name : 'Ungrouped';
    
    if (groupId) flowGroups.assignments[key] = String(groupId);
    else delete flowGroups.assignments[key];
    
    renderWorkflows();
    if (!await saveFlowGroups(false)) {
        if (previous) flowGroups.assignments[key] = previous;
        else delete flowGroups.assignments[key];
        renderWorkflows();
        showAppToast('Failed to update workflow group', 'error');
    } else {
        showAppToast(`"${wf ? wf.name : 'Workflow'}" moved to ${targetName}`, 'success');
    }
}

function renderFlowGroupOptions(selectedId = '') {
    const select = document.getElementById('flow-group-id');
    if (!select) return;
    select.innerHTML = '<option value="">Ungrouped</option>' + flowGroups.groups.map(group =>
        `<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)}</option>`
    ).join('');
    select.value = flowGroups.groups.some(group => group.id === String(selectedId)) ? String(selectedId) : '';
}

async function saveFlowGroups(showError = true) {
    try {
        const serialized = JSON.stringify(flowGroups);
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow_groups: serialized })
        });
        const result = await readJsonResponse(response, 'Workflow groups');
        if (!response.ok || !result.success) throw new Error(result.error || 'Unable to save groups');
        settings.workflow_groups = serialized;
        return true;
    } catch (error) {
        if (showError) showAppToast('Failed to save workflow groups: ' + error.message, 'error');
        return false;
    }
}

function initColorPicker(containerId, activeColor, onSelect) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = FLOW_GROUP_COLORS.map(c => `
        <button type="button" class="color-swatch-btn ${c.key === activeColor ? 'selected' : ''}" data-color="${c.key}" style="background:${c.hex}; color:${c.hex};" title="${c.name}"></button>
    `).join('');
    container.querySelectorAll('.color-swatch-btn').forEach(btn => {
        btn.onclick = () => {
            const colorKey = btn.dataset.color;
            container.querySelectorAll('.color-swatch-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            onSelect(colorKey);
        };
    });
}

function openFlowGroupsModal() {
    initColorPicker('quick-group-color-picker', selectedQuickGroupColor, color => {
        selectedQuickGroupColor = color;
    });
    renderFlowGroupsManager();
    document.getElementById('flow-groups-modal')?.classList.remove('hidden');
}

function closeFlowGroupsModal() {
    document.getElementById('flow-groups-modal')?.classList.add('hidden');
}

function openCreateFlowGroupModal() {
    const input = document.getElementById('new-flow-group-name');
    if (input) input.value = '';
    selectedModalGroupColor = 'cyan';
    initColorPicker('create-group-color-picker', selectedModalGroupColor, color => {
        selectedModalGroupColor = color;
    });
    document.getElementById('create-flow-group-modal')?.classList.remove('hidden');
    setTimeout(() => input?.focus(), 50);
}

function closeCreateFlowGroupModal() {
    document.getElementById('create-flow-group-modal')?.classList.add('hidden');
}

function filterFlowGroupsManager(query) {
    renderFlowGroupsManager(String(query || '').trim().toLowerCase());
}

function renderFlowGroupsManager(filterQuery = '') {
    const container = document.getElementById('flow-groups-list');
    const badge = document.getElementById('flow-groups-total-badge');
    if (badge) badge.innerText = `${flowGroups.groups.length} Group${flowGroups.groups.length === 1 ? '' : 's'}`;
    if (!container) return;

    if (!flowGroups.groups.length) {
        container.innerHTML = `
            <div style="text-align:center;padding:2.5rem 1rem;color:var(--text-muted);border:1px dashed var(--border-color);border-radius:12px;">
                <div style="margin-bottom:8px;display:flex;justify-content:center;">
                    <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                </div>
                <strong style="color:#f3f4f6;display:block;margin-bottom:6px;">No Groups Created Yet</strong>
                <p style="font-size:.84rem;max-width:320px;margin:0 auto 14px;">Use the quick creator above to make your first workflow folder.</p>
            </div>`;
        return;
    }

    const filtered = filterQuery ? flowGroups.groups.filter(g => g.name.toLowerCase().includes(filterQuery)) : flowGroups.groups;

    if (!filtered.length) {
        container.innerHTML = `<p style="color:var(--text-muted);font-size:.86rem;padding:2rem 0;text-align:center;">No groups match "<strong>${escapeHtml(filterQuery)}</strong>".</p>`;
        return;
    }

    container.innerHTML = filtered.map(group => {
        const theme = getGroupTheme(group.color);
        const groupFlows = workflows.filter(wf => String(flowGroups.assignments[String(wf.id)] || '') === group.id);
        const activeCount = groupFlows.filter(wf => wf.is_active).length;
        const isDrawerOpen = expandedGroupDrawers.has(group.id);
        const otherFlows = workflows.filter(wf => String(flowGroups.assignments[String(wf.id)] || '') !== group.id);

        return `
            <div class="flow-group-card" id="flow-group-card-${escapeHtml(group.id)}" style="border-left: 4px solid ${theme.hex};">
                <div class="flow-group-card-header">
                    <div class="flow-group-card-left">
                        <div class="flow-group-reorder-btns">
                            <button type="button" class="flow-group-reorder-btn" onclick="moveFlowGroup('${escapeHtml(group.id)}', -1)" title="Move group up"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg></button>
                            <button type="button" class="flow-group-reorder-btn" onclick="moveFlowGroup('${escapeHtml(group.id)}', 1)" title="Move group down"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
                        </div>
                        <div class="color-dropdown-wrapper" style="position:relative;">
                            <button type="button" class="color-swatch-btn selected" onclick="cycleFlowGroupColor('${escapeHtml(group.id)}')" style="background:${theme.hex}; color:${theme.hex};" title="Click to cycle theme color"></button>
                        </div>
                        <input class="flow-group-title-input" id="flow-group-name-${escapeHtml(group.id)}" value="${escapeHtml(group.name)}" onchange="renameFlowGroup('${escapeHtml(group.id)}')" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}" aria-label="Group name" title="Click to rename" />
                    </div>

                    <div class="flow-group-card-actions" id="group-actions-${escapeHtml(group.id)}">
                        <span class="flow-group-badge-pill" style="background:${theme.bg}; color:${theme.text}; border:1px solid ${theme.border};">
                            ${groupFlows.length} flow${groupFlows.length === 1 ? '' : 's'} · ${activeCount} active
                        </span>
                        <button type="button" class="flow-group-btn-expand" onclick="toggleGroupDrawer('${escapeHtml(group.id)}')" title="View and manage member workflows">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transform:${isDrawerOpen ? 'rotate(180deg)' : 'rotate(0deg)'};transition:transform 0.15s ease;"><polyline points="6 9 12 15 18 9"/></svg>
                            <span>${isDrawerOpen ? 'Close' : 'Manage Flows'}</span>
                            <span style="opacity:0.6;font-size:0.72rem;">(${groupFlows.length})</span>
                        </button>
                        <button type="button" class="flow-group-btn-delete" onclick="showInlineGroupDelete('${escapeHtml(group.id)}')" title="Delete group at will">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                            <span>Delete</span>
                        </button>
                    </div>
                </div>

                <!-- Inline delete confirmation banner -->
                <div class="flow-group-delete-banner hidden" id="group-delete-banner-${escapeHtml(group.id)}">
                    <p style="display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>Delete <strong>${escapeHtml(group.name)}</strong>? Its ${groupFlows.length} workflow(s) will move to Ungrouped.</p>
                    <div style="display:flex;gap:8px;">
                        <button type="button" class="btn-icon" style="font-size:0.76rem;padding:3px 8px;" onclick="hideInlineGroupDelete('${escapeHtml(group.id)}')">Cancel</button>
                        <button type="button" class="btn-primary" style="background:linear-gradient(135deg,#dc2626,#ef4444);font-size:0.76rem;padding:3px 9px;" onclick="deleteFlowGroup('${escapeHtml(group.id)}')">Confirm Delete</button>
                    </div>
                </div>

                <!-- Expandable workflows drawer -->
                ${isDrawerOpen ? `
                    <div class="flow-group-drawer">
                        <div class="flow-group-drawer-heading">
                            <span>Workflows in this group (${groupFlows.length})</span>
                            <button type="button" class="flow-folder-create-button" onclick="openAssignFlowsModal('${escapeHtml(group.id)}')" style="font-size:0.72rem;">Bulk Select Flows</button>
                        </div>
                        
                        <div class="flow-group-flows-list">
                            ${groupFlows.length ? groupFlows.map(wf => `
                                <div class="flow-group-flow-item">
                                    <div class="flow-group-flow-info">
                                        <span class="flow-status-dot ${wf.is_active ? 'active' : 'paused'}" title="${wf.is_active ? 'Active' : 'Paused'}"></span>
                                        <strong class="flow-group-flow-title" title="${escapeHtml(wf.name || 'Unnamed')}">${escapeHtml(wf.name || 'Unnamed')}</strong>
                                        <span class="flow-group-flow-target">${escapeHtml(wf.target_channel || (wf.target_channel_id ? 'Chat ' + wf.target_channel_id : 'Fallback'))}</span>
                                    </div>
                                    <div class="flow-group-flow-actions">
                                        <select class="flow-group-flow-move-select" onchange="moveWorkflowToGroup(${wf.id}, this.value)" title="Move to another group">
                                            <option value="${escapeHtml(group.id)}" selected>In this group</option>
                                            <option value="">Move to Ungrouped</option>
                                            ${flowGroups.groups.filter(g => g.id !== group.id).map(g => `<option value="${escapeHtml(g.id)}">Move to ${escapeHtml(g.name)}</option>`).join('')}
                                        </select>
                                        <button type="button" class="flow-group-flow-btn-remove" onclick="removeWorkflowFromGroup(${wf.id}, '${escapeHtml(group.name)}')" title="Remove from this group"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
                                    </div>
                                </div>
                            `).join('') : '<div style="color:var(--text-muted);font-size:0.8rem;text-align:center;padding:12px 0;">No workflows in this group yet. Add one below!</div>'}
                        </div>

                        ${otherFlows.length ? `
                            <div class="flow-group-add-bar">
                                <select id="add-to-group-select-${escapeHtml(group.id)}" class="form-input" style="flex:1;font-size:0.78rem;padding:5px 8px;">
                                    <option value="">Select workflow to add...</option>
                                    ${otherFlows.map(wf => {
                                        const curId = flowGroups.assignments[String(wf.id)];
                                        const curGrp = flowGroups.groups.find(g => g.id === curId);
                                        const curLabel = curGrp ? ` (${curGrp.name})` : ' (Ungrouped)';
                                        return `<option value="${wf.id}">${escapeHtml(wf.name || 'Unnamed')}${curLabel}</option>`;
                                    }).join('')}
                                </select>
                                <button type="button" class="btn-primary" onclick="quickAddWorkflowToGroup('${escapeHtml(group.id)}')" style="font-size:0.76rem;padding:5px 10px;white-space:nowrap;">+ Add</button>
                            </div>
                        ` : ''}
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

function toggleGroupDrawer(groupId) {
    if (expandedGroupDrawers.has(groupId)) expandedGroupDrawers.delete(groupId);
    else expandedGroupDrawers.add(groupId);
    renderFlowGroupsManager();
}

function showInlineGroupDelete(groupId) {
    document.getElementById(`group-delete-banner-${groupId}`)?.classList.remove('hidden');
}

function hideInlineGroupDelete(groupId) {
    document.getElementById(`group-delete-banner-${groupId}`)?.classList.add('hidden');
}

async function quickAddWorkflowToGroup(groupId) {
    const select = document.getElementById(`add-to-group-select-${groupId}`);
    const workflowId = select?.value;
    if (!workflowId) return;
    await moveWorkflowToGroup(workflowId, groupId);
    renderFlowGroupsManager();
}

async function removeWorkflowFromGroup(workflowId, groupName) {
    await moveWorkflowToGroup(workflowId, '');
    renderFlowGroupsManager();
}

async function cycleFlowGroupColor(groupId) {
    const group = flowGroups.groups.find(g => g.id === groupId);
    if (!group) return;
    const currentIndex = FLOW_GROUP_COLORS.findIndex(c => c.key === (group.color || 'indigo'));
    const nextTheme = FLOW_GROUP_COLORS[(currentIndex + 1) % FLOW_GROUP_COLORS.length];
    group.color = nextTheme.key;
    renderFlowGroupsManager();
    renderWorkflows();
    if (await saveFlowGroups(false)) {
        showAppToast(`Group color updated to ${nextTheme.name}`, 'info');
    }
}

async function quickCreateGroup() {
    const input = document.getElementById('quick-group-name-input');
    const name = String(input?.value || '').trim();
    if (!name) {
        showAppToast('Please enter a group name', 'error');
        return;
    }
    if (flowGroups.groups.some(g => g.name.toLowerCase() === name.toLowerCase())) {
        showAppToast('A group with that name already exists', 'error');
        return;
    }
    const id = `group_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    flowGroups.groups.push({ id, name, color: selectedQuickGroupColor });
    if (await saveFlowGroups()) {
        input.value = '';
        renderFlowGroupsManager();
        renderWorkflows();
        showAppToast(`Group "${name}" created!`, 'success');
    } else {
        flowGroups.groups = flowGroups.groups.filter(g => g.id !== id);
    }
}

function handleQuickGroupKey(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    quickCreateGroup();
}

async function createFlowGroup() {
    const input = document.getElementById('new-flow-group-name');
    const name = String(input?.value || '').trim();
    if (!name) {
        showAppToast('Please enter a group name', 'error');
        return;
    }
    if (flowGroups.groups.some(g => g.name.toLowerCase() === name.toLowerCase())) {
        showAppToast('A group with that name already exists', 'error');
        return;
    }
    const id = `group_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    flowGroups.groups.push({ id, name, color: selectedModalGroupColor });
    if (await saveFlowGroups()) {
        input.value = '';
        closeCreateFlowGroupModal();
        renderFlowGroupsManager();
        renderWorkflows();
        showAppToast(`Group "${name}" created!`, 'success');
    } else {
        flowGroups.groups = flowGroups.groups.filter(g => g.id !== id);
    }
}

async function renameFlowGroup(groupId) {
    const group = flowGroups.groups.find(item => item.id === groupId);
    const input = document.getElementById(`flow-group-name-${groupId}`);
    const name = String(input?.value || '').trim();
    if (!group || !name || name === group.name) return;
    if (flowGroups.groups.some(item => item.id !== groupId && item.name.toLowerCase() === name.toLowerCase())) {
        showAppToast('A group with that name already exists', 'error');
        input.value = group.name;
        return;
    }
    const previous = group.name;
    group.name = name;
    if (!await saveFlowGroups(false)) {
        group.name = previous;
        input.value = previous;
        showAppToast('Failed to rename group', 'error');
    } else {
        renderFlowGroupsManager();
        renderWorkflows();
        showAppToast(`Renamed group to "${name}"`, 'success');
    }
}

async function moveFlowGroup(groupId, direction) {
    const index = flowGroups.groups.findIndex(group => group.id === groupId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= flowGroups.groups.length) return;
    [flowGroups.groups[index], flowGroups.groups[target]] = [flowGroups.groups[target], flowGroups.groups[index]];
    renderFlowGroupsManager();
    renderWorkflows();
    if (!await saveFlowGroups(false)) {
        [flowGroups.groups[index], flowGroups.groups[target]] = [flowGroups.groups[target], flowGroups.groups[index]];
        renderFlowGroupsManager();
        renderWorkflows();
    }
}

function promptDeleteFlowGroup(groupId) {
    const group = flowGroups.groups.find(g => g.id === groupId);
    if (!group) return;
    pendingDeleteGroupId = groupId;
    const title = document.getElementById('delete-group-modal-title');
    const desc = document.getElementById('delete-group-modal-desc');
    const confirmBtn = document.getElementById('confirm-delete-group-btn');
    const count = Object.values(flowGroups.assignments).filter(val => String(val) === groupId).length;
    if (title) title.innerText = `Delete "${group.name}"?`;
    if (desc) desc.innerText = `All ${count} workflow(s) in this group will be safely preserved and moved to "Ungrouped". No rules or data will be deleted.`;
    if (confirmBtn) {
        confirmBtn.onclick = () => {
            deleteFlowGroup(groupId);
            closeDeleteFlowGroupModal();
        };
    }
    document.getElementById('delete-flow-group-modal')?.classList.remove('hidden');
}

function closeDeleteFlowGroupModal() {
    pendingDeleteGroupId = null;
    document.getElementById('delete-flow-group-modal')?.classList.add('hidden');
}

async function deleteFlowGroup(groupId) {
    const group = flowGroups.groups.find(item => item.id === groupId);
    if (!group) return;
    const previousGroups = flowGroups.groups.map(item => ({ ...item }));
    const previousAssignments = { ...flowGroups.assignments };
    flowGroups.groups = flowGroups.groups.filter(item => item.id !== groupId);
    Object.keys(flowGroups.assignments).forEach(workflowId => {
        if (String(flowGroups.assignments[workflowId]) === groupId) delete flowGroups.assignments[workflowId];
    });
    if (activeFlowGroupView === groupId) {
        activeFlowGroupView = 'all';
        try { localStorage.setItem('teleflow_active_flow_group', 'all'); } catch (_) {}
    }
    renderFlowGroupsManager();
    renderWorkflows();
    if (await saveFlowGroups()) {
        showAppToast(`Group "${group.name}" deleted. Flows moved to Ungrouped.`, 'info');
    } else {
        flowGroups.groups = previousGroups;
        flowGroups.assignments = previousAssignments;
        renderFlowGroupsManager();
        renderWorkflows();
    }
}

function handleFlowGroupNameKey(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    createFlowGroup();
}

/* Bulk Assign Flows to Group Modal */
function openAssignFlowsModal(groupId) {
    const group = flowGroups.groups.find(g => g.id === groupId);
    if (!group) return;
    activeAssignGroupId = groupId;
    const title = document.getElementById('assign-flows-title');
    if (title) title.innerText = `Assign Workflows to "${group.name}"`;
    const searchInput = document.getElementById('assign-flows-search');
    if (searchInput) searchInput.value = '';
    renderAssignFlowsList();
    document.getElementById('assign-flows-modal')?.classList.remove('hidden');
}

function closeAssignFlowsModal() {
    activeAssignGroupId = null;
    document.getElementById('assign-flows-modal')?.classList.add('hidden');
}

function filterAssignFlowsList(search) {
    renderAssignFlowsList(String(search || '').trim().toLowerCase());
}

function renderAssignFlowsList(search = '') {
    const container = document.getElementById('assign-flows-list');
    if (!container || !activeAssignGroupId) return;
    const filtered = workflows.filter(wf => {
        if (!search) return true;
        return (wf.name || '').toLowerCase().includes(search) || (wf.target_channel || '').toLowerCase().includes(search);
    });

    if (!filtered.length) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:0.84rem;text-align:center;padding:2rem 0;">No matching workflows found.</p>';
        updateAssignFlowsSummary();
        return;
    }

    container.innerHTML = filtered.map(wf => {
        const isAssigned = String(flowGroups.assignments[String(wf.id)] || '') === activeAssignGroupId;
        const curGroupId = String(flowGroups.assignments[String(wf.id)] || '');
        const otherGroup = curGroupId && curGroupId !== activeAssignGroupId ? flowGroups.groups.find(g => g.id === curGroupId) : null;
        return `
            <label class="assign-flow-item ${isAssigned ? 'checked' : ''}" onclick="toggleAssignFlowItem(event, this)">
                <input type="checkbox" class="assign-flow-checkbox" value="${wf.id}" ${isAssigned ? 'checked' : ''} onchange="this.closest('.assign-flow-item').classList.toggle('checked', this.checked); updateAssignFlowsSummary();" />
                <span class="flow-status-dot ${wf.is_active ? 'active' : 'paused'}"></span>
                <strong style="color:#f3f4f6;font-size:0.86rem;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(wf.name || 'Unnamed')}</strong>
                <span style="font-size:0.74rem;color:var(--text-muted);background:rgba(255,255,255,0.04);padding:2px 6px;border-radius:4px;">${escapeHtml(wf.target_channel || 'Fallback')}</span>
                ${otherGroup ? `<span style="font-size:0.7rem;color:#fcd34d;background:rgba(245,158,11,0.12);padding:2px 6px;border-radius:4px;">Currently: ${escapeHtml(otherGroup.name)}</span>` : ''}
            </label>
        `;
    }).join('');
    updateAssignFlowsSummary();
}

function toggleAssignFlowItem(event, labelEl) {
    if (event.target.tagName === 'INPUT') return;
    const checkbox = labelEl.querySelector('input[type="checkbox"]');
    if (!checkbox) return;
    checkbox.checked = !checkbox.checked;
    labelEl.classList.toggle('checked', checkbox.checked);
    updateAssignFlowsSummary();
}

function setAllAssignFlows(checkAll) {
    const checkboxes = document.querySelectorAll('#assign-flows-list .assign-flow-checkbox');
    checkboxes.forEach(cb => {
        cb.checked = checkAll;
        cb.closest('.assign-flow-item')?.classList.toggle('checked', checkAll);
    });
    updateAssignFlowsSummary();
}

function updateAssignFlowsSummary() {
    const summary = document.getElementById('assign-flows-summary');
    if (!summary) return;
    const total = document.querySelectorAll('#assign-flows-list .assign-flow-checkbox').length;
    const checked = document.querySelectorAll('#assign-flows-list .assign-flow-checkbox:checked').length;
    summary.innerText = `${checked} of ${total} workflows selected`;
}

async function saveAssignFlows() {
    if (!activeAssignGroupId) return;
    const group = flowGroups.groups.find(g => g.id === activeAssignGroupId);
    const checkboxes = document.querySelectorAll('#assign-flows-list .assign-flow-checkbox');
    checkboxes.forEach(cb => {
        const wfId = String(cb.value);
        if (cb.checked) {
            flowGroups.assignments[wfId] = activeAssignGroupId;
        } else if (flowGroups.assignments[wfId] === activeAssignGroupId) {
            delete flowGroups.assignments[wfId];
        }
    });

    renderWorkflows();
    renderFlowGroupsManager();
    closeAssignFlowsModal();
    if (await saveFlowGroups()) {
        showAppToast(`Workflows updated for "${group ? group.name : 'Group'}"`, 'success');
    }
}

function openModal() {
    document.getElementById('workflow-modal').classList.remove('hidden');
    document.getElementById('modal-title').innerText = 'New Workflow';
    document.getElementById('flow-id').value = '';
    document.getElementById('flow-name').value = '';
    renderFlowGroupOptions('');
    
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
    renderFlowGroupOptions(flowGroups.assignments[String(wf.id)] || '');
    
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
        div.className = `rule-row${rule.rule_type === 'token_name' ? ' token-name-rule-row' : ''}`;
        
        let badgeColor = 'var(--accent-color)';
        if (rule.rule_type === 'token_age') badgeColor = '#3b82f6';
        else if (rule.rule_type === 'market_cap') badgeColor = '#10b981';
        else if (rule.rule_type === 'dex_payment') badgeColor = '#06b6d4';
        else if (rule.rule_type === 'category') badgeColor = '#8b5cf6';
        else if (rule.rule_type === 'migrated') badgeColor = '#06b6d4';
        else if (rule.rule_type === 'performance') badgeColor = '#f59e0b';
        else if (rule.rule_type === 'liquidity') badgeColor = '#14b8a6';
        else if (rule.rule_type === 'volume') badgeColor = '#0ea5e9';
        else if (rule.rule_type === 'show_wallet') badgeColor = '#0ea5e9';
        else if (rule.rule_type === 'exclude_platform') badgeColor = '#ef4444';
        else if (rule.rule_type === 'token_name') badgeColor = '#ec4899';
        else if (rule.rule_type === 'filter') badgeColor = '#f43f5e';
        
        const ruleLabel = rule.rule_type === 'token_name' ? 'TOKEN NAME FILTER' : rule.rule_type.replaceAll('_', ' ').toUpperCase();
        let badgeHtml = `<div class="rule-row__badge" style="display: flex; align-items: center;">
            <span class="status-badge" style="background: ${badgeColor}; color: white; width: auto; min-width: 120px; padding: 6px 12px; text-align: center; justify-content: center; display: inline-flex;">${ruleLabel}</span>
        </div>`;
        
        let fieldsHtml = `<div class="rule-row__fields${rule.rule_type === 'token_name' ? ' token-name-rule-fields' : ''}" style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex: 1;">`;
        
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
        } else if (rule.rule_type === 'liquidity' || rule.rule_type === 'volume') {
            const timeframe = rule.search_text || '24h';
            fieldsHtml += rule.rule_type === 'volume' ? `
                <label style="font-size:0.85rem; color:var(--text-muted)">Timeframe:</label>
                <select class="form-input" style="width:85px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="5m" ${timeframe === '5m' ? 'selected' : ''}>5m</option>
                    <option value="1h" ${timeframe === '1h' ? 'selected' : ''}>1h</option>
                    <option value="6h" ${timeframe === '6h' ? 'selected' : ''}>6h</option>
                    <option value="24h" ${timeframe === '24h' ? 'selected' : ''}>24h</option>
                </select>` : '';
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Min ($):</label>
                <input type="number" class="form-input" style="width:95px" placeholder="0" value="${rule.time_min ?? ''}" oninput="updateRule(${index}, 'time_min', this.value)">
                <label style="font-size:0.85rem; color:var(--text-muted)">Max ($):</label>
                <input type="number" class="form-input" style="width:95px" placeholder="∞" value="${rule.time_max ?? ''}" oninput="updateRule(${index}, 'time_max', this.value)">
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
        } else if (rule.rule_type === 'category') {
            const category = rule.search_text || 'boost';
            fieldsHtml += `
                <label style="font-size:0.85rem;color:var(--text-muted)">Allow category:</label>
                <select class="form-input" style="width:180px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="boost" ${category === 'boost' ? 'selected' : ''}>Boost</option>
                    <option value="cto" ${category === 'cto' ? 'selected' : ''}>CTO</option>
                    <option value="dex_profile" ${category === 'dex_profile' ? 'selected' : ''}>DEX Profile</option>
                    <option value="dex_ad" ${category === 'dex_ad' ? 'selected' : ''}>DEX Ad</option>
                </select>
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
        } else if (rule.rule_type === 'show_wallet') {
            const isShow = (rule.search_text || 'yes').toLowerCase();
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Show Wallet Link:</label>
                <select class="form-input" style="width:90px" onchange="updateRule(${index}, 'search_text', this.value)">
                    <option value="yes" ${isShow === 'yes' ? 'selected' : ''}>Yes</option>
                    <option value="no" ${isShow === 'no' ? 'selected' : ''}>No</option>
                </select>
            `;
        } else if (rule.rule_type === 'exclude_platform') {
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Regex:</label>
                <input class="form-input" style="flex: 1; max-width: 250px;" placeholder="e.g. pump\\.fun|raydium" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)" title="Case-insensitive regular expression">
            `;
        } else if (rule.rule_type === 'token_name') {
            const mode = (rule.replace_text || 'include').toLowerCase();
            const counts = tokenNameFilterCounts(rule);
            fieldsHtml += `
                <div class="token-name-mode-group">
                    <label class="token-name-field-label">When a name matches</label>
                    <select class="form-input" style="width:100%;" onchange="updateRule(${index}, 'replace_text', this.value)">
                        <option value="include" ${mode === 'include' ? 'selected' : ''}>Allow this token</option>
                        <option value="exclude" ${mode === 'exclude' ? 'selected' : ''}>Block this token</option>
                    </select>
                </div>
                <div class="token-name-counts" aria-label="Configured name matches">
                    <span class="token-name-count"><strong>${counts.exact.length}</strong> Exact</span>
                    <span class="token-name-count"><strong>${counts.contains.length}</strong> Contains</span>
                </div>
                <button type="button" class="btn-primary token-name-edit-button" style="padding:0.5rem 0.9rem; white-space:nowrap; box-shadow:none;" onclick="openTokenNameFilterModal(${index})">Edit list</button>
            `;
        } else if (rule.rule_type === 'filter') {
            const mode = (rule.replace_text || 'include').toLowerCase();
            fieldsHtml += `
                <label style="font-size:0.85rem; color:var(--text-muted)">Mode:</label>
                <select class="form-input" style="width:185px" onchange="updateRule(${index}, 'replace_text', this.value)">
                    <option value="include" ${mode === 'include' ? 'selected' : ''}>Must Contain (Pass if any found)</option>
                    <option value="exclude" ${mode === 'exclude' ? 'selected' : ''}>Must Not Contain (Drop if found)</option>
                </select>
                <label style="font-size:0.85rem; color:var(--text-muted)">Regex:</label>
                <input class="form-input" style="flex: 1; min-width: 240px;" placeholder="e.g. sol|ai|trump|pepe|doge" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)" title="Case-insensitive regular expression. Use | to match alternatives.">
            `;
        } else {
            fieldsHtml += `<input class="form-input" style="flex: 1; max-width: 250px;" placeholder="Regex to find" value="${rule.search_text || ''}" oninput="updateRule(${index}, 'search_text', this.value)" title="Case-insensitive regular expression">`;
            if (rule.rule_type === 'replace') {
                fieldsHtml += `<input class="form-input" style="flex: 1; max-width: 250px;" placeholder="Replace with..." value="${rule.replace_text || ''}" oninput="updateRule(${index}, 'replace_text', this.value)">`;
            }
        }
        fieldsHtml += '</div>';
        
        let deleteHtml = `<div class="rule-row__remove" style="display: flex; align-items: center; justify-content: flex-end;">
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
    } else if (type === 'show_wallet') {
        newRule.search_text = 'yes';
    } else if (type === 'token_name') {
        newRule.search_text = encodeTokenNameFilter({ exact: [], contains: [] });
        newRule.replace_text = 'include';
    } else if (type === 'filter') {
        newRule.search_text = '';
        newRule.replace_text = 'include';
    } else if (type === 'volume') {
        newRule.search_text = '24h';
    } else if (type === 'category') {
        newRule.search_text = 'boost';
    }
    currentRules.push(newRule);
    renderRules();
    if (type === 'token_name') openTokenNameFilterModal(currentRules.length - 1);
}

function openTokenNameFilterModal(index) {
    const rule = currentRules[index];
    if (!rule || rule.rule_type !== 'token_name') return;
    tokenNameRuleIndex = index;
    const parsed = parseTokenNameFilter(rule.search_text);
    tokenNameDraft = parsed ? { exact: [...parsed.exact], contains: [...parsed.contains] } : { exact: [], contains: [] };
    document.getElementById('token-name-filter-error')?.classList.add('hidden');
    renderTokenNameEntries();
    document.getElementById('token-name-filter-modal')?.classList.remove('hidden');
    setTimeout(() => document.getElementById('token-name-exact-input')?.focus(), 0);
}

function closeTokenNameFilterModal() {
    document.getElementById('token-name-filter-modal')?.classList.add('hidden');
    tokenNameRuleIndex = null;
}

function renderTokenNameEntries() {
    ['exact', 'contains'].forEach(type => {
        const list = document.getElementById(`token-name-${type}-list`);
        if (!list) return;
        const entries = tokenNameDraft[type];
        list.innerHTML = entries.length ? entries.map((entry, index) => `
            <div class="token-name-entry">
                <span>${escapeHtml(entry)}</span>
                <button type="button" class="btn-icon" style="color:#f87171; padding:0 3px;" onclick="removeTokenNameEntry('${type}', ${index})" aria-label="Remove ${escapeHtml(entry)}"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>`).join('') : '<p style="font-size:0.78rem; color:var(--text-muted); margin-top:10px;">No entries added yet.</p>';
    });
}

function addTokenNameEntry(type) {
    const input = document.getElementById(`token-name-${type}-input`);
    const rawValue = String(input?.value || '');
    // Trimming outside the quotes keeps intentional spaces inside, e.g. "Mr ".
    const value = rawValue.trim();
    if (!value || !['exact', 'contains'].includes(type)) return;
    if (!tokenNameDraft[type].some(entry => entry.toLowerCase() === value.toLowerCase())) tokenNameDraft[type].push(value);
    input.value = '';
    document.getElementById('token-name-filter-error')?.classList.add('hidden');
    renderTokenNameEntries();
    input.focus();
}

function removeTokenNameEntry(type, index) {
    if (!['exact', 'contains'].includes(type)) return;
    tokenNameDraft[type].splice(index, 1);
    renderTokenNameEntries();
}

function handleTokenNameEntryKey(event, type) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addTokenNameEntry(type);
}

function saveTokenNameFilter() {
    if (tokenNameRuleIndex === null) return;
    if (!tokenNameDraft.exact.length && !tokenNameDraft.contains.length) {
        document.getElementById('token-name-filter-error')?.classList.remove('hidden');
        return;
    }
    currentRules[tokenNameRuleIndex].search_text = encodeTokenNameFilter(tokenNameDraft);
    closeTokenNameFilterModal();
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
    const selectedGroupId = document.getElementById('flow-group-id')?.value || '';
    
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
                if (selectedGroupId) flowGroups.assignments[String(result.id)] = selectedGroupId;
            } else {
                const idx = workflows.findIndex(w => w.id == id);
                workflows[idx] = { ...workflows[idx], ...workflow };
                if (selectedGroupId) flowGroups.assignments[String(id)] = selectedGroupId;
                else delete flowGroups.assignments[String(id)];
            }
            await saveFlowGroups(false);
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
            delete flowGroups.assignments[String(id)];
            await saveFlowGroups(false);
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
            const sourceGroup = flowGroups.assignments[String(id)];
            if (sourceGroup) flowGroups.assignments[String(result.id)] = sourceGroup;
            await saveFlowGroups(false);
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
    const text = document.getElementById('tester-input').value.trim();
    const container = document.getElementById('tester-result-container');
    const button = document.getElementById('tester-run-button');
    
    if (!workflowId) {
        container.innerHTML = '<div class="result-error">Please select a workflow to test.</div>';
        return;
    }
    if (!text) {
        container.innerHTML = '<div class="result-error">Please paste a sample message.</div>';
        return;
    }
    
    container.innerHTML = '<div style="color: var(--text-muted);">Testing flow...</div>';
    if (button) button.disabled = true;

    try {
        const res = await fetch('/api/tester', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow_id: workflowId, text })
        });
        const result = await readJsonResponse(res, 'The flow tester');
        if (!res.ok || result.success === false) {
            throw new Error(result.error || 'The test could not be completed.');
        }

        const workflowName = escapeHtml(result.workflow_name || 'Selected workflow');
        const ruleSummary = `${result.rule_count || 0} rule${result.rule_count === 1 ? '' : 's'} checked`;
        if (result.dropped) {
            container.innerHTML = `
                <div class="result-error">
                    <div class="result-header">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>
                        Does Not Pass — ${workflowName}
                    </div>
                    <div style="margin-top: 8px;">${escapeHtml(result.reason || 'Dropped by filters')}</div>
                    <div style="margin-top: 8px; font-size: 0.78rem; color: var(--text-muted);">${ruleSummary}</div>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="result-success">
                    <div class="result-header" style="color: #6ee7b7;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                        Passes — ${workflowName}
                    </div>
                    <div style="margin: 8px 0;">The message passed every filter in this workflow. Nothing was sent.</div>
                    <div style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 10px;">${ruleSummary}</div>
                    <div style="font-family: monospace; font-size: 0.85rem; white-space: pre-wrap; color: white; background: rgba(0,0,0,0.3); padding: 1rem; border-radius: 6px;">${escapeHtml(result.text || text)}</div>
                </div>
            `;
        }
    } catch (error) {
        container.innerHTML = `<div class="result-error"><strong>Test failed:</strong> ${escapeHtml(error.message)}</div>`;
    } finally {
        if (button) button.disabled = false;
    }
}

async function loadTesterWorkflows(preferredWorkflowId = '') {
    const select = document.getElementById('tester-workflow');
    const resultContainer = document.getElementById('tester-result-container');
    const runButton = document.getElementById('tester-run-button');
    if (!select) return;

    select.disabled = true;
    select.innerHTML = '<option value="">Loading active Supabase workflows...</option>';
    if (runButton) runButton.disabled = true;

    try {
        const response = await fetch('/api/tester/workflows', { cache: 'no-store' });
        const result = await readJsonResponse(response, 'The Supabase workflow endpoint');
        if (!response.ok || result.success === false) {
            throw new Error(result.error || 'Could not load Supabase workflows.');
        }

        const testerWorkflows = result.workflows || [];
        select.innerHTML = '<option value="">Select an active Supabase workflow...</option>' + testerWorkflows
            .map(wf => `<option value="${wf.id}">${escapeHtml(wf.name || 'Unnamed Flow')}</option>`)
            .join('');
        if (preferredWorkflowId && testerWorkflows.some(wf => String(wf.id) === String(preferredWorkflowId))) {
            select.value = String(preferredWorkflowId);
        }
        if (!testerWorkflows.length && resultContainer) {
            resultContainer.innerHTML = '<div class="result-error">No active workflows were found in Supabase.</div>';
        }
    } catch (error) {
        select.innerHTML = '<option value="">Supabase workflows unavailable</option>';
        if (resultContainer) {
            resultContainer.innerHTML = `<div class="result-error"><strong>Cannot load flows:</strong> ${escapeHtml(error.message)}</div>`;
        }
    } finally {
        select.disabled = false;
        if (runButton) runButton.disabled = false;
    }
}

function openTesterModal(workflowId = '') {
    const modal = document.getElementById('tester-modal');
    const result = document.getElementById('tester-result-container');
    if (result) result.innerHTML = '';
    if (modal) modal.classList.remove('hidden');
    loadTesterWorkflows(workflowId);
}

function closeTesterModal() {
    const modal = document.getElementById('tester-modal');
    if (modal) modal.classList.add('hidden');
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
        warningDiv.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:6px;"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><strong>Localhost Detected:</strong> Helius cannot send webhooks to a local URL. You must expose port 5000 using a tunnel (e.g., <code>ngrok http 5000</code>) and paste your public tunnel URL in Helius Developer Portal pointing to <code>/api/helius/webhook</code>.';
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
                typeBadge = `<span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(59, 130, 246, 0.3); font-weight: 500; display:inline-flex; align-items:center; gap:4px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg> User</span>`;
            } else if (c.type === 'Group') {
                typeBadge = `<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 500; display:inline-flex; align-items:center; gap:4px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> Group</span>`;
            } else {
                typeBadge = `<span style="background: rgba(6, 182, 212, 0.15); color: #67e8f9; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(6, 182, 212, 0.3); font-weight: 500; display:inline-flex; align-items:center; gap:4px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg> Channel</span>`;
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

function updateDexValidatedStatus() {
    const badge = document.getElementById('dex-validated-status');
    if (!badge) return;
    const active = String(settings.dex_validated_auto_scan || 'false').toLowerCase() === 'true';
    badge.textContent = active ? 'Active' : 'Inactive';
    badge.style.color = active ? '#10b981' : '#9ca3af';
    badge.style.background = active ? 'rgba(16,185,129,.12)' : 'rgba(255,255,255,.05)';
}

function updateDexValidatedCountdown() {
    const el = document.getElementById('dex-monitor-countdown');
    if (!el || !dexValidatedRuntime) return;
    if (!dexValidatedRuntime.active) {
        el.textContent = 'Paused';
        return;
    }
    if (dexValidatedRuntime.mode === 'websocket') {
        const connectedCount = Number(dexValidatedRuntime.ws_connected || 0);
        el.textContent = connectedCount > 0 ? `Live (${connectedCount} WS)` : 'Connecting…';
        return;
    }
    if (!dexValidatedRuntime.last_checked_at) {
        el.textContent = 'Starting…';
        return;
    }
    const nextCheck = dexValidatedRuntime.next_check_at
        ? new Date(dexValidatedRuntime.next_check_at).getTime()
        : new Date(dexValidatedRuntime.last_checked_at).getTime() + Number(dexValidatedRuntime.interval || 10) * 1000;
    const remaining = Math.max(0, Math.ceil((nextCheck - Date.now()) / 1000));
    el.textContent = `${remaining}s`;
}

async function refreshDexValidatedStatus() {
    try {
        const response = await fetch('/api/dex-validated/status', { cache: 'no-store' });
        const result = await readJsonResponse(response, 'DEX Validated status');
        if (!response.ok || !result.success) throw new Error(result.error || 'Status unavailable');
        dexValidatedRuntime = result.status || {};

        const setText = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        const checkedAt = dexValidatedRuntime.last_checked_at
            ? new Date(dexValidatedRuntime.last_checked_at).toLocaleTimeString()
            : 'Never';
        setText('dex-monitor-last-check', checkedAt);
        setText('dex-monitor-scan-time', dexValidatedRuntime.last_scan_seconds == null ? '—' : `${dexValidatedRuntime.last_scan_seconds}s`);
        setText('dex-monitor-latency', dexValidatedRuntime.last_detection_seconds == null ? '—' : `${dexValidatedRuntime.last_detection_seconds}s`);
        setText('dex-monitor-events', `${dexValidatedRuntime.last_new_events || 0} / ${dexValidatedRuntime.last_forwarded || 0}`);
        setText('dex-monitor-checks', String(dexValidatedRuntime.total_checks || 0));

        settings.dex_validated_auto_scan = dexValidatedRuntime.active ? 'true' : 'false';
        updateDexValidatedStatus();
        updateDexValidatedCountdown();

        const latestId = Number(dexValidatedRuntime.latest_record_id || 0);
        if (latestId > lastSeenDexValidatedRecordId) {
            lastSeenDexValidatedRecordId = latestId;
            const dvdTab = document.getElementById('tab-dex-validated-data');
            if (dvdTab && !dvdTab.classList.contains('hidden')) {
                loadDexValidatedTableData(currentDexValidatedDataPage, false);
            }
            const dvTab = document.getElementById('tab-dex-validated');
            if (dvTab && !dvTab.classList.contains('hidden')) {
                loadDexValidatedLiveResults();
            }
        }
    } catch (error) {
        const el = document.getElementById('dex-monitor-countdown');
        if (el) el.textContent = 'Offline';
    }
}

async function loadDexValidatedLiveResults() {
    try {
        const res = await fetch('/api/dex-validated/records?page=1&limit=40', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (!data.success || !Array.isArray(data.records)) return;
        const mapped = data.records.map(r => ({
            ca: r.ca,
            name: r.token_name || r.name || 'Unknown token',
            platform: r.platform || 'Solana (SOL)',
            market_cap: r.market_cap || 'Unknown',
            age: r.creation_age || r.age || 'Unknown',
            perf_5m: r.perf_5m,
            perf_1h: r.perf_1h,
            perf_6h: r.perf_6h,
            perf_24h: r.perf_24h,
            liquidity_usd: r.liquidity_usd,
            volume_5m: r.volume_5m,
            volume_1h: r.volume_1h,
            volume_6h: r.volume_6h,
            volume_24h: r.volume_24h,
            status: (r.status === 'forwarded' || r.status === 'detected') ? 'passed' : (r.status || 'dropped'),
            reason: r.reason || '',
            dex_url: r.dex_url || '',
            signal_type: r.category_display || r.signal_type || 'DEX',
            order_status: r.order_status || 'approved',
            payment_timestamp: r.payment_timestamp || 0,
            formatted_message: r.formatted_message || ''
        }));
        if (mapped.length > 0) {
            renderDexValidatedResults(mapped);
        }
    } catch (_) {}
}

async function saveDexValidatedSettings(showConfirmation = true) {
    const workflowId = document.getElementById('dex-validated-workflow')?.value || 'active_all';
    const target = document.getElementById('dex-validated-target')?.value.trim() || '';
    const interval = document.getElementById('dex-validated-interval')?.value || '10';
    const autoScan = !!document.getElementById('dex-validated-auto')?.checked;
    const testMode = !!document.getElementById('dex-validated-test')?.checked;
    const includeBoosts = !!document.getElementById('dex-validated-include-boosts')?.checked;
    const includeCto = !!document.getElementById('dex-validated-include-cto')?.checked;
    const includeDex = !!document.getElementById('dex-validated-include-dex')?.checked;
    if (!includeBoosts && !includeCto && !includeDex) {
        alert('Select at least one validated signal type.');
        return false;
    }
    const payload = {
        dex_validated_workflow_id: workflowId,
        dex_validated_target_channel: target,
        dex_validated_scan_interval: interval,
        dex_validated_auto_scan: autoScan ? 'true' : 'false',
        dex_validated_test_mode: testMode ? 'true' : 'false',
        dex_validated_include_boosts: includeBoosts ? 'true' : 'false',
        dex_validated_include_cto: includeCto ? 'true' : 'false',
        dex_validated_include_dex: includeDex ? 'true' : 'false'
    };
    try {
        const response = await fetch('/api/settings', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        });
        const result = await readJsonResponse(response, 'DEX Validated settings');
        if (!response.ok || !result.success) throw new Error(result.error || 'Unable to save settings');
        Object.assign(settings, payload);
        updateDexValidatedStatus();
        refreshDexValidatedStatus();
        if (showConfirmation) alert('DEX Validated settings saved.');
        return true;
    } catch (error) {
        alert('Failed to save DEX Validated settings: ' + error.message);
        return false;
    }
}

function splitWorkflowReasons(rawReason) {
    const text = String(rawReason || '').trim();
    if (!text) return [];
    const parts = text.split(/;\s*(?=\[)/).map(s => s.trim()).filter(Boolean);
    return parts.length > 1 ? parts : text.split(/;\s+/).map(s => s.trim()).filter(Boolean);
}

function toggleDexReasonExpand(event, btn) {
    if (event) event.stopPropagation();
    const wrap = btn.closest('.dex-reason-wrap');
    if (!wrap) return;
    const moreSpan = wrap.querySelector('.dex-reason-more');
    if (!moreSpan) return;
    const isHidden = moreSpan.classList.contains('hidden');
    moreSpan.classList.toggle('hidden', !isHidden);
    btn.textContent = isHidden ? 'Show less' : btn.dataset.moreLabel;
}

function renderDexValidatedResults(results) {
    const container = document.getElementById('dex-validated-results');
    const count = document.getElementById('dex-validated-count');
    if (!container) return;
    dexValidatedRenderedResults = Array.isArray(results) ? results : [];
    const isTestMode = String(settings.dex_validated_test_mode || 'false').toLowerCase() === 'true';
    if (count) count.textContent = String(results.length);
    if (!results.length) {
        container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:3rem 1rem;">No new published Dexscreener events were found.</div>';
        return;
    }
    container.innerHTML = results.map((item, index) => {
        const status = item.status || 'dropped';
        const color = status === 'passed' ? '#10b981' : status === 'duplicate' ? '#f59e0b' : '#ef4444';
        const label = status === 'passed' ? (isTestMode ? 'Passed · Preview' : 'Forwarded') : status === 'duplicate' ? 'Duplicate' : 'Dropped';
        const ca = escapeHtml(item.ca || 'Unknown');
        const chart = item.dex_url ? `<a href="${escapeHtml(item.dex_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="color:#60a5fa;text-decoration:none;">Chart ↗</a>` : '';
        const canPreview = !!item.ca;
        const reasonParts = splitWorkflowReasons(item.reason);
        const firstTwo = reasonParts.slice(0, 2).map(escapeHtml).join('<br>');
        const extraParts = reasonParts.slice(2);
        const extraHtml = extraParts.length > 0
            ? `<span class="dex-reason-more hidden"><br>${extraParts.map(escapeHtml).join('<br>')}</span> <button type="button" data-more-label="+${extraParts.length} more" onclick="toggleDexReasonExpand(event, this)" style="background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.16);color:#67e8f9;border-radius:5px;padding:1px 7px;font-size:.71rem;font-weight:600;cursor:pointer;margin-left:4px;">+${extraParts.length} more</button>`
            : '';
        return `<div ${canPreview ? `role="button" tabindex="0" data-dex-preview-index="${index}"` : ''} style="padding:14px;border:1px solid var(--border-color);border-left:3px solid ${color};border-radius:9px;background:rgba(0,0,0,.16);${canPreview ? 'cursor:pointer;' : ''}">
            <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:8px;"><strong>${escapeHtml(item.name || 'Unknown token')}</strong><span style="color:${color};font-size:.78rem;font-weight:700;">${label}</span></div>
            <div style="font-size:.76rem;color:var(--text-muted);word-break:break-all;margin-bottom:8px;">${ca}</div>
            <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;font-size:.8rem;margin-bottom:8px;">
                <span>Event: <strong>${escapeHtml(item.signal_type || 'DEX order')}</strong></span><span>Status: <strong>${escapeHtml(item.order_status || 'approved')}</strong></span>
                <span>MC: <strong>${escapeHtml(item.market_cap || 'Unknown')}</strong></span><span>Age: <strong>${escapeHtml(item.age || 'Unknown')}</strong></span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;color:var(--text-muted);font-size:.77rem;line-height:1.45;"><span class="dex-reason-wrap" style="flex:1;min-width:0;">${firstTwo}${extraHtml}</span><span style="display:flex;gap:10px;align-items:center;flex-shrink:0;">${canPreview ? '<button type="button" class="dex-preview-button" style="border:1px solid rgba(96,165,250,.45);background:rgba(96,165,250,.12);color:#93c5fd;border-radius:6px;padding:4px 8px;cursor:pointer;white-space:nowrap;">View details</button>' : ''}${chart}</span></div>
        </div>`;
    }).join('');

    container.querySelectorAll('[data-dex-preview-index]').forEach(card => {
        const open = () => openDexValidatedPreview(Number(card.dataset.dexPreviewIndex));
        card.addEventListener('click', event => {
            if (event.target.closest('a') || event.target.closest('[onclick*="toggleDexReasonExpand"]')) return;
            open();
        });
        card.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                open();
            }
        });
    });
}

async function openDexValidatedPreview(index) {
    const item = dexValidatedRenderedResults[index];
    if (!item) return;
    const formatPreviewUsd = value => {
        const number = Number(value || 0);
        if (number >= 1_000_000) return `$${(number / 1_000_000).toFixed(2)}M`;
        if (number >= 1_000) return `$${(number / 1_000).toFixed(2)}K`;
        return `$${number.toFixed(2)}`;
    };
    const category = String(item.signal_type || 'DEX').toUpperCase();
    const orderLabels = {
        BOOST: 'Token Boost',
        CTO: 'Community Takeover',
        DEX: 'Token Profile',
        DEX_PROFILE: 'Token Profile',
        DEX_AD: 'Token Ad'
    };
    const orderLabel = orderLabels[category.replace(' ', '_')] || 'Dexscreener Event';
    const status = String(item.order_status || 'approved').toUpperCase();
    const statusIcon = status === 'APPROVED' ? '✅' : status === 'PROCESSING' ? '⏳' : '❓';
    const fallbackMessage = [
        `🚀 PROJECT: ${String(item.name || 'Unknown token').toUpperCase()} 🚀`,
        '━━━━━━━━━━━',
        `💰 Market Cap: ${item.market_cap || 'Unknown'}`,
        `🌐 Platform: ${item.platform || 'Solana (SOL)'}`,
        `🧬 CA: ${item.ca || 'Unknown'}`,
        '━━━━━━━━━━━',
        '',
        '⏱️ TOKEN TIMINGS',
        `┃ Category: ${category}`,
        `┃ Age (From creation): ${item.age || 'Unknown'}`,
        '┃ Age (After migration): Unknown',
        `┃ Order: 📦 ${orderLabel}`,
        '┃ DEX Payment: $0.00 USD',
        '┃ Migrated: Unknown',
        '┃ Current DEX: Unknown',
        `┃ Status: ${statusIcon} ${status}`,
        '┃ Order Placed: Unknown',
        '',
        '📊 PRICE PERFORMANCE',
        `┃ 🟩 5m: ${Number(item.perf_5m || 0) >= 0 ? '+' : ''}${Number(item.perf_5m || 0).toFixed(2)}%`,
        `┃ ⚡ 1h: ${Number(item.perf_1h || 0) >= 0 ? '+' : ''}${Number(item.perf_1h || 0).toFixed(2)}%`,
        `┃ 📉 6h: ${Number(item.perf_6h || 0) >= 0 ? '+' : ''}${Number(item.perf_6h || 0).toFixed(2)}%`,
        `┃ 🟥 24h: ${Number(item.perf_24h || 0) >= 0 ? '+' : ''}${Number(item.perf_24h || 0).toFixed(2)}%`,
        '',
        '💧 LIQUIDITY & VOLUME',
        `┃ Liquidity: ${formatPreviewUsd(item.liquidity_usd)}`,
        `┃ 5m Volume: ${formatPreviewUsd(item.volume_5m)}`,
        `┃ 1h Volume: ${formatPreviewUsd(item.volume_1h)}`,
        `┃ 6h Volume: ${formatPreviewUsd(item.volume_6h)}`,
        `┃ 24h Volume: ${formatPreviewUsd(item.volume_24h)}`,
        '━━━━━━━━━━━',
        `📈 Chart: ${item.dex_url || 'Unavailable'}`
    ].join('\n');
    let previewMessage = item.formatted_message || '';
    openTgMsgModal(
        encodeURIComponent(previewMessage || 'Loading complete signal details…'),
        encodeURIComponent(item.name || 'Token')
    );
    if (!previewMessage) {
        try {
            const response = await fetch('/api/dex-validated/details', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ca: item.ca,
                    signal_type: item.signal_type,
                    order_status: item.order_status,
                    payment_timestamp: item.payment_timestamp
                })
            });
            const payload = await readJsonResponse(response, 'DEX Validated details');
            if (!response.ok || !payload.success) throw new Error(payload.error || 'Unable to load details');
            previewMessage = payload.result?.formatted_message || fallbackMessage;
            item.formatted_message = previewMessage;
            currentTgModalRawMessage = previewMessage;
            const content = document.getElementById('tg-msg-content');
            if (content) content.innerText = previewMessage;
        } catch (error) {
            previewMessage = fallbackMessage;
            currentTgModalRawMessage = previewMessage;
            const content = document.getElementById('tg-msg-content');
            if (content) content.innerText = previewMessage;
            showAppToast(`Live enrichment unavailable: ${error.message}`, 'error', 5000);
        }
    }
    const subtitle = document.getElementById('tg-msg-modal-subtitle');
    if (subtitle) {
        if (item.status === 'passed') {
            subtitle.textContent = 'Exact message sent or ready for Telegram';
        } else {
            const parts = splitWorkflowReasons(item.reason);
            const summary = parts.length > 2
                ? `${parts.slice(0, 2).join('; ')} (+${parts.length - 2} more)`
                : (item.reason || 'Signal did not pass the workflow');
            subtitle.textContent = `${String(item.status || 'result').toUpperCase()}: ${summary}`;
        }
    }
}

async function runDexValidatedScan() {
    const button = document.getElementById('btn-run-dex-validated');
    const container = document.getElementById('dex-validated-results');
    if (button) { button.disabled = true; button.textContent = 'Scanning…'; }
    if (container) container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:3rem 1rem;">Checking Dexscreener orders and boosts…</div>';
    try {
        if (!await saveDexValidatedSettings(false)) return;
        const response = await fetch('/api/dex-validated/scan', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                target_channel: settings.dex_validated_target_channel || '',
                workflow_id: settings.dex_validated_workflow_id || 'active_all',
                test_mode: String(settings.dex_validated_test_mode).toLowerCase() === 'true'
            })
        });
        const result = await readJsonResponse(response, 'DEX Validated scanner');
        if (!response.ok || !result.success) throw new Error(result.error || 'Scan failed');
        renderDexValidatedResults(result.results || []);
        await refreshDexValidatedStatus();
    } catch (error) {
        if (container) container.innerHTML = `<div class="result-error">${escapeHtml(error.message)}</div>`;
    } finally {
        if (button) { button.disabled = false; button.textContent = 'Run Now'; }
    }
}

function initializeDexValidated() {
    const target = document.getElementById('dex-validated-target');
    const interval = document.getElementById('dex-validated-interval');
    const autoScan = document.getElementById('dex-validated-auto');
    const testMode = document.getElementById('dex-validated-test');
    const includeBoosts = document.getElementById('dex-validated-include-boosts');
    const includeCto = document.getElementById('dex-validated-include-cto');
    const includeDex = document.getElementById('dex-validated-include-dex');
    if (target) target.value = settings.dex_validated_target_channel || '';
    if (interval) interval.value = ['2','5','10','30','60','120','300'].includes(String(settings.dex_validated_scan_interval)) ? String(settings.dex_validated_scan_interval) : '5';
    if (autoScan) autoScan.checked = String(settings.dex_validated_auto_scan || 'false').toLowerCase() === 'true';
    if (testMode) testMode.checked = String(settings.dex_validated_test_mode || 'false').toLowerCase() === 'true';
    if (includeBoosts) includeBoosts.checked = String(settings.dex_validated_include_boosts || 'true').toLowerCase() === 'true';
    if (includeCto) includeCto.checked = String(settings.dex_validated_include_cto || 'true').toLowerCase() === 'true';
    if (includeDex) includeDex.checked = String(settings.dex_validated_include_dex || 'true').toLowerCase() === 'true';
    updateDexValidatedStatus();
    refreshDexValidatedStatus();
    if (!dexValidatedStatusTimer) dexValidatedStatusTimer = setInterval(refreshDexValidatedStatus, 2000);
    if (!dexValidatedCountdownTimer) dexValidatedCountdownTimer = setInterval(updateDexValidatedCountdown, 1000);
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
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                                <span>PASSED WORKFLOW CHECKLIST</span>
                            </div>
                        `;
                    } else if (s.test_status === 'dropped') {
                        testBadgeHtml = `
                            <div style="margin-bottom: 10px; padding: 6px 10px; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 6px; display: flex; flex-direction: column; gap: 2px; font-size: 0.78rem; color: #f87171; font-weight: 500;">
                                <div style="font-weight: 700; color: #fca5a5; display: flex; align-items: center; gap: 6px;">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                                    <span>DROPPED BY WORKFLOW</span>
                                </div>
                                <div style="font-size: 0.72rem; color: #fca5a5; margin-top: 2px;">Reason: ${s.test_reason}</div>
                            </div>
                        `;
                    } else if (s.test_status === 'error') {
                        testBadgeHtml = `
                            <div style="margin-bottom: 10px; padding: 6px 10px; background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 6px; display: flex; flex-direction: column; gap: 2px; font-size: 0.78rem; color: #fbbf24; font-weight: 500;">
                                <div style="font-weight: 700; display: flex; align-items: center; gap: 6px;">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                                    <span>TEST RUN ERROR</span>
                                </div>
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
                        <div style="margin-top: 8px; margin-bottom: 8px; padding: 8px; background: rgba(6, 182, 212, 0.08); border: 1px solid rgba(6, 182, 212, 0.2); border-radius: 6px;">
                            <div style="font-size: 0.72rem; color: #22d3ee; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 4px;">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
                                <span>Last Token Bought Before Payment:</span>
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                                <div style="display: flex; flex-direction: column; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: calc(100% - 100px);">
                                    <span style="font-weight: bold; color: white; font-size: 0.82rem;" title="${s.last_bought_token_name || ''}">${s.last_bought_token_name || 'Unknown Token'}</span>
                                    <span style="font-family: monospace; color: var(--text-muted); font-size: 0.7rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: all;" title="${s.last_bought_token}">${s.last_bought_token}</span>
                                </div>
                                <a href="https://dexscreener.com/solana/${s.last_bought_token}" target="_blank" class="btn-primary" style="padding: 3px 8px; font-size: 0.7rem; border-radius: 4px; text-decoration: none; background: #06b6d4; color: white; flex-shrink: 0; font-weight: 600; box-shadow: none;">
                                    Dexscreener ↗
                                </a>
                            </div>
                        </div>
                        ` : `
                        <div style="margin-top: 8px; margin-bottom: 8px; padding: 6px 8px; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 6px; font-size: 0.72rem; color: var(--text-muted); display: flex; align-items: center; gap: 4px;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
                            <span>Last Token Bought: None detected in recent history</span>
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
                    actionBadge = `<span style="color:#34d399; background:rgba(16, 185, 129, 0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem; display:inline-flex; align-items:center; gap:4px;"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> BUY</span>`;
                } else if (tx.action === 'SELL') {
                    actionBadge = `<span style="color:#f87171; background:rgba(239, 68, 68, 0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem; display:inline-flex; align-items:center; gap:4px;"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> SELL</span>`;
                } else {
                    actionBadge = `<span style="color:#9ca3af; background:rgba(156,163,175,0.15); padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.72rem; display:inline-flex; align-items:center; gap:4px;"><svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10"/></svg> ${tx.action.slice(0, 10)}</span>`;
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
                                <button onclick="navigator.clipboard.writeText('${tx.token_bought}'); alert('Token Address copied!');" class="btn-icon" style="padding:2px; font-size:0.75rem;" title="Copy Address"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg></button>
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
                    <div class="result-success" style="padding: 1rem; border: 1px solid var(--border-color); border-radius: 8px; background: rgba(0,0,0,0.1); border-left: 3px solid #06b6d4; margin-bottom: 0.5rem;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <span style="font-weight:600; color:#22d3ee; font-size:0.82rem; display:inline-flex; align-items:center; gap:5px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg> Incoming Transfer</span>
                            <span style="font-size:0.72rem; color:var(--text-muted);">${dateStr}</span>
                        </div>
                        <div style="font-size:0.95rem; font-weight:bold; color:white; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                            <span>+${p.amount.toFixed(4)} ${tokenDisplayName}</span>
                            <span style="color:#22d3ee; font-size:0.9rem;">${amountUsdText}</span>
                        </div>
                        <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:4px; display:flex; flex-direction:column; gap:4px;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span>Sender: <span style="font-family:monospace; color:#60a5fa; user-select:all;">${p.sender_address.slice(0,10)}...${p.sender_address.slice(-6)}</span></span>
                                <button onclick="openWalletTxModal('${p.sender_address}')" class="btn-primary" style="padding: 3px 8px; font-size: 0.72rem; border-radius: 4px; box-shadow: none; background: rgba(6, 182, 212, 0.16); border: 1px solid rgba(6, 182, 212, 0.4); color: #67e8f9;">
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
        btn.innerHTML = '<span class="status-dot-green"></span> Live Scan Active';
        btn.style.background = 'rgba(16, 185, 129, 0.14)';
        btn.style.borderColor = 'rgba(16, 185, 129, 0.45)';
        btn.style.color = '#34d399';
        btn.style.boxShadow = '0 0 16px rgba(16, 185, 129, 0.2)';
    } else {
        btn.innerHTML = '● Live Scan';
        btn.style.background = 'rgba(255, 255, 255, 0.05)';
        btn.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        btn.style.color = 'var(--text-muted)';
        btn.style.boxShadow = 'none';
    }
}

function updateTrackerStatusUI() {
    const indicator = document.getElementById('tracker-status-indicator');
    if (!indicator) return;
    const hasAddress = settings.tracked_wallet_address && settings.tracked_wallet_address.trim().length >= 32;
    const isPolling = String(settings.wallet_tracker_poll_enabled).toLowerCase() === 'true';
    const isLiveScan = String(settings.cto_auto_scan).toLowerCase() === 'true';
    if (hasAddress && (isPolling || isLiveScan)) {
        indicator.textContent = 'Active';
        indicator.className = 'status-badge status-active';
        indicator.removeAttribute('style');
    } else {
        indicator.textContent = 'Inactive';
        indicator.className = 'status-badge status-paused';
        indicator.removeAttribute('style');
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

// Close modals when clicking their overlays
document.addEventListener('click', function(e) {
    const modal = document.getElementById('wallet-tx-modal');
    if (modal && e.target === modal) {
        closeWalletTxModal();
    }
    const tgModal = document.getElementById('tg-msg-modal');
    if (tgModal && e.target === tgModal) {
        closeTgMsgModal();
    }
    const testerModal = document.getElementById('tester-modal');
    if (testerModal && e.target === testerModal) {
        closeTesterModal();
    }
    const groupsModal = document.getElementById('flow-groups-modal');
    if (groupsModal && e.target === groupsModal) {
        closeFlowGroupsModal();
    }
    const createGroupModal = document.getElementById('create-flow-group-modal');
    if (createGroupModal && e.target === createGroupModal) {
        closeCreateFlowGroupModal();
    }
});

// ==========================================
// Token Wallet Data & Table Management
// ==========================================

function debounceTokenSearch() {
    clearTimeout(tokenDataSearchTimer);
    tokenDataSearchTimer = setTimeout(() => {
        loadTokenWalletData(1);
    }, 300);
}

function populateTokenWorkflowPresets() {
    ['td-workflow-preset', 'dvd-workflow-preset'].forEach(selectId => {
        const selectEl = document.getElementById(selectId);
        if (!selectEl) return;
        const currentPreset = selectEl.value;
        selectEl.innerHTML = '<option value="">No Preset (Custom Filters)</option>';
        (workflows || []).forEach(wf => {
            selectEl.innerHTML += `<option value="${wf.id}">${escapeHtml(wf.name)} (${(wf.rules || []).length} rules)</option>`;
        });
        selectEl.value = currentPreset;
    });
}

function updateActiveFiltersCount() {
    let count = 0;
    const search = String(document.getElementById('td-search')?.value || '').trim();
    const status = document.getElementById('td-status')?.value;
    const platform = document.getElementById('td-platform')?.value;
    const excludePlatform = document.getElementById('td-exclude-platform')?.value;
    const timeframe = document.getElementById('td-timeframe')?.value;
    const minAmount = String(document.getElementById('td-min-amount')?.value || '').trim();
    const maxAmount = String(document.getElementById('td-max-amount')?.value || '').trim();
    const minMc = String(document.getElementById('td-min-mc')?.value || '').trim();
    const maxMc = String(document.getElementById('td-max-mc')?.value || '').trim();
    const minLiquidity = String(document.getElementById('td-min-liquidity')?.value || '').trim();
    const maxLiquidity = String(document.getElementById('td-max-liquidity')?.value || '').trim();
    const minVolume = String(document.getElementById('td-min-volume')?.value || '').trim();
    const maxVolume = String(document.getElementById('td-max-volume')?.value || '').trim();
    const minPerf = String(document.getElementById('td-min-perf')?.value || '').trim();
    const maxPerf = String(document.getElementById('td-max-perf')?.value || '').trim();
    const minAge = String(document.getElementById('td-min-age')?.value || '').trim();
    const maxAge = String(document.getElementById('td-max-age')?.value || '').trim();
    const minMigrationAge = String(document.getElementById('td-min-migration-age')?.value || '').trim();
    const maxMigrationAge = String(document.getElementById('td-max-migration-age')?.value || '').trim();
    const migrated = document.getElementById('td-migrated')?.value;
    const namePat = String(document.getElementById('td-token-name-pattern')?.value || '').trim();
    const wordFilter = String(document.getElementById('td-word-filter')?.value || '').trim();
    const enrichedOnly = document.getElementById('td-enriched-only')?.checked;
    const preset = document.getElementById('td-workflow-preset')?.value;

    if (search) count++;
    if (status && status !== 'all') count++;
    if (platform && platform !== 'all') count++;
    if (excludePlatform && excludePlatform !== 'none') count++;
    if (timeframe && timeframe !== 'all') count++;
    if (minAmount) count++;
    if (maxAmount) count++;
    if (minMc) count++;
    if (maxMc) count++;
    if (minLiquidity) count++;
    if (maxLiquidity) count++;
    if (minVolume) count++;
    if (maxVolume) count++;
    if (minPerf) count++;
    if (maxPerf) count++;
    if (minAge) count++;
    if (maxAge) count++;
    if (minMigrationAge) count++;
    if (maxMigrationAge) count++;
    if (migrated && migrated !== 'all') count++;
    if (namePat && namePat !== '.*') count++;
    if (wordFilter) count++;
    if (enrichedOnly) count++;
    if (preset) count++;

    const badge = document.getElementById('td-active-filters-count');
    if (badge) {
        if (count > 0) {
            badge.innerText = `${count} filter${count > 1 ? 's' : ''} active`;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }
}

function applyWorkflowPresetToFilters() {
    const select = document.getElementById('td-workflow-preset');
    const wfId = select ? select.value : '';
    if (!wfId) {
        updateActiveFiltersCount();
        loadTokenWalletData(1);
        return;
    }

    const wf = (workflows || []).find(w => String(w.id) === String(wfId));
    if (!wf || !wf.rules) {
        loadTokenWalletData(1);
        return;
    }

    // Reset metric inputs first
    const clearIds = ['td-min-age', 'td-max-age', 'td-min-migration-age', 'td-max-migration-age', 'td-min-mc', 'td-max-mc', 'td-min-liquidity', 'td-max-liquidity', 'td-min-volume', 'td-max-volume', 'td-min-perf', 'td-max-perf', 'td-min-amount', 'td-max-amount', 'td-token-name-pattern', 'td-word-filter'];
    clearIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const perfTfEl = document.getElementById('td-perf-tf');
    if (perfTfEl) {
        perfTfEl.value = '5m';
        delete perfTfEl.dataset.appliedTf;
    }
    const migratedEl = document.getElementById('td-migrated');
    if (migratedEl) migratedEl.value = 'all';
    const excludePlatformEl = document.getElementById('td-exclude-platform');
    if (excludePlatformEl) excludePlatformEl.value = 'none';
    const nameModeEl = document.getElementById('td-token-name-mode');
    if (nameModeEl) nameModeEl.value = 'include';

    // Map rules to filter controls
    wf.rules.forEach(rule => {
        const type = rule.rule_type;
        if (type === 'token_age') {
            const ageType = (rule.search_text || 'creation').toLowerCase();
            if (ageType === 'migration') {
                const minMig = document.getElementById('td-min-migration-age');
                const maxMig = document.getElementById('td-max-migration-age');
                if (minMig && rule.time_min !== undefined && rule.time_min !== null) minMig.value = rule.time_min;
                if (maxMig && rule.time_max !== undefined && rule.time_max !== null) maxMig.value = rule.time_max;
            } else {
                const minAge = document.getElementById('td-min-age');
                const maxAge = document.getElementById('td-max-age');
                if (minAge && rule.time_min !== undefined && rule.time_min !== null) minAge.value = rule.time_min;
                if (maxAge && rule.time_max !== undefined && rule.time_max !== null) maxAge.value = rule.time_max;
            }
        } else if (type === 'market_cap') {
            const minMc = document.getElementById('td-min-mc');
            const maxMc = document.getElementById('td-max-mc');
            if (minMc && rule.time_min !== undefined && rule.time_min !== null) minMc.value = rule.time_min;
            if (maxMc && rule.time_max !== undefined && rule.time_max !== null) maxMc.value = rule.time_max;
        } else if (type === 'performance') {
            const tfClean = (rule.search_text || '5m').toLowerCase().replace('hr', 'h');
            const currentAppliedTf = perfTfEl ? perfTfEl.dataset.appliedTf : null;
            if (!currentAppliedTf || tfClean === '5m' || (tfClean === '1h' && currentAppliedTf !== '5m')) {
                if (perfTfEl && ['5m', '1h', '6h', '24h'].includes(tfClean)) {
                    perfTfEl.value = tfClean;
                    perfTfEl.dataset.appliedTf = tfClean;
                }
                const minP = document.getElementById('td-min-perf');
                const maxP = document.getElementById('td-max-perf');
                if (minP && rule.time_min !== undefined && rule.time_min !== null) minP.value = rule.time_min;
                if (maxP && rule.time_max !== undefined && rule.time_max !== null) maxP.value = rule.time_max;
            }
        } else if (type === 'liquidity') {
            const minL = document.getElementById('td-min-liquidity');
            const maxL = document.getElementById('td-max-liquidity');
            if (minL && rule.time_min !== undefined && rule.time_min !== null) minL.value = rule.time_min;
            if (maxL && rule.time_max !== undefined && rule.time_max !== null) maxL.value = rule.time_max;
        } else if (type === 'volume') {
            const tf = (rule.search_text || '24h').toLowerCase().replace('hr', 'h');
            const tfEl = document.getElementById('td-volume-tf');
            const minV = document.getElementById('td-min-volume');
            const maxV = document.getElementById('td-max-volume');
            if (tfEl && ['5m', '1h', '6h', '24h'].includes(tf)) tfEl.value = tf;
            if (minV && rule.time_min !== undefined && rule.time_min !== null) minV.value = rule.time_min;
            if (maxV && rule.time_max !== undefined && rule.time_max !== null) maxV.value = rule.time_max;
        } else if (type === 'dex_payment') {
            const minA = document.getElementById('td-min-amount');
            const maxA = document.getElementById('td-max-amount');
            if (minA && rule.time_min !== undefined && rule.time_min !== null) minA.value = rule.time_min;
            if (maxA && rule.time_max !== undefined && rule.time_max !== null) maxA.value = rule.time_max;
        } else if (type === 'migrated') {
            if (migratedEl) migratedEl.value = (rule.search_text || 'yes').toLowerCase();
        } else if (type === 'exclude_platform') {
            if (excludePlatformEl) {
                const p = (rule.search_text || '').toLowerCase();
                if (p.includes('pump')) excludePlatformEl.value = 'pump';
                else if (p.includes('raydium')) excludePlatformEl.value = 'raydium';
                else if (p.includes('moon')) excludePlatformEl.value = 'moonshot';
            }
        } else if (type === 'token_name') {
            const patEl = document.getElementById('td-token-name-pattern');
            if (patEl) patEl.value = rule.search_text || '.*';
            if (nameModeEl) nameModeEl.value = (rule.replace_text || 'include').toLowerCase();
        } else if (type === 'filter') {
            const wfEl = document.getElementById('td-word-filter');
            if (wfEl) wfEl.value = rule.search_text || '';
        }
    });

    updateActiveFiltersCount();
    loadTokenWalletData(1);
}

function resetTokenFilters() {
    const els = {
        'td-workflow-preset': '',
        'td-search': '',
        'td-status': 'all',
        'td-platform': 'all',
        'td-exclude-platform': 'none',
        'td-timeframe': 'all',
        'td-sort-by': 'id_desc',
        'td-min-amount': '',
        'td-max-amount': '',
        'td-min-mc': '',
        'td-max-mc': '',
        'td-min-liquidity': '',
        'td-max-liquidity': '',
        'td-volume-tf': '24h',
        'td-min-volume': '',
        'td-max-volume': '',
        'td-perf-tf': '5m',
        'td-min-perf': '',
        'td-max-perf': '',
        'td-min-age': '',
        'td-max-age': '',
        'td-min-migration-age': '',
        'td-max-migration-age': '',
        'td-migrated': 'all',
        'td-token-name-mode': 'include',
        'td-token-name-pattern': '',
        'td-word-filter': '',
        'td-outcome-filter': 'all',
        'td-path-filter': 'all',
        'td-min-peak-1h': '',
        'td-min-peak-4h': '',
        'td-max-pre-dd': ''
    };
    for (const [id, val] of Object.entries(els)) {
        const el = document.getElementById(id);
        if (el) el.value = val;
    }
    const enrichedEl = document.getElementById('td-enriched-only');
    if (enrichedEl) enrichedEl.checked = false;

    updateActiveFiltersCount();
    loadTokenWalletData(1);
}

function toggleTokenDataAutoRefresh() {
    const select = document.getElementById('token-data-autorefresh');
    let intervalMs = parseInt(select ? select.value : '60000', 10);
    if (isNaN(intervalMs) || intervalMs < 0) {
        intervalMs = 60000;
        if (select) select.value = '60000';
    }
    
    try {
        localStorage.setItem('teleflow_token_data_refresh_pref', String(intervalMs));
    } catch (e) {}

    if (tokenDataAutoRefreshInterval) {
        clearInterval(tokenDataAutoRefreshInterval);
        tokenDataAutoRefreshInterval = null;
    }
    
    if (intervalMs > 0) {
        tokenDataAutoRefreshInterval = setInterval(() => {
            const tokenTab = document.getElementById('tab-token-data');
            if (tokenTab && !tokenTab.classList.contains('hidden')) {
                loadTokenWalletData(currentTokenDataPage, false);
            }
        }, intervalMs);
    }
}

function renderTokenDataFromCache() {
    try {
        const cachedStr = localStorage.getItem('teleflow_token_data_cache');
        if (!cachedStr) return false;
        const cached = JSON.parse(cachedStr);
        if (!cached || !cached.payments || cached.payments.length === 0) return false;

        const tbody = document.getElementById('token-data-tbody');
        if (tbody) {
            tbody.innerHTML = cached.payments.map(p => renderTokenRow(p)).join('');
        }

        if (cached.stats) {
            const kpiTotal = document.getElementById('kpi-total-payments');
            const kpiForwarded = document.getElementById('kpi-forwarded-payments');
            const kpiTokens = document.getElementById('kpi-unique-tokens');
            const kpiVolume = document.getElementById('kpi-total-volume');

            if (kpiTotal) kpiTotal.innerText = Number(cached.stats.total || 0).toLocaleString();
            if (kpiForwarded) kpiForwarded.innerText = Number(cached.stats.forwarded || 0).toLocaleString();
            if (kpiTokens) kpiTokens.innerText = Number(cached.stats.unique_tokens || 0).toLocaleString();
            if (kpiVolume) {
                const vol = Number(cached.stats.total_volume || 0);
                kpiVolume.innerText = `$${vol.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            }
            if (cached.stats && cached.stats.sol_price) {
                window.currentSolPrice = cached.stats.sol_price;
            }
        }

        renderTokenDataPagination(cached.total || cached.payments.length, cached.payments.length, cached.page || 1, cached.total_pages || 1);

        const badge = document.getElementById('td-sync-badge');
        if (badge) {
            badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> <span>Cached (Syncing fresh...)</span>';
            badge.style.color = '#22d3ee';
            badge.style.borderColor = 'rgba(6, 182, 212, 0.3)';
            badge.style.background = 'rgba(6, 182, 212, 0.15)';
        }
        return true;
    } catch (e) {
        console.warn("Error rendering token cache:", e);
        return false;
    }
}

async function loadTokenWalletData(page = 1, showLoading = false) {
    currentTokenDataPage = page;
    const tbody = document.getElementById('token-data-tbody');
    
    // Only show loading placeholder if we don't have any cached rows showing
    const tbodyText = tbody ? (tbody.innerText || tbody.textContent || '') : '';
    const hasRows = tbody && tbody.children && tbody.children.length > 0 && !tbodyText.includes('Loading');
    if (showLoading && tbody && !hasRows) {
        tbody.innerHTML = `
            <tr>
                <td colspan="16" style="text-align: center; padding: 3rem; color: var(--text-muted);">
                    <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                        <span>Loading token wallet data...</span>
                    </div>
                </td>
            </tr>
        `;
    }

    const badge = document.getElementById('td-sync-badge');
    if (badge) {
        badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> <span>Syncing...</span>';
        badge.style.color = '#fbbf24';
        badge.style.borderColor = 'rgba(245, 158, 11, 0.3)';
        badge.style.background = 'rgba(245, 158, 11, 0.15)';
    }

    try {
        const search = String(document.getElementById('td-search')?.value || '').trim();
        const status = document.getElementById('td-status')?.value || 'all';
        const platform = document.getElementById('td-platform')?.value || 'all';
        const excludePlatform = document.getElementById('td-exclude-platform')?.value || 'none';
        const timeframe = document.getElementById('td-timeframe')?.value || 'all';
        const sortVal = document.getElementById('td-sort-by')?.value || 'id_desc';
        const enrichedOnly = document.getElementById('td-enriched-only') ? document.getElementById('td-enriched-only').checked : true;
        const minAmount = String(document.getElementById('td-min-amount')?.value || '').trim();
        const maxAmount = String(document.getElementById('td-max-amount')?.value || '').trim();
        const minMc = String(document.getElementById('td-min-mc')?.value || '').trim();
        const maxMc = String(document.getElementById('td-max-mc')?.value || '').trim();
        const minLiquidity = String(document.getElementById('td-min-liquidity')?.value || '').trim();
        const maxLiquidity = String(document.getElementById('td-max-liquidity')?.value || '').trim();
        const volumeTf = document.getElementById('td-volume-tf')?.value || '24h';
        const minVolume = String(document.getElementById('td-min-volume')?.value || '').trim();
        const maxVolume = String(document.getElementById('td-max-volume')?.value || '').trim();
        const perfTf = document.getElementById('td-perf-tf')?.value || '5m';
        const minPerf = String(document.getElementById('td-min-perf')?.value || '').trim();
        const maxPerf = String(document.getElementById('td-max-perf')?.value || '').trim();
        const minAge = String(document.getElementById('td-min-age')?.value || '').trim();
        const maxAge = String(document.getElementById('td-max-age')?.value || '').trim();
        const minMigrationAge = String(document.getElementById('td-min-migration-age')?.value || '').trim();
        const maxMigrationAge = String(document.getElementById('td-max-migration-age')?.value || '').trim();
        const migrated = document.getElementById('td-migrated')?.value || 'all';
        const tokenNamePattern = String(document.getElementById('td-token-name-pattern')?.value || '').trim();
        const tokenNameMode = document.getElementById('td-token-name-mode')?.value || 'include';
        const wordFilter = String(document.getElementById('td-word-filter')?.value || '').trim();
        const outcomeFilter = document.getElementById('td-outcome-filter')?.value || 'all';
        const pathFilter = document.getElementById('td-path-filter')?.value || 'all';
        const minPeak1h = String(document.getElementById('td-min-peak-1h')?.value || '').trim();
        const minPeak4h = String(document.getElementById('td-min-peak-4h')?.value || '').trim();
        const maxPreDd = String(document.getElementById('td-max-pre-dd')?.value || '').trim();
        const workflowId = document.getElementById('td-workflow-preset')?.value || '';
        const limit = document.getElementById('td-limit')?.value || '100';

        const lastIdx = sortVal.lastIndexOf('_');
        const sortBy = lastIdx !== -1 ? sortVal.slice(0, lastIdx) : sortVal;
        const sortOrder = lastIdx !== -1 ? sortVal.slice(lastIdx + 1) : 'desc';
        TABLE_SORT_STATE.td = { sortBy: sortBy || 'id', sortOrder: sortOrder || 'desc' };
        syncTableHeaderSortIndicators('td', sortBy || 'id', sortOrder || 'desc');

        const params = new URLSearchParams({
            page: page,
            limit: limit,
            search: search,
            status: status,
            platform: platform,
            exclude_platform: excludePlatform,
            timeframe: timeframe,
            sort_by: sortBy || 'id',
            sort_order: sortOrder || 'desc',
            enriched_only: enrichedOnly
        });

        if (minAmount) params.append('min_amount', minAmount);
        if (maxAmount) params.append('max_amount', maxAmount);
        if (minMc) params.append('min_mc', minMc);
        if (maxMc) params.append('max_mc', maxMc);
        if (minLiquidity) params.append('min_liquidity', minLiquidity);
        if (maxLiquidity) params.append('max_liquidity', maxLiquidity);
        if (volumeTf) params.append('volume_tf', volumeTf);
        if (minVolume) params.append('min_volume', minVolume);
        if (maxVolume) params.append('max_volume', maxVolume);
        if (perfTf) params.append('perf_tf', perfTf);
        if (minPerf) params.append('min_perf', minPerf);
        if (maxPerf) params.append('max_perf', maxPerf);
        if (minAge) params.append('min_age', minAge);
        if (maxAge) params.append('max_age', maxAge);
        if (minMigrationAge) params.append('min_migration_age', minMigrationAge);
        if (maxMigrationAge) params.append('max_migration_age', maxMigrationAge);
        if (migrated && migrated !== 'all') params.append('migrated', migrated);
        if (tokenNamePattern) params.append('token_name_pattern', tokenNamePattern);
        if (tokenNameMode) params.append('token_name_mode', tokenNameMode);
        if (wordFilter) params.append('word_filter', wordFilter);
        if (outcomeFilter && outcomeFilter !== 'all') params.append('outcome_filter', outcomeFilter);
        if (pathFilter && pathFilter !== 'all') params.append('path_filter', pathFilter);
        if (minPeak1h) params.append('min_peak_1h', minPeak1h);
        if (minPeak4h) params.append('min_peak_4h', minPeak4h);
        if (maxPreDd) params.append('max_pre_dd', maxPreDd);
        if (workflowId) params.append('workflow_id', workflowId);

        updateActiveFiltersCount();

        const res = await fetch(`/api/wallet/token_payments?${params.toString()}`);
        const data = await res.json();

        if (!data.success) {
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="16" style="text-align: center; color: #ef4444; padding: 2rem;">Error: ${data.error || 'Failed to fetch data'}</td></tr>`;
            }
            if (badge) {
                badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg> <span>Sync Failed</span>';
                badge.style.color = '#ef4444';
            }
            return;
        }

        // Update KPI Stats Cards
        if (data.stats) {
            if (data.stats.sol_price) {
                window.currentSolPrice = data.stats.sol_price;
            }
            const kpiTotal = document.getElementById('kpi-total-payments');
            const kpiForwarded = document.getElementById('kpi-forwarded-payments');
            const kpiTokens = document.getElementById('kpi-unique-tokens');
            const kpiVolume = document.getElementById('kpi-total-volume');

            if (kpiTotal) kpiTotal.innerText = Number(data.stats.total || 0).toLocaleString();
            if (kpiForwarded) kpiForwarded.innerText = Number(data.stats.forwarded || 0).toLocaleString();
            if (kpiTokens) kpiTokens.innerText = Number(data.stats.unique_tokens || 0).toLocaleString();
            if (kpiVolume) {
                const vol = Number(data.stats.total_volume || 0);
                kpiVolume.innerText = `$${vol.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            }
        }

        const payments = data.payments || [];
        if (payments.length === 0) {
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="16" style="text-align: center; padding: 3.5rem; color: var(--text-muted);">
                            <div style="display:flex; justify-content:center; margin-bottom: 10px;">
                                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                            </div>
                            <div style="font-weight: 500; font-size: 0.95rem; color: white;">No token records found</div>
                            <div style="font-size: 0.8rem; margin-top: 4px;">Try adjusting or resetting your filter criteria.</div>
                        </td>
                    </tr>
                `;
            }
            renderTokenDataPagination(0, 0, page, 1);
            if (badge) {
                badge.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 6px #10b981;"></span> <span>Live & Synced</span>';
                badge.style.color = '#34d399';
                badge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                badge.style.background = 'rgba(16, 185, 129, 0.15)';
            }
            return;
        }

        if (tbody) {
            tbody.innerHTML = payments.map(p => renderTokenRow(p)).join('');
            applyTableColumnVisibility('td');
        }

        renderTokenDataPagination(data.total, payments.length, data.page, data.total_pages);

        // Cache first page to browser storage
        if (page === 1 && !search && status === 'all' && platform === 'all' && timeframe === 'all' && !enrichedOnly) {
            try {
                localStorage.setItem('teleflow_token_data_cache', JSON.stringify({
                    payments: data.payments,
                    stats: data.stats,
                    total: data.total,
                    total_pages: data.total_pages,
                    page: data.page,
                    timestamp: Date.now()
                }));
            } catch (e) {
                console.warn("Error caching token data:", e);
            }
        }

        if (badge) {
            badge.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 6px #10b981;"></span> <span>Live & Synced</span>';
            badge.style.color = '#34d399';
            badge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
            badge.style.background = 'rgba(16, 185, 129, 0.15)';
        }
    } catch (err) {
        console.error("Failed to load token wallet data:", err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="16" style="text-align: center; color: #ef4444; padding: 2rem;">Error loading data: ${err.message}</td></tr>`;
        }
        if (badge) {
            badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> <span>Connection Error</span>';
            badge.style.color = '#ef4444';
        }
    }
}

function extractForwardedFlowNames(status, rawReason) {
    const s = String(status || '').toLowerCase();
    if (s !== 'forwarded') return [];
    let cleaned = String(rawReason || '')
        .replace(/^Sent via workflows to:\s*/i, '')
        .replace(/^Passed test via:\s*/i, '')
        .trim();
    if (!cleaned || cleaned === '-') return [];
    const tokens = cleaned.split(/\s*[,;]\s*/).map(t => t.replace(/^Preview:\s*/i, '').trim()).filter(Boolean);
    const resolved = [];
    tokens.forEach(tok => {
        if (/^-?\d{6,}$/.test(tok) && Array.isArray(workflows)) {
            const matched = workflows.filter(w => String(w.target_channel_id || '').trim() === tok || String(w.target_channel || '').trim() === tok);
            if (matched.length > 0) {
                matched.forEach(w => {
                    if (w.name && !resolved.includes(w.name)) resolved.push(w.name);
                });
                return;
            }
        }
        if (!resolved.includes(tok)) resolved.push(tok);
    });
    return resolved;
}

function formatForwardedFlowsCell(status, rawReason) {
    const flows = extractForwardedFlowNames(status, rawReason);
    if (!flows.length) {
        return `<td title="Not forwarded"><span style="color: var(--text-muted); font-size: 0.78rem;">None</span></td>`;
    }
    const fullText = flows.join(', ');
    const badgesHtml = flows.map(name => `<span style="display:inline-block; background:rgba(16,185,129,0.12); color:#6ee7b7; border:1px solid rgba(16,185,129,0.3); padding:1px 7px; border-radius:5px; font-size:0.74rem; font-weight:600; white-space:nowrap;">${escapeHtml(name)}</span>`).join(' ');
    return `<td title="${escapeHtml(fullText)}"><div style="display:flex; flex-wrap:wrap; gap:4px; align-items:center;">${badgesHtml}</div></td>`;
}

function getRowEventEpochSeconds(row) {
    const ts = row.timestamp;
    if (typeof ts === 'number' && ts > 0) {
        return ts > 1e12 ? Math.floor(ts / 1000) : Math.floor(ts);
    }
    if (row.created_at) {
        const raw = String(row.created_at).trim();
        const iso = (!raw.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(raw))
            ? raw.replace(' ', 'T') + 'Z'
            : raw;
        const parsed = Date.parse(iso);
        if (!isNaN(parsed)) return Math.floor(parsed / 1000);
    }
    return 0;
}

function formatCompactMc(val) {
    if (val === null || val === undefined || val === '') return '-';
    const num = Number(val);
    if (!Number.isFinite(num) || num <= 0) return '$0';
    if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
    if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
    if (num >= 1e3) return `$${(num / 1e3).toFixed(num >= 1e5 ? 0 : 1)}K`;
    return `$${num.toFixed(0)}`;
}

function formatPeakPerformanceCell(row, horizon = '1h') {
    const is4h = horizon === '4h';
    const maxMc = is4h ? row.max_mc_4h : row.max_mc_1h;
    const peakMult = is4h ? row.peak_mult_4h : row.peak_mult_1h;
    const minMc = is4h ? row.min_mc_4h : row.min_mc_1h;
    const minDd = is4h ? row.min_dd_4h_pct : row.min_dd_1h_pct;
    const preLowMc = is4h ? row.pre_peak_low_mc_4h : row.pre_peak_low_mc_1h;
    const preDd = is4h ? row.pre_peak_dd_4h_pct : row.pre_peak_dd_1h_pct;
    const pathCode = String((is4h ? row.path_4h : row.path_1h) || '').toUpperCase();

    if (peakMult === null || peakMult === undefined || maxMc === null || maxMc === undefined) {
        const eventSec = getRowEventEpochSeconds(row);
        const nowSec = Math.floor(Date.now() / 1000);
        const reqSec = is4h ? 14400 : 3600;
        if (eventSec > 0 && (nowSec - eventSec) < reqSec) {
            const remMin = Math.max(1, Math.ceil((reqSec - (nowSec - eventSec)) / 60));
            const remStr = remMin >= 60 ? `${Math.floor(remMin / 60)}h ${remMin % 60}m` : `${remMin}m`;
            return `<td title="Waiting for ${horizon} window to complete (${remStr} left)"><span style="color: var(--text-muted); font-size: 0.75rem; white-space: nowrap;">⏳ ${remStr} left</span></td>`;
        }
        return `<td title="Queued for 1h/4h OHLCV Peak scan"><span style="color: var(--text-muted); font-size: 0.75rem; white-space: nowrap;">Pending scan</span></td>`;
    }

    const multNum = Number(peakMult);
    const maxMcFormatted = formatCompactMc(maxMc);
    const minMcFormatted = formatCompactMc(minMc);
    const preLowFormatted = formatCompactMc(preLowMc);
    const preDdNum = Number(preDd || 0);
    const minDdNum = Number(minDd || 0);

    let multColor = '#94a3b8';
    let multBg = 'rgba(148, 163, 184, 0.12)';
    let multBorder = 'rgba(148, 163, 184, 0.25)';
    if (multNum >= 2.0) {
        multColor = '#34d399';
        multBg = 'rgba(16, 185, 129, 0.16)';
        multBorder = 'rgba(16, 185, 129, 0.4)';
    } else if (multNum >= 1.2) {
        multColor = '#22d3ee';
        multBg = 'rgba(6, 182, 212, 0.14)';
        multBorder = 'rgba(6, 182, 212, 0.35)';
    } else if (multNum < 0.95) {
        multColor = '#f87171';
        multBg = 'rgba(239, 68, 68, 0.14)';
        multBorder = 'rgba(239, 68, 68, 0.35)';
    }

    let pathLabel = 'Flat';
    let pathColor = '#94a3b8';
    if (pathCode === 'DIP_THEN_PUMP') {
        pathLabel = 'Dip→Pump';
        pathColor = '#fbbf24';
    } else if (pathCode === 'PUMP_THEN_DUMP') {
        pathLabel = 'Pump→Dump';
        pathColor = '#fb923c';
    } else if (pathCode === 'DIRECT_PUMP') {
        pathLabel = 'Pump & Hold';
        pathColor = '#34d399';
    } else if (pathCode === 'STRAIGHT_DUMP') {
        pathLabel = 'Dumped';
        pathColor = '#f87171';
    }

    const preDdDisplay = preDdNum < -0.5 ? `${preDdNum.toFixed(0)}% (${preLowFormatted})` : `0% (${preLowFormatted})`;
    const sublineText = pathCode === 'STRAIGHT_DUMP'
        ? `<span style="color:${pathColor}; font-weight:600;">${pathLabel}</span> · Low: ${minMcFormatted} (${minDdNum.toFixed(0)}%)`
        : `<span style="color:${pathColor}; font-weight:600;">${pathLabel}</span> · Pre: ${preDdDisplay}`;

    const tooltip = [
        `${horizon.toUpperCase()} Peak: ${multNum.toFixed(2)}x (${maxMcFormatted})`,
        `Trajectory: ${pathLabel} (${pathCode || 'N/A'})`,
        `Pre-Peak Low: ${preLowFormatted} (${preDdNum.toFixed(1)}% before peak)`,
        `${horizon.toUpperCase()} Window Low: ${minMcFormatted} (${minDdNum.toFixed(1)}% max drawdown)`
    ].join('\n');

    return `
        <td title="${escapeHtml(tooltip)}" style="white-space: nowrap;">
            <div style="display:inline-block; background:${multBg}; color:${multColor}; border:1px solid ${multBorder}; padding:1px 7px; border-radius:5px; font-size:0.78rem; font-weight:700;">
                ${multNum.toFixed(1)}x (${maxMcFormatted})
            </div>
            <div style="font-size:0.69rem; color:var(--text-muted); margin-top:2px;">
                ${sublineText}
            </div>
        </td>
    `;
}

async function triggerPostSignalPeakScan(btnEl) {
    const origHtml = btnEl ? btnEl.innerHTML : '';
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.innerHTML = '<span>Scanning OHLCV...</span>';
    }
    try {
        const res = await fetch('/api/performance/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ batch_size: 50 })
        });
        const data = await res.json();
        if (btnEl) {
            btnEl.innerHTML = '<span>Scan Running...</span>';
        }
        setTimeout(() => {
            const dvdTab = document.getElementById('tab-dex-validated-data');
            if (dvdTab && !dvdTab.classList.contains('hidden')) {
                loadDexValidatedTableData(currentDexValidatedDataPage || 1, false);
            } else {
                loadTokenWalletData(currentTokenDataPage || 1, false);
            }
            if (btnEl) {
                btnEl.disabled = false;
                btnEl.innerHTML = origHtml;
            }
        }, 4500);
    } catch (e) {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.innerHTML = origHtml;
        }
    }
}

const TABLE_COLUMNS_STATE = {
    td: null,
    dvd: null
};

function getTableElementForPrefix(prefix) {
    return document.getElementById(prefix === 'dvd' ? 'dex-validated-data-table' : 'token-data-table');
}

function initTableColumnsMenu(prefix) {
    const table = getTableElementForPrefix(prefix);
    const container = document.getElementById(`${prefix}-columns-checkboxes`);
    if (!table || !container) return;
    const ths = Array.from(table.querySelectorAll('thead th'));
    if (!ths.length) return;

    if (!TABLE_COLUMNS_STATE[prefix] || TABLE_COLUMNS_STATE[prefix].length !== ths.length) {
        TABLE_COLUMNS_STATE[prefix] = ths.map(() => true);
    }

    container.innerHTML = ths.map((th, idx) => {
        const label = (th.innerText || th.textContent || `Col ${idx + 1}`).replace(/[⇅↑↓]/g, '').trim();
        const checked = TABLE_COLUMNS_STATE[prefix][idx] !== false ? 'checked' : '';
        return `
            <label style="display:flex; align-items:center; gap:5px; cursor:pointer; user-select:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${escapeHtml(label)}">
                <input type="checkbox" ${checked} onchange="onTableColumnCheckboxChange('${prefix}', ${idx}, this.checked)" style="accent-color:#10b981; cursor:pointer;">
                <span style="overflow:hidden; text-overflow:ellipsis;">${escapeHtml(label)}</span>
            </label>
        `;
    }).join('');
}

const TABLE_SORT_STATE = {
    td: { sortBy: 'id', sortOrder: 'desc' },
    dvd: { sortBy: 'id', sortOrder: 'desc' }
};

function syncTableHeaderSortIndicators(prefix, sortBy, sortOrder) {
    const table = getTableElementForPrefix(prefix);
    if (!table) return;
    const normOrder = String(sortOrder || 'desc').toLowerCase() === 'asc' ? 'asc' : 'desc';
    table.querySelectorAll('thead th.sortable-th').forEach(th => {
        const colKey = th.getAttribute('data-sort');
        const arrow = th.querySelector('.sort-arrow');
        if (colKey === sortBy) {
            th.classList.add('sort-active');
            if (arrow) arrow.textContent = normOrder === 'asc' ? '↑' : '↓';
        } else {
            th.classList.remove('sort-active');
            if (arrow) arrow.textContent = '⇅';
        }
    });
}

function onSortDropdownChange(prefix) {
    const select = document.getElementById(`${prefix}-sort-by`);
    const sortVal = select ? select.value : 'id_desc';
    const lastIdx = sortVal.lastIndexOf('_');
    const sortBy = lastIdx !== -1 ? sortVal.slice(0, lastIdx) : sortVal;
    const sortOrder = lastIdx !== -1 ? sortVal.slice(lastIdx + 1) : 'desc';
    TABLE_SORT_STATE[prefix] = { sortBy, sortOrder };
    syncTableHeaderSortIndicators(prefix, sortBy, sortOrder);
    if (prefix === 'dvd') {
        loadDexValidatedTableData(1);
    } else {
        loadTokenWalletData(1);
    }
}

function sortTableByColumn(prefix, sortKey, defaultDir = 'desc') {
    const current = TABLE_SORT_STATE[prefix] || { sortBy: 'id', sortOrder: 'desc' };
    let nextOrder = defaultDir;
    if (current.sortBy === sortKey) {
        nextOrder = current.sortOrder === 'desc' ? 'asc' : 'desc';
    }
    TABLE_SORT_STATE[prefix] = { sortBy: sortKey, sortOrder: nextOrder };

    const select = document.getElementById(`${prefix}-sort-by`);
    if (select) {
        const targetVal = `${sortKey}_${nextOrder}`;
        let opt = Array.from(select.options).find(o => o.value === targetVal);
        if (!opt) {
            opt = document.createElement('option');
            opt.value = targetVal;
            opt.textContent = `Sort: ${sortKey.replace(/_/g, ' ')} (${nextOrder === 'asc' ? '↑' : '↓'})`;
            select.appendChild(opt);
        }
        select.value = targetVal;
    }

    syncTableHeaderSortIndicators(prefix, sortKey, nextOrder);
    if (prefix === 'dvd') {
        loadDexValidatedTableData(1);
    } else {
        loadTokenWalletData(1);
    }
}

function toggleTableColumnsMenu(prefix) {
    const menu = document.getElementById(`${prefix}-columns-menu`);
    if (!menu) return;
    const wasHidden = menu.classList.contains('hidden');
    document.querySelectorAll('#td-columns-menu, #dvd-columns-menu').forEach(el => el.classList.add('hidden'));
    if (wasHidden) {
        initTableColumnsMenu(prefix);
        menu.classList.remove('hidden');
    }
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('#td-columns-menu') && !e.target.closest('#dvd-columns-menu') && !e.target.closest('[onclick*="toggleTableColumnsMenu"]')) {
        document.querySelectorAll('#td-columns-menu, #dvd-columns-menu').forEach(el => el.classList.add('hidden'));
    }
});

function onTableColumnCheckboxChange(prefix, colIdx, isChecked) {
    if (!TABLE_COLUMNS_STATE[prefix]) initTableColumnsMenu(prefix);
    if (TABLE_COLUMNS_STATE[prefix]) {
        TABLE_COLUMNS_STATE[prefix][colIdx] = Boolean(isChecked);
    }
    applyTableColumnVisibility(prefix);
}

function applyTableColumnPreset(prefix, preset) {
    const table = getTableElementForPrefix(prefix);
    if (!table) return;
    const ths = Array.from(table.querySelectorAll('thead th'));
    if (preset === 'outcome') {
        const keepKeywords = ['time', 'token', 'contract', 'category', 'payment', 'market cap', 'age', '1h peak', '4h peak', 'status', 'actions'];
        TABLE_COLUMNS_STATE[prefix] = ths.map(th => {
            const txt = (th.innerText || th.textContent || '').trim().toLowerCase();
            return keepKeywords.some(k => txt === k || txt.startsWith(k));
        });
    } else {
        TABLE_COLUMNS_STATE[prefix] = ths.map(() => true);
    }
    initTableColumnsMenu(prefix);
    applyTableColumnVisibility(prefix);
}

function applyTableColumnVisibility(prefix) {
    const table = getTableElementForPrefix(prefix);
    if (!table) return;
    const state = TABLE_COLUMNS_STATE[prefix];
    if (!state || !state.length) return;

    const ths = table.querySelectorAll('thead th');
    ths.forEach((th, idx) => {
        th.style.display = state[idx] === false ? 'none' : '';
    });

    const rows = table.querySelectorAll('tbody tr');
    rows.forEach(tr => {
        if (tr.children.length === 1 && tr.children[0].hasAttribute('colspan')) return;
        Array.from(tr.children).forEach((td, idx) => {
            if (idx < state.length) {
                td.style.display = state[idx] === false ? 'none' : '';
            }
        });
    });
}

function renderTokenRow(p) {
    const timeFormatted = formatRelativeTime(p.timestamp || p.created_at);
    const fullDate = p.timestamp ? new Date(p.timestamp * 1000).toLocaleString() : (p.created_at || '');
    
    const tokenName = p.token_name || (p.mint === 'SOL' ? 'SOL' : 'Unknown');
    const ca = p.ca || (p.mint !== 'SOL' ? p.mint : '');
    const caShort = ca ? `${ca.slice(0, 4)}...${ca.slice(-4)}` : '-';
    
    const platform = p.platform ? p.platform.toUpperCase() : 'SOLANA';
    const isMigrated = p.migration_status && p.migration_status.toLowerCase().includes('migrated');
    
    const rawAmount = Number(p.amount || 0);
    const currLabel = p.currency || (p.mint === 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' ? 'USDC' : (p.mint === 'SOL' ? 'SOL' : (p.mint ? p.mint.slice(0, 4) : 'SOL')));
    const isStable = currLabel === 'USDC' || currLabel === 'USDT' || p.mint === 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
    
    let amountUsd = Number(p.amount_usd);
    if (isNaN(amountUsd) || (amountUsd === 0 && rawAmount > 0)) {
        if (currLabel === 'USD' || isStable || rawAmount >= 20) {
            amountUsd = rawAmount;
        } else {
            amountUsd = rawAmount * (window.currentSolPrice || 140);
        }
    }
    const amountUsdDisplay = `$${amountUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    let nativeDisplay = '';
    if (currLabel === 'USD' || rawAmount >= 20) {
        nativeDisplay = `$${rawAmount.toFixed(2)} USD`;
    } else if (currLabel === 'SOL') {
        nativeDisplay = `${rawAmount.toFixed(4)} SOL`;
    } else {
        nativeDisplay = `${rawAmount.toFixed(2)} ${currLabel}`;
    }
    const sender = p.sender_address || '';
    const senderShort = sender ? `${sender.slice(0, 4)}...${sender.slice(-4)}` : '-';

    const mc = p.market_cap || '-';
    const liquidity = formatUsdMetric(p.liquidity_usd);
    const volume5m = formatUsdMetric(p.volume_5m);
    const volume1h = formatUsdMetric(p.volume_1h);
    const volume6h = formatUsdMetric(p.volume_6h);
    const volume24h = formatUsdMetric(p.volume_24h);
    const creationAge = p.creation_age || p.age || '-';
    const migrationAge = p.migration_age && p.migration_age !== '0' && p.migration_age !== '-' ? p.migration_age : null;

    const perf5mBadge = formatPerfBadge(p.perf_5m);
    const perf1hBadge = formatPerfBadge(p.perf_1h);
    const perf6hBadge = formatPerfBadge(p.perf_6h);
    const perf24hBadge = formatPerfBadge(p.perf_24h);

    const statusPill = formatStatusPill(p.status);
    const reasonText = p.reason || '-';
    const reasonShort = reasonText.length > 35 ? `${reasonText.slice(0, 32)}...` : reasonText;

    const dexUrl = p.dex_url || (ca ? `https://dexscreener.com/solana/${ca}` : '');
    const solscanUrl = ca ? `https://solscan.io/token/${ca}` : (p.signature ? `https://solscan.io/tx/${p.signature}` : '');
    
    // Safely encode formatted message
    const rawMsg = p.formatted_message || '';
    const encodedMsg = encodeURIComponent(rawMsg);
    const encodedTokenName = encodeURIComponent(tokenName);

    return `
        <tr>
            <td title="${fullDate}">
                <div style="color: var(--text-main); font-weight: 500;">${timeFormatted}</div>
                <div style="font-size: 0.72rem; color: var(--text-muted);">${fullDate.split(',')[0]}</div>
            </td>
            <td>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-weight: 600; color: white; font-size: 0.9rem;">${escapeHtml(tokenName)}</span>
                    <span class="badge-platform">${escapeHtml(platform)}</span>
                    ${isMigrated ? '<span style="background: rgba(16,185,129,0.15); color: #10b981; border: 1px solid rgba(16,185,129,0.3); padding: 1px 4px; border-radius: 4px; font-size: 0.68rem;">Raydium</span>' : ''}
                </div>
            </td>
            <td>
                ${ca ? `
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span class="code-address" onclick="copyTextToClipboard('${ca}', this)" title="Click to copy full CA">${caShort}</span>
                        <a href="${dexUrl}" target="_blank" title="View Chart on DexScreener" style="color: #60a5fa; text-decoration: none; display: inline-flex; align-items: center;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></a>
                        <a href="${solscanUrl}" target="_blank" title="View on Solscan" style="color: var(--text-muted); text-decoration: none; display: inline-flex; align-items: center;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>
                    </div>
                ` : '<span style="color: var(--text-muted);">-</span>'}
            </td>
            <td>
                <div style="font-weight: 700; color: #10b981; font-size: 0.92rem;">${amountUsdDisplay}</div>
                <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 2px;">${nativeDisplay}</div>
            </td>
            <td>
                ${sender ? `
                    <span class="code-address" onclick="copyTextToClipboard('${sender}', this)" title="Click to copy Sender: ${sender}">${senderShort}</span>
                ` : '<span style="color: var(--text-muted);">-</span>'}
            </td>
            <td><strong style="color: #e2e8f0;">${mc}</strong></td>
            <td><strong style="color: #2dd4bf;">${liquidity}</strong></td>
            <td title="5m: ${volume5m}, 1h: ${volume1h}, 6h: ${volume6h}, 24h: ${volume24h}">
                <div style="font-size:0.74rem; white-space:nowrap;">5m ${volume5m} · 1h ${volume1h}</div>
                <div style="font-size:0.74rem; color:var(--text-muted); white-space:nowrap;">6h ${volume6h} · 24h ${volume24h}</div>
            </td>
            <td>
                <div style="font-weight: 500; color: #e2e8f0; font-size: 0.85rem;" title="Creation Age: ${escapeHtml(creationAge)}">${escapeHtml(creationAge)}</div>
                ${migrationAge ? `<div style="font-size: 0.72rem; color: #34d399; margin-top: 1px;" title="Migration Age: ${escapeHtml(migrationAge)}">Mig: ${escapeHtml(migrationAge)}</div>` : ''}
            </td>
            <td>${perf5mBadge}</td>
            <td>${perf1hBadge}</td>
            <td>${perf6hBadge}</td>
            <td>${perf24hBadge}</td>
            ${formatPeakPerformanceCell(p, '1h')}
            ${formatPeakPerformanceCell(p, '4h')}
            <td>${statusPill}</td>
            ${formatForwardedFlowsCell(p.status, p.reason)}
            <td style="text-align: right; padding-right: 1.5rem;">
                ${dexUrl ? `
                    <a href="${dexUrl}" target="_blank" class="btn-table-action" title="Open DexScreener Chart">
                        Chart
                    </a>
                ` : '<span style="color: var(--text-muted); font-size: 0.8rem;">-</span>'}
            </td>
        </tr>
    `;
}

function formatPerfBadge(val) {
    if (val === null || val === undefined || val === '') return '<span class="badge-perf badge-perf-neu">-</span>';
    const num = Number(val);
    if (isNaN(num)) return '<span class="badge-perf badge-perf-neu">-</span>';
    const formatted = `${num > 0 ? '+' : ''}${num.toFixed(1)}%`;
    if (num > 0) return `<span class="badge-perf badge-perf-pos">${formatted}</span>`;
    if (num < 0) return `<span class="badge-perf badge-perf-neg">${formatted}</span>`;
    return `<span class="badge-perf badge-perf-neu">0.0%</span>`;
}

function formatUsdMetric(val) {
    if (val === null || val === undefined || val === '') return '-';
    const num = Number(val || 0);
    if (!Number.isFinite(num)) return '-';
    if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
    if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
    if (num >= 1e3) return `$${(num / 1e3).toFixed(2)}K`;
    return `$${num.toFixed(2)}`;
}

function formatStatusPill(status) {
    const s = (status || 'detected').toLowerCase();
    if (s === 'forwarded') return '<span class="status-pill status-pill-forwarded">Forwarded</span>';
    if (s === 'skipped') return '<span class="status-pill status-pill-skipped">Skipped</span>';
    if (s === 'send_failed') return '<span class="status-pill status-pill-dropped">Send Failed</span>';
    if (s === 'dropped') return '<span class="status-pill status-pill-dropped">Dropped</span>';
    return `<span class="status-pill status-pill-detected">${s}</span>`;
}

function formatRelativeTime(ts) {
    if (!ts) return 'Just now';
    let timeMs;
    if (typeof ts === 'number') {
        timeMs = ts > 1e12 ? ts : ts * 1000;
    } else {
        const raw = String(ts).trim();
        const iso = (!raw.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(raw))
            ? raw.replace(' ', 'T') + 'Z'
            : raw;
        timeMs = new Date(iso).getTime();
    }
    if (isNaN(timeMs)) return String(ts);
    const diffSec = Math.floor((Date.now() - timeMs) / 1000);
    if (diffSec < 60) return `${Math.max(1, diffSec)}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
}

function renderTokenDataPagination(total, count, page, totalPages) {
    const infoEl = document.getElementById('td-pagination-info');
    const controlsEl = document.getElementById('td-pagination-controls');
    
    if (infoEl) {
        const limitVal = parseInt(document.getElementById('td-limit')?.value || '100', 10);
        const start = total === 0 ? 0 : (page - 1) * limitVal + 1;
        const end = total === 0 ? 0 : Math.min(total, start + count - 1);
        infoEl.innerHTML = `Showing <strong>${start.toLocaleString()}-${end.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> records`;
    }

    if (!controlsEl) return;
    
    let html = '';
    html += `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="loadTokenWalletData(${page - 1})">← Prev</button>`;
    
    const maxButtons = 5;
    let startPage = Math.max(1, page - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);
    if (endPage - startPage + 1 < maxButtons) {
        startPage = Math.max(1, endPage - maxButtons + 1);
    }

    if (startPage > 1) {
        html += `<button class="page-btn" onclick="loadTokenWalletData(1)">1</button>`;
        if (startPage > 2) html += `<span style="color: var(--text-muted); padding: 0 4px;">...</span>`;
    }

    for (let p = startPage; p <= endPage; p++) {
        html += `<button class="page-btn ${p === page ? 'active' : ''}" onclick="loadTokenWalletData(${p})">${p}</button>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += `<span style="color: var(--text-muted); padding: 0 4px;">...</span>`;
        html += `<button class="page-btn" onclick="loadTokenWalletData(${totalPages})">${totalPages}</button>`;
    }

    html += `<button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="loadTokenWalletData(${page + 1})">Next →</button>`;
    controlsEl.innerHTML = html;
}

function openTgMsgModal(encodedMsg, encodedTokenName) {
    const rawMsg = decodeURIComponent(encodedMsg || '');
    const tokenName = decodeURIComponent(encodedTokenName || 'Token');
    currentTgModalRawMessage = rawMsg;

    const modal = document.getElementById('tg-msg-modal');
    const title = document.getElementById('tg-msg-modal-title');
    const content = document.getElementById('tg-msg-content');
    const subtitle = document.getElementById('tg-msg-modal-subtitle');

    if (title) title.innerText = `Telegram Message: ${tokenName}`;
    if (subtitle) subtitle.innerText = 'Exact message sent or formatted for Telegram';
    if (content) content.innerText = rawMsg || '(No formatted message content available)';
    if (modal) modal.classList.remove('hidden');
}

function closeTgMsgModal() {
    const modal = document.getElementById('tg-msg-modal');
    if (modal) modal.classList.add('hidden');
}

function copyTgMsgContent() {
    if (!currentTgModalRawMessage) return;
    navigator.clipboard.writeText(currentTgModalRawMessage).then(() => {
        alert("Telegram message copied to clipboard!");
    }).catch(err => {
        console.error("Copy error:", err);
    });
}

function copyTextToClipboard(text, el) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        const originalText = el.innerText;
        el.innerText = 'Copied!';
        el.style.borderColor = '#10b981';
        el.style.color = '#10b981';
        setTimeout(() => {
            el.innerText = originalText;
            el.style.borderColor = '';
            el.style.color = '';
        }, 1200);
    });
}

function quickScanSender(senderAddress) {
    switchTab('wallet-tracker');
    const input = document.getElementById('scan-wallet-address');
    if (input) input.value = senderAddress;
    scanWalletHistory();
}

async function exportTokenDataCSV() {
    try {
        const search = String(document.getElementById('td-search')?.value || '').trim();
        const status = document.getElementById('td-status')?.value || 'all';
        const platform = document.getElementById('td-platform')?.value || 'all';
        const excludePlatform = document.getElementById('td-exclude-platform')?.value || 'none';
        const timeframe = document.getElementById('td-timeframe')?.value || 'all';
        const sortVal = document.getElementById('td-sort-by')?.value || 'id_desc';
        const enrichedOnly = document.getElementById('td-enriched-only') ? document.getElementById('td-enriched-only').checked : true;
        const minAmount = String(document.getElementById('td-min-amount')?.value || '').trim();
        const maxAmount = String(document.getElementById('td-max-amount')?.value || '').trim();
        const minMc = String(document.getElementById('td-min-mc')?.value || '').trim();
        const maxMc = String(document.getElementById('td-max-mc')?.value || '').trim();
        const minLiquidity = String(document.getElementById('td-min-liquidity')?.value || '').trim();
        const maxLiquidity = String(document.getElementById('td-max-liquidity')?.value || '').trim();
        const volumeTf = document.getElementById('td-volume-tf')?.value || '24h';
        const minVolume = String(document.getElementById('td-min-volume')?.value || '').trim();
        const maxVolume = String(document.getElementById('td-max-volume')?.value || '').trim();
        const perfTf = document.getElementById('td-perf-tf')?.value || '5m';
        const minPerf = String(document.getElementById('td-min-perf')?.value || '').trim();
        const maxPerf = String(document.getElementById('td-max-perf')?.value || '').trim();
        const minAge = String(document.getElementById('td-min-age')?.value || '').trim();
        const maxAge = String(document.getElementById('td-max-age')?.value || '').trim();
        const minMigrationAge = String(document.getElementById('td-min-migration-age')?.value || '').trim();
        const maxMigrationAge = String(document.getElementById('td-max-migration-age')?.value || '').trim();
        const migrated = document.getElementById('td-migrated')?.value || 'all';
        const tokenNamePattern = String(document.getElementById('td-token-name-pattern')?.value || '').trim();
        const tokenNameMode = document.getElementById('td-token-name-mode')?.value || 'include';
        const wordFilter = String(document.getElementById('td-word-filter')?.value || '').trim();
        const workflowId = document.getElementById('td-workflow-preset')?.value || '';

        const lastIdx = sortVal.lastIndexOf('_');
        const sortBy = lastIdx !== -1 ? sortVal.slice(0, lastIdx) : sortVal;
        const sortOrder = lastIdx !== -1 ? sortVal.slice(lastIdx + 1) : 'desc';

        const params = new URLSearchParams({
            page: 1,
            limit: 1000,
            search: search,
            status: status,
            platform: platform,
            exclude_platform: excludePlatform,
            timeframe: timeframe,
            sort_by: sortBy || 'id',
            sort_order: sortOrder || 'desc',
            enriched_only: enrichedOnly
        });

        if (minAmount) params.append('min_amount', minAmount);
        if (maxAmount) params.append('max_amount', maxAmount);
        if (minMc) params.append('min_mc', minMc);
        if (maxMc) params.append('max_mc', maxMc);
        if (minLiquidity) params.append('min_liquidity', minLiquidity);
        if (maxLiquidity) params.append('max_liquidity', maxLiquidity);
        if (volumeTf) params.append('volume_tf', volumeTf);
        if (minVolume) params.append('min_volume', minVolume);
        if (maxVolume) params.append('max_volume', maxVolume);
        if (perfTf) params.append('perf_tf', perfTf);
        if (minPerf) params.append('min_perf', minPerf);
        if (maxPerf) params.append('max_perf', maxPerf);
        if (minAge) params.append('min_age', minAge);
        if (maxAge) params.append('max_age', maxAge);
        if (minMigrationAge) params.append('min_migration_age', minMigrationAge);
        if (maxMigrationAge) params.append('max_migration_age', maxMigrationAge);
        if (migrated && migrated !== 'all') params.append('migrated', migrated);
        if (tokenNamePattern) params.append('token_name_pattern', tokenNamePattern);
        if (tokenNameMode) params.append('token_name_mode', tokenNameMode);
        if (wordFilter) params.append('word_filter', wordFilter);
        if (workflowId) params.append('workflow_id', workflowId);

        const res = await fetch(`/api/wallet/token_payments?${params.toString()}`);
        const data = await res.json();
        
        if (!data.success || !data.payments || data.payments.length === 0) {
            alert("No data available to export.");
            return;
        }

        const headers = ["ID", "Timestamp", "Token Name", "CA", "Platform", "Amount (SOL)", "Amount (USD)", "Sender", "Tracked Address", "Market Cap", "Liquidity USD", "Volume 5m", "Volume 1h", "Volume 6h", "Volume 24h", "Creation Age", "Migration Age", "5m %", "1h %", "6h %", "24h %", "1h Peak Mult", "1h Max MC", "1h Min MC", "1h Pre-Peak Low MC", "1h Pre-Peak DD %", "1h Path", "4h Peak Mult", "4h Max MC", "4h Min MC", "4h Pre-Peak Low MC", "4h Pre-Peak DD %", "4h Path", "Status", "Reason", "Dex URL"];
        const csvRows = [headers.join(",")];

        data.payments.forEach(p => {
            const row = [
                p.id,
                `"${p.created_at || ''}"`,
                `"${(p.token_name || '').replace(/"/g, '""')}"`,
                `"${p.ca || p.mint || ''}"`,
                `"${p.platform || ''}"`,
                p.amount || 0,
                p.amount_usd || 0,
                `"${p.sender_address || ''}"`,
                `"${p.tracked_address || ''}"`,
                `"${(p.market_cap || '').replace(/"/g, '""')}"`,
                p.liquidity_usd || 0,
                p.volume_5m || 0,
                p.volume_1h || 0,
                p.volume_6h || 0,
                p.volume_24h || 0,
                `"${(p.creation_age || p.age || '').replace(/"/g, '""')}"`,
                `"${(p.migration_age || '').replace(/"/g, '""')}"`,
                p.perf_5m || 0,
                p.perf_1h || 0,
                p.perf_6h || 0,
                p.perf_24h || 0,
                p.peak_mult_1h ?? '',
                p.max_mc_1h ?? '',
                p.min_mc_1h ?? '',
                p.pre_peak_low_mc_1h ?? '',
                p.pre_peak_dd_1h_pct ?? '',
                `"${p.path_1h || ''}"`,
                p.peak_mult_4h ?? '',
                p.max_mc_4h ?? '',
                p.min_mc_4h ?? '',
                p.pre_peak_low_mc_4h ?? '',
                p.pre_peak_dd_4h_pct ?? '',
                `"${p.path_4h || ''}"`,
                `"${p.status || ''}"`,
                `"${(p.reason || '').replace(/"/g, '""')}"`,
                `"${p.dex_url || ''}"`
            ];
            csvRows.push(row.join(","));
        });

        const blob = new Blob([csvRows.join("\n")], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `token_wallet_data_${new Date().toISOString().slice(0,10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (e) {
        alert(`Export failed: ${e.message}`);
    }
}

async function syncSupabaseData() {
    const btn = document.getElementById('btn-sync-supabase');
    const origText = btn ? btn.innerHTML : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> <span>Sync Supabase</span>';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> <span>Syncing...</span>';
    }
    const badge = document.getElementById('td-sync-badge');
    if (badge) {
        badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> <span>Syncing Supabase...</span>';
        badge.style.color = '#fbbf24';
        badge.style.borderColor = 'rgba(245, 158, 11, 0.3)';
        badge.style.background = 'rgba(245, 158, 11, 0.15)';
    }

    try {
        const res = await fetch('/api/wallet/sync_supabase', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            if (btn) btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> <span>${escapeHtml(data.message || 'Synced!')}</span>`;
            // Invalidate cache so fresh data renders immediately
            try { localStorage.removeItem(TOKEN_DATA_CACHE_KEY); } catch (e) {}
            await loadTokenWalletData(1, false);
        } else {
            alert(`Sync error: ${data.error || 'Failed to sync with Supabase'}`);
            if (btn) btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg> <span>Failed</span>';
        }
    } catch (err) {
        console.error('Error syncing Supabase:', err);
        alert(`Error connecting to server for sync: ${err.message}`);
        if (btn) btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> <span>Error</span>';
    } finally {
        setTimeout(() => {
            if (btn) {
                btn.innerHTML = origText;
                btn.disabled = false;
            }
        }, 2500);
    }
}

// ==========================================
// Application Bootstrapping
// ==========================================
try {
    if (localStorage.getItem('akoue_sidebar_collapsed') === 'true') {
        const sidebar = document.getElementById('akoue-sidebar') || document.querySelector('.akoue-sidebar');
        if (sidebar) {
            sidebar.classList.add('collapsed');
            const toggleBtn = document.getElementById('akoue-sidebar-toggle');
            if (toggleBtn) toggleBtn.title = 'Expand sidebar';
        }
    }
    const isWfDropdownOpen = localStorage.getItem('akoue_workflows_dropdown_open');
    const wfSection = document.querySelector('.akoue-workflows-section');
    if (wfSection && isWfDropdownOpen === 'false') {
        wfSection.classList.remove('open');
    }
} catch (_) {}

document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        const searchInput = document.getElementById('grok-folder-search');
        if (searchInput) {
            const sidebar = document.getElementById('akoue-sidebar');
            if (sidebar && sidebar.classList.contains('collapsed')) {
                toggleAkoueSidebar(true);
            }
            searchInput.focus();
            searchInput.select();
        }
    }
});
renderWorkflows();
populateTokenWorkflowPresets();
initializeDexValidated();
loadRealtimePayments();
setInterval(loadRealtimePayments, 15000);
updateLiveScanButtonUI();
updateTrackerStatusUI();
// Restore saved auto-refresh preference if present
const validIntervals = ['0', '60000', '300000', '900000', '3600000'];
const savedRefreshPref = localStorage.getItem('teleflow_token_data_refresh_pref');
const refreshSelect = document.getElementById('token-data-autorefresh');
if (refreshSelect) {
    if (savedRefreshPref !== null && validIntervals.includes(savedRefreshPref)) {
        refreshSelect.value = savedRefreshPref;
    } else {
        refreshSelect.value = '60000'; // Default to 1 min
        try { localStorage.setItem('teleflow_token_data_refresh_pref', '60000'); } catch (e) {}
    }
}
toggleTokenDataAutoRefresh();

// If on Token Wallet Data tab, immediately render cache and fetch live
const activeTokenTab = document.getElementById('tab-token-data');
if (activeTokenTab && !activeTokenTab.classList.contains('hidden')) {
    const hasCache = renderTokenDataFromCache();
    loadTokenWalletData(1, !hasCache);
} else {
    renderTokenDataFromCache();
}

// ==========================================
// DEX Validated Enriched Data Table
// ==========================================

function debounceDexValidatedDataSearch() {
    clearTimeout(dexValidatedDataSearchTimer);
    dexValidatedDataSearchTimer = setTimeout(() => {
        loadDexValidatedTableData(1);
    }, 300);
}

function updateDexValidatedDataActiveFiltersCount() {
    let count = 0;
    const search = String(document.getElementById('dvd-search')?.value || '').trim();
    const category = document.getElementById('dvd-category')?.value;
    const status = document.getElementById('dvd-status')?.value;
    const platform = document.getElementById('dvd-platform')?.value;
    const excludePlatform = document.getElementById('dvd-exclude-platform')?.value;
    const timeframe = document.getElementById('dvd-timeframe')?.value;
    const minMc = String(document.getElementById('dvd-min-mc')?.value || '').trim();
    const maxMc = String(document.getElementById('dvd-max-mc')?.value || '').trim();
    const minLiquidity = String(document.getElementById('dvd-min-liquidity')?.value || '').trim();
    const maxLiquidity = String(document.getElementById('dvd-max-liquidity')?.value || '').trim();
    const minVolume = String(document.getElementById('dvd-min-volume')?.value || '').trim();
    const maxVolume = String(document.getElementById('dvd-max-volume')?.value || '').trim();
    const minPerf = String(document.getElementById('dvd-min-perf')?.value || '').trim();
    const maxPerf = String(document.getElementById('dvd-max-perf')?.value || '').trim();
    const minAge = String(document.getElementById('dvd-min-age')?.value || '').trim();
    const maxAge = String(document.getElementById('dvd-max-age')?.value || '').trim();
    const minMigrationAge = String(document.getElementById('dvd-min-migration-age')?.value || '').trim();
    const maxMigrationAge = String(document.getElementById('dvd-max-migration-age')?.value || '').trim();
    const migrated = document.getElementById('dvd-migrated')?.value;
    const namePat = String(document.getElementById('dvd-token-name-pattern')?.value || '').trim();
    const wordFilter = String(document.getElementById('dvd-word-filter')?.value || '').trim();
    const preset = document.getElementById('dvd-workflow-preset')?.value;

    if (search) count++;
    if (category && category !== 'all') count++;
    if (status && status !== 'all') count++;
    if (platform && platform !== 'all') count++;
    if (excludePlatform && excludePlatform !== 'none') count++;
    if (timeframe && timeframe !== 'all') count++;
    if (minMc) count++;
    if (maxMc) count++;
    if (minLiquidity) count++;
    if (maxLiquidity) count++;
    if (minVolume) count++;
    if (maxVolume) count++;
    if (minPerf) count++;
    if (maxPerf) count++;
    if (minAge) count++;
    if (maxAge) count++;
    if (minMigrationAge) count++;
    if (maxMigrationAge) count++;
    if (migrated && migrated !== 'all') count++;
    if (namePat && namePat !== '.*') count++;
    if (wordFilter) count++;
    if (preset) count++;

    const badge = document.getElementById('dvd-active-filters-count');
    if (badge) {
        if (count > 0) {
            badge.innerText = `${count} filter${count > 1 ? 's' : ''} active`;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }
}

function applyDexValidatedPresetToFilters() {
    const select = document.getElementById('dvd-workflow-preset');
    const wfId = select ? select.value : '';
    if (!wfId) {
        updateDexValidatedDataActiveFiltersCount();
        loadDexValidatedTableData(1);
        return;
    }

    const wf = (workflows || []).find(w => String(w.id) === String(wfId));
    if (!wf || !wf.rules) {
        loadDexValidatedTableData(1);
        return;
    }

    const clearIds = [
        'dvd-min-age', 'dvd-max-age', 'dvd-min-migration-age', 'dvd-max-migration-age',
        'dvd-min-mc', 'dvd-max-mc', 'dvd-min-liquidity', 'dvd-max-liquidity',
        'dvd-min-volume', 'dvd-max-volume', 'dvd-min-perf', 'dvd-max-perf',
        'dvd-token-name-pattern', 'dvd-word-filter'
    ];
    clearIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    const perfTfEl = document.getElementById('dvd-perf-tf');
    if (perfTfEl) {
        perfTfEl.value = '5m';
        delete perfTfEl.dataset.appliedTf;
    }
    const migratedEl = document.getElementById('dvd-migrated');
    if (migratedEl) migratedEl.value = 'all';
    const excludePlatformEl = document.getElementById('dvd-exclude-platform');
    if (excludePlatformEl) excludePlatformEl.value = 'none';
    const nameModeEl = document.getElementById('dvd-token-name-mode');
    if (nameModeEl) nameModeEl.value = 'include';
    const categoryEl = document.getElementById('dvd-category');
    if (categoryEl) categoryEl.value = 'all';

    wf.rules.forEach(rule => {
        const type = rule.rule_type;
        if (type === 'token_age') {
            const ageType = (rule.search_text || 'creation').toLowerCase();
            if (ageType === 'migration') {
                const minMig = document.getElementById('dvd-min-migration-age');
                const maxMig = document.getElementById('dvd-max-migration-age');
                if (minMig && rule.time_min !== undefined && rule.time_min !== null) minMig.value = rule.time_min;
                if (maxMig && rule.time_max !== undefined && rule.time_max !== null) maxMig.value = rule.time_max;
            } else {
                const minAge = document.getElementById('dvd-min-age');
                const maxAge = document.getElementById('dvd-max-age');
                if (minAge && rule.time_min !== undefined && rule.time_min !== null) minAge.value = rule.time_min;
                if (maxAge && rule.time_max !== undefined && rule.time_max !== null) maxAge.value = rule.time_max;
            }
        } else if (type === 'market_cap') {
            const minMc = document.getElementById('dvd-min-mc');
            const maxMc = document.getElementById('dvd-max-mc');
            if (minMc && rule.time_min !== undefined && rule.time_min !== null) minMc.value = rule.time_min;
            if (maxMc && rule.time_max !== undefined && rule.time_max !== null) maxMc.value = rule.time_max;
        } else if (type === 'performance') {
            const tfClean = (rule.search_text || '5m').toLowerCase().replace('hr', 'h');
            const currentAppliedTf = perfTfEl ? perfTfEl.dataset.appliedTf : null;
            if (!currentAppliedTf || tfClean === '5m' || (tfClean === '1h' && currentAppliedTf !== '5m')) {
                if (perfTfEl && ['5m', '1h', '6h', '24h'].includes(tfClean)) {
                    perfTfEl.value = tfClean;
                    perfTfEl.dataset.appliedTf = tfClean;
                }
                const minP = document.getElementById('dvd-min-perf');
                const maxP = document.getElementById('dvd-max-perf');
                if (minP && rule.time_min !== undefined && rule.time_min !== null) minP.value = rule.time_min;
                if (maxP && rule.time_max !== undefined && rule.time_max !== null) maxP.value = rule.time_max;
            }
        } else if (type === 'liquidity') {
            const minL = document.getElementById('dvd-min-liquidity');
            const maxL = document.getElementById('dvd-max-liquidity');
            if (minL && rule.time_min !== undefined && rule.time_min !== null) minL.value = rule.time_min;
            if (maxL && rule.time_max !== undefined && rule.time_max !== null) maxL.value = rule.time_max;
        } else if (type === 'volume') {
            const tf = (rule.search_text || '24h').toLowerCase().replace('hr', 'h');
            const tfEl = document.getElementById('dvd-volume-tf');
            const minV = document.getElementById('dvd-min-volume');
            const maxV = document.getElementById('dvd-max-volume');
            if (tfEl && ['5m', '1h', '6h', '24h'].includes(tf)) tfEl.value = tf;
            if (minV && rule.time_min !== undefined && rule.time_min !== null) minV.value = rule.time_min;
            if (maxV && rule.time_max !== undefined && rule.time_max !== null) maxV.value = rule.time_max;
        } else if (type === 'migrated') {
            if (migratedEl) migratedEl.value = (rule.search_text || 'yes').toLowerCase();
        } else if (type === 'exclude_platform') {
            if (excludePlatformEl) {
                const p = (rule.search_text || '').toLowerCase();
                if (p.includes('pump')) excludePlatformEl.value = 'pump';
                else if (p.includes('raydium')) excludePlatformEl.value = 'raydium';
                else if (p.includes('moon')) excludePlatformEl.value = 'moonshot';
            }
        } else if (type === 'token_name') {
            const patEl = document.getElementById('dvd-token-name-pattern');
            if (patEl) patEl.value = rule.search_text || '.*';
            if (nameModeEl) nameModeEl.value = (rule.replace_text || 'include').toLowerCase();
        } else if (type === 'filter') {
            const wfEl = document.getElementById('dvd-word-filter');
            if (wfEl) wfEl.value = rule.search_text || '';
        } else if (type === 'category' && categoryEl) {
            const catVal = (rule.search_text || '').toLowerCase();
            if (catVal === 'cto' || catVal === 'boost' || catVal === 'dex_profile' || catVal === 'dex_ad') {
                categoryEl.value = catVal;
            }
        }
    });

    updateDexValidatedDataActiveFiltersCount();
    loadDexValidatedTableData(1);
}

function resetDexValidatedDataFilters() {
    const els = {
        'dvd-workflow-preset': '',
        'dvd-search': '',
        'dvd-category': 'all',
        'dvd-status': 'all',
        'dvd-platform': 'all',
        'dvd-exclude-platform': 'none',
        'dvd-timeframe': 'all',
        'dvd-sort-by': 'id_desc',
        'dvd-min-mc': '',
        'dvd-max-mc': '',
        'dvd-min-liquidity': '',
        'dvd-max-liquidity': '',
        'dvd-volume-tf': '24h',
        'dvd-min-volume': '',
        'dvd-max-volume': '',
        'dvd-perf-tf': '5m',
        'dvd-min-perf': '',
        'dvd-max-perf': '',
        'dvd-min-age': '',
        'dvd-max-age': '',
        'dvd-min-migration-age': '',
        'dvd-max-migration-age': '',
        'dvd-migrated': 'all',
        'dvd-token-name-mode': 'include',
        'dvd-token-name-pattern': '',
        'dvd-word-filter': '',
        'dvd-outcome-filter': 'all',
        'dvd-path-filter': 'all',
        'dvd-min-peak-1h': '',
        'dvd-min-peak-4h': '',
        'dvd-max-pre-dd': ''
    };
    for (const [id, val] of Object.entries(els)) {
        const el = document.getElementById(id);
        if (el) el.value = val;
    }
    updateDexValidatedDataActiveFiltersCount();
    loadDexValidatedTableData(1);
}

function toggleDexValidatedDataAutoRefresh() {
    const select = document.getElementById('dvd-autorefresh');
    let intervalMs = parseInt(select ? select.value : '3000', 10);
    if (isNaN(intervalMs) || intervalMs < 0) {
        intervalMs = 3000;
        if (select) select.value = '3000';
    }
    if (dexValidatedDataAutoRefreshInterval) {
        clearInterval(dexValidatedDataAutoRefreshInterval);
        dexValidatedDataAutoRefreshInterval = null;
    }
    if (intervalMs > 0) {
        dexValidatedDataAutoRefreshInterval = setInterval(() => {
            const dvdTab = document.getElementById('tab-dex-validated-data');
            if (dvdTab && !dvdTab.classList.contains('hidden')) {
                loadDexValidatedTableData(currentDexValidatedDataPage, false);
            }
        }, intervalMs);
    }
}

async function loadDexValidatedTableData(page = 1, showLoading = false) {
    currentDexValidatedDataPage = page;
    const tbody = document.getElementById('dex-validated-data-tbody');
    const tbodyText = tbody ? (tbody.innerText || tbody.textContent || '') : '';
    const hasRows = tbody && tbody.children && tbody.children.length > 0 && !tbodyText.includes('Connecting') && !tbodyText.includes('Loading');
    if (showLoading && tbody && !hasRows) {
        tbody.innerHTML = `
            <tr>
                <td colspan="15" style="text-align: center; padding: 3rem; color: var(--text-muted);">
                    <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                        <span>Loading enriched DEX Validated records...</span>
                    </div>
                </td>
            </tr>
        `;
    }

    try {
        const search = String(document.getElementById('dvd-search')?.value || '').trim();
        const category = document.getElementById('dvd-category')?.value || 'all';
        const status = document.getElementById('dvd-status')?.value || 'all';
        const platform = document.getElementById('dvd-platform')?.value || 'all';
        const excludePlatform = document.getElementById('dvd-exclude-platform')?.value || 'none';
        const timeframe = document.getElementById('dvd-timeframe')?.value || 'all';
        const sortVal = document.getElementById('dvd-sort-by')?.value || 'id_desc';
        const minMc = String(document.getElementById('dvd-min-mc')?.value || '').trim();
        const maxMc = String(document.getElementById('dvd-max-mc')?.value || '').trim();
        const minLiquidity = String(document.getElementById('dvd-min-liquidity')?.value || '').trim();
        const maxLiquidity = String(document.getElementById('dvd-max-liquidity')?.value || '').trim();
        const volumeTf = document.getElementById('dvd-volume-tf')?.value || '24h';
        const minVolume = String(document.getElementById('dvd-min-volume')?.value || '').trim();
        const maxVolume = String(document.getElementById('dvd-max-volume')?.value || '').trim();
        const perfTf = document.getElementById('dvd-perf-tf')?.value || '5m';
        const minPerf = String(document.getElementById('dvd-min-perf')?.value || '').trim();
        const maxPerf = String(document.getElementById('dvd-max-perf')?.value || '').trim();
        const minAge = String(document.getElementById('dvd-min-age')?.value || '').trim();
        const maxAge = String(document.getElementById('dvd-max-age')?.value || '').trim();
        const minMigrationAge = String(document.getElementById('dvd-min-migration-age')?.value || '').trim();
        const maxMigrationAge = String(document.getElementById('dvd-max-migration-age')?.value || '').trim();
        const migrated = document.getElementById('dvd-migrated')?.value || 'all';
        const tokenNamePattern = String(document.getElementById('dvd-token-name-pattern')?.value || '').trim();
        const tokenNameMode = document.getElementById('dvd-token-name-mode')?.value || 'include';
        const wordFilter = String(document.getElementById('dvd-word-filter')?.value || '').trim();
        const outcomeFilter = document.getElementById('dvd-outcome-filter')?.value || 'all';
        const pathFilter = document.getElementById('dvd-path-filter')?.value || 'all';
        const minPeak1h = String(document.getElementById('dvd-min-peak-1h')?.value || '').trim();
        const minPeak4h = String(document.getElementById('dvd-min-peak-4h')?.value || '').trim();
        const maxPreDd = String(document.getElementById('dvd-max-pre-dd')?.value || '').trim();
        const limit = document.getElementById('dvd-limit')?.value || '100';

        const lastIdx = sortVal.lastIndexOf('_');
        const sortBy = lastIdx !== -1 ? sortVal.slice(0, lastIdx) : sortVal;
        const sortOrder = lastIdx !== -1 ? sortVal.slice(lastIdx + 1) : 'desc';
        TABLE_SORT_STATE.dvd = { sortBy: sortBy || 'id', sortOrder: sortOrder || 'desc' };
        syncTableHeaderSortIndicators('dvd', sortBy || 'id', sortOrder || 'desc');

        const params = new URLSearchParams({
            page: page,
            limit: limit,
            search: search,
            category: category,
            status: status,
            platform: platform,
            exclude_platform: excludePlatform,
            timeframe: timeframe,
            sort_by: sortBy || 'id',
            sort_order: sortOrder || 'desc'
        });

        if (minMc) params.append('min_mc', minMc);
        if (maxMc) params.append('max_mc', maxMc);
        if (minLiquidity) params.append('min_liquidity', minLiquidity);
        if (maxLiquidity) params.append('max_liquidity', maxLiquidity);
        if (volumeTf) params.append('volume_tf', volumeTf);
        if (minVolume) params.append('min_volume', minVolume);
        if (maxVolume) params.append('max_volume', maxVolume);
        if (perfTf) params.append('perf_tf', perfTf);
        if (minPerf) params.append('min_perf', minPerf);
        if (maxPerf) params.append('max_perf', maxPerf);
        if (minAge) params.append('min_age', minAge);
        if (maxAge) params.append('max_age', maxAge);
        if (minMigrationAge) params.append('min_migration_age', minMigrationAge);
        if (maxMigrationAge) params.append('max_migration_age', maxMigrationAge);
        if (migrated && migrated !== 'all') params.append('migrated', migrated);
        if (tokenNamePattern) params.append('token_name_pattern', tokenNamePattern);
        if (tokenNameMode) params.append('token_name_mode', tokenNameMode);
        if (wordFilter) params.append('word_filter', wordFilter);
        if (outcomeFilter && outcomeFilter !== 'all') params.append('outcome_filter', outcomeFilter);
        if (pathFilter && pathFilter !== 'all') params.append('path_filter', pathFilter);
        if (minPeak1h) params.append('min_peak_1h', minPeak1h);
        if (minPeak4h) params.append('min_peak_4h', minPeak4h);
        if (maxPreDd) params.append('max_pre_dd', maxPreDd);

        updateDexValidatedDataActiveFiltersCount();

        const res = await fetch(`/api/dex-validated/records?${params.toString()}`, { cache: 'no-store' });
        const data = await res.json();

        const badge = document.getElementById('dvd-sync-badge');
        if (!data.success) {
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="15" style="text-align: center; color: #ef4444; padding: 2rem;">Error: ${escapeHtml(data.error || 'Failed to fetch data')}</td></tr>`;
            }
            return;
        }

        if (badge) {
            const wsCount = Number(data.ws_connected || 0);
            badge.innerHTML = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${wsCount > 0 ? '#34d399' : '#fbbf24'};box-shadow:0 0 6px ${wsCount > 0 ? '#10b981' : '#f59e0b'};"></span> <span>${wsCount > 0 ? `Live WebSocket (${wsCount}/4 Feeds)` : 'WebSocket Connecting...'}</span>`;
        }

        if (data.stats) {
            const kpiTotal = document.getElementById('dvd-kpi-total');
            const kpiForwarded = document.getElementById('dvd-kpi-forwarded');
            const kpiUnique = document.getElementById('dvd-kpi-unique');
            const kpiVolume = document.getElementById('dvd-kpi-volume');
            const breakdown = document.getElementById('dvd-category-breakdown');

            if (kpiTotal) kpiTotal.innerText = Number(data.stats.total || 0).toLocaleString();
            if (kpiForwarded) kpiForwarded.innerText = Number(data.stats.forwarded || 0).toLocaleString();
            if (kpiUnique) kpiUnique.innerText = Number(data.stats.unique_tokens || 0).toLocaleString();
            if (kpiVolume) {
                kpiVolume.innerText = formatUsdMetric(data.stats.total_volume || 0);
            }
            if (breakdown) {
                breakdown.innerHTML = `CTO: <strong style="color:#a78bfa;">${Number(data.stats.cto_count || 0).toLocaleString()}</strong> · Boost: <strong style="color:#fbbf24;">${Number(data.stats.boost_count || 0).toLocaleString()}</strong> · DEX: <strong style="color:#67e8f9;">${Number(data.stats.dex_count || 0).toLocaleString()}</strong>`;
            }
        }

        const records = data.records || [];
        if (records.length === 0) {
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="15" style="text-align: center; padding: 3.5rem; color: var(--text-muted);">
                            <div style="display:flex; justify-content:center; margin-bottom: 10px;">
                                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                            </div>
                            <div style="font-weight: 500; font-size: 0.95rem; color: white;">Waiting for DEX Validated events...</div>
                            <div style="font-size: 0.8rem; margin-top: 4px;">Tokens from DEX Screener WebSockets will be enriched and displayed here automatically as soon as they appear.</div>
                        </td>
                    </tr>
                `;
            }
            renderDexValidatedDataPagination(0, 0, page, 1);
            return;
        }

        if (tbody) {
            tbody.innerHTML = records.map(r => renderDexValidatedDataRow(r)).join('');
            applyTableColumnVisibility('dvd');
        }
        renderDexValidatedDataPagination(data.total, records.length, data.page, data.total_pages);
    } catch (err) {
        console.error('Failed to load DEX Validated table data:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="15" style="text-align: center; color: #ef4444; padding: 2rem;">Error loading data: ${escapeHtml(err.message)}</td></tr>`;
        }
    }
}

function renderDexValidatedDataRow(r) {
    const timeFormatted = formatRelativeTime(r.timestamp || r.created_at);
    const fullDate = r.timestamp ? new Date(r.timestamp * 1000).toLocaleString() : (r.created_at || '');
    const tokenName = r.token_name || r.name || 'Unknown';
    const ca = r.ca || '';
    const caShort = ca ? `${ca.slice(0, 4)}...${ca.slice(-4)}` : '-';
    const platform = r.platform ? r.platform.toUpperCase() : 'SOLANA';
    const isMigrated = Boolean(r.is_migrated);

    const cat = String(r.category_display || 'DEX PROFILE').toUpperCase();
    let catColor = '#67e8f9';
    let catBg = 'rgba(6, 182, 212, 0.15)';
    let catBorder = 'rgba(6, 182, 212, 0.35)';
    if (cat === 'CTO') {
        catColor = '#c4b5fd';
        catBg = 'rgba(139, 92, 246, 0.18)';
        catBorder = 'rgba(139, 92, 246, 0.4)';
    } else if (cat === 'BOOST') {
        catColor = '#fcd34d';
        catBg = 'rgba(245, 158, 11, 0.18)';
        catBorder = 'rgba(245, 158, 11, 0.4)';
    } else if (cat === 'DEX AD') {
        catColor = '#f472b6';
        catBg = 'rgba(236, 72, 153, 0.16)';
        catBorder = 'rgba(236, 72, 153, 0.38)';
    }

    const boostSub = r.boost_amount ? `⚡ ${r.boost_amount} Boosts` : (r.detection_seconds != null ? `⚡ ${Number(r.detection_seconds).toFixed(1)}s latency` : (r.order_status ? r.order_status.toUpperCase() : 'APPROVED'));

    const mc = r.market_cap || '-';
    const liquidity = formatUsdMetric(r.liquidity_usd);
    const volume5m = formatUsdMetric(r.volume_5m);
    const volume1h = formatUsdMetric(r.volume_1h);
    const volume6h = formatUsdMetric(r.volume_6h);
    const volume24h = formatUsdMetric(r.volume_24h);
    const creationAge = r.creation_age || r.age || '-';
    const migrationAge = r.migration_age && r.migration_age !== '0' && r.migration_age !== '-' ? r.migration_age : null;

    const perf5mBadge = formatPerfBadge(r.perf_5m);
    const perf1hBadge = formatPerfBadge(r.perf_1h);
    const perf6hBadge = formatPerfBadge(r.perf_6h);
    const perf24hBadge = formatPerfBadge(r.perf_24h);

    const statusPill = formatStatusPill(r.status);
    const reasonText = r.reason || '-';
    const reasonShort = reasonText.length > 35 ? `${reasonText.slice(0, 32)}...` : reasonText;

    const dexUrl = r.dex_url || (ca ? `https://dexscreener.com/solana/${ca}` : '');
    const solscanUrl = ca ? `https://solscan.io/token/${ca}` : '';
    const encodedMsg = encodeURIComponent(r.formatted_message || '');
    const encodedTokenName = encodeURIComponent(tokenName);

    return `
        <tr>
            <td title="${escapeHtml(fullDate)}">
                <div style="color: var(--text-main); font-weight: 500;">${escapeHtml(timeFormatted)}</div>
                <div style="font-size: 0.72rem; color: var(--text-muted);">${escapeHtml(fullDate.split(',')[0])}</div>
            </td>
            <td>
                <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                    <span style="font-weight: 600; color: white; font-size: 0.9rem;">${escapeHtml(tokenName)}</span>
                    <span class="badge-platform">${escapeHtml(platform)}</span>
                    ${isMigrated ? '<span style="background: rgba(16,185,129,0.15); color: #10b981; border: 1px solid rgba(16,185,129,0.3); padding: 1px 4px; border-radius: 4px; font-size: 0.68rem;">Raydium</span>' : ''}
                </div>
            </td>
            <td>
                ${ca ? `
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span class="code-address" onclick="copyTextToClipboard('${ca}', this)" title="Click to copy full CA">${caShort}</span>
                        <a href="${dexUrl}" target="_blank" title="View Chart on DexScreener" style="color: #60a5fa; text-decoration: none; display: inline-flex; align-items: center;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></a>
                        <a href="${solscanUrl}" target="_blank" title="View on Solscan" style="color: var(--text-muted); text-decoration: none; display: inline-flex; align-items: center;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>
                    </div>
                ` : '<span style="color: var(--text-muted);">-</span>'}
            </td>
            <td>
                <span style="display:inline-block; background:${catBg}; color:${catColor}; border:1px solid ${catBorder}; padding:2px 7px; border-radius:5px; font-size:0.74rem; font-weight:700;">${escapeHtml(cat)}</span>
                <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 3px;">${escapeHtml(boostSub)}</div>
            </td>
            <td><strong style="color: #e2e8f0;">${escapeHtml(mc)}</strong></td>
            <td><strong style="color: #2dd4bf;">${liquidity}</strong></td>
            <td title="5m: ${volume5m}, 1h: ${volume1h}, 6h: ${volume6h}, 24h: ${volume24h}">
                <div style="font-size:0.74rem; white-space:nowrap;">5m ${volume5m} · 1h ${volume1h}</div>
                <div style="font-size:0.74rem; color:var(--text-muted); white-space:nowrap;">6h ${volume6h} · 24h ${volume24h}</div>
            </td>
            <td>
                <div style="font-weight: 500; color: #e2e8f0; font-size: 0.85rem;" title="Creation Age: ${escapeHtml(creationAge)}">${escapeHtml(creationAge)}</div>
                ${migrationAge ? `<div style="font-size: 0.72rem; color: #34d399; margin-top: 1px;" title="Migration Age: ${escapeHtml(migrationAge)}">Mig: ${escapeHtml(migrationAge)}</div>` : ''}
            </td>
            <td>${perf5mBadge}</td>
            <td>${perf1hBadge}</td>
            <td>${perf6hBadge}</td>
            <td>${perf24hBadge}</td>
            ${formatPeakPerformanceCell(r, '1h')}
            ${formatPeakPerformanceCell(r, '4h')}
            <td>${statusPill}</td>
            ${formatForwardedFlowsCell(r.status, r.reason)}
            <td style="text-align: right; padding-right: 1.5rem; white-space: nowrap;">
                <button type="button" class="btn-table-action" onclick="openTgMsgModal('${encodedMsg}', '${encodedTokenName}')" title="View Formatted Signal Details" style="margin-right: 6px; cursor: pointer;">
                    Details
                </button>
                ${dexUrl ? `
                    <a href="${dexUrl}" target="_blank" class="btn-table-action" title="Open DexScreener Chart">
                        Chart
                    </a>
                ` : ''}
            </td>
        </tr>
    `;
}

function renderDexValidatedDataPagination(total, count, page, totalPages) {
    const infoEl = document.getElementById('dvd-pagination-info');
    const controlsEl = document.getElementById('dvd-pagination-controls');

    if (infoEl) {
        const limitVal = parseInt(document.getElementById('dvd-limit')?.value || '100', 10);
        const start = total === 0 ? 0 : (page - 1) * limitVal + 1;
        const end = total === 0 ? 0 : Math.min(total, start + count - 1);
        infoEl.innerHTML = `Showing <strong>${start.toLocaleString()}-${end.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> records`;
    }

    if (!controlsEl) return;

    let html = '';
    html += `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="loadDexValidatedTableData(${page - 1})">← Prev</button>`;

    const maxButtons = 5;
    let startPage = Math.max(1, page - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);
    if (endPage - startPage + 1 < maxButtons) {
        startPage = Math.max(1, endPage - maxButtons + 1);
    }

    if (startPage > 1) {
        html += `<button class="page-btn" onclick="loadDexValidatedTableData(1)">1</button>`;
        if (startPage > 2) html += `<span style="color: var(--text-muted); padding: 0 4px;">...</span>`;
    }

    for (let p = startPage; p <= endPage; p++) {
        html += `<button class="page-btn ${p === page ? 'active' : ''}" onclick="loadDexValidatedTableData(${p})">${p}</button>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += `<span style="color: var(--text-muted); padding: 0 4px;">...</span>`;
        html += `<button class="page-btn" onclick="loadDexValidatedTableData(${totalPages})">${totalPages}</button>`;
    }

    html += `<button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="loadDexValidatedTableData(${page + 1})">Next →</button>`;
    controlsEl.innerHTML = html;
}

async function exportDexValidatedDataCSV() {
    try {
        const res = await fetch('/api/dex-validated/records?page=1&limit=10000', { cache: 'no-store' });
        const data = await res.json();
        if (!data.success || !data.records || data.records.length === 0) {
            alert('No DEX Validated records available to export.');
            return;
        }
        const headers = ['ID', 'Time', 'Token Name', 'CA', 'Category', 'Platform', 'Market Cap', 'Liquidity USD', 'Vol 5m', 'Vol 1h', 'Vol 6h', 'Vol 24h', 'Creation Age', 'Migration Age', 'Perf 5m %', 'Perf 1h %', 'Perf 6h %', 'Perf 24h %', '1h Peak Mult', '1h Max MC', '1h Min MC', '1h Pre-Peak Low MC', '1h Pre-Peak DD %', '1h Path', '4h Peak Mult', '4h Max MC', '4h Min MC', '4h Pre-Peak Low MC', '4h Pre-Peak DD %', '4h Path', 'Status', 'Reason', 'DEX URL'];
        const csvRows = [headers.join(',')];
        data.records.forEach(r => {
            const vals = [
                r.id,
                r.timestamp ? new Date(r.timestamp * 1000).toISOString() : (r.created_at || ''),
                `"${String(r.token_name || '').replace(/"/g, '""')}"`,
                r.ca || '',
                r.category_display || '',
                `"${String(r.platform || '').replace(/"/g, '""')}"`,
                `"${String(r.market_cap || '').replace(/"/g, '""')}"`,
                r.liquidity_usd || 0,
                r.volume_5m || 0,
                r.volume_1h || 0,
                r.volume_6h || 0,
                r.volume_24h || 0,
                `"${String(r.creation_age || '').replace(/"/g, '""')}"`,
                `"${String(r.migration_age || '').replace(/"/g, '""')}"`,
                r.perf_5m || 0,
                r.perf_1h || 0,
                r.perf_6h || 0,
                r.perf_24h || 0,
                r.peak_mult_1h ?? '',
                r.max_mc_1h ?? '',
                r.min_mc_1h ?? '',
                r.pre_peak_low_mc_1h ?? '',
                r.pre_peak_dd_1h_pct ?? '',
                r.path_1h || '',
                r.peak_mult_4h ?? '',
                r.max_mc_4h ?? '',
                r.min_mc_4h ?? '',
                r.pre_peak_low_mc_4h ?? '',
                r.pre_peak_dd_4h_pct ?? '',
                r.path_4h || '',
                r.status || '',
                `"${String(r.reason || '').replace(/"/g, '""')}"`,
                r.dex_url || ''
            ];
            csvRows.push(vals.join(','));
        });
        const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dex_validated_data_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    } catch (e) {
        alert('Failed to export CSV: ' + e.message);
    }
}

async function loadDashboardOverview(showToast = false) {
    try {
        const res = await fetch('/api/dashboard/overview', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (!data.success) return;

        const sys = data.system || {};
        const dv = data.dex_validated || {};
        const dp = data.dex_paid || {};

        const setEl = (id, html, isHtml = false) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (isHtml) el.innerHTML = html;
            else el.textContent = html;
        };

        const wsCount = Number(sys.ws_connected || 0);
        setEl('dash-ws-badge', `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${wsCount > 0 ? '#34d399' : '#fbbf24'};box-shadow:0 0 8px ${wsCount > 0 ? '#10b981' : '#f59e0b'};"></span><span>${wsCount}/4 WebSockets Live</span>`, true);
        setEl('dash-sol-price', sys.sol_price ? `$${Number(sys.sol_price).toFixed(2)}` : '$—');

        setEl('dash-sys-ws', `${wsCount}/4 Feeds · ${sys.dex_validated_auto ? 'Auto Forward ON' : 'Stream Capture ON'}`);
        setEl('dash-sys-paid', sys.helius_configured ? (sys.dex_paid_auto ? 'Live Scan Active' : 'Helius Ready') : 'API Key Needed');
        setEl('dash-sys-workflows', `${Number(sys.active_workflows || 0)} Active / ${Number(sys.total_workflows || 0)} Total`);
        setEl('dash-sys-cloud', sys.supabase_connected ? 'Supabase Synced' : 'Local SQLite Mode');

        const dvTotal = Number(dv.total || 0);
        const dpTotal = Number(dp.total || 0);
        const combinedTotal = dvTotal + dpTotal;
        const combinedForwarded = Number(dv.forwarded || 0) + Number(dp.forwarded || 0);
        const combinedVol = Number(dv.total_volume_24h || 0) + Number(dp.total_volume_24h || 0);
        const passRate = combinedTotal > 0 ? ((combinedForwarded / combinedTotal) * 100).toFixed(1) : '0.0';
        const avgLiq = dvTotal > 0 ? Number(dv.avg_liquidity_usd || 0) : Number(dp.avg_liquidity_usd || 0);

        setEl('dash-kpi-dv-total', dvTotal.toLocaleString());
        setEl('dash-kpi-dv-sub', `${Number(dv.unique_tokens || 0).toLocaleString()} Unique · ${formatUsdMetric(dv.total_volume_24h || 0)} 24h Vol`);

        setEl('dash-kpi-dp-total', dpTotal.toLocaleString());
        setEl('dash-kpi-dp-sub', `${formatUsdMetric(dp.total_paid_usd || 0)} Spend · ${Number(dp.unique_tokens || 0).toLocaleString()} Unique`);

        setEl('dash-kpi-forwarded', combinedForwarded.toLocaleString());
        setEl('dash-kpi-pass-rate', `${passRate}% pass rate · ${Number(dv.detected || 0).toLocaleString()} detected`);

        setEl('dash-kpi-combined-vol', formatUsdMetric(combinedVol));
        setEl('dash-kpi-avg-liq', `Avg Liquidity: ${formatUsdMetric(avgLiq)}`);

        setEl('dash-total-combined-pill', `${combinedTotal.toLocaleString()} total signals`);

        // Category Distribution Bars
        const distEl = document.getElementById('dash-distribution-bars');
        if (distEl) {
            const items = [
                { label: 'CTO (Community Takeover)', count: Number(dv.cto_count || 0), color: '#a78bfa', tab: 'dex-validated-data', cat: 'cto' },
                { label: 'Token Boosts', count: Number(dv.boost_count || 0), color: '#fbbf24', tab: 'dex-validated-data', cat: 'boost' },
                { label: 'DEX Profiles', count: Number(dv.profile_count || 0), color: '#34d399', tab: 'dex-validated-data', cat: 'dex_profile' },
                { label: 'DEX Ads', count: Number(dv.ad_count || 0), color: '#f472b6', tab: 'dex-validated-data', cat: 'dex_ad' },
                { label: 'DEX Paid (Helius Wallet)', count: dpTotal, color: '#22d3ee', tab: 'token-data', cat: null }
            ];
            const maxVal = Math.max(1, ...items.map(i => i.count));
            distEl.innerHTML = items.map(item => {
                const pct = combinedTotal > 0 ? ((item.count / combinedTotal) * 100).toFixed(1) : '0.0';
                const barWidth = Math.max(3, Math.round((item.count / maxVal) * 100));
                return `
                    <div style="cursor:pointer;" onclick="openDashboardCategory('${item.tab}', '${item.cat || ''}')">
                        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:5px;">
                            <span style="color:#e5e7eb; font-weight:500;">${escapeHtml(item.label)}</span>
                            <span style="color:var(--text-muted);"><strong style="color:white;">${item.count.toLocaleString()}</strong> (${pct}%)</span>
                        </div>
                        <div style="width:100%; height:8px; background:rgba(255,255,255,0.05); border-radius:999px; overflow:hidden;">
                            <div style="width:${barWidth}%; height:100%; background:${item.color}; border-radius:999px; transition:width 0.35s ease;"></div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        const totalMigYes = Number(dv.migrated_count || 0) + Number(dp.migrated_count || 0);
        const totalMigNo = Number(dv.unmigrated_count || 0) + Number(dp.unmigrated_count || 0);
        setEl('dash-mig-yes', totalMigYes.toLocaleString());
        setEl('dash-mig-no', totalMigNo.toLocaleString());
        setEl('dash-avg-mc', formatUsdMetric(dv.avg_mc_usd || 0));

        // Top Momentum Movers
        const moversEl = document.getElementById('dash-top-movers');
        const movers = data.top_movers || [];
        if (moversEl) {
            if (!movers.length) {
                moversEl.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem;padding:2rem;text-align:center;">Waiting for enriched tokens...</div>';
            } else {
                moversEl.innerHTML = movers.map(m => {
                    const p1h = Number(m.perf_1h || 0);
                    const p5m = Number(m.perf_5m || 0);
                    const p1hColor = p1h >= 0 ? '#34d399' : '#f87171';
                    const p5mColor = p5m >= 0 ? '#34d399' : '#f87171';
                    const caShort = m.ca ? `${m.ca.slice(0, 4)}...${m.ca.slice(-4)}` : '-';
                    const encMsg = encodeURIComponent(m.formatted_message || '');
                    const encName = encodeURIComponent(m.token_name || 'Token');
                    return `
                        <div style="background:rgba(255,255,255,0.025); border:1px solid var(--border-color); border-radius:11px; padding:11px 13px; display:flex; flex-direction:column; gap:6px;">
                            <div style="display:flex; justify-content:space-between; align-items:center; gap:6px;">
                                <strong style="color:white; font-size:0.88rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(m.token_name)}</strong>
                                <span style="font-size:0.66rem; font-weight:700; padding:2px 6px; border-radius:4px; background:rgba(6,182,212,0.14); color:#67e8f9; white-space:nowrap;">${escapeHtml(m.source || 'SIGNAL')}</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; font-size:0.76rem; color:var(--text-muted);">
                                <span>MC: <strong style="color:#e2e8f0;">${escapeHtml(m.market_cap || '-')}</strong></span>
                                <span>Liq: <strong style="color:#2dd4bf;">${formatUsdMetric(m.liquidity_usd)}</strong></span>
                            </div>
                            <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.76rem; padding-top:4px; border-top:1px solid rgba(255,255,255,0.05);">
                                <span>5m: <strong style="color:${p5mColor};">${p5m >= 0 ? '+' : ''}${p5m.toFixed(1)}%</strong> · 1h: <strong style="color:${p1hColor};">${p1h >= 0 ? '+' : ''}${p1h.toFixed(1)}%</strong></span>
                                <div style="display:flex; gap:6px;">
                                    <button type="button" onclick="openTgMsgModal('${encMsg}', '${encName}')" style="background:none;border:none;color:#93c5fd;font-size:0.73rem;cursor:pointer;padding:0;">Details</button>
                                    <a href="${escapeHtml(m.dex_url || '#')}" target="_blank" style="color:#34d399;font-size:0.73rem;text-decoration:none;" title="${escapeHtml(caShort)}">Chart ↗</a>
                                </div>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        }

        // Render Dual Live Streams
        const renderStreamRow = (item, isPaid) => {
            const timeStr = formatRelativeTime(item.timestamp || item.created_at);
            const p5m = Number(item.perf_5m || 0);
            const p1h = Number(item.perf_1h || 0);
            const p5mColor = p5m >= 0 ? '#34d399' : '#f87171';
            const p1hColor = p1h >= 0 ? '#34d399' : '#f87171';
            const badgeText = isPaid ? `$${Number(item.amount_usd || 0).toFixed(0)} PAID` : (item.category_display || 'DEX');
            const badgeBg = isPaid ? 'rgba(6,182,212,0.15)' : (badgeText === 'CTO' ? 'rgba(139,92,246,0.18)' : badgeText === 'BOOST' ? 'rgba(245,158,11,0.18)' : 'rgba(16,185,129,0.16)');
            const badgeColor = isPaid ? '#67e8f9' : (badgeText === 'CTO' ? '#c4b5fd' : badgeText === 'BOOST' ? '#fcd34d' : '#34d399');
            const encMsg = encodeURIComponent(item.formatted_message || '');
            const encName = encodeURIComponent(item.token_name || 'Token');
            const ca = item.ca || '';
            const caShort = ca ? `${ca.slice(0, 4)}...${ca.slice(-4)}` : '-';

            return `
                <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:9px 12px; border-radius:9px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05);">
                    <div style="min-width:0; flex:1.2;">
                        <div style="display:flex; align-items:center; gap:7px;">
                            <strong style="color:white; font-size:0.86rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(item.token_name)}</strong>
                            <span style="font-size:0.66rem; font-weight:700; padding:1px 6px; border-radius:4px; background:${badgeBg}; color:${badgeColor}; white-space:nowrap;">${escapeHtml(badgeText)}</span>
                        </div>
                        <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px; display:flex; gap:8px; align-items:center;">
                            <span>${escapeHtml(timeStr)}</span>
                            <span>·</span>
                            <span class="code-address" onclick="copyTextToClipboard('${ca}', this)" style="font-size:0.7rem; padding:0 4px; cursor:pointer;" title="Copy CA">${escapeHtml(caShort)}</span>
                            <span>· Age ${escapeHtml(item.age || '-')}</span>
                        </div>
                    </div>
                    <div style="text-align:right; font-size:0.76rem; white-space:nowrap;">
                        <div>MC <strong style="color:#e2e8f0;">${escapeHtml(item.market_cap || '-')}</strong> · Liq <strong style="color:#2dd4bf;">${formatUsdMetric(item.liquidity_usd)}</strong></div>
                        <div style="margin-top:2px;">5m <strong style="color:${p5mColor};">${p5m >= 0 ? '+' : ''}${p5m.toFixed(1)}%</strong> · 1h <strong style="color:${p1hColor};">${p1h >= 0 ? '+' : ''}${p1h.toFixed(1)}%</strong></div>
                    </div>
                    <div style="display:flex; gap:6px; align-items:center;">
                        <button type="button" class="btn-table-action" onclick="openTgMsgModal('${encMsg}', '${encName}')" style="padding:4px 8px; font-size:0.72rem; cursor:pointer;">Details</button>
                        <a href="${escapeHtml(item.dex_url || '#')}" target="_blank" class="btn-table-action" style="padding:4px 8px; font-size:0.72rem;">Chart</a>
                    </div>
                </div>
            `;
        };

        const dvStreamEl = document.getElementById('dash-dv-stream');
        if (dvStreamEl) {
            const dvList = data.dv_recent || [];
            dvStreamEl.innerHTML = dvList.length
                ? dvList.map(item => renderStreamRow(item, false)).join('')
                : '<div style="color:var(--text-muted);font-size:0.85rem;padding:2rem;text-align:center;">Waiting for DEX Validated WebSocket signals...</div>';
        }

        const dpStreamEl = document.getElementById('dash-dp-stream');
        if (dpStreamEl) {
            const dpList = data.dp_recent || [];
            dpStreamEl.innerHTML = dpList.length
                ? dpList.map(item => renderStreamRow(item, true)).join('')
                : '<div style="color:var(--text-muted);font-size:0.85rem;padding:2rem;text-align:center;">No DEX Paid records yet.</div>';
        }

        if (showToast) {
            showAppToast('Dashboard insights refreshed.', 'success', 1800);
        }
    } catch (err) {
        console.error('Error loading dashboard overview:', err);
    }
}

function openDashboardCategory(tabId, category) {
    if (tabId === 'dex-validated-data' && category) {
        const catEl = document.getElementById('dvd-category');
        if (catEl) catEl.value = category;
    }
    switchTab(tabId);
}

toggleDexValidatedDataAutoRefresh();
loadDexValidatedTableData(1, false);
loadDexValidatedLiveResults();
loadDashboardOverview(false);
setInterval(() => {
    const dashTab = document.getElementById('tab-dashboard');
    if (dashTab && !dashTab.classList.contains('hidden')) {
        loadDashboardOverview(false);
    }
}, 4000);

const initialUrlTab = getTabFromPathname(window.location.pathname) || 'dashboard';
switchTab(initialUrlTab, { skipPushState: true });
