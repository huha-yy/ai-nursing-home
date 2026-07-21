import React, { useState, useEffect } from 'react';
import { LoginPage } from './pages/Login';
import { DashboardPage } from './pages/Dashboard';
import { AgentsPage } from './pages/Agents';
import { RequestLogPage } from './pages/RequestLog';
import { CalibrationPage } from './pages/Calibration';
import { JobsWatchPage } from './pages/JobsWatch';
import { api } from './api';
import { useI18n } from './i18n/context';

type Page = 'login' | 'dashboard' | 'agents' | 'log' | 'calibration' | 'jobs';

function getPage(): Page {
  const hash = window.location.hash.replace('#', '') || 'dashboard';
  if (['login', 'dashboard', 'agents', 'log', 'calibration', 'jobs'].includes(hash)) return hash as Page;
  return 'dashboard';
}

export function App() {
  const [page, setPage] = useState<Page>(getPage);
  const { t, locale, setLocale } = useI18n();

  useEffect(() => {
    const onHash = () => setPage(getPage());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const navigate = (p: Page) => {
    window.location.hash = p;
    setPage(p);
  };

  if (page === 'login') {
    return <LoginPage onLogin={() => navigate('dashboard')} />;
  }

  const handleSignOutEverywhere = async () => {
    if (!confirm(t('app.confirm.signOutEverywhere'))) {
      return;
    }
    try {
      await api.signOutEverywhere();
    } catch {
      // Even if the call fails, push to login — cookie is likely already invalid.
    }
    navigate('login');
  };

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar-logo">{t('app.title')}</div>
        <div className="sidebar-nav">
          <a className={`nav-item ${page === 'dashboard' ? 'active' : ''}`}
             onClick={() => navigate('dashboard')}>{t('app.nav.dashboard')}</a>
          <a className={`nav-item ${page === 'agents' ? 'active' : ''}`}
             onClick={() => navigate('agents')}>{t('app.nav.agents')}</a>
          <a className={`nav-item ${page === 'log' ? 'active' : ''}`}
             onClick={() => navigate('log')}>{t('app.nav.requestLog')}</a>
          <a className={`nav-item ${page === 'calibration' ? 'active' : ''}`}
             onClick={() => navigate('calibration')}>{t('app.nav.calibration')}</a>
          <a className={`nav-item ${page === 'jobs' ? 'active' : ''}`}
             onClick={() => navigate('jobs')}>{t('app.nav.jobsWatch')}</a>
        </div>
        <div style={{
          marginTop: 'auto',
          padding: '12px',
          borderTop: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          {/* Language switcher */}
          <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
            <button
              onClick={() => setLocale('en')}
              style={{
                flex: 1,
                background: locale === 'en' ? 'var(--accent)' : 'transparent',
                border: `1px solid ${locale === 'en' ? 'var(--accent)' : 'var(--border)'}`,
                color: locale === 'en' ? '#fff' : 'var(--text-secondary)',
                padding: '4px 8px',
                borderRadius: 4,
                fontSize: 12,
                cursor: 'pointer',
                fontWeight: locale === 'en' ? 600 : 400,
              }}
            >
              EN
            </button>
            <button
              onClick={() => setLocale('zh')}
              style={{
                flex: 1,
                background: locale === 'zh' ? 'var(--accent)' : 'transparent',
                border: `1px solid ${locale === 'zh' ? 'var(--accent)' : 'var(--border)'}`,
                color: locale === 'zh' ? '#fff' : 'var(--text-secondary)',
                padding: '4px 8px',
                borderRadius: 4,
                fontSize: 12,
                cursor: 'pointer',
                fontWeight: locale === 'zh' ? 600 : 400,
              }}
            >
              中文
            </button>
          </div>
          <button
            onClick={handleSignOutEverywhere}
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              padding: '6px 10px',
              borderRadius: 6,
              fontSize: 12,
              cursor: 'pointer',
              width: '100%',
            }}
            title={t('app.btn.signOutEverywhere')}
          >
            {t('app.btn.signOutEverywhere')}
          </button>
        </div>
      </nav>
      <main className="main">
        {page === 'dashboard' && <DashboardPage />}
        {page === 'agents' && <AgentsPage />}
        {page === 'log' && <RequestLogPage />}
        {page === 'calibration' && <CalibrationPage />}
        {page === 'jobs' && <JobsWatchPage />}
      </main>
    </div>
  );
}
