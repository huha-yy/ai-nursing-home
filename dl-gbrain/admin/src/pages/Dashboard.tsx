import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import { useI18n, timeAgo } from '../i18n/context';

interface FeedEvent {
  agent: string;
  operation: string;
  scopes: string;
  latency_ms: number;
  status: string;
  timestamp: string;
}

interface Agent {
  id: string;
  name: string;
  status: 'active' | 'revoked';
  auth_type: string;
  last_used_at: string | null;
}

export function DashboardPage() {
  const [stats, setStats] = useState({ connected_agents: 0, requests_today: 0, active_tokens: 0 });
  const [health, setHealth] = useState({ expiring_soon: 0, error_rate: '0%' });
  const [activeAgents, setActiveAgents] = useState(0);
  const [agentList, setAgentList] = useState<Agent[]>([]);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [sseStatus, setSseStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const eventSourceRef = useRef<EventSource | null>(null);
  const { t, locale } = useI18n();

  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
    api.health().then(setHealth).catch(() => {});
    api.agents().then((list: Agent[]) => {
      setAgentList(list);
      setActiveAgents(list.filter(a => a.status === 'active').length);
    }).catch(() => {});

    const es = new EventSource('/admin/events');
    eventSourceRef.current = es;
    es.onopen = () => setSseStatus('connected');
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as FeedEvent;
        setEvents(prev => [event, ...prev].slice(0, 50));
      } catch {}
    };
    es.onerror = () => {
      setSseStatus('disconnected');
      setTimeout(() => {
        setSseStatus('connecting');
        es.close();
        // Reconnect handled by browser EventSource auto-retry
      }, 3000);
    };

    const interval = setInterval(() => {
      api.stats().then(setStats).catch(() => {});
      api.health().then(setHealth).catch(() => {});
    }, 30000);

    return () => { es.close(); clearInterval(interval); };
  }, []);

  return (
    <>
      <h1 className="page-title">{t('dashboard.title')}</h1>

      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ flex: 1 }}>
          <div className="metrics">
            <div className="metric" title={agentList.map(a => `${a.name} (${a.status})`).join('\n')}
                 onClick={() => window.location.hash = '#agents'}
                 style={{ cursor: 'pointer' }}>
              <div className="metric-value">{activeAgents}</div>
              <div className="metric-label">{t('dashboard.metric.connectedAgents')}</div>
            </div>
            <div className="metric">
              <div className="metric-value">{stats.requests_today}</div>
              <div className="metric-label">{t('dashboard.metric.requestsToday')}</div>
            </div>
            <div className="metric">
              <div className="metric-value">{stats.active_tokens}</div>
              <div className="metric-label">{t('dashboard.metric.activeTokens')}</div>
            </div>
          </div>

          <h2 className="section-title">
            {t('dashboard.section.liveActivity')}
            <span style={{ marginLeft: 8, fontSize: 10, color: sseStatus === 'connected' ? 'var(--success)' : sseStatus === 'connecting' ? 'var(--warning)' : 'var(--error)' }}>
              {sseStatus === 'connected' ? t('dashboard.sse.connected') : sseStatus === 'connecting' ? t('dashboard.sse.connecting') : t('dashboard.sse.disconnected')}
            </span>
          </h2>

          <div className="feed">
            {events.length === 0 ? (
              <div className="feed-empty">
                {sseStatus === 'connected' ? t('dashboard.feed.empty.connected') : t('dashboard.feed.empty.connecting')}
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>{t('dashboard.table.agent')}</th>
                    <th>{t('dashboard.table.operation')}</th>
                    <th>{t('dashboard.table.scopes')}</th>
                    <th>{t('dashboard.table.latency')}</th>
                    <th>{t('dashboard.table.status')}</th>
                    <th>{t('dashboard.table.time')}</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e, i) => (
                    <tr key={i}>
                      <td className="mono">{e.agent}</td>
                      <td className="mono">{e.operation}</td>
                      <td>{e.scopes.split(',').map(s => (
                        <span key={s} className={`badge badge-${s.trim()}`} style={{ marginRight: 4 }}>{s.trim()}</span>
                      ))}</td>
                      <td className="mono">{e.latency_ms} ms</td>
                      <td><span className={`badge badge-${e.status}`}>{e.status}</span></td>
                      <td style={{ color: 'var(--text-secondary)' }}>{timeAgo(e.timestamp, locale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div style={{ width: 220 }}>
          <h2 className="section-title">{t('dashboard.section.tokenHealth')}</h2>
          <div className="health-panel">
            <div className="health-row">
              <span style={{ color: 'var(--warning)' }}>{t('dashboard.health.expiringSoon')}</span>
              <span className="mono">{health.expiring_soon}</span>
            </div>
            <div className="health-row">
              <span style={{ color: 'var(--error)' }}>{t('dashboard.health.errorRate')}</span>
              <span className="mono">{health.error_rate}</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
