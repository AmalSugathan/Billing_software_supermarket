import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { z } from 'zod';
import { request, schemas, type Business, type Session, type Store, type Terminal } from './identity-api';
import { finance, type Account, type CashSession, type Expense, type FinancialMovement } from './finance-api';

const amount = '[0-9]{1,12}(\\.[0-9]{1,2})?';
const value = (form: FormData, name: string) => String(form.get(name) ?? '').trim();
const message = (error: unknown) => error instanceof Error ? error.message : 'Financial command failed.';
const money = (value: string | null) => value === null ? 'Restricted' : 'INR ' + value;
function Amount({ label, name }: { label: string; name: string }) { return <label>{label}<input name={name} inputMode="decimal" required pattern={amount} /></label>; }

export default function FinanceWorkspace({ business, session, stores, mode, onRecorded }: {
  business: Business; session: Session; stores: Store[]; mode: 'expenses' | 'cash'; onRecorded: () => Promise<void>;
}) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [cashSessions, setCashSessions] = useState<CashSession[]>([]);
  const [movements, setMovements] = useState<FinancialMovement[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [expenseAccount, setExpenseAccount] = useState('');
  const [moveAccount, setMoveAccount] = useState('');
  const [accountKind, setAccountKind] = useState('cash');
  const [closeId, setCloseId] = useState('');
  const [openingAccount, setOpeningAccount] = useState('');
  const [pending, setPending] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [offset, setOffset] = useState(0);
  const replay = useRef(new Map<string, { fingerprint: string; key: string }>());
  const base = '/businesses/' + business.id;
  const path = base + '/stores/' + storeId;
  const canApprove = business.capabilities.includes('actions.approve');
  const canExpense = business.capabilities.includes('expenses.manage');
  const canFinance = business.capabilities.includes('finance.read');
  const canCash = business.capabilities.includes('cash.sessions');
  const ownSession = cashSessions.find((item) => item.id === closeId);
  const active = cashSessions.filter((item) => !item.closed);
  const load = useCallback(async (signal?: AbortSignal) => {
    const [accountItems, expenseItems, sessionItems, movementItems, terminalItems] = await Promise.all([
      request(path + '/accounts', finance.accounts, { signal }),
      canExpense ? request(path + '/expenses?offset=' + offset, finance.expenses, { signal }) : Promise.resolve([]),
      request(path + '/cash-sessions', finance.sessions, { signal }),
      canFinance ? request(path + '/money-movements', finance.movements, { signal }) : Promise.resolve([]),
      canApprove ? request(base + '/terminals', schemas.terminals, { signal }) : Promise.resolve([]),
    ]);
    return { accountItems, expenseItems, sessionItems, movementItems, terminalItems };
  }, [path, base, canExpense, canFinance, canApprove, offset]);
  const apply = useCallback((data: Awaited<ReturnType<typeof load>>) => {
    setAccounts(data.accountItems); setExpenses(data.expenseItems); setCashSessions(data.sessionItems); setMovements(data.movementItems); setTerminals(data.terminalItems); setLoaded(true);
  }, []);
  useEffect(() => {
    if (!storeId) return;
    const controller = new AbortController();
    void load(controller.signal).then((data) => { if (!controller.signal.aborted) apply(data); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [storeId, load, apply]);
  function clearConfirmation(event: FormEvent<HTMLFormElement>) {
    if ((event.target as HTMLElement).getAttribute('name') === 'confirmed') return;
    const field = event.currentTarget.elements.namedItem('confirmed');
    if (field instanceof HTMLInputElement) field.checked = false;
  }
  async function send<T>(route: string, schema: z.ZodType<T>, body: Record<string, unknown>, text: (result: T) => string) {
    const fingerprint = JSON.stringify({ route, body });
    if (replay.current.get(route)?.fingerprint !== fingerprint) replay.current.set(route, { fingerprint, key: crypto.randomUUID() });
    const idempotencyKey = replay.current.get(route)!.key;
    const result = await request(route, schema, { method: 'POST', body, csrf: session.csrf_token, idempotencyKey });
    replay.current.delete(route); setNotice(text(result));
    return result;
  }
  async function submit(event: FormEvent<HTMLFormElement>, kind: string, expense?: Expense) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    const body: Record<string, unknown> = { confirmed: form.has('confirmed') };
    setPending(true); setError(''); setNotice('');
    try {
      if (kind === 'account') {
        for (const name of ['name', 'kind', 'opening_amount', 'reason']) body[name] = value(form, name);
        body.terminal_id = value(form, 'terminal_id') || null;
        await send(path + '/accounts', finance.account, body, (result) => 'Account recorded: ' + result.name + '. Opening funds are not revenue.');
      } else if (kind === 'opening-variance') {
        body.actual_cash = value(form, 'actual_cash'); body.reason = value(form, 'reason'); body.expected_cash = accounts.find((item) => item.id === openingAccount)?.balance ?? '';
        await send(path + '/accounts/' + openingAccount + '/opening-variance', finance.movement, body, () => 'Opening cash difference recorded separately from receipts and expenses.');
      } else if (kind === 'expense') {
        for (const name of ['account_id', 'reference', 'expense_date', 'category', 'description', 'amount']) body[name] = value(form, name);
        body.cash_session_id = value(form, 'cash_session_id') || null;
        await send(path + '/expenses', finance.expense, body, (result) => 'Expense posted. Reference: ' + result.id);
      } else if (kind === 'movement') {
        for (const name of ['account_id', 'kind', 'amount', 'reason']) body[name] = value(form, name);
        body.cash_session_id = value(form, 'cash_session_id') || null;
        await send(path + '/money-movements', finance.movement, body, (result) => 'Money movement recorded. Reference: ' + result.id);
      } else if (kind === 'open') {
        for (const name of ['account_id', 'opening_cash', 'reason']) body[name] = value(form, name);
        await send(path + '/cash-sessions', finance.cashSession, body, (result) => 'Cashier session opened. Reference: ' + result.id);
      } else if (kind === 'close') {
        body.actual_cash = value(form, 'actual_cash'); body.reason = value(form, 'reason'); body.expected_cash = ownSession?.expected_cash ?? '';
        await send(path + '/cash-sessions/' + closeId + '/close', finance.closing, body, (result) => 'Cashier session closed. Cash variance: ' + money(result.variance) + '. Recorded for review.');
      } else if (expense) {
        body.reason = value(form, 'reason'); body.cash_session_id = value(form, 'cash_session_id') || null;
        await send(path + '/expenses/' + expense.id + '/reverse', finance.reversal, body, () => 'Expense reversed with a linked movement. Original retained.');
      }
      element.reset(); setCloseId(''); setExpenseAccount(''); setMoveAccount('');
      try { apply(await load()); await onRecorded(); } catch { setError('Posting succeeded; refresh failed. Refresh this page before another command.'); }
    } catch (error) { setError(message(error)); } finally { setPending(false); }
  }
  async function refresh() {
    setPending(true); setError('');
    document.querySelectorAll<HTMLInputElement>('.finance-workspace input[name="confirmed"]').forEach((input) => { input.checked = false; });
    try { apply(await load()); } catch (error) { setError(message(error)); } finally { setPending(false); }
  }
  const accountSelect = (name: string, current: string, change: (id: string) => void, cashOnly = false) => <label>{name}<select name="account_id" value={current} onChange={(event) => change(event.target.value)} required><option value="">Select account</option>{accounts.filter((item) => !cashOnly || item.kind === 'cash').map((item) => <option key={item.id} value={item.id}>{item.name} ({item.kind})</option>)}</select></label>;
  const sessionSelect = (accountId: string) => accounts.find((item) => item.id === accountId)?.kind === 'cash' && <label>Open drawer session<select name="cash_session_id" required><option value="">Select active session</option>{active.filter((item) => item.account_id === accountId).map((item) => <option key={item.id} value={item.id}>{item.id} / {item.actor_user_id}</option>)}</select></label>;
  const confirm = (text: string) => <label className="checkbox-label full-width"><input name="confirmed" type="checkbox" required />{text}</label>;
  return <section className="workspace-card finance-workspace"><h4>{mode === 'expenses' ? 'Paid expenses' : 'Cash and bank records'}</h4>
    <p className="hint">These are recorded funds and payments. Opening balances, receipts and cash variance are not sales or profit. Bank/UPI/card entries are manually recorded, without bank verification.</p>
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status" className="success-notice">{notice}</p>}
    <label>Financial store<select disabled={pending} value={storeId} onChange={(event) => { setStoreId(event.target.value); setCloseId(''); setLoaded(false); setOffset(0); setNotice(''); }}>{stores.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <button type="button" disabled={pending} onClick={() => void refresh()}>Refresh financial records</button>
    {!storeId ? <p>No assigned stores.</p> : !loaded ? <p>Loading financial records...</p> : <>
      <fieldset disabled={pending}><legend>Recorded account balances</legend><ul className="record-list">{accounts.map((item) => <li key={item.id}><strong>{item.name} ({item.kind}): {money(item.balance)}</strong><span>Opening amount: {money(item.opening_amount)}</span></li>)}</ul>{!accounts.length && <p>An owner must set up payment accounts first.</p>}</fieldset>
      {mode === 'expenses' && canExpense && <><form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'expense')}><fieldset disabled={pending} className="form-grid"><legend>Record paid expense</legend>
        {accountSelect('Expense payment account', expenseAccount, setExpenseAccount)}{sessionSelect(expenseAccount)}
        <label>Expense reference<input name="reference" required maxLength={100} /></label><label>Expense date<input name="expense_date" type="date" required /></label><label>Expense category<input name="category" required maxLength={80} /></label><Amount name="amount" label="Paid amount (INR)" /><label className="full-width">Expense description<textarea name="description" required minLength={3} maxLength={500} /></label>
        {confirm('I reviewed this paid expense and the account it will debit.')}<button>Confirm paid expense</button>
      </fieldset></form><h4>Expense records</h4><p className="hint">Up to 50 expenses per page. Reversal records return funds to the account and retain the original expense.</p>
      <ul className="record-list">{expenses.map((item) => <li key={item.id}><strong>{item.reference}: {money(item.amount)} {item.reversed && '(reversed)'}</strong><span>{item.category} / {item.expense_date} / {item.description}</span>
        {canApprove && !item.reversed && <details><summary>Reverse {item.reference}</summary><form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'reverse', item)}><fieldset disabled={pending} className="form-grid">{sessionSelect(item.account_id)}<label>Reversal reason<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I approve returning this expense amount to its recorded account.')}<button>Confirm expense reversal</button></fieldset></form></details>}
      </li>)}</ul><div className="pagination"><button disabled={pending || offset === 0} onClick={() => setOffset(offset - 50)}>Previous expenses</button><button disabled={pending || expenses.length < 50} onClick={() => setOffset(offset + 50)}>Next expenses</button></div></>}
      {mode === 'cash' && <>
        {canApprove && <details><summary>Add a payment account</summary><form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'account')}><fieldset disabled={pending} className="form-grid"><legend>Reviewed account setup</legend><label>Account name<input name="name" required maxLength={150} /></label><label>Account type<select name="kind" value={accountKind} onChange={(event) => setAccountKind(event.target.value)}>{['cash', 'bank', 'upi', 'card'].map((kind) => <option key={kind}>{kind}</option>)}</select></label>{accountKind === 'cash' && <label>Drawer terminal<select name="terminal_id" required><option value="">Select terminal</option>{terminals.filter((item) => item.store_id === storeId).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}<Amount name="opening_amount" label="Verified opening funds (INR)" /><label className="full-width">Opening funds source and reason<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I approve this opening balance. It is not revenue.')}<button>Confirm account setup</button></fieldset></form></details>}
        {canApprove && <details><summary>Reconcile cash before opening</summary><form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'opening-variance')}><fieldset disabled={pending} className="form-grid"><legend>Owner-reviewed opening count difference</legend><label>Drawer to reconcile<select required value={openingAccount} onChange={(event) => setOpeningAccount(event.target.value)}><option value="">Select closed drawer</option>{accounts.filter((item) => item.kind === 'cash' && !active.some((session) => session.account_id === item.id)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><p>Recorded cash: {money(accounts.find((item) => item.id === openingAccount)?.balance ?? null)}</p><Amount name="actual_cash" label="Actual opening count (INR)" /><label>Opening difference explanation<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I approve recording this opening count difference as a cash variance.')}<button>Confirm opening cash adjustment</button></fieldset></form></details>}
        {canCash && <form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'open')}><fieldset disabled={pending} className="form-grid"><legend>Open cashier session</legend><label>Opening drawer<select name="account_id" required><option value="">Select drawer</option>{accounts.filter((item) => item.kind === 'cash' && !active.some((session) => session.account_id === item.id)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><Amount name="opening_cash" label="Counted opening cash (INR)" /><label>Opening count notes<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I counted the opening cash and confirm this cashier session.')}<button>Confirm session opening</button></fieldset></form>}
        {canApprove && <details><summary>Record other money in or out</summary><form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'movement')}><fieldset disabled={pending} className="form-grid"><legend>Manual receipt or withdrawal</legend>{accountSelect('Money movement account', moveAccount, setMoveAccount)}{sessionSelect(moveAccount)}<label>Movement type<select name="kind"><option value="receipt">Other receipt</option><option value="withdrawal">Withdrawal</option></select></label><Amount name="amount" label="Movement amount (INR)" /><label>Source and reason<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I approve this receipt or withdrawal. It is not a POS sale or refund.')}<button>Confirm money movement</button></fieldset></form></details>}
        <h4>Cashier sessions</h4><p className="hint">First 50 sessions, newest first. Cashiers see their own sessions. Closing reconciles the drawer to the actual count and keeps variance separate from expenses.</p>
        <ul className="record-list">{cashSessions.map((item) => <li key={item.id}><strong>{accounts.find((account) => account.id === item.account_id)?.name ?? item.account_id}: {item.closed ? 'Closed' : 'Open'}</strong><span>Expected {money(item.expected_cash)} / Actual {item.actual_cash === null ? 'Not counted' : money(item.actual_cash)} / Variance {item.variance === null ? 'Not closed' : money(item.variance)}</span></li>)}</ul>
        {canCash && <form onChange={clearConfirmation} onSubmit={(event) => void submit(event, 'close')}><fieldset disabled={pending} className="form-grid"><legend>Close cashier session</legend><label>Session to close<select value={closeId} required onChange={(event) => setCloseId(event.target.value)}><option value="">Select session</option>{active.filter((item) => canApprove || item.actor_user_id === session.user.id).map((item) => <option key={item.id} value={item.id}>{accounts.find((account) => account.id === item.account_id)?.name ?? item.id}</option>)}</select></label><p>Expected cash: {ownSession ? money(ownSession.expected_cash) : 'Select a session'}</p><Amount name="actual_cash" label="Actual closing cash (INR)" /><label className="full-width">Count explanation and variance reason<textarea name="reason" required minLength={3} maxLength={500} /></label>{confirm('I confirm the actual cash count and approve the recorded variance.')}<button>Confirm session closing</button></fieldset></form>}
        {canFinance && <><h4>Recent money movements</h4><p className="hint">First 50 movements. Opening funds and cash variance are shown separately from operating receipts/payments.</p><ul className="record-list">{movements.map((item) => <li key={item.id}><strong>{item.kind}: {money(item.amount)}</strong><span>{item.reason} / {item.id}</span></li>)}</ul></>}
      </>}
    </>}
  </section>;
}
