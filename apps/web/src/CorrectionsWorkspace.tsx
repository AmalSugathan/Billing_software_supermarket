import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { z } from 'zod';
import { commerce, loadPending, persistPending, type PendingCommand, type Receipt, type SupplierPayment } from './commerce-api';
import { finance, type Account, type CashSession } from './finance-api';
import { ApiError, request, type Business, type Session, type Store } from './identity-api';
import { decimal, units } from './offline-store';
const signed = z.string().regex(/^-?\d+(\.\d+)?$/);
const creditLine = z.object({ product_id: z.string(), quantity: signed, disposition: z.string(), taxable: signed, cgst: signed, sgst: signed, total: signed });
const correction = z.object({ id: z.string(), target_id: z.string(), kind: z.string(), reason: z.string(), total: signed, created_at: z.string(), lines: z.array(creditLine) });
const batch = z.object({ id: z.string(), product_id: z.string(), product_name: z.string(), quantity: signed, batch_number: z.string().nullable(), expiry_date: z.string().nullable() });
type CreditLine = z.infer<typeof creditLine>;
export default function CorrectionsWorkspace({ business, session, stores, onRecorded }: { business: Business; session: Session; stores: Store[]; onRecorded: () => Promise<void> }) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [payments, setPayments] = useState<SupplierPayment[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<CashSession[]>([]);
  const [batches, setBatches] = useState<z.infer<typeof batch>[]>([]);
  const [history, setHistory] = useState<z.infer<typeof correction>[]>([]);
  const [selected, setSelected] = useState('');
  const [preview, setPreview] = useState<CreditLine[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [recovery, setRecovery] = useState<PendingCommand | null>(null);
  const base = '/businesses/' + business.id + '/stores/' + storeId;
  const storageKey = 'corrections:' + session.user.id + ':' + business.id + ':' + storeId;
  const load = useCallback(async () => {
    const [sales, paid, cash, shifts, stock, corrected] = await Promise.all([request(base + '/sales', commerce.receipts), request(base + '/supplier-payments', commerce.supplierPayments), request(base + '/accounts', finance.accounts), request(base + '/cash-sessions', finance.sessions), request(base + '/stock-batches', z.array(batch)), request(base + '/corrections', z.array(correction))]);
    setReceipts(sales); setPayments(paid); setAccounts(cash); setSessions(shifts); setBatches(stock); setHistory(corrected);
  }, [base]);
  useEffect(() => { void Promise.resolve().then(load).catch((error: unknown) => setError(error instanceof Error ? error.message : 'Corrections could not load.')); void Promise.resolve().then(() => loadPending(storageKey)).then(setRecovery).catch(() => setError('Saved correction command could not be read. Review before posting again.')); }, [load, storageKey]);
  async function action(work: () => Promise<void>) { if (busy.current) return; busy.current = true; setPending(true); setError(''); setNotice(''); try { await work(); } catch (error) { setError(error instanceof Error ? error.message : 'Correction failed.'); } finally { busy.current = false; setPending(false); } }
  async function post(command: PendingCommand) {
    persistPending(storageKey, command); setRecovery(command);
    try { const result = await request(command.route, correction, { method: 'POST', body: command.body, csrf: session.csrf_token, idempotencyKey: command.key }); localStorage.removeItem(storageKey); setRecovery(null); setNotice('Correction recorded: ' + result.id + '. Original records remain available.'); setPreview([]); setDraft(null); await load(); await onRecorded(); }
    catch (error) { if (error instanceof ApiError && [401, 403, 404, 409, 422].includes(error.status)) { localStorage.removeItem(storageKey); setRecovery(null); } throw error; }
  }
  function command(route: string, body: Record<string, unknown>) { return { route, body, key: crypto.randomUUID() }; }
  const sale = receipts.find((item) => item.id === selected);
  async function previewCredit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!sale) return; const form = new FormData(event.currentTarget);
    const kind = String(form.get('kind')); const lines = sale.lines.map((line) => ({ product_id: line.product_id, quantity: kind === 'cancel' ? line.quantity : String(form.get('quantity:' + line.product_id) ?? ''), disposition: kind === 'cancel' ? 'restock' : String(form.get('disposition:' + line.product_id)) })).filter((line) => line.quantity && units(line.quantity, 3) > 0n);
    const body = { kind, lines, reason: String(form.get('reason')), confirmed: form.has('confirmed'), payments: [] };
    await action(async () => { const computed = await request(base + '/sales/' + sale.id + '/credits/preview', z.array(creditLine), { method: 'POST', csrf: session.csrf_token, body }); setPreview(computed); setDraft(body); });
  }
  function sessionFor(accountId: string) { return sessions.find((item) => item.account_id === accountId && !item.closed)?.id ?? null; }
  function accountField() { return <label>Refund account<select name="account_id" required>{accounts.filter((account) => ['cash', 'upi', 'card'].includes(account.kind)).map((account) => <option key={account.id} value={account.id}>{account.name} · {account.kind}</option>)}</select></label>; }
  return <section className="workspace-card"><h4>Owner-approved corrections</h4><label>Store<select disabled={pending || !!recovery} value={storeId} onChange={(event) => { setStoreId(event.target.value); setPreview([]); setDraft(null); setSelected(''); }}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
    <p>Corrections create linked credit notes or compensating ledger entries. Review physical goods and money before approving.</p>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {recovery && <div role="status"><p>A correction response is unconfirmed. Retry this saved command before starting another.</p><button disabled={pending} onClick={() => void action(() => post(recovery))}>Retry saved correction</button></div>}
    <fieldset disabled={pending || !!recovery}><legend>Sales return or cancellation</legend><label>Original receipt<select value={selected} onChange={(event) => { setSelected(event.target.value); setPreview([]); setDraft(null); }}><option value="">Choose receipt</option>{receipts.map((receipt) => <option key={receipt.id} value={receipt.id}>{receipt.invoice_number} · ₹{receipt.total}</option>)}</select></label>
      {sale && <form onSubmit={(event) => void previewCredit(event)} onChange={() => { setPreview([]); setDraft(null); }}><label>Correction type<select name="kind"><option value="return">Return selected goods</option><option value="cancel">Cancel full bill; all goods restocked</option></select></label>
      {sale.lines.map((line) => <div className="search-row" key={line.product_id}><label>{line.product_name} · originally {line.quantity}<input name={'quantity:' + line.product_id} aria-label={'Return ' + line.product_name} inputMode="decimal" pattern="[0-9]{1,12}(\.[0-9]{1,3})?" /></label><label>Condition<select name={'disposition:' + line.product_id}><option value="restock">Restock goods</option><option value="discard">Damaged; discard</option></select></label></div>)}<label>Reason<input name="reason" required minLength={3} maxLength={500} /></label><label><input name="confirmed" type="checkbox" required />I have checked the goods and original receipt.</label><button>Calculate refund for review</button></form>}
      {draft && preview.length > 0 && <form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const accountId = String(form.get('account_id')); const account = accounts.find((item) => item.id === accountId); const amount = decimal(preview.reduce((sum, line) => sum + units(line.total, 2), 0n), 2); void action(() => post(command(base + '/sales/' + selected + '/credits', { ...draft, payments: amount === '0.00' ? [] : [{ account_id: accountId, amount, method: account?.kind, cash_session_id: account?.kind === 'cash' ? sessionFor(accountId) : null }] }))); }}><p>Exact refund ₹{decimal(preview.reduce((sum, line) => sum + units(line.total, 2), 0n), 2)}</p>{accountField()}<label><input type="checkbox" required />I approve paying this refund.</label><button>Approve and record refund</button></form>}
    </fieldset>
    <fieldset disabled={pending || !!recovery}><legend>Reverse an incorrect supplier payment</legend><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const id = String(form.get('payment_id')); const payment = payments.find((item) => item.id === id); void action(() => post(command(base + '/supplier-payments/' + id + '/reverse', { reason: String(form.get('reason')), confirmed: form.has('confirmed'), cash_session_id: payment ? sessionFor(payment.account_id) : null }))); }}><label>Payment<select name="payment_id" required>{payments.map((payment) => <option key={payment.id} value={payment.id}>{payment.reference} · ₹{payment.amount}</option>)}</select></label><label>Reason<input name="reason" required minLength={3} maxLength={500} /></label><label><input type="checkbox" name="confirmed" required />I verified the money has returned to its original account.</label><button disabled={!payments.length}>Approve payment reversal</button></form></fieldset>
    <fieldset disabled={pending || !!recovery}><legend>Reverse an unused, unpaid purchase</legend><p>The server blocks reversal if goods were used or any payment remains. Used goods need a separately reviewed supplier return.</p><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(() => post(command(base + '/purchases/' + String(form.get('purchase_id')) + '/reverse', { reason: String(form.get('reason')), confirmed: form.has('confirmed') }))); }}><label>Original purchase ID<input name="purchase_id" required pattern="[0-9a-fA-F-]{36}" /></label><label>Reason<input name="reason" required minLength={3} maxLength={500} /></label><label><input type="checkbox" name="confirmed" required />I approve reversing this incorrect purchase.</label><button>Approve purchase reversal</button></form></fieldset>
    <fieldset disabled={pending || !!recovery}><legend>Record a physical batch count</legend><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const id = String(form.get('batch_id')); const source = batches.find((item) => item.id === id); const body = { expected_count: source?.quantity, actual_count: String(form.get('actual')), added_unit_cost: String(form.get('cost')) || null, reason: String(form.get('reason')), confirmed: form.has('confirmed') }; void action(() => post(command(base + '/stock-batches/' + id + '/adjust', body))); }}><label>Batch<select name="batch_id" required>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.product_name} · {batch.batch_number ?? 'Opening lot'} · {batch.quantity}</option>)}</select></label><label>Physical count<input name="actual" required inputMode="decimal" pattern="[0-9]{1,12}(\.[0-9]{1,3})?" /></label><label>Cost per added unit (required for increase)<input name="cost" inputMode="decimal" pattern="[0-9]{1,12}(\.[0-9]{1,6})?" /></label><label>Count discrepancy reason<input name="reason" required minLength={3} maxLength={500} /></label><label><input type="checkbox" name="confirmed" required />I counted this batch and approve the adjustment.</label><button disabled={!batches.length}>Approve count adjustment</button></form></fieldset>
    <h4>Correction history</h4>{history.map((item) => <p key={item.id}>{item.kind} · ₹{item.total} · {item.reason} · {item.id}</p>)}
  </section>;
}
