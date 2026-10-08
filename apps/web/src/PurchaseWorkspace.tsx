import { useEffect, useRef, useState, type FormEvent } from 'react';
import { request, type Business, type Session, type Store } from './identity-api';
import { operations, type Product, type Supplier } from './operations-api';
import { purchaseSchemas, type Purchase, type PurchasePreview } from './purchase-api';

const amount = '[0-9]{1,12}(\\.[0-9]{1,2})?';
const quantity = '[0-9]{1,12}(\\.[0-9]{1,3})?';
const rate = '[0-9]{1,12}(\\.[0-9]{1,6})?';
const value = (form: FormData, name: string) => String(form.get(name) ?? '').trim();
const message = (error: unknown) => error instanceof Error ? error.message : 'Purchase could not be recorded.';

export default function PurchaseWorkspace({ business, session, stores, suppliers, onRecorded }: {
  business: Business; session: Session; stores: Store[]; suppliers: Supplier[]; onRecorded: () => Promise<void>;
}) {
  const base = '/businesses/' + business.id;
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [products, setProducts] = useState<Product[]>([]);
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [rows, setRows] = useState([crypto.randomUUID()]);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PurchasePreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [offset, setOffset] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const replay = useRef<{ fingerprint: string; key: string } | null>(null);
  const path = base + '/stores/' + storeId + '/purchases';
  const invalidate = () => { setPreview(null); setConfirmed(false); setNotice(''); };
  useEffect(() => {
    const controller = new AbortController();
    void request(base + '/products?limit=50', operations.products, { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setProducts(items); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [base]);
  useEffect(() => {
    if (!storeId) return;
    const controller = new AbortController();
    void request(path + '?offset=' + offset, purchaseSchemas.purchases, { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setPurchases(items); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [path, storeId, offset]);
  async function action(work: () => Promise<void>) {
    setPending(true); setError(''); setNotice('');
    try { await work(); } catch (error) { setError(message(error)); } finally { setPending(false); }
  }
  function body() {
    if (!formRef.current) throw new Error('Purchase form unavailable.');
    const form = new FormData(formRef.current);
    return { supplier_id: value(form, 'supplier_id'), invoice_number: value(form, 'invoice_number'),
      invoice_date: value(form, 'invoice_date'), tax_mode: value(form, 'tax_mode'), tax_kind: value(form, 'tax_kind'),
      round_off: value(form, 'round_off'), invoice_total: value(form, 'invoice_total'), review_reason: value(form, 'review_reason'), confirmed: false,
      lines: rows.map((id) => ({ product_id: value(form, id + ':product_id'), supplier_description: value(form, id + ':supplier_description'),
        purchase_unit: value(form, id + ':purchase_unit'), purchase_quantity: value(form, id + ':purchase_quantity'),
        units_per_purchase: value(form, id + ':units_per_purchase'), free_stock_quantity: value(form, id + ':free_stock_quantity'),
        conversion_evidence: value(form, id + ':conversion_evidence'), unit_rate: value(form, id + ':unit_rate'),
        discount: value(form, id + ':discount'), gst_rate: value(form, id + ':gst_rate'), hsn: value(form, id + ':hsn') || null,
        batch_number: value(form, id + ':batch_number') || null, expiry_date: value(form, id + ':expiry_date') || null })) };
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const q = value(new FormData(event.currentTarget), 'q');
    await action(async () => {
      const found = await request(base + '/products?limit=50&q=' + encodeURIComponent(q), operations.products);
      setProducts((current) => [...current.filter((item) => Object.values(selected).includes(item.id) && !found.some((match) => match.id === item.id)), ...found]);
      if (!found.length) setNotice('No existing products found. Add the product in Products, then search here.');
    });
  }
  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const payload = body(); setConfirmed(false); setPreview(null);
    await action(async () => setPreview(await request(path + '/preview', purchaseSchemas.preview, { method: 'POST', csrf: session.csrf_token, body: payload })));
  }
  async function post() {
    if (!preview || !confirmed) return;
    const payload = { ...body(), confirmed: true }; const fingerprint = JSON.stringify({ storeId, payload });
    if (replay.current?.fingerprint !== fingerprint) replay.current = { fingerprint, key: crypto.randomUUID() };
    const idempotencyKey = replay.current.key;
    await action(async () => {
      const recorded = await request(path, purchaseSchemas.purchase, { method: 'POST', body: payload, csrf: session.csrf_token, idempotencyKey });
      // Commit success is shown before optional refreshes: a failed refresh is not a failed purchase.
      formRef.current?.reset(); setRows([crypto.randomUUID()]); setSelected({}); setPreview(null); setConfirmed(false); replay.current = null;
      setNotice('Purchase posted. Inventory updated. Reference: ' + recorded.id + '. No payment recorded.');
      setPurchases((current) => [recorded, ...current.filter((item) => item.id !== recorded.id)].slice(0, 50));
      try { await onRecorded(); } catch { setError('Purchase posted successfully; audit refresh failed. Refresh the page to view it.'); }
    });
  }
  return <section className="workspace-card"><h4>Reviewed purchase entry</h4>
    <p className="hint">Enter the supplier bill, match existing products and review stock quantities before posting. Prices stay unchanged. Payments and purchase returns are not available in this increment.</p>
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status" className="success-notice">{notice}</p>}
    <label>Purchase store<select value={storeId} disabled={pending} onChange={(event) => { setStoreId(event.target.value); setOffset(0); setPurchases([]); invalidate(); }}>{stores.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {!storeId ? <p>No stores assigned.</p> : <>
      <form className="search-row" onSubmit={(event) => void search(event)}><label>Find purchase product by name or SKU<input name="q" maxLength={180} /></label><button disabled={pending}>Find products</button></form>
      <form ref={formRef} onSubmit={(event) => void review(event)} onChange={invalidate}>
        <fieldset disabled={pending} className="form-grid"><legend>Supplier invoice</legend>
          <label>Purchase supplier<select name="supplier_id" required><option value="">Select supplier</option>{suppliers.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Supplier invoice number<input name="invoice_number" required maxLength={100} /></label>
          <label>Supplier invoice date<input name="invoice_date" type="date" required /></label>
          <label>Invoice prices<select name="tax_mode"><option value="exclusive">Exclude GST</option><option value="inclusive">Include GST</option></select></label>
          <label>Invoice tax split<select name="tax_kind"><option value="intra">CGST + SGST</option><option value="inter">IGST</option></select></label>
          <label>Printed invoice total (INR)<input name="invoice_total" required inputMode="decimal" pattern={amount} /></label>
          <label>Invoice round-off (INR)<input name="round_off" required inputMode="decimal" pattern="-?[0-1](\\.[0-9]{1,2})?" defaultValue="0" /></label>
          <label className="full-width">Review notes and rounding explanation<textarea name="review_reason" required minLength={3} maxLength={500} /></label>
        </fieldset>
        {rows.map((id, index) => {
          const product = products.find((item) => item.id === selected[id]);
          return <fieldset key={id} disabled={pending} className="form-grid"><legend>Invoice line {index + 1}</legend>
            <label>Product for line {index + 1}<select name={id + ':product_id'} value={selected[id] ?? ''} required onChange={(event) => setSelected({ ...selected, [id]: event.target.value })}><option value="">Select existing product</option>{products.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name} / {item.sku} ({item.unit})</option>)}</select></label>
            <label>Supplier description {index + 1}<input name={id + ':supplier_description'} required maxLength={250} /></label>
            <label>Purchase unit {index + 1}<select name={id + ':purchase_unit'}>{['pcs', 'pack', 'carton', 'bag', 'kg', 'g', 'l', 'ml'].map((unit) => <option key={unit}>{unit}</option>)}</select></label>
            <label>Invoice quantity {index + 1}<input name={id + ':purchase_quantity'} required inputMode="decimal" pattern={quantity} /></label>
            <label>Stock units per purchase unit {index + 1}<input name={id + ':units_per_purchase'} required inputMode="decimal" pattern={quantity} defaultValue="1" /></label>
            <label>Free stock units {index + 1}<input name={id + ':free_stock_quantity'} required inputMode="decimal" pattern={quantity} defaultValue="0" /></label>
            <label>Rate per purchase unit (INR) {index + 1}<input name={id + ':unit_rate'} required inputMode="decimal" pattern={rate} /></label>
            <label>Line discount (INR) {index + 1}<input name={id + ':discount'} required inputMode="decimal" pattern={amount} defaultValue="0" /></label>
            <label>Invoice GST rate (%) {index + 1}<input name={id + ':gst_rate'} required inputMode="decimal" pattern={amount} /></label>
            <label>Invoice HSN {index + 1}<input name={id + ':hsn'} pattern="[0-9]{4}|[0-9]{6}|[0-9]{8}" /></label>
            {product?.batch_tracking && <label>Purchase batch {index + 1}<input name={id + ':batch_number'} required maxLength={100} /></label>}
            {product?.expiry_tracking && <label>Purchase expiry {index + 1}<input name={id + ':expiry_date'} required type="date" /></label>}
            <label className="full-width">Pack conversion evidence {index + 1}<textarea name={id + ':conversion_evidence'} required minLength={3} maxLength={500} /></label>
            <p className="hint full-width">Stock unit: {product?.unit ?? 'select product'}. Purchased quantity ? conversion + free units. Enter paid loose pieces as a separate line. Rates apply to the purchase unit.</p>
            <button type="button" className="secondary-button" disabled={rows.length === 1} onClick={() => { setRows(rows.filter((row) => row !== id)); invalidate(); }}>Remove line {index + 1}</button>
          </fieldset>;
        })}
        <button type="button" className="secondary-button" disabled={pending || rows.length >= 200} onClick={() => { setRows([...rows, crypto.randomUUID()]); invalidate(); }}>Add invoice line</button>{' '}
        <button disabled={pending}>Review purchase totals</button>
      </form>
      {preview && <section aria-label="Purchase review"><h4>Confirm invoice and stock</h4>
        <div className="table-scroll"><table><caption>Purchase review quantities and amounts</caption><thead><tr><th>Line</th><th>Product</th><th>Incoming stock</th><th>Taxable value</th><th>CGST</th><th>SGST</th><th>IGST</th><th>Total</th></tr></thead><tbody>{preview.lines.map((line) => <tr key={line.line_number}><td>{line.line_number}</td><td>{line.product_name}</td><td>{line.stock_quantity} {line.stock_unit}</td><td>{line.taxable_value}</td><td>{line.cgst}</td><td>{line.sgst}</td><td>{line.igst}</td><td>{line.line_total}</td></tr>)}</tbody></table></div>
        <p>Invoice total: INR {preview.invoice_total}. Round-off: INR {preview.round_off}.</p>
        <label className="checkbox-label"><input type="checkbox" disabled={pending} checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I reviewed this invoice, product matches and pack conversions. Post purchase and incoming stock.</label>
        <button disabled={pending || !confirmed} onClick={() => void post()}>Confirm and post purchase</button>
      </section>}
      <h4>Posted purchases</h4><p className="hint">Up to 50 per page. Amounts are purchase invoices, not recorded supplier payments. Supplier options show the first 200 suppliers.</p>
      <ul className="record-list">{purchases.map((item) => <li key={item.id}><strong>{item.supplier_name} / {item.invoice_number} / INR {item.invoice_total}</strong><span>{item.invoice_date} / {item.id} / Reviewed by {item.actor_user_id}</span><details><summary>Invoice stock and tax details</summary><ul>{item.lines.map((line) => <li key={line.line_number}>{line.product_name}: {line.stock_quantity} {line.stock_unit}; taxable INR {line.taxable_value}; total INR {line.line_total}</li>)}</ul><p>{item.review_reason}</p></details></li>)}</ul>
      {!purchases.length && <p>No purchases posted in this view.</p>}
      <div className="pagination"><button disabled={pending || offset === 0} onClick={() => setOffset(offset - 50)}>Previous purchases</button><button disabled={pending || purchases.length < 50} onClick={() => setOffset(offset + 50)}>Next purchases</button></div>
    </>}
  </section>;
}
