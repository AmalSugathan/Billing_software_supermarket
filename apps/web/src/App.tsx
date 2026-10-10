import { useEffect, useState } from 'react';
import { loadHealth, type Health } from './health';
import IdentityWorkspace from './IdentityWorkspace';

export default function App() {
  const [health, setHealth] = useState<Health>({ api: 'checking', database: 'checking' });
  const [attempt, setAttempt] = useState(0);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    void loadHealth(controller.signal).then((result) => {
      // A timed-out request still reports failure; an unmounted effect cannot update UI.
      if (active) { setHealth(result); setChecking(false); }
    });
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  function refresh() {
    setChecking(true);
    setHealth({ api: 'checking', database: 'checking' });
    setAttempt((value) => value + 1);
  }

  const connected = health.api === 'available' && health.database === 'available';
  return <main className="app-shell">
    <a className="skip-link" href="#workspace-content">Skip to workspace</a>
    <header className="topbar">
      <div className="brand-lockup"><span className="brand" aria-hidden="true">S</span><div><strong>Storewise</strong><span>Supermarket workspace</span></div></div>
      <details className="connection-details"><summary><span className={'connection-dot ' + (connected ? 'connected' : '')} />{checking ? 'Connecting' : connected ? 'Store service connected' : 'Connection needs attention'}</summary>
        <div className="connection-popover"><div className="status-list" role="status" aria-label="Environment health" aria-live="polite">
          <p><span>Store service</span><strong data-state={health.api}>{health.api}</strong></p>
          <p><span>Business data</span><strong data-state={health.database}>{health.database}</strong></p>
        </div><button className="secondary-button" onClick={refresh} disabled={checking}>{checking ? 'Checking...' : 'Check again'}</button></div>
      </details>
    </header>
    <div id="workspace-content" tabIndex={-1}><IdentityWorkspace /></div>
  </main>;
}
