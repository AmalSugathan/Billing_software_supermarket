import { useEffect, useState, type FormEvent } from 'react';
import { request, type Business, type Store } from './identity-api';
import { insightSchema, rupees, type Insights } from './insights-api';

function indiaDate() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()); }
const paise = (value: string) => BigInt(value.replace('.', ''));
function CashChart({ daily }: { daily: Insights['daily'] }) {
  const shown = daily.slice(-14);
  const maximum = shown.reduce((max, item) => [paise(item.money_in), paise(item.money_out)].reduce((a, b) => a > b ? a : b, max), 1n);
  const height = (value: string) => String(paise(value) * 10000n / maximum / 100n) + '%';
  return <figure className="cash-chart"><figcaption>Money in and out {daily.length > 14 ? '(last 14 days of selection)' : ''}</figcaption>
    <p className="chart-legend"><span>Money in</span><span>Money out</span></p>
    <div className="chart-columns" role="img" aria-label="Daily money in and out. Exact amounts are available in the daily records table below.">
      {shown.map((item) => <div className="chart-day" key={item.date} title={item.date + ': in ' + rupees(item.money_in) + ', out ' + rupees(item.money_out)}>
        <div className="chart-bars"><span style={{ height: height(item.money_in) }} /><span style={{ height: height(item.money_out) }} /></div><small>{item.date.slice(8)}/{item.date.slice(5, 7)}</small>
      </div>)}
    </div>
  </figure>;
}
function Metric({ title, value, detail }: { title: string; value: string; detail: string }) {
  return <article className="metric-card"><h4>{title}</h4><strong>{rupees(value)}</strong><p>{detail}</p></article>;
}
export default function OwnerDashboard({ business, stores, onNavigate }: { business: Business; stores: Store[]; onNavigate: (module: string) => void }) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [period, setPeriod] = useState({ start: indiaDate(), end: indiaDate() });
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: Insights; error?: string } | null>(null);
  const route = '/businesses/' + business.id + '/stores/' + storeId + '/insights?start=' + period.start + '&end=' + period.end;
  const key = route + ':' + revision;
  const current = result?.key === key ? result : null;
  useEffect(() => {
    if (!storeId) return;
    const controller = new AbortController();
    void request(route, insightSchema, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setResult({ key, data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : 'Business data is unavailable.' });
    });
    return () => controller.abort();
  }, [storeId, route, key]);
  function selectPeriod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setPeriod({ start: String(form.get('start')), end: String(form.get('end')) }); setRevision((value) => value + 1);
  }
  const data = current?.data;
  const canOpen = (module: string) => ({ inventory: 'inventory.read', cash: 'finance.read', payments: 'finance.read', products: 'business.read' })[module];
  return <div className="owner-dashboard">
    <section className="workspace-card dashboard-filters" aria-label="Dashboard filters">
      <label>Dashboard store<select value={storeId} onChange={(event) => setStoreId(event.target.value)}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
      <form onSubmit={selectPeriod}><label>From<input name="start" type="date" defaultValue={period.start} max={indiaDate()} required /></label><label>Through<input name="end" type="date" defaultValue={period.end} max={indiaDate()} required /></label><button disabled={!storeId}>Show business</button></form>
    </section>
    {!storeId ? <p>No stores assigned. Set up a store to see its recorded business.</p> : !current ? <p role="status">Reading your business records...</p> : current.error ? <div className="form-error" role="alert"><p>Business data is unavailable. {current.error}</p><button onClick={() => setRevision((value) => value + 1)}>Retry dashboard</button></div> : data && <>
      <p className="dashboard-context">{data.start} to {data.end} · India time · Updated {new Date(data.generated_at).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}</p>
      <div className="metrics-grid" aria-label="Recorded business totals">
        <Metric title="Net sales" value={data.metrics.revenue} detail="Excludes GST; returns and cancellations deducted." />
        <Metric title="Money in" value={data.metrics.money_in} detail="Recorded receipts across cash, UPI, card and bank." />
        <Metric title="Money out" value={data.metrics.money_out} detail="Payments, refunds, withdrawals and paid expenses." />
        <Metric title="Estimated gross profit" value={data.metrics.estimated_gross_profit} detail="Net sales less recorded goods cost, before expenses." />
      </div>
      <div className="dashboard-grid">
        <section className="workspace-card briefing-card"><div className="section-title"><h4>{data.start === data.end ? 'Daily owner briefing' : 'Period briefing'}</h4><span className="role-badge">From recorded data</span></div><p className="hint">Rules-based checks. Recommendations require your review.</p>
          <ul className="briefing-list">{data.briefing.map((item) => <li key={item.title} data-priority={item.priority}><span className="priority-label">{item.priority}</span><h5>{item.title}</h5><p>{item.detail}</p>{item.module && business.capabilities.includes(canOpen(item.module) ?? '') && <button className="text-button" onClick={() => onNavigate(item.module!)}>Open related records</button>}</li>)}</ul>
        </section>
        <section className="workspace-card"><h4>Follow the money</h4><CashChart daily={data.daily} /><dl className="financial-breakdown"><div><dt>Net cash flow</dt><dd>{rupees(data.metrics.net_cash_flow)}</dd></div><div><dt>Opening funds recorded in period</dt><dd>{rupees(data.metrics.opening_funds)}</dd></div><div><dt>Cash count differences</dt><dd>{rupees(data.metrics.cash_variance)}</dd></div><div><dt>Recorded account balance at end date</dt><dd>{rupees(data.snapshot.recorded_balance)}</dd></div></dl><p className="hint">Opening funds and cash count differences are excluded from cash flow. Bank entries are manually recorded.</p></section>
      </div>
      <div className="dashboard-grid">
        <section className="workspace-card"><h4>Understand the earnings estimate</h4><dl className="financial-breakdown"><div><dt>Net billed, including GST</dt><dd>{rupees(data.metrics.billed)}</dd></div><div><dt>Net sales, excluding GST</dt><dd>{rupees(data.metrics.revenue)}</dd></div><div><dt>Recorded net cost of goods sold</dt><dd>{rupees(data.metrics.cogs)}</dd></div><div><dt>Net paid expenses</dt><dd>{rupees(data.metrics.expenses)}</dd></div><div><dt>Recorded damage and wastage</dt><dd>{rupees(data.metrics.stock_loss)}</dd></div><div><dt>Estimate after these expenses and losses</dt><dd>{rupees(data.metrics.after_recorded_expenses)}</dd></div></dl><p className="hint">This is not net profit. Unrecorded costs, accruals, financing, depreciation and stock count adjustments are excluded.</p></section>
        <section className="workspace-card"><h4>Stock and commitments</h4><p className="hint">Recorded position through {data.end}.</p><dl className="financial-breakdown"><div><dt>Supplier outstanding</dt><dd>{rupees(data.snapshot.supplier_outstanding)}</dd></div><div><dt>Recorded inventory value</dt><dd>{rupees(data.snapshot.inventory_value)}</dd></div><div><dt>Products at reorder level</dt><dd>{data.snapshot.low_stock_count}</dd></div><div><dt>Batches expiring within 30 days</dt><dd>{data.snapshot.expiring_batch_count}</dd></div><div><dt>Expired batches with stock</dt><dd>{data.snapshot.expired_batch_count}</dd></div></dl><p className="hint">Stock with no recorded movement in this store is not assessed. Unsynchronized offline activity is absent.</p></section>
      </div>
      <details className="workspace-card"><summary>Daily records and calculation notes</summary><p>{data.basis}</p><p>{data.metrics.sale_count} posted sales · {data.metrics.credit_count} credit notes in this period.</p><div className="table-scroll"><table><caption>Daily recorded business</caption><thead><tr><th>Date</th><th>Net sales excl. GST</th><th>Money in</th><th>Money out</th><th>Net cash flow</th></tr></thead><tbody>{data.daily.map((day) => <tr key={day.date}><td>{day.date}</td><td>{rupees(day.revenue)}</td><td>{rupees(day.money_in)}</td><td>{rupees(day.money_out)}</td><td>{rupees(day.net_cash_flow)}</td></tr>)}</tbody></table></div><ul>{data.limitations.map((note) => <li key={note}>{note}</li>)}</ul></details>
    </>}
  </div>;
}
