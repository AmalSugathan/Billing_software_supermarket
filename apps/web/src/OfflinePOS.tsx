import { useEffect, useRef, useState, type FormEvent } from 'react';
import { commerce } from './commerce-api';
import { request, sessionSchema } from './identity-api';
import { beginFinalization, completeLocalSale, decimal, exportProfile, importProfile, leaseSchema, listProfiles, offlineQuote, readState, units, unlockProfile, updateState, type LocalLine, type LocalSale, type OfflineState, type ProfileMeta } from './offline-store';
const message = (error: unknown) => error instanceof Error ? error.message : 'The action could not be completed.';
export default function OfflinePOS() {
  const [profiles, setProfiles] = useState<ProfileMeta[]>([]);
  const [state, setState] = useState<OfflineState | null>(null);
  const key = useRef<CryptoKey | null>(null);
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [cart, setCart] = useState<LocalLine[]>([]);
  const [receipt, setReceipt] = useState<LocalSale | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    void listProfiles().then(setProfiles).catch((error: unknown) => setError(message(error)));
    const connected = () => setOnline(navigator.onLine);
    window.addEventListener('online', connected); window.addEventListener('offline', connected);
    return () => { window.removeEventListener('online', connected); window.removeEventListener('offline', connected); };
  }, []);
  useEffect(() => {
    if (!state) return;
    const lock = () => { key.current = null; setState(null); setCart([]); setReceipt(null); };
    let timer = window.setTimeout(lock, 900000);
    const active = () => { window.clearTimeout(timer); timer = window.setTimeout(lock, 900000); };
    window.addEventListener('pointerdown', active); window.addEventListener('keydown', active);
    return () => { window.clearTimeout(timer); window.removeEventListener('pointerdown', active); window.removeEventListener('keydown', active); };
  }, [state]);
  async function action(work: () => Promise<void>) {
    if (busy.current) return; busy.current = true; setPending(true); setError(''); setNotice('');
    try { await work(); } catch (error) { setError(message(error)); } finally { busy.current = false; setPending(false); }
  }
  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await action(async () => { const unlocked = await unlockProfile(String(form.get('profile')), String(form.get('passphrase'))); key.current = unlocked.key; setState(unlocked.state); });
  }
  async function checkout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!state || !key.current) return;
    const received = String(new FormData(event.currentTarget).get('received'));
    await action(async () => { const saved = await completeLocalSale(state.lease.id, key.current!, cart, received); setState(saved.state); setReceipt(saved.sale); setCart([]); setNotice('Receipt saved on this device. Synchronize before closing the drawer.'); });
  }
  async function synchronize() {
    if (!state || !key.current) return;
    await action(async () => {
      const current = await request('/auth/session', sessionSchema);
      let journal = await readState(state.lease.id, key.current!);
      for (const sale of journal.sales.filter((item) => !item.posted)) {
        try {
          const posted = await request('/businesses/' + journal.business_id + '/stores/' + journal.lease.store_id + '/offline-leases/' + journal.lease.id + '/sales', commerce.receipt, { method: 'POST', csrf: current.csrf_token, idempotencyKey: sale.body.client_sale_id, body: { ...sale.body, ...(sale.recovery_reason ? { recovery_reason: sale.recovery_reason } : {}) } });
          if (posted.id !== sale.body.client_sale_id || units(posted.total, 2) !== units(sale.body.expected_total, 2)) throw new Error('Server receipt differs from saved receipt; review required.');
          journal = await updateState(journal.lease.id, key.current!, (latest) => ({ ...latest, sales: latest.sales.map((item) => item.body.client_sale_id === sale.body.client_sale_id ? { ...item, posted: true, sync_error: '' } : item) }));
          setState(journal);
        } catch (error) {
          journal = await updateState(journal.lease.id, key.current!, (latest) => ({ ...latest, sales: latest.sales.map((item) => item.body.client_sale_id === sale.body.client_sale_id ? { ...item, sync_error: message(error) } : item) }));
          setState(journal); throw error;
        }
      }
      setNotice('All saved receipts are synchronized.');
    });
  }
  async function finalize(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!state || !key.current) return; const form = new FormData(event.currentTarget);
    await action(async () => {
      if (!form.has('confirmed')) throw new Error('Confirm finalization first.');
      const latest = await beginFinalization(state.lease.id, key.current!, String(form.get('reason')));
      setState(latest);
      const current = await request('/auth/session', sessionSchema);
      const command = latest.finalization!;
      const sealed = await request('/businesses/' + latest.business_id + '/stores/' + latest.lease.store_id + '/offline-leases/' + latest.lease.id + '/finalize', leaseSchema, { method: 'POST', csrf: current.csrf_token, idempotencyKey: command.key, body: command.body });
      if (!sealed.sealed) throw new Error('Finalization was not confirmed.');
      setState(await updateState(latest.lease.id, key.current!, (journal) => { const next = { ...journal }; delete next.finalization; return next; }));
      setNotice('Unused reservation released. You may now close the cash session online.');
    });
  }
  function add(productId: string) { setCart((lines) => lines.some((line) => line.product_id === productId) ? lines.map((line) => line.product_id === productId ? { ...line, quantity: decimal(units(line.quantity, 3) + 1000n, 3) } : line) : [...lines, { product_id: productId, quantity: '1' }]); }
  function download(source: string, filename: string) { const url = URL.createObjectURL(new Blob([source], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
  let total = ''; try { if (state && cart.length) total = offlineQuote(state.lease, cart).total; } catch { /* invalid edits cannot be billed */ }
  const receiptQuote = state && receipt ? offlineQuote(state.lease, receipt.body.lines) : null;
  return <main className="identity-workspace"><h1>Offline cash till</h1><p>{online ? 'Network available' : 'Working offline'} · <a href="/">Store portal</a></p>
    <p>Fixed prepared prices, cash only. Saved receipts remain on this browser until synchronized. Keep an encrypted backup and protect the device.</p>
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!state ? <section className="workspace-card"><h2>Unlock prepared till</h2><form onSubmit={(event) => void unlock(event)}>
      <label>Prepared till<select name="profile" required>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select></label>
      <label>Device passphrase<input name="passphrase" type="password" minLength={12} required autoComplete="off" /></label><button disabled={pending || !profiles.length}>Unlock till</button></form>
      {!profiles.length && <p>Prepare a till online in Store operations → Offline preparation.</p>}
      <label>Restore encrypted journal<input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void action(async () => { if (file.size > 20000000) throw new Error('Journal file is too large.'); await importProfile(await file.text()); setProfiles(await listProfiles()); setNotice('Journal restored. Unlock with its original device passphrase.'); }); }} /></label></section> : <>
      <h2>{state.label}</h2><p>Prepared until {new Date(state.lease.expires_at).toLocaleString()} · {state.finalization ? 'Finalization awaiting confirmation' : state.lease.sealed ? 'Finalized' : 'Open'}</p>
      <button disabled={pending} onClick={() => { key.current = null; setState(null); setCart([]); setReceipt(null); }}>Lock till</button>
      <button disabled={pending} onClick={() => void action(async () => download(await exportProfile(state.lease.id), 'offline-journal-' + state.lease.id + '.json'))}>Export encrypted journal</button>
      <section className="workspace-card"><h3>New cash sale</h3><label>Search product, SKU or barcode<input value={query} onChange={(event) => setQuery(event.target.value)} autoFocus /></label>
      <div className="operations-tabs">{state.lease.products.filter((product) => [product.name, product.sku, ...product.barcodes].some((text) => text.toLowerCase().includes(query.toLowerCase()))).map((product) => <button key={product.id} disabled={pending || state.lease.sealed} onClick={() => add(product.id)}>{product.name} · ₹{product.selling_price}</button>)}</div>
      <form onSubmit={(event) => void checkout(event)}>{cart.map((line) => <div className="search-row" key={line.product_id}><label>{state.lease.products.find((product) => product.id === line.product_id)?.name}<input aria-label="Sale quantity" value={line.quantity} onChange={(event) => setCart(cart.map((item) => item.product_id === line.product_id ? { ...item, quantity: event.target.value } : item))} /></label><button type="button" onClick={() => setCart(cart.filter((item) => item.product_id !== line.product_id))}>Remove</button></div>)}
      <p>Bill total: ₹{total || '—'}</p><label>Cash received<input name="received" inputMode="decimal" pattern="[0-9]{1,12}(\.[0-9]{1,2})?" required /></label>
      <label className="checkbox-label"><input name="cash_confirmed" type="checkbox" required />I received the cash and will give the correct change.</label><button disabled={pending || !total || state.lease.sealed}>Save cash receipt</button></form></section>
      {receipt && <section className="workspace-card pos-receipt"><h3>Saved receipt</h3><p>OFF-{receipt.body.client_sale_id.replaceAll('-', '').toUpperCase()}</p><p>Total ₹{receipt.body.expected_total} · Received ₹{receipt.body.received_cash} · Change ₹{decimal(units(receipt.body.received_cash, 2) - units(receipt.body.expected_total, 2), 2)}</p>{receipt.body.lines.map((line) => <p key={line.product_id}>{state.lease.products.find((product) => product.id === line.product_id)?.name} · {line.quantity}</p>)}<p>Taxable ₹{receiptQuote?.taxable} · CGST ₹{receiptQuote?.cgst} · SGST ₹{receiptQuote?.sgst}</p><p>{receipt.body.completed_at}</p><button onClick={() => window.print()}>Print receipt</button></section>}
      <section className="workspace-card"><h3>Saved receipts</h3><p>{state.sales.filter((sale) => !sale.posted).length} pending synchronization</p><button disabled={pending || !online} onClick={() => void synchronize()}>Synchronize receipts</button>
      {state.sales.map((sale) => <div key={sale.body.client_sale_id}><button className="secondary-button" onClick={() => setReceipt(sale)}>Receipt {sale.body.sequence} · ₹{sale.body.expected_total} · {sale.posted ? 'Synchronized' : 'Pending'}</button>{sale.sync_error && <><p role="alert">Requires review: {sale.sync_error}</p><form onSubmit={(event) => { event.preventDefault(); const reason = String(new FormData(event.currentTarget).get('reason')); void action(async () => { const updated = await updateState(state.lease.id, key.current!, (latest) => ({ ...latest, sales: latest.sales.map((item) => item.body.client_sale_id === sale.body.client_sale_id ? { ...item, recovery_reason: reason } : item) })); setState(updated); }); }}><label>Owner recovery reason<input name="reason" minLength={5} required maxLength={500} /></label><label><input type="checkbox" required />I approve recovery of this saved receipt as owner.</label><button disabled={pending || sale.posted}>Save recovery approval</button></form></>}</div>)}
      {(!state.lease.sealed || state.finalization) && <form onSubmit={(event) => void finalize(event)}><label>Finalization reason<input name="reason" required minLength={3} maxLength={500} defaultValue={state.finalization?.body.reason ?? ''} /></label><label className="checkbox-label"><input name="confirmed" type="checkbox" required />All completed receipts are saved and synchronized from this till.</label><button disabled={pending || !online || state.sales.some((sale) => !sale.posted)}>Finalize offline session</button></form>}</section>
    </>}
  </main>;
}
