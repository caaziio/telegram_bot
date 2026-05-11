"use client";
import React, { useState } from 'react';

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('workflows');
  const [showModal, setShowModal] = useState(false);
  const [workflows, setWorkflows] = useState([
    {
      id: 1,
      name: 'Solana CA Extractor',
      source_channel: '@SourceSignals',
      source_channel_id: '',
      target_channel: '@MyVipGroup',
      target_channel_id: '',
      is_active: true,
      rules: [
        { type: 'extract_ca' },
        { type: 'token_age', min: 0, max: 60 }
      ]
    }
  ]);
  const [editingFlow, setEditingFlow] = useState(null);
  
  // Settings State
  const [settings, setSettings] = useState({
    api_id: '',
    api_hash: '',
    phone: ''
  });

  // Tester State
  const [testerInput, setTesterInput] = useState('🤖 AI Solana CTO\n🏷️ Pandamic | PANDAMIC\n\n📌 CA ETZKRf2VWzQKi5X3Lr8CzpLHREB5jRB1SWUGzXNGpump\n\n🏛️ Dex: Pumpswap\n📈 24h Volume: $363.16K\n📊 Market Cap: $21.76K\n💧 Liquidity: $11.76K\n⏳ Token Age: 1h 46m\n\n⚡ Boost: ✅ 10\n🌐 Socials: TWT\n🔗 Links: DEXS | BIRDEYE | DEXT\n🔍 Checks: RUG CHECK | ORDER STATUS');
  const [testerTime, setTesterTime] = useState('12:00');
  const [testerWorkflowId, setTesterWorkflowId] = useState(1);
  const [testerResult, setTesterResult] = useState('');
  const [testerStatus, setTesterStatus] = useState('');

  const handleEdit = (flow) => {
    setEditingFlow({ ...flow, rules: [...flow.rules] });
    setShowModal(true);
  };

  const handleAddNew = () => {
    setEditingFlow({
      name: '',
      source_channel: '',
      source_channel_id: '',
      target_channel: '',
      target_channel_id: '',
      is_active: true,
      rules: []
    });
    setShowModal(true);
  };

  const handleSaveFlow = (e) => {
    e.preventDefault();
    if (editingFlow.id) {
      setWorkflows(workflows.map(w => w.id === editingFlow.id ? editingFlow : w));
    } else {
      setWorkflows([...workflows, { ...editingFlow, id: Date.now() }]);
      setTesterWorkflowId(Date.now());
    }
    setShowModal(false);
  };

  const handleSaveSettings = (e) => {
    e.preventDefault();
    alert('Settings saved successfully!');
  };

  const addRule = (type) => {
    let newRule = { type };
    if (type === 'token_age') newRule = { type, min: 0, max: 60 };
    if (type === 'replace') newRule = { type, search: '', replace: '' };
    if (type === 'filter') newRule = { type, search: '' };
    
    setEditingFlow({
      ...editingFlow,
      rules: [...editingFlow.rules, newRule]
    });
  };

  const updateRule = (index, key, value) => {
    const newRules = [...editingFlow.rules];
    newRules[index][key] = value;
    setEditingFlow({ ...editingFlow, rules: newRules });
  };

  const removeRule = (index) => {
    const newRules = [...editingFlow.rules];
    newRules.splice(index, 1);
    setEditingFlow({ ...editingFlow, rules: newRules });
  };

  const runTester = () => {
    const wf = workflows.find(w => w.id === testerWorkflowId);
    if (!wf) {
      setTesterResult('Please select a workflow.');
      setTesterStatus('error');
      return;
    }

    let processedText = testerInput;
    let dropped = false;
    let dropReason = '';

    for (const rule of wf.rules) {
      if (rule.type === 'token_age') {
        const ageMatch = processedText.match(/Token Age:\s*(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?/i);
        let tokenMinutes = 0;
        if (ageMatch) {
          const d = parseInt(ageMatch[1] || '0');
          const h = parseInt(ageMatch[2] || '0');
          const m = parseInt(ageMatch[3] || '0');
          tokenMinutes = (d * 1440) + (h * 60) + m;
        }
        
        if (tokenMinutes < Number(rule.min) || tokenMinutes > Number(rule.max)) {
          dropped = true;
          dropReason = `Dropped by Token Age Filter (Allowed: ${rule.min}-${rule.max}m, Found: ${tokenMinutes}m)`;
          break;
        }
      } 

      else if (rule.type === 'filter') {
        if (rule.search && processedText.toLowerCase().includes(rule.search.toLowerCase())) {
          dropped = true;
          dropReason = `Dropped by Word Filter (Found forbidden word: "${rule.search}")`;
          break;
        }
      }
      else if (rule.type === 'replace') {
        if (rule.search) {
          processedText = processedText.split(rule.search).join(rule.replace || '');
        }
      }
      else if (rule.type === 'extract_ca') {
        // Regex to find "CA" followed by spaces/colons and then a base58 solana address or generic address
        const caRegex = /(?:CA|Contract|ca)[\s:]*([1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40})/i;
        const match = processedText.match(caRegex);
        if (match && match[1]) {
          processedText = match[1];
        } else {
          dropped = true;
          dropReason = `Dropped by Extract CA (No Contract Address found in text)`;
          break;
        }
      }
    }

    if (dropped) {
      setTesterStatus('dropped');
      setTesterResult(dropReason);
    } else {
      setTesterStatus('success');
      setTesterResult(processedText);
    }
  };

  return (
    <div className="dashboard-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo-container">
          <div className="logo-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13"></path>
              <path d="M22 2l-7 20-4-9-9-4 20-7z"></path>
            </svg>
          </div>
          <div className="logo-text">TeleFlow</div>
        </div>

        <nav>
          <div className={`nav-link ${activeTab === 'workflows' ? 'active' : ''}`} onClick={() => setActiveTab('workflows')}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
            Workflows
          </div>
          <div className={`nav-link ${activeTab === 'tester' ? 'active' : ''}`} onClick={() => setActiveTab('tester')}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
            Flow Tester
          </div>
          <div className={`nav-link ${activeTab === 'settings' ? 'active' : ''}`} onClick={() => setActiveTab('settings')}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            Settings
          </div>
        </nav>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {activeTab === 'workflows' && (
          <>
            <div className="header">
              <div className="header-title">
                <h1>Your Workflows</h1>
                <p>Manage your automated channel routing and filters</p>
              </div>
              <button className="btn-primary" onClick={handleAddNew}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                Add Flow
              </button>
            </div>

            <div className="workflows-grid">
              {workflows.map(wf => (
                <div className="workflow-card" key={wf.id}>
                  <div className="workflow-header">
                    <div className="workflow-title">{wf.name || 'Unnamed Flow'}</div>
                    <div className="status-badge" style={!wf.is_active ? {background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444'} : {}}>
                      {wf.is_active ? 'Active' : 'Paused'}
                    </div>
                  </div>
                  
                  <div className="workflow-flow">
                    <div className="channel-tag">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                      {wf.source_channel || wf.source_channel_id || 'Not set'}
                    </div>
                    <div className="flow-arrow">
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                    </div>
                    <div className="channel-tag">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                      {wf.target_channel || wf.target_channel_id || 'Not set'}
                    </div>
                  </div>

                  <div className="workflow-footer">
                    <div className="rules-count">{wf.rules.length} Active Rules/Filters</div>
                    <div style={{display: 'flex', gap: '8px'}}>
                      <button className="btn-icon" onClick={() => handleEdit(wf)}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {activeTab === 'tester' && (
          <div className="tester-panel">
            <div className="header">
              <div className="header-title">
                <h1>Flow Tester</h1>
                <p>Simulate how your bot will process incoming messages before saving.</p>
              </div>
            </div>

            <div style={{display: 'flex', gap: '2rem'}}>
              {/* Input Area */}
              <div style={{flex: 1}}>
                <div className="workflow-card">
                  <h3 style={{marginBottom: '1rem', fontSize: '1rem', fontWeight: 600}}>1. Setup Simulation</h3>
                  
                  <div className="form-group">
                    <label>Select Workflow Rules to Apply</label>
                    <select 
                      className="form-input" 
                      style={{WebkitAppearance: 'none'}}
                      value={testerWorkflowId}
                      onChange={e => setTesterWorkflowId(Number(e.target.value))}
                    >
                      {workflows.map(wf => (
                        <option key={wf.id} value={wf.id}>{wf.name} ({wf.rules.length} rules)</option>
                      ))}
                    </select>
                  </div>

                  <div className="form-group">
                    <label>Simulated Message Time</label>
                    <input 
                      type="time" 
                      className="form-input" 
                      value={testerTime}
                      onChange={e => setTesterTime(e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label>Original Telegram Message</label>
                    <textarea 
                      className="form-input" 
                      style={{height: '200px', resize: 'vertical', fontFamily: 'monospace', fontSize: '0.85rem'}}
                      value={testerInput}
                      onChange={e => setTesterInput(e.target.value)}
                    />
                  </div>

                  <button className="btn-primary" style={{width: '100%', justifyContent: 'center'}} onClick={runTester}>
                    Process Message
                  </button>
                </div>
              </div>

              {/* Output Area */}
              <div style={{flex: 1}}>
                <div className="workflow-card" style={{height: '100%', display: 'flex', flexDirection: 'column'}}>
                  <h3 style={{marginBottom: '1rem', fontSize: '1rem', fontWeight: 600}}>2. Processed Result</h3>
                  
                  {!testerStatus ? (
                    <div style={{flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)'}}>
                      Click Process to see the result
                    </div>
                  ) : (
                    <div style={{flex: 1}}>
                      {testerStatus === 'dropped' ? (
                        <div style={{background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '1rem', borderRadius: '8px', color: '#fca5a5'}}>
                          <div style={{fontWeight: 'bold', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px'}}>
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>
                            Message Dropped
                          </div>
                          {testerResult}
                        </div>
                      ) : (
                        <div style={{background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.3)', padding: '1rem', borderRadius: '8px', height: '100%'}}>
                          <div style={{fontWeight: 'bold', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px', color: '#6ee7b7'}}>
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                            Message Forwarded Successfully
                          </div>
                          <div style={{fontFamily: 'monospace', fontSize: '0.85rem', whiteSpace: 'pre-wrap', color: 'white', background: 'rgba(0,0,0,0.3)', padding: '1rem', borderRadius: '6px'}}>
                            {testerResult}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'settings' && (
          <div className="settings-panel">
            <div className="header">
              <div className="header-title">
                <h1>Telegram Connection</h1>
                <p>Enter your API details to connect the userbot</p>
              </div>
            </div>
            
            <div className="workflow-card" style={{maxWidth: '600px'}}>
              <form onSubmit={handleSaveSettings}>
                <div className="form-group">
                  <label>API ID</label>
                  <input 
                    className="form-input" 
                    value={settings.api_id} 
                    onChange={e => setSettings({...settings, api_id: e.target.value})}
                    placeholder="e.g. 1234567" 
                    required 
                  />
                  <small style={{color: 'var(--text-muted)', fontSize: '0.8rem', display: 'block', marginTop: '4px'}}>
                    Get this from my.telegram.org
                  </small>
                </div>
                <div className="form-group">
                  <label>API Hash</label>
                  <input 
                    className="form-input" 
                    value={settings.api_hash} 
                    onChange={e => setSettings({...settings, api_hash: e.target.value})}
                    placeholder="e.g. abcdef1234567890" 
                    required 
                  />
                </div>
                <div className="form-group">
                  <label>Phone Number</label>
                  <input 
                    className="form-input" 
                    value={settings.phone} 
                    onChange={e => setSettings({...settings, phone: e.target.value})}
                    placeholder="e.g. +1234567890" 
                  />
                </div>
                <button type="submit" className="btn-primary">Save Connection Settings</button>
              </form>
            </div>
          </div>
        )}
      </main>

      {/* Flow Editor Modal */}
      {showModal && editingFlow && (
        <div className="modal-overlay">
          <div className="modal-content" style={{maxHeight: '90vh', overflowY: 'auto'}}>
            <div className="header" style={{marginBottom: '1.5rem'}}>
              <h2>{editingFlow.id ? 'Edit Workflow' : 'New Workflow'}</h2>
              <button className="btn-icon" onClick={() => setShowModal(false)}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>

            <form onSubmit={handleSaveFlow}>
              <div className="form-group">
                <label>Workflow Name</label>
                <input 
                  className="form-input" 
                  value={editingFlow.name}
                  onChange={e => setEditingFlow({...editingFlow, name: e.target.value})}
                  placeholder="e.g. VIP Signals Forwarder" 
                  required 
                />
              </div>

              <div style={{display: 'flex', gap: '1rem'}}>
                <div className="form-group" style={{flex: 1}}>
                  <label>Source Channel (Username)</label>
                  <input 
                    className="form-input" 
                    value={editingFlow.source_channel}
                    onChange={e => setEditingFlow({...editingFlow, source_channel: e.target.value})}
                    placeholder="@SourceChannel" 
                  />
                </div>
                <div className="form-group" style={{flex: 1}}>
                  <label>Source Channel ID (Optional)</label>
                  <input 
                    className="form-input" 
                    value={editingFlow.source_channel_id}
                    onChange={e => setEditingFlow({...editingFlow, source_channel_id: e.target.value})}
                    placeholder="-100123456789" 
                  />
                </div>
              </div>

              <div style={{display: 'flex', gap: '1rem'}}>
                <div className="form-group" style={{flex: 1}}>
                  <label>Target Channel (Username)</label>
                  <input 
                    className="form-input" 
                    value={editingFlow.target_channel}
                    onChange={e => setEditingFlow({...editingFlow, target_channel: e.target.value})}
                    placeholder="@TargetChannel" 
                  />
                </div>
                <div className="form-group" style={{flex: 1}}>
                  <label>Target Channel ID (Optional)</label>
                  <input 
                    className="form-input" 
                    value={editingFlow.target_channel_id}
                    onChange={e => setEditingFlow({...editingFlow, target_channel_id: e.target.value})}
                    placeholder="-100987654321" 
                  />
                </div>
              </div>

              <div className="form-group">
                <label>Rules & Filters</label>
                <div style={{background: 'rgba(255,255,255,0.03)', padding: '1rem', borderRadius: '8px', marginBottom: '1rem'}}>
                  {editingFlow.rules.map((rule, index) => (
                    <div key={index} style={{display: 'flex', gap: '10px', marginBottom: '10px', alignItems: 'center'}}>
                      <span className="status-badge" style={{background: 'var(--accent-color)', color: 'white'}}>{rule.type.toUpperCase()}</span>
                      
                      {rule.type === 'token_age' ? (
                        <>
                          <label style={{fontSize:'0.85rem', color:'var(--text-muted)'}}>Min Age (m):</label>
                          <input type="number" className="form-input" value={rule.min} onChange={e => updateRule(index, 'min', e.target.value)} style={{width: '80px'}} />
                          <label style={{fontSize:'0.85rem', color:'var(--text-muted)'}}>Max Age (m):</label>
                          <input type="number" className="form-input" value={rule.max} onChange={e => updateRule(index, 'max', e.target.value)} style={{width: '80px'}} />
                        </>
                      ) : rule.type === 'extract_ca' ? (
                        <span style={{color: 'var(--text-muted)', fontSize: '0.85rem'}}>Automatically extracts Contract Address from message.</span>
                      ) : (
                        <>
                          <input 
                            className="form-input" 
                            placeholder={rule.type === 'filter' ? 'Word to drop message' : 'Word to find'} 
                            value={rule.search} 
                            onChange={e => updateRule(index, 'search', e.target.value)}
                          />
                          {rule.type === 'replace' && (
                            <input 
                              className="form-input" 
                              placeholder="Replace with..." 
                              value={rule.replace} 
                              onChange={e => updateRule(index, 'replace', e.target.value)}
                            />
                          )}
                        </>
                      )}
                      <button type="button" className="btn-icon" onClick={() => removeRule(index)} style={{color: '#ef4444'}}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                      </button>
                    </div>
                  ))}
                  
                  <div style={{display: 'flex', gap: '10px', marginTop: '1rem', flexWrap: 'wrap'}}>
                    <button type="button" className="btn-primary" style={{padding: '0.5rem 1rem', fontSize: '0.85rem'}} onClick={() => addRule('filter')}>+ Add Word Filter</button>
                    <button type="button" className="btn-primary" style={{padding: '0.5rem 1rem', fontSize: '0.85rem'}} onClick={() => addRule('replace')}>+ Add Replace</button>
                    <button type="button" className="btn-primary" style={{padding: '0.5rem 1rem', fontSize: '0.85rem'}} onClick={() => addRule('token_age')}>+ Add Token Age</button>
                    <button type="button" className="btn-primary" style={{padding: '0.5rem 1rem', fontSize: '0.85rem', background: '#10b981'}} onClick={() => addRule('extract_ca')}>+ Extract CA</button>
                  </div>
                </div>
              </div>

              <div style={{display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '2rem'}}>
                <button type="button" className="btn-icon" style={{padding: '0 1rem'}} onClick={() => setShowModal(false)}>Cancel</button>
                <button type="submit" className="btn-primary">Save Workflow</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
