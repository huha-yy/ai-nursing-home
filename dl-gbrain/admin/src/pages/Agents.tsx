import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { ALLOWED_SCOPES_LIST, type Scope } from '../lib/scope-constants';
import { useI18n, timeAgo } from '../i18n/context';

interface Agent {
  id: string;
  name: string;
  auth_type: 'oauth' | 'api_key';
  client_id?: string;  // compat
  client_name?: string; // compat
  grant_types: string[];
  scope: string;
  created_at: string;
  last_used_at: string | null;
  total_requests: number;
  requests_today: number;
  token_ttl: number | null;
  status: 'active' | 'revoked';
}

interface ApiKey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  status: 'active' | 'revoked';
}

export function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [hideRevoked, setHideRevoked] = useState(true);
  const [showRegister, setShowRegister] = useState(false);
  const [showCredentials, setShowCredentials] = useState<{ clientId: string; clientSecret: string; name: string } | null>(null);
  const [showApiKeyCreate, setShowApiKeyCreate] = useState(false);
  const [showApiKeyToken, setShowApiKeyToken] = useState<{ name: string; token: string } | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const { t, locale } = useI18n();

  useEffect(() => { loadAgents(); }, []);

  const loadAgents = () => { api.agents().then(setAgents).catch(() => {}); };

  const agentTimeAgo = (ts: string | null) => {
    if (!ts) return t('agents.lastUsed.never');
    return timeAgo(ts, locale);
  };

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>{t('agents.title')}</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ fontSize: 13, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={hideRevoked} onChange={e => setHideRevoked(e.target.checked)} /> {t('agents.filter.hideRevoked')}
          </label>
          <button className="btn btn-secondary" onClick={() => setShowApiKeyCreate(true)}>{t('agents.btn.createApiKey')}</button>
          <button className="btn btn-primary" onClick={() => setShowRegister(true)}>{t('agents.btn.registerOAuth')}</button>
        </div>
      </div>

      {(() => {
        const visibleAgents = agents.filter(a => !hideRevoked || a.status !== 'revoked');
        if (agents.length === 0) {
          return (
            <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
              {t('agents.empty.noAgents')}
            </div>
          );
        }
        if (visibleAgents.length === 0) {
          return (
            <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
              {t('agents.empty.allRevoked')}
            </div>
          );
        }
        return (
        <>
          <table>
            <thead>
              <tr>
                <th>{t('agents.table.name')}</th>
                <th>{t('agents.table.type')}</th>
                <th>{t('agents.table.scopes')}</th>
                <th>{t('agents.table.status')}</th>
                <th>{t('agents.table.requests')}</th>
                <th>{t('agents.table.lastUsed')}</th>
              </tr>
            </thead>
            <tbody>
              {visibleAgents.map(a => (
                <tr key={a.id} onClick={() => setSelectedAgent(a)}
                    style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: 500 }}>{a.name || a.client_name}</td>
                  <td>
                    <span className={`badge ${a.auth_type === 'oauth' ? 'badge-read' : 'badge-write'}`} style={{ fontSize: 11 }}>
                      {a.auth_type === 'oauth' ? 'OAuth' : 'API Key'}
                    </span>
                  </td>
                  <td>
                    {(a.scope || '').split(' ').filter(Boolean).map(s => (
                      <span key={s} className={`badge badge-${s}`} style={{ marginRight: 4 }}>{s}</span>
                    ))}
                  </td>
                  <td>
                    <span className={`badge ${a.status === 'active' ? 'badge-success' : 'badge-danger'}`}>{a.status}</span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 500 }}>{a.requests_today || 0}</span>
                    <span style={{ color: 'var(--text-muted)', fontSize: 12 }}> / {a.total_requests || 0}</span>
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>
                    {agentTimeAgo(a.last_used_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 12 }}>
            {t('agents.summary', { active: agents.filter(a => a.status === 'active').length })}
          </div>
        </>
        );
      })()}

      {showRegister && (
        <RegisterModal
          onClose={() => setShowRegister(false)}
          onRegistered={(creds) => { setShowRegister(false); setShowCredentials(creds); loadAgents(); }}
        />
      )}

      {showCredentials && (
        <CredentialsModal
          credentials={showCredentials}
          onClose={() => setShowCredentials(null)}
        />
      )}

      {selectedAgent && (
        <AgentDrawer agent={selectedAgent} onClose={() => setSelectedAgent(null)} onRevoked={loadAgents} />
      )}

      {showApiKeyCreate && (
        <ApiKeyCreateModal
          onClose={() => setShowApiKeyCreate(false)}
          onCreated={(result) => { setShowApiKeyCreate(false); setShowApiKeyToken(result); loadAgents(); }}
        />
      )}

      {showApiKeyToken && (
        <ApiKeyTokenModal token={showApiKeyToken} onClose={() => setShowApiKeyToken(null)} />
      )}
    </>
  );
}

function ApiKeyCreateModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (result: { name: string; token: string }) => void;
}) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { t } = useI18n();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError(t('agents.apiKey.error.nameRequired')); return; }
    setLoading(true);
    try {
      const data = await api.createApiKey(name.trim());
      onCreated({ name: data.name, token: data.token });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('agents.apiKey.error.failed'));
    } finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={e => e.stopPropagation()} onSubmit={handleSubmit}>
        <div className="modal-title">{t('agents.apiKey.title')}</div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
          {t('agents.apiKey.description')}
        </p>
        <div style={{ marginBottom: 16 }}>
          <label>{t('agents.apiKey.label')}</label>
          <input placeholder={t('agents.apiKey.placeholder')} value={name} onChange={e => setName(e.target.value)} autoFocus />
        </div>
        {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>{t('agents.apiKey.btn.cancel')}</button>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? t('agents.apiKey.btn.creating') : t('agents.apiKey.btn.create')}
          </button>
        </div>
      </form>
    </div>
  );
}

function ApiKeyTokenModal({ token, onClose }: {
  token: { name: string; token: string };
  onClose: () => void;
}) {
  const copy = (text: string) => navigator.clipboard.writeText(text);
  const { t } = useI18n();

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 560 }}>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <div style={{ fontSize: 36, color: 'var(--success)', marginBottom: 8 }}>&#10003;</div>
          <div style={{ fontSize: 20, fontWeight: 600 }}>{t('agents.apiKeyToken.title')}</div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>{t('agents.apiKeyToken.label.name')}</label>
          <div className="code-block"><span>{token.name}</span></div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>{t('agents.apiKeyToken.label.token')}</label>
          <div className="code-block">
            <span>{token.token}</span>
            <button className="copy-btn" onClick={() => copy(token.token)}>Copy</button>
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>{t('agents.apiKeyToken.label.usage')}</label>
          <div className="code-block">
            <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>{`Authorization: Bearer ${token.token}`}</pre>
            <button className="copy-btn" onClick={() => copy(`Authorization: Bearer ${token.token}`)}>Copy</button>
          </div>
        </div>
        <div className="warning-bar">{t('agents.apiKeyToken.warning')}</div>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 20 }}>
          <button className="btn btn-primary" onClick={onClose}>{t('agents.apiKeyToken.btn.done')}</button>
        </div>
      </div>
    </div>
  );
}

function RegisterModal({ onClose, onRegistered }: {
  onClose: () => void;
  onRegistered: (creds: { clientId: string; clientSecret: string; name: string }) => void;
}) {
  const [name, setName] = useState('');
  // v0.28: scope set sourced from admin/src/lib/scope-constants.ts (mirror
  // of src/core/scope.ts). CI drift check at scripts/check-admin-scope-drift.sh
  // fails the build if these diverge.
  const [scopes, setScopes] = useState<Record<Scope, boolean>>(() =>
    Object.fromEntries(ALLOWED_SCOPES_LIST.map(s => [s, s === 'read'])) as Record<Scope, boolean>,
  );
  const [ttl, setTtl] = useState('86400'); // 24h default
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { t } = useI18n();

  const ttlOptions = [
    { label: t('agents.register.ttl.1h'), value: '3600' },
    { label: t('agents.register.ttl.24h'), value: '86400' },
    { label: t('agents.register.ttl.7d'), value: '604800' },
    { label: t('agents.register.ttl.30d'), value: '2592000' },
    { label: t('agents.register.ttl.1y'), value: '31536000' },
    { label: t('agents.register.ttl.noExpiry'), value: '0' },
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError(t('agents.register.error.required')); return; }
    setLoading(true);
    setError('');
    try {
      // Use the CLI registration endpoint (POST to admin API)
      const selectedScopes = Object.entries(scopes).filter(([, v]) => v).map(([k]) => k).join(' ');
      const res = await fetch('/admin/api/register-client', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), scopes: selectedScopes, tokenTtl: ttl === '0' ? 315360000 : Number(ttl) }),
      });
      if (!res.ok) throw new Error(t('agents.register.error.failed'));
      const data = await res.json();
      onRegistered({ clientId: data.clientId, clientSecret: data.clientSecret, name: name.trim() });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('agents.register.error.failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={e => e.stopPropagation()} onSubmit={handleSubmit}>
        <div className="modal-title">{t('agents.register.title')}</div>
        <div style={{ marginBottom: 16 }}>
          <label>{t('agents.register.label.name')}</label>
          <input placeholder={t('agents.register.placeholder.name')} value={name} onChange={e => setName(e.target.value)} autoFocus />
        </div>
        <div style={{ marginBottom: 16 }}>
          <label>{t('agents.register.label.scopes')}</label>
          <div className="checkbox-group">
            {ALLOWED_SCOPES_LIST.map(s => (
              <label key={s} className="checkbox-label">
                <input type="checkbox" checked={scopes[s]} onChange={e => setScopes(p => ({ ...p, [s]: e.target.checked }))} />
                {s}
              </label>
            ))}
          </div>
        </div>
        <div style={{ marginBottom: 20 }}>
          <label>{t('agents.register.label.tokenLifetime')}</label>
          <select value={ttl} onChange={e => setTtl(e.target.value)}
            style={{ width: '100%', background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: 14 }}>
            {ttlOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>{t('agents.register.btn.cancel')}</button>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? t('agents.register.btn.registering') : t('agents.register.btn.register')}
          </button>
        </div>
      </form>
    </div>
  );
}

function CredentialsModal({ credentials, onClose }: {
  credentials: { clientId: string; clientSecret: string; name: string };
  onClose: () => void;
}) {
  const copy = (text: string) => navigator.clipboard.writeText(text);
  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(credentials, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${credentials.name}-credentials.json`; a.click();
    URL.revokeObjectURL(url);
  };
  const { t } = useI18n();

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 560 }}>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <div style={{ fontSize: 36, color: 'var(--success)', marginBottom: 8 }}>&#10003;</div>
          <div style={{ fontSize: 20, fontWeight: 600 }}>{t('agents.credentials.title')}</div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>{t('agents.credentials.label.clientId')}</label>
          <div className="code-block">
            <span>{credentials.clientId}</span>
            <button className="copy-btn" onClick={() => copy(credentials.clientId)}>Copy</button>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>{t('agents.credentials.label.clientSecret')}</label>
          <div className="code-block">
            <span>{credentials.clientSecret}</span>
            <button className="copy-btn" onClick={() => copy(credentials.clientSecret)}>Copy</button>
          </div>
        </div>

        <div className="warning-bar">
          {t('agents.credentials.warning')}
        </div>

        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 20 }}>
          <button className="btn btn-secondary" onClick={downloadJson}>{t('agents.credentials.btn.downloadJson')}</button>
          <button className="btn btn-primary" onClick={onClose}>{t('agents.credentials.btn.done')}</button>
        </div>
      </div>
    </div>
  );
}

function AgentDrawer({ agent, onClose, onRevoked }: { agent: Agent; onClose: () => void; onRevoked: () => void }) {
  const [tab, setTab] = useState<'claude-code' | 'chatgpt' | 'claude-cowork' | 'perplexity' | 'cursor' | 'json'>('claude-code');
  const copy = (text: string) => navigator.clipboard.writeText(text);
  const serverUrl = window.location.origin;
  const { t } = useI18n();

  const cid = agent.id || agent.client_id || '';
  const isOAuth = agent.auth_type === 'oauth';
  const agentName = agent.name || agent.client_name || 'unknown';

  // For API keys, we can't show the actual token (it was shown once at creation).
  // For OAuth, we show the client_id and tell them to use their secret.

  const configSnippets: Record<string, string> = {
    'claude-code': isOAuth
      ? [
        `The user wants to connect GBrain MCP to your context. Here's how.`,
        ``,
        `# Step 1 — read the client secret from the operator without putting it in shell history.`,
        `# read -s suppresses the echo; export keeps it scoped to this shell session.`,
        `read -rs -p "Client Secret: " GBRAIN_CS && echo`,
        ``,
        `# Step 2 — mint a token and register GBrain in one go.`,
        `claude mcp add --transport http gbrain ${serverUrl}/mcp \\`,
        `  --header "Authorization: Bearer $(curl -s -X POST ${serverUrl}/token \\`,
        `    -d 'grant_type=client_credentials' \\`,
        `    -d 'client_id=${cid}' \\`,
        `    --data-urlencode "client_secret=$GBRAIN_CS" \\`,
        `    -d 'scope=${agent.scope || 'read write'}' | jq -r .access_token)"`,
        ``,
        `# Step 3 — clear the secret from this shell.`,
        `unset GBRAIN_CS`,
        ``,
        `The token will last ${agent.token_ttl ? (agent.token_ttl >= 86400 ? Math.floor(agent.token_ttl / 86400) + ' days' : Math.floor(agent.token_ttl / 3600) + ' hours') : '1 hour (default)'}.`,
        ``,
        `─── Fallback: 2-step curl + paste ───`,
        ``,
        `If your shell doesn't support read -s, mint the token first, then paste:`,
        ``,
        `curl -s -X POST ${serverUrl}/token \\`,
        `  -d 'grant_type=client_credentials' \\`,
        `  -d 'client_id=${cid}' \\`,
        `  -d 'client_secret=PASTE_YOUR_CLIENT_SECRET_HERE' \\`,
        `  -d 'scope=${agent.scope || 'read write'}' | jq -r .access_token`,
        ``,
        `claude mcp add --transport http gbrain ${serverUrl}/mcp \\`,
        `  --header "Authorization: Bearer PASTE_TOKEN_FROM_ABOVE"`,
        ``,
        `# Then run: history -d $((HISTCMD-1))   # zsh: print -s -- '' && fc -p`,
      ].join('\n')
      : [
        `The user wants to connect GBrain MCP to your context. Here's how.`,
        ``,
        `Run this command in terminal to register GBrain:`,
        ``,
        `claude mcp add --transport http gbrain ${serverUrl}/mcp \\`,
        `  --header "Authorization: Bearer PASTE_YOUR_API_KEY_HERE"`,
        ``,
        `Replace PASTE_YOUR_API_KEY_HERE with the API key shown when "${agentName}" was created.`,
        `API keys never expire.`,
      ].join('\n'),

    'chatgpt': [
      `The user wants to connect GBrain MCP to ChatGPT.`,
      ``,
      `1. Open ChatGPT > Settings > Tools & Integrations > Add MCP Server`,
      `2. Enter this URL — ChatGPT will auto-discover the OAuth configuration:`,
      ``,
      `   ${serverUrl}/.well-known/oauth-authorization-server`,
      ``,
      `3. When prompted for credentials:`,
      `   Client ID: ${cid}`,
      `   Client Secret: (the secret from agent registration)`,
      `   Grant Type: client_credentials`,
      `   Scope: ${agent.scope || 'read write'}`,
    ].join('\n'),

    'claude-cowork': [
      `The user wants to connect GBrain MCP to Claude.ai.`,
      ``,
      `1. Open claude.ai > Settings > Connected Apps > Add MCP Server`,
      `2. Server URL: ${serverUrl}/mcp`,
      `3. When prompted for auth:`,
      `   Token endpoint: ${serverUrl}/token`,
      `   Client ID: ${cid}`,
      `   Client Secret: (the secret from agent registration)`,
      `   Scope: ${agent.scope || 'read write'}`,
      ``,
      `Discovery URL: ${serverUrl}/.well-known/oauth-authorization-server`,
    ].join('\n'),

    cursor: isOAuth
      ? [
        `The user wants to connect GBrain MCP to Cursor.`,
        ``,
        `Cursor supports OAuth for remote MCP. Add to .cursor/mcp.json:`,
        ``,
        `{`,
        `  "mcpServers": {`,
        `    "gbrain": {`,
        `      "url": "${serverUrl}/mcp",`,
        `      "transport": "sse"`,
        `    }`,
        `  }`,
        `}`,
        ``,
        `Cursor will auto-discover OAuth via:`,
        `${serverUrl}/.well-known/oauth-authorization-server`,
        ``,
        `When prompted: Client ID ${cid}, use the secret from registration.`,
      ].join('\n')
      : [
        `The user wants to connect GBrain MCP to Cursor.`,
        ``,
        `Add to .cursor/mcp.json:`,
        ``,
        `{`,
        `  "mcpServers": {`,
        `    "gbrain": {`,
        `      "url": "${serverUrl}/mcp",`,
        `      "transport": "sse",`,
        `      "headers": {`,
        `        "Authorization": "Bearer PASTE_YOUR_API_KEY_HERE"`,
        `      }`,
        `    }`,
        `  }`,
        `}`,
        ``,
        `Replace PASTE_YOUR_API_KEY_HERE with the API key shown when "${agentName}" was created.`,
      ].join('\n'),

    perplexity: [
      `The user wants to connect GBrain MCP to Perplexity.`,
      ``,
      `1. Go to Settings > Connectors > Add MCP`,
      `2. Server URL: ${serverUrl}/mcp`,
      `3. Client ID: ${cid}`,
      `4. Client Secret: (the secret from agent registration)`,
    ].join('\n'),

    json: JSON.stringify({
      server_url: serverUrl + '/mcp',
      token_url: serverUrl + '/token',
      discovery_url: serverUrl + '/.well-known/oauth-authorization-server',
      client_id: cid,
      client_name: agentName,
      auth_type: agent.auth_type,
      scope: agent.scope,
    }, null, 2),
  };

  const ttlDisplay = agent.token_ttl
    ? (agent.token_ttl >= 31536000 ? t('agents.drawer.noExpiry')
       : agent.token_ttl >= 86400 ? `${Math.floor(agent.token_ttl / 86400)}d`
       : agent.token_ttl >= 3600 ? `${Math.floor(agent.token_ttl / 3600)}h`
       : `${agent.token_ttl}s`)
    : t('agents.drawer.ttlDefault');

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer">
        <button className="drawer-close" onClick={onClose}>&#10005;</button>
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>{agent.name || agent.client_name}</div>
        <span className={`badge ${agent.status === 'active' ? 'badge-success' : 'badge-danger'}`}>{agent.status}</span>

        <div className="section-title">{t('agents.drawer.details')}</div>
        <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '6px 12px', fontSize: 13 }}>
          <span style={{ color: 'var(--text-secondary)' }}>{t('agents.drawer.label.clientId')}</span>
          <span className="mono">{(agent.id || agent.id || agent.client_id || '').substring(0, 24)}...</span>
          <span style={{ color: 'var(--text-secondary)' }}>{t('agents.drawer.label.scopes')}</span>
          <span>{(agent.scope || '').split(' ').filter(Boolean).map(s => (
            <span key={s} className={`badge badge-${s}`} style={{ marginRight: 4 }}>{s}</span>
          ))}</span>
          <span style={{ color: 'var(--text-secondary)' }}>{t('agents.drawer.label.registered')}</span>
          <span>{new Date(agent.created_at).toLocaleDateString()}</span>
          <span style={{ color: 'var(--text-secondary)' }}>{t('agents.drawer.label.tokenTtl')}</span>
          <span>{ttlDisplay}</span>
        </div>

        {/*
          Config Export visible for both auth_type=oauth AND auth_type=api_key.
          Claude Code + Cursor + JSON tabs render real snippets regardless
          (commit 15's snippets are auth-type-aware for those two clients;
          JSON is just structured metadata). ChatGPT, Claude.ai, and
          Perplexity tabs render an "OAuth client required" message on
          api_key agents — those MCP clients only speak OAuth 2.0
          client_credentials, not raw bearer tokens.

          Pre-fix (Wintermute commit 16): the entire Config Export
          section was hidden for api_key agents, dropping the working
          Claude Code + Cursor snippets along with the broken ones.
          (D5=C in the eng review.)
        */}
        <div className="section-title">{t('agents.drawer.section.configExport')}</div>
        <div className="tabs" style={{ flexWrap: 'wrap' }}>
          <div className={`tab ${tab === 'claude-code' ? 'active' : ''}`} onClick={() => setTab('claude-code')}>{t('agents.drawer.tab.claudeCode')}</div>
          <div className={`tab ${tab === 'chatgpt' ? 'active' : ''}`} onClick={() => setTab('chatgpt')}>{t('agents.drawer.tab.chatgpt')}</div>
          <div className={`tab ${tab === 'claude-cowork' ? 'active' : ''}`} onClick={() => setTab('claude-cowork')}>{t('agents.drawer.tab.claudeAi')}</div>
          <div className={`tab ${tab === 'cursor' ? 'active' : ''}`} onClick={() => setTab('cursor')}>{t('agents.drawer.tab.cursor')}</div>
          <div className={`tab ${tab === 'perplexity' ? 'active' : ''}`} onClick={() => setTab('perplexity')}>{t('agents.drawer.tab.perplexity')}</div>
          <div className={`tab ${tab === 'json' ? 'active' : ''}`} onClick={() => setTab('json')}>{t('agents.drawer.tab.json')}</div>
        </div>
        {(() => {
          const oauthOnlyTabs = new Set(['chatgpt', 'claude-cowork', 'perplexity']);
          if (!isOAuth && oauthOnlyTabs.has(tab)) {
            const clientName = { chatgpt: 'ChatGPT', 'claude-cowork': 'Claude.ai', perplexity: 'Perplexity' }[tab] || tab;
            return (
              <div style={{
                background: 'rgba(255, 200, 100, 0.08)',
                border: '1px solid rgba(255, 200, 100, 0.2)',
                borderRadius: 8,
                padding: '14px 16px',
                marginTop: 12,
                fontSize: 13,
                lineHeight: 1.6,
                color: 'var(--text-secondary)',
              }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>
                  {t('agents.drawer.oauthRequired.title', { client: clientName })}
                </div>
                {t('agents.drawer.oauthRequired.description', { client: clientName })}
              </div>
            );
          }
          return (
            <div className="code-block">
              <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{configSnippets[tab]}</pre>
              <button className="copy-btn" onClick={() => copy(configSnippets[tab])}>Copy</button>
            </div>
          );
        })()}

        <div style={{ marginTop: 32 }}>
          {agent.status === 'active' && (
            <button className="btn btn-danger" onClick={async () => {
              if (!confirm(t('agents.drawer.confirm.revoke', { name: agent.name || agent.client_name || '' }))) return;
              try {
                if (agent.auth_type === 'oauth') {
                  await api.revokeClient(agent.id || agent.client_id || '');
                } else {
                  await api.revokeApiKey(agent.name || '');
                }
                onRevoked();
                onClose();
              } catch (e) {
                alert(t('agents.drawer.revokeFailed', { msg: e instanceof Error ? e.message : 'unknown error' }));
              }
            }}>{t('agents.drawer.btn.revoke')}</button>
          )}
          {agent.status === 'revoked' && (
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{t('agents.drawer.revoked')}</span>
          )}
        </div>
      </div>
    </>
  );
}
