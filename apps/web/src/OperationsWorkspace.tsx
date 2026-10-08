import FinanceWorkspace from './FinanceWorkspace';
import PurchaseWorkspace from './PurchaseWorkspace';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { request, type Business, type Session, type Store } from './identity-api';
import { operations, type Candidate, type Movement, type Named, type Product, type Stock, type Supplier } from './operations-api';

const value = (form: FormData, name: string) => String(form.get(name) ?? '').trim();
const amountPattern = '[0-9]{1,12}(\\.[0-9]{1,2})?';
const quantityPattern = '[0-9]{1,12}(\\.[0-9]{1,3})?';
const message = (error: unknown) => error instanceof Error ? error.message : 'The action could not be completed.';
const money = (amount: string | null) => amount === null ? 'Restricted' : '₹' + amount;

function MoneyField({ label, name, initial = '0', required = true }: { label: string; name: string; initial?: string; required?: boolean }) {
  return <label>{label}<input name={name} inputMode="decimal" pattern={amountPattern} defaultValue={initial} required={required} /></label>;
}

export default function OperationsWorkspace({ business, session, stores, onRecorded }: {
  business: Business; session: Session; stores: Store[]; onRecorded: () => Promise<void>;
}) {
  const base = '/businesses/' + business.id;
  const canCatalog = business.capabilities.includes('catalog.manage');
  const canPurchase = business.capabilities.includes('purchases.manage');
  const canInventory = business.capabilities.includes('inventory.read');
  const canApprove = business.capabilities.includes('actions.approve');
  const canExpense = business.capabilities.includes('expenses.manage');
  const canCash = business.capabilities.includes('cash.sessions') || business.capabilities.includes('finance.read') || canExpense;
  const [tab, setTab] = useState('products');
  const [products, setProducts] = useState<Product[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [categories, setCategories] = useState<Named[]>([]);
  const [brands, setBrands] = useState<Named[]>([]);
  const [matches, setMatches] = useState<Candidate[]>([]);
  const [stock, setStock] = useState<Stock[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [openingProductId, setOpeningProductId] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [stockLoaded, setStockLoaded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const replay = useRef<{ fingerprint: string; key: string } | null>(null);
  const productForm = useRef<HTMLFormElement>(null);
  const openingProduct = products.find((item) => item.id === openingProductId);

  const loadCatalog = useCallback(async (signal?: AbortSignal) => {
    const [items, supplierItems, categoryItems, brandItems] = await Promise.all([
      request(base + '/products?q=' + encodeURIComponent(query) + '&offset=' + offset, operations.products, { signal }),
      canPurchase ? request(base + '/suppliers?limit=200', operations.suppliers, { signal }) : Promise.resolve([]),
      canCatalog ? request(base + '/categories', operations.names, { signal }) : Promise.resolve([]),
      canCatalog ? request(base + '/brands', operations.names, { signal }) : Promise.resolve([]),
    ]);
    return { items, supplierItems, categoryItems, brandItems };
  }, [base, canPurchase, canCatalog, query, offset]);
  const applyCatalog = useCallback((data: Awaited<ReturnType<typeof loadCatalog>>) => {
    setProducts(data.items); setSuppliers(data.supplierItems); setCategories(data.categoryItems); setBrands(data.brandItems); setLoaded(true);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void loadCatalog(controller.signal).then((data) => { if (!controller.signal.aborted) applyCatalog(data); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [loadCatalog, applyCatalog]);

  const loadStock = useCallback(async (signal?: AbortSignal) => {
    const [balances, history] = await Promise.all([
      request(base + '/stores/' + storeId + '/stock?limit=200', operations.stock, { signal }),
      request(base + '/stores/' + storeId + '/stock-movements', operations.movements, { signal }),
    ]);
    return { balances, history, storeId };
  }, [base, storeId]);
  const applyStock = useCallback((data: Awaited<ReturnType<typeof loadStock>>) => {
    setStock(data.balances); setMovements(data.history); setStockLoaded(true);
  }, []);
  useEffect(() => {
    if (tab !== 'inventory' || !canInventory || !storeId) return;
    const controller = new AbortController();
    void loadStock(controller.signal).then((data) => { if (!controller.signal.aborted) applyStock(data); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [tab, canInventory, storeId, loadStock, applyStock]);

  async function action(work: () => Promise<void>) {
    setPending(true); setError(''); setNotice('');
    try { await work(); return true; }
    catch (error) { setError(message(error)); return false; }
    finally { setPending(false); }
  }
  async function recordProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement);
    const body: Record<string, unknown> = {};
    for (const name of ['sku', 'name', 'unit', 'purchase_price', 'landed_cost', 'selling_price', 'mrp', 'gst_rate', 'minimum_selling_price', 'target_margin', 'reorder_level', 'reorder_quantity']) body[name] = value(form, name);
    for (const name of ['category_id', 'brand_id', 'supplier_id', 'hsn']) body[name] = value(form, name) || null;
    body.barcodes = value(form, 'barcodes').split(',').map((item) => item.trim()).filter(Boolean);
    body.alternate_names = value(form, 'alternate_names').split(',').map((item) => item.trim()).filter(Boolean);
    body.batch_tracking = form.has('batch_tracking'); body.expiry_tracking = form.has('expiry_tracking'); body.confirm_distinct_product = form.has('confirm_distinct_product');
    if (await action(async () => {
      await request(base + '/products', operations.product, { method: 'POST', csrf: session.csrf_token, body });
      applyCatalog(await loadCatalog()); await onRecorded(); setNotice('Product saved. Stock has not changed.');
    })) { formElement.reset(); setMatches([]); }
  }
  async function checkMatches() {
    if (!productForm.current) return;
    const name = value(new FormData(productForm.current), 'name');
    if (!name) { setError('Enter a product name first.'); return; }
    await action(async () => { const result = await request(base + '/products/matches?name=' + encodeURIComponent(name), operations.matches); if (productForm.current && value(new FormData(productForm.current), 'name') === name) setMatches(result); if (!result.length) setNotice('No similar names found in the current review window. Check the barcode and SKU too.'); });
  }
  async function recordSupplier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    if (await action(async () => {
      await request(base + '/suppliers', operations.supplier, { method: 'POST', csrf: session.csrf_token, body: {
        name: value(form, 'name'), gstin: value(form, 'gstin') || null, phone: value(form, 'phone'), address: value(form, 'address'), payment_terms_days: Number(value(form, 'payment_terms_days')),
      } }); applyCatalog(await loadCatalog()); await onRecorded(); setNotice('Supplier saved. No purchase or payment has been created.');
    })) element.reset();
  }
  async function recordOpening(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    const body = { product_id: value(form, 'product_id'), quantity: value(form, 'quantity'), unit_cost: value(form, 'unit_cost'), reason: value(form, 'reason'),
      batch_number: value(form, 'batch_number') || null, expiry_date: value(form, 'expiry_date') || null, confirmed: form.has('confirmed') };
    const fingerprint = JSON.stringify({ storeId, body });
    if (replay.current?.fingerprint !== fingerprint) replay.current = { fingerprint, key: crypto.randomUUID() };
    const idempotencyKey = replay.current.key;
    if (await action(async () => {
      const posted = await request(base + '/stores/' + storeId + '/opening-stock', operations.movement, { method: 'POST', csrf: session.csrf_token, idempotencyKey, body });
      setNotice('Opening stock recorded. Movement reference: ' + posted.id);
      replay.current = null; element.reset(); setOpeningProductId('');
      applyStock(await loadStock()); await onRecorded();
    })) replay.current = null;
  }
  async function addName(event: FormEvent<HTMLFormElement>, path: string) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    if (await action(async () => { await request(base + path, operations.named, { method: 'POST', csrf: session.csrf_token, body: { name: value(form, 'name') } }); applyCatalog(await loadCatalog()); await onRecorded(); })) element.reset();
  }

  return <section className="operations" aria-labelledby="operations-heading">
    <h3 id="operations-heading">Store operations</h3>
    <div className="operations-tabs" aria-label="Operation modules">
      <button className={tab === 'products' ? '' : 'secondary-button'} disabled={pending} onClick={() => { setTab('products'); setError(''); }}>Products & barcodes</button>
      {canInventory && <button className={tab === 'inventory' ? '' : 'secondary-button'} disabled={pending} onClick={() => { setTab('inventory'); setError(''); }}>Inventory</button>}
      {canPurchase && <button className={tab === 'purchases' ? 'active' : 'secondary-button'} disabled={pending} onClick={() => setTab('purchases')}>Purchases</button>}
      {canPurchase && <button className={tab === 'suppliers' ? '' : 'secondary-button'} disabled={pending} onClick={() => { setTab('suppliers'); setError(''); }}>Suppliers</button>}
      {canExpense && <button className={tab === 'expenses' ? '' : 'secondary-button'} disabled={pending} onClick={() => setTab('expenses')}>Expenses</button>}
      {canCash && <button className={tab === 'cash' ? '' : 'secondary-button'} disabled={pending} onClick={() => setTab('cash')}>Cash & bank</button>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p className="success-notice" role="status">{notice}</p>}
    {tab === 'products' && <>
      <section className="workspace-card"><h4>Product catalog</h4><form className="search-row" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); setQuery(value(form, 'q')); setOffset(0); }}>
        <label>Search name, alternate name or SKU<input name="q" maxLength={180} /></label><button disabled={pending}>Search products</button></form>
        <form className="search-row" onSubmit={(event) => { event.preventDefault(); const code = value(new FormData(event.currentTarget), 'barcode'); void action(async () => { const item = await request(base + '/products/resolve-barcode?value=' + encodeURIComponent(code), operations.product); setNotice('Barcode found: ' + item.name + ' · ' + money(item.selling_price)); }); }}>
          <label>Scan or enter barcode<input name="barcode" required maxLength={64} autoComplete="off" /></label><button disabled={pending}>Look up barcode</button></form>
        {!loaded ? <p role="status">Loading catalog…</p> : products.length ? <div className="table-scroll"><table><caption>Products · page {offset / 50 + 1}</caption><thead><tr><th>Product</th><th>SKU / barcode</th><th>Unit</th><th>Selling price</th><th>MRP</th><th>Purchase price</th></tr></thead>
          <tbody>{products.map((item) => <tr key={item.id}><td>{item.name}{!item.active && ' (inactive)'}</td><td>{item.sku}<small>{item.barcodes.join(', ') || 'No barcode'}</small></td><td>{item.unit}</td><td>{money(item.selling_price)}</td><td>{money(item.mrp)}</td><td>{money(item.purchase_price)}</td></tr>)}</tbody></table></div> : <p>No products match this view. Add your actual products to get started.</p>}
        <div className="pagination"><button className="secondary-button" disabled={pending || offset === 0} onClick={() => setOffset(offset - 50)}>Previous products</button><button className="secondary-button" disabled={pending || products.length < 50} onClick={() => setOffset(offset + 50)}>Next products</button></div>
      </section>
      {canCatalog && <section className="workspace-card"><h4>Add a product</h4><p className="hint">Use the supplier bill and packaging. Money is entered per selected unit. This records a product without adding stock.</p>
        <form ref={productForm} className="form-grid" onSubmit={(event) => void recordProduct(event)}>
          <label>Product name<input name="name" required maxLength={180} onChange={() => setMatches([])} /></label><label>SKU<input name="sku" required maxLength={64} pattern="[A-Za-z0-9._/\-]+" /></label>
          <label>Unit<select name="unit">{['pcs', 'pack', 'kg', 'g', 'l', 'ml'].map((unit) => <option key={unit}>{unit}</option>)}</select></label><label>Barcodes (comma separated)<input name="barcodes" maxLength={1300} /></label>
          <MoneyField label="Purchase price (₹)" name="purchase_price" initial="" /><MoneyField label="Landed cost (₹)" name="landed_cost" initial="" /><MoneyField label="Selling price (₹)" name="selling_price" initial="" /><MoneyField label="MRP (₹)" name="mrp" initial="" />
          <label>Category<select name="category_id"><option value="">Uncategorized</option>{categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Brand<select name="brand_id"><option value="">Unspecified</option>{brands.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          {canPurchase && <label>Preferred supplier<select name="supplier_id"><option value="">Unspecified</option>{suppliers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
          <label>HSN<input name="hsn" pattern="[0-9]{4}|[0-9]{6}|[0-9]{8}" /></label><MoneyField label="GST rate (%)" name="gst_rate" />
          <details className="full-width"><summary>Reorder, margin, aliases and batch tracking</summary><div className="form-grid">
            <MoneyField label="Minimum selling price (₹)" name="minimum_selling_price" /><MoneyField label="Target margin (%)" name="target_margin" />
            <label>Reorder level<input name="reorder_level" inputMode="decimal" pattern={quantityPattern} defaultValue="0" required /></label><label>Reorder quantity<input name="reorder_quantity" inputMode="decimal" pattern={quantityPattern} defaultValue="0" required /></label>
            <label>Alternate names (comma separated)<input name="alternate_names" maxLength={3600} /></label>
            <label className="checkbox-label"><input type="checkbox" name="batch_tracking" />Track batch numbers</label><label className="checkbox-label"><input type="checkbox" name="expiry_tracking" />Track expiry (requires batch tracking)</label>
          </div></details>
          <button type="button" className="secondary-button" disabled={pending} onClick={() => void checkMatches()}>Check existing products</button>
          {matches.length > 0 && <div className="match-review full-width"><p>Review likely matches. Similarity is a name comparison, not AI confidence.</p><ul>{matches.map((item) => <li key={item.id}>{item.name} · {item.sku} · {item.similarity}% similarity</li>)}</ul><label className="checkbox-label"><input name="confirm_distinct_product" type="checkbox" required />I reviewed these matches; this is a distinct product.</label></div>}
          <button disabled={pending}>{pending ? 'Saving…' : 'Save product'}</button>
        </form>
        <details><summary>Add category or brand</summary><form className="search-row" onSubmit={(event) => void addName(event, '/categories')}><label>New category<input name="name" required maxLength={150} /></label><button disabled={pending}>Add category</button></form><form className="search-row" onSubmit={(event) => void addName(event, '/brands')}><label>New brand<input name="name" required maxLength={150} /></label><button disabled={pending}>Add brand</button></form></details>
      </section>}
    </>}
    {tab === 'suppliers' && canPurchase && <section className="workspace-card"><h4>Suppliers</h4><p className="hint">Up to 200 suppliers shown. Outstanding payments become available with purchase posting.</p>
      <ul className="record-list">{suppliers.map((item) => <li key={item.id}><strong>{item.name}</strong><span>{item.gstin || 'GSTIN not recorded'} · {item.phone || 'Phone not recorded'} · {item.payment_terms_days} day terms</span></li>)}</ul>
      {!suppliers.length && <p>No suppliers recorded.</p>}<form className="form-grid" onSubmit={(event) => void recordSupplier(event)}>
        <label>Supplier name<input name="name" required maxLength={150} /></label><label>GSTIN (optional)<input name="gstin" minLength={15} maxLength={15} pattern="[0-9]{2}[A-Z0-9]{13}" /></label><label>Phone<input name="phone" maxLength={20} /></label><label>Payment terms (days)<input name="payment_terms_days" type="number" min={0} max={365} step={1} defaultValue={0} required /></label><label>Supplier address<textarea name="address" maxLength={500} /></label><button disabled={pending}>Save supplier</button>
      </form></section>}
    {((tab === 'expenses' && canExpense) || (tab === 'cash' && canCash)) && <FinanceWorkspace key={tab} business={business} session={session} stores={stores} mode={tab === 'expenses' ? 'expenses' : 'cash'} onRecorded={onRecorded} />}
    {tab === 'purchases' && canPurchase && <PurchaseWorkspace business={business} session={session} stores={stores} suppliers={suppliers} onRecorded={onRecorded} />}
    {tab === 'inventory' && canInventory && <section className="workspace-card"><h4>Stock and opening counts</h4>
      <label>Inventory store<select value={storeId} disabled={pending} onChange={(event) => { setStoreId(event.target.value); setStockLoaded(false); setNotice(''); setError(''); }}>{stores.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {!storeId ? <p>No stores assigned.</p> : !stockLoaded ? <p role="status">Loading recorded stock…</p> : <>
        <p className="hint">First 200 products. Quantities come from recorded movements. Opening value is opening quantity × recorded unit cost; profit is not calculated here.</p>
        <div className="table-scroll"><table><caption>Recorded inventory</caption><thead><tr><th>Product</th><th>Quantity</th><th>Unit</th><th>Opening value</th></tr></thead><tbody>{stock.map((item) => <tr key={item.product_id}><td>{item.name}<small>{item.sku}</small></td><td>{item.quantity}</td><td>{item.unit}</td><td>{money(item.opening_value)}</td></tr>)}</tbody></table></div>
        {!stock.length && <p>Add products before recording opening stock.</p>}
        {canApprove && <form className="form-grid opening-form" onChange={(event) => {
          const input = event.target;
          const confirmation = event.currentTarget.elements.namedItem('confirmed');
          if (input.getAttribute('name') !== 'confirmed' && confirmation instanceof HTMLInputElement) confirmation.checked = false;
        }} onSubmit={(event) => void recordOpening(event)}><h4 className="full-width">Record reviewed opening stock</h4>
          <label>Opening product<select name="product_id" value={openingProductId} onChange={(event) => setOpeningProductId(event.target.value)} required><option value="">Select a product from the current catalog page</option>{products.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name} ({item.unit})</option>)}</select></label>
          <label>Counted quantity<input name="quantity" inputMode="decimal" pattern={quantityPattern} required /></label><MoneyField label="Opening unit cost (₹)" name="unit_cost" initial="" />
          {openingProduct?.batch_tracking && <label>Batch number<input name="batch_number" required maxLength={100} /></label>}{openingProduct?.expiry_tracking && <label>Expiry date<input name="expiry_date" type="date" required /></label>}
          <label className="full-width">Count source and reason<textarea name="reason" required minLength={3} maxLength={500} /></label>
          <label className="checkbox-label full-width"><input name="confirmed" type="checkbox" required />I confirm the physical count and cost. This creates an immutable stock movement.</label>
          <p className="hint full-width">Each product/lot can have one opening entry. Later corrections need a separate reviewed adjustment workflow, which is not available yet.</p><button disabled={pending || !openingProductId}>Confirm opening stock</button>
        </form>}
        <h4>Recent movements</h4><ul className="record-list">{movements.map((item) => <li key={item.id}><strong>{item.kind} · {item.quantity} · {products.find((product) => product.id === item.product_id)?.name ?? item.product_id}</strong><span>{item.reason} · {new Date(item.created_at).toLocaleString()} · {item.source} · {item.id}</span></li>)}</ul>
        {!movements.length && <p>No stock movements recorded.</p>}
      </>}
    </section>}
  </section>;
}
