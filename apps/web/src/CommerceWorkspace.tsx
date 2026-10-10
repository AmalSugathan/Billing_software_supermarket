import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, request, schemas, type Business, type Session, type Store, type Terminal } from './identity-api';
import { finance, type Account, type CashSession } from './finance-api';
import { operations, type Product } from './operations-api';
import { addOne, commerce, loadPending, persistPending, type PendingCommand, type Quote, type Receipt, type Payable, type SupplierPayment } from './commerce-api';

const moneyPattern = '[0-9]{1,12}(\\.[0-9]{1,2})?';
const quantityPattern = '[0-9]{1,12}(\\.[0-9]{1,3})?';
const message = (error: unknown) => error instanceof Error ? error.message : 'The command failed.';
type CartLine = { product: Product; quantity: string; discount: string };
type Payment = { account_id: string; amount: string };

export default function CommerceWorkspace({ business, session, stores, mode, onRecorded }: {
  business: Business; session: Session; stores: Store[]; mode: 'pos' | 'suppliers'; onRecorded: () => Promise<void>;
}) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  return <section className="workspace-card"><h4>{mode === 'pos' ? 'POS billing' : 'Supplier payments'}</h4>
    <label>Store<select value={storeId} onChange={(event) => setStoreId(event.target.value)}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
    {storeId ? <CommerceStore key={session.user.id + storeId + mode} {...{ business, session, storeId, mode, onRecorded }} storeName={stores.find((store) => store.id === storeId)?.name ?? ''} /> : <p>Create a store first.</p>}
  </section>;
}

function CommerceStore({ business, session, storeId, storeName, mode, onRecorded }: {
  business: Business; session: Session; storeId: string; storeName: string; mode: 'pos' | 'suppliers'; onRecorded: () => Promise<void>;
}) {
  const base = '/businesses/' + business.id;
  const path = base + '/stores/' + storeId;
  const storageKey = 'supermarket-command:' + session.user.id + ':' + business.id + ':' + storeId + ':' + mode;
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<CashSession[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [terminalId, setTerminalId] = useState('');
  const [cart, setCart] = useState<CartLine[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [search, setSearch] = useState('');
  const [billDiscount, setBillDiscount] = useState('0');
  const [tenders, setTenders] = useState<Payment[]>([]);
  const [quoted, setQuoted] = useState<{ fingerprint: string; value: Quote } | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [history, setHistory] = useState<Receipt[]>([]);
  const [payables, setPayables] = useState<Payable[]>([]);
  const [payments, setPayments] = useState<SupplierPayment[]>([]);
  const [payAccount, setPayAccount] = useState('');
  const [recovery] = useState(() => {
    try { const command = loadPending(storageKey); if (command && command.route !== path + (mode === 'pos' ? '/sales' : '/supplier-payments')) throw new Error('Saved command does not belong to this store.'); return { command, error: '' }; }
    catch (error) { return { command: null, error: message(error) }; }
  });
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(recovery.command);
  const [pending, setPending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState(recovery.error);
  const [notice, setNotice] = useState('');
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const storageError = !!recovery.error;
  const searchRef = useRef<HTMLInputElement>(null);
  const canApprove = business.capabilities.includes('actions.approve');
  const canFinance = business.capabilities.includes('finance.read');
  const canCash = business.capabilities.includes('cash.sessions') || canFinance || business.capabilities.includes('expenses.manage');
  const activeSessions = sessions.filter((item) => !item.closed);
  const ownSession = activeSessions.find((item) => item.actor_user_id === session.user.id && accounts.find((account) => account.id === item.account_id)?.terminal_id === terminalId);
  const saleAccounts = accounts.filter((account) => ['cash', 'upi', 'card'].includes(account.kind) && (account.kind !== 'cash' || account.id === ownSession?.account_id));
  const saleBody = useCallback(() => ({ terminal_id: terminalId, lines: cart.map((line) => ({ product_id: line.product.id, quantity: line.quantity, expected_price: line.product.selling_price, discount: line.discount })), bill_discount: billDiscount }), [terminalId, cart, billDiscount]);
  const quote = quoted?.fingerprint === JSON.stringify(saleBody()) && !pendingCommand ? quoted.value : null;
  const load = useCallback(async (signal?: AbortSignal) => {
    const [accountItems, sessionItems, terminalItems, receipts, bills, paid] = await Promise.all([
      mode === 'pos' || canApprove ? request(path + '/accounts', finance.accounts, { signal }) : Promise.resolve([]),
      canCash ? request(path + '/cash-sessions?limit=200', finance.sessions, { signal }) : Promise.resolve([]),
      mode === 'pos' ? request(base + '/terminals', schemas.terminals, { signal }) : Promise.resolve([]),
      mode === 'pos' ? request(path + '/sales?offset=' + offset, commerce.receipts, { signal }) : Promise.resolve([]),
      mode === 'suppliers' ? request(path + '/supplier-payables?offset=' + offset, commerce.payables, { signal }) : Promise.resolve([]),
      mode === 'suppliers' && canFinance ? request(path + '/supplier-payments?offset=' + offset, commerce.supplierPayments, { signal }) : Promise.resolve([]),
    ]);
    return { accountItems, sessionItems, terminalItems: terminalItems.filter((terminal) => terminal.store_id === storeId), receipts, bills, paid };
  }, [mode, path, base, canCash, canApprove, canFinance, storeId, offset]);
  const apply = useCallback((data: Awaited<ReturnType<typeof load>>) => {
    setAccounts(data.accountItems); setSessions(data.sessionItems); setTerminals(data.terminalItems);
    setTerminalId((previous) => previous || data.terminalItems[0]?.id || '');
    setHistory(data.receipts); setPayables(data.bills); setPayments(data.paid); setLoaded(true);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).then((data) => { if (!controller.signal.aborted) apply(data); }).catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [load, apply]);
  useEffect(() => {
    if (mode !== 'pos' || !cart.length || !terminalId || pendingCommand) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void request(path + '/sales/preview', commerce.quote, { method: 'POST', csrf: session.csrf_token, body: saleBody(), signal: controller.signal }).then((value) => { if (!controller.signal.aborted) setQuoted({ fingerprint: JSON.stringify(saleBody()), value }); }).catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    }, 150);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [cart, terminalId, mode, pendingCommand, path, saleBody, session.csrf_token]);

  function addProduct(product: Product) {
    setError(''); setReceipt(null); setTenders([]); setConfirmed(false);
    try { const found = cart.find((line) => line.product.id === product.id); const quantity = found ? addOne(found.quantity) : '1'; setCart(found ? cart.map((line) => line.product.id === product.id ? { ...line, quantity } : line) : [...cart, { product, quantity, discount: '0' }]); }
    catch (error) { setError(message(error)); }
    searchRef.current?.focus();
  }
  async function findProducts(event: FormEvent) {
    event.preventDefault(); setPending(true); setError('');
    try {
      let scanned: Product | null = null;
      try { scanned = await request(base + '/products/resolve-barcode?value=' + encodeURIComponent(search), operations.product); }
      catch (error) { if (!(error instanceof ApiError) || error.status !== 404) throw error; }
      if (scanned) { addProduct(scanned); setSearch(''); setProducts([]); }
      else { const results = await request(base + '/products?q=' + encodeURIComponent(search), operations.products); const exact = results.find((product) => product.sku.toLowerCase() === search.trim().toLowerCase()); if (exact) { addProduct(exact); setSearch(''); setProducts([]); } else setProducts(results.filter((product) => product.active)); }
    } catch (error) { setError(message(error)); }
    finally { setPending(false); }
  }
  async function send(command: PendingCommand) {
    setPending(true); setError(''); setConfirmed(false);
    try {
      if (mode === 'pos') {
        const result = await request(command.route, commerce.receipt, { method: 'POST', csrf: session.csrf_token, body: command.body, idempotencyKey: command.key });
        setReceipt(result); setNotice('Sale completed: ' + result.invoice_number); setCart([]); setTenders([]); setBillDiscount('0');
      } else {
        const result = await request(command.route, commerce.supplierPayment, { method: 'POST', csrf: session.csrf_token, body: command.body, idempotencyKey: command.key });
        setNotice('Supplier payment recorded: ' + result.reference);
      }
      localStorage.removeItem(storageKey); setPendingCommand(null);
      try { apply(await load()); await onRecorded(); } catch (error) { setError('Transaction completed. Refresh failed: ' + message(error)); }
    } catch (error) {
      if (error instanceof ApiError && [401, 403, 404, 409, 422].includes(error.status)) { localStorage.removeItem(storageKey); setPendingCommand(null); }
      setError(message(error));
    } finally { setPending(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (pending || pendingCommand || storageError) return;
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = mode === 'pos' ? { ...saleBody(), confirmed, payments: tenders.map((payment) => {
      const account = accounts.find((item) => item.id === payment.account_id);
      return { ...payment, method: account?.kind, cash_session_id: account?.kind === 'cash' ? ownSession?.id ?? null : null };
    }) } : { purchase_id: String(form.get('purchase_id')), account_id: payAccount, cash_session_id: String(form.get('cash_session_id') || '') || null, amount: String(form.get('amount')), reference: String(form.get('reference')), reason: String(form.get('reason')), confirmed };
    const command = { route: path + (mode === 'pos' ? '/sales' : '/supplier-payments'), key: crypto.randomUUID(), body };
    try { persistPending(storageKey, command); setPendingCommand(command); await send(command); }
    catch (error) { setError(message(error)); }
  }
  const blocked = pending || !!pendingCommand || storageError;
  return <div className="commerce-workspace">
    <p className="hint">{mode === 'pos' ? 'Online checkout. Search by name or enter a SKU; a barcode scanner is optional. Prices include GST. Open your drawer in Cash & bank before accepting cash.' : 'Record a payment already made against a reviewed purchase invoice. This does not send money through a bank.'}</p>
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status" className="success-notice">{notice}</p>}
    {pendingCommand && <section role="status"><p>A submitted transaction needs its server result. Retry it before starting another transaction.</p><button type="button" disabled={pending} onClick={() => void send(pendingCommand)}>Recover submitted transaction</button></section>}
    {!loaded && <p role="status">Loading store records...</p>}
    {mode === 'pos' && <>
      <label>Terminal<select disabled={blocked || cart.length > 0} value={terminalId} onChange={(event) => { setTerminalId(event.target.value); setConfirmed(false); setTenders([]); }}>{terminals.map((terminal) => <option key={terminal.id} value={terminal.id}>{terminal.name}</option>)}</select></label>
      {!terminals.length && loaded && <p>Add a checkout terminal in Settings & access first.</p>}
      <form className="search-row" onSubmit={(event) => void findProducts(event)}><label>Product name, SKU or barcode<input ref={searchRef} value={search} onChange={(event) => setSearch(event.target.value)} required maxLength={64} disabled={blocked} autoComplete="off" /></label><button disabled={blocked}>Find / add product</button></form>
      <ul className="record-list">{products.map((product) => <li key={product.id}><button type="button" disabled={blocked} onClick={() => addProduct(product)}>{product.name} / {product.sku} / INR {product.selling_price}</button></li>)}</ul>
      <form onSubmit={(event) => void submit(event)}><fieldset disabled={blocked} className="checkout-grid"><div className="checkout-items">
        <div className="table-scroll"><table><caption>Current bill</caption><thead><tr><th>Product</th><th>Quantity</th><th>Price</th><th>Item discount (INR)</th><th>Remove</th></tr></thead><tbody>{cart.map((line, index) => <tr key={line.product.id}><td>{line.product.name}<small>{line.product.sku} / {line.product.unit}</small></td><td><input aria-label={'Quantity for ' + line.product.name} value={line.quantity} inputMode="decimal" required pattern={quantityPattern} onChange={(event) => { setCart(cart.map((item, i) => i === index ? { ...item, quantity: event.target.value } : item)); setTenders([]); setConfirmed(false); }} /></td><td>INR {line.product.selling_price}</td><td><input aria-label={'Discount for ' + line.product.name} value={line.discount} inputMode="decimal" required pattern={moneyPattern} onChange={(event) => { setCart(cart.map((item, i) => i === index ? { ...item, discount: event.target.value } : item)); setTenders([]); setConfirmed(false); }} /></td><td><button type="button" className="secondary-button" onClick={() => { setCart(cart.filter((_, i) => i !== index)); setTenders([]); setConfirmed(false); }}>Remove</button></td></tr>)}</tbody></table></div>
        {!cart.length && <div className="empty-bill"><strong>Your bill is empty</strong><p>Search for a product above to start billing.</p></div>}
        </div><div className="checkout-payment"><h4>Payment</h4><label>Bill discount (INR)<input value={billDiscount} inputMode="decimal" required pattern={moneyPattern} onChange={(event) => { setBillDiscount(event.target.value); setTenders([]); setConfirmed(false); }} /></label>
        {quote && <div className="bill-total" role="status"><strong>Total INR {quote.total}</strong><span>Savings INR {quote.discount}</span><details><summary>Tax breakdown</summary><p>Taxable INR {quote.taxable_total} | CGST INR {quote.cgst} | SGST INR {quote.sgst}</p></details></div>}
        {cart.length > 0 && !quote && <p>Waiting for a valid server quote...</p>}
        {tenders.map((payment, index) => <div className="search-row" key={index}><label>Payment account<select required value={payment.account_id} onChange={(event) => { setTenders(tenders.map((item, i) => i === index ? { ...item, account_id: event.target.value } : item)); setConfirmed(false); }}><option value="">Choose account</option>{saleAccounts.map((account) => <option key={account.id} value={account.id}>{account.name} ({account.kind})</option>)}</select></label><label>Received amount (INR)<input required inputMode="decimal" pattern={moneyPattern} value={payment.amount} onChange={(event) => { setTenders(tenders.map((item, i) => i === index ? { ...item, amount: event.target.value } : item)); setConfirmed(false); }} /></label><button type="button" className="secondary-button" onClick={() => { setTenders(tenders.filter((_, i) => i !== index)); setConfirmed(false); }}>Remove payment</button></div>)}
        <button type="button" className="secondary-button" disabled={!quote || tenders.length >= 10 || !saleAccounts.length} onClick={() => { setTenders([...tenders, { account_id: tenders.length ? '' : saleAccounts[0]?.id ?? '', amount: tenders.length ? '' : quote?.total ?? '' }]); setConfirmed(false); }}>Add payment / split</button>
        <p className="hint">Enter the amount applied to this bill, after giving cash change. UPI/card must be verified by the cashier.</p>
        <label className="checkbox-label"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I confirm the goods and received payments.</label><button disabled={!quote || !confirmed || !tenders.length}>Complete sale</button></div>
      </fieldset></form>
      <h4>Recent receipts</h4><ul className="record-list">{history.map((sale) => <li key={sale.id}><button type="button" className="secondary-button" disabled={blocked} onClick={() => setReceipt(sale)}>{sale.invoice_number} / INR {sale.total}</button></li>)}</ul>
      {receipt && <><button type="button" onClick={() => window.print()}>Print / reprint receipt</button><article className="pos-receipt"><h3>{business.name}</h3><p>{storeName} | {receipt.invoice_number}</p><p>{new Date(receipt.created_at).toLocaleString()}</p>{receipt.lines.map((line) => <p key={line.line_number}>{line.product_name} | {line.quantity} {line.unit} | INR {line.total}</p>)}<p>Discount INR {receipt.discount}</p><p>Taxable INR {receipt.taxable_total} | CGST INR {receipt.cgst} | SGST INR {receipt.sgst}</p><strong>Total INR {receipt.total}</strong>{receipt.payments.map((payment) => <p key={payment.id}>{payment.method}: INR {payment.amount}</p>)}</article></>}
    </>}
    {mode === 'suppliers' && <>
      <div className="table-scroll"><table><caption>Reviewed supplier invoices</caption><thead><tr><th>Supplier / invoice</th><th>Invoice amount</th><th>Paid</th><th>Outstanding</th></tr></thead><tbody>{payables.map((bill) => <tr key={bill.purchase_id}><td>{bill.supplier_name}<small>{bill.invoice_number}</small></td><td>INR {bill.total}</td><td>INR {bill.paid}</td><td>INR {bill.outstanding}</td></tr>)}</tbody></table></div>
      {canApprove && <form className="form-grid" onSubmit={(event) => void submit(event)} onChange={(event) => { if ((event.target as HTMLElement).getAttribute('name') !== 'confirmed') setConfirmed(false); }}><fieldset disabled={blocked} className="full-width form-grid">
        <label>Purchase invoice<select name="purchase_id" required><option value="">Select invoice</option>{payables.filter((bill) => /[1-9]/.test(bill.outstanding)).map((bill) => <option key={bill.purchase_id} value={bill.purchase_id}>{bill.supplier_name} / {bill.invoice_number} / due INR {bill.outstanding}</option>)}</select></label>
        <label>Source account<select value={payAccount} required onChange={(event) => setPayAccount(event.target.value)}><option value="">Choose account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name} / INR {account.balance ?? 'restricted'}</option>)}</select></label>
        {accounts.find((account) => account.id === payAccount)?.kind === 'cash' && <label>Open cash session<select name="cash_session_id" required><option value="">Choose session</option>{activeSessions.filter((item) => item.account_id === payAccount).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>}
        <label>Paid amount (INR)<input name="amount" required inputMode="decimal" pattern={moneyPattern} /></label><label>Payment reference<input name="reference" required maxLength={100} /></label><label>Reason / payment evidence<textarea name="reason" required minLength={3} maxLength={500} /></label>
        <label className="checkbox-label full-width"><input name="confirmed" type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I approve this invoice allocation and confirm payment was made.</label><p className="hint full-width">Recorded payments stay in your audit history. Use Corrections & returns to review a reversal.</p><button disabled={!confirmed}>Record supplier payment</button>
      </fieldset></form>}
      {canFinance && <><h4>Payment history</h4><ul className="record-list">{payments.map((payment) => <li key={payment.id}><strong>{payment.reference} / INR {payment.amount}</strong><span>{payment.reason} / {new Date(payment.created_at).toLocaleString()}</span></li>)}</ul></>}
    </>}
    <div className="pagination"><button type="button" className="secondary-button" disabled={blocked || offset === 0} onClick={() => setOffset(offset - 50)}>Previous records</button><button type="button" className="secondary-button" disabled={blocked || (mode === 'pos' ? history : payables).length < 50} onClick={() => setOffset(offset + 50)}>Next records</button><button type="button" className="secondary-button" disabled={blocked} onClick={() => { setConfirmed(false); void load().then(apply).catch((error: unknown) => setError(message(error))); }}>Refresh records</button></div>
  </div>;
}
