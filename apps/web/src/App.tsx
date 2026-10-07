import { useEffect, useState } from 'react';
import { loadHealth, type Health } from './health';
import IdentityWorkspace from './IdentityWorkspace';

const phases = [
  ['Foundation', 'POS, inventory, purchases and expenses'],
  ['OCR', 'Invoice extraction, matching and reviewed posting'],
  ['Owner intelligence', 'Dashboard, cash flow, profit and daily briefing'],
  ['AI agents', 'Inventory, purchase, finance, pricing, expiry and suppliers'],
  ['GST intelligence', 'Validation, reconciliation and return preparation'],
  ['Predictive AI', 'Demand, reorder, pricing and cash-flow forecasts'],
];

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

  return (
    <main>
      <header className="topbar"><span className="brand">S<span className="brand-dot">·</span></span>
        <span>Supermarket operating system</span><span className="pill">Development</span>
      </header>
      <section className="intro">
        <p className="eyebrow">PHASE 1 · BUSINESS FOUNDATION</p>
        <h1>Your supermarket.<br /><span>A clearer way to run it.</span></h1>
        <p className="description">A reliable foundation for every sale, stock movement and business decision.</p>
      </section>
      <IdentityWorkspace />
      <section className="status-panel" aria-labelledby="status-heading">
        <div><h2 id="status-heading">Environment status</h2><p>Live checks against your local backend.</p></div>
        <div className="status-list" role="status" aria-label="Environment health" aria-live="polite">
          <p><span>API process</span><strong data-state={health.api}>{health.api}</strong></p>
          <p><span>Database &amp; migration</span><strong data-state={health.database}>{health.database}</strong></p>
        </div>
        <button onClick={refresh} disabled={checking}>{checking ? 'Checking…' : 'Check again'}</button>
      </section>
      <p className="development-note">Setup, products, suppliers and opening stock are available. Billing, purchases and expenses are not available yet.</p>
      <section aria-labelledby="roadmap-heading">
        <div className="section-title"><h2 id="roadmap-heading">The development path</h2><span>Six focused phases</span></div>
        <ol className="phase-grid">{phases.map(([title, description], index) => (
          <li key={title}><div className="phase-top"><span className="number">0{index + 1}</span>
            <span className="phase-state">{index === 0 ? 'Foundation in progress' : 'Planned'}</span></div>
            <h3>{title}</h3><p>{description}</p>
          </li>
        ))}</ol>
      </section>
      <footer>Correctness first. Human approval for significant actions. AI grounded in actual business data.</footer>
    </main>
  );
}
