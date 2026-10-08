import { useEffect, useRef, useState, type FormEvent } from 'react';
import { z } from 'zod';
import { loadPending, persistPending, type PendingCommand } from './commerce-api';
import { finance, type CashSession } from './finance-api';
import { ApiError, request, schemas, type Business, type Session, type Store, type Terminal } from './identity-api';
import { operations, type Product } from './operations-api';
import { createProfile, leaseSchema, listProfiles, type Lease } from './offline-store';
export default function OfflinePrepare({ business, session, stores }: { business: Business; session: Session; stores: Store[] }) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [products, setProducts] = useState<Product[]>([]);
  const [sessions, setSessions] = useState<CashSession[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [leases, setLeases] = useState<Lease[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const storageKey = 'offline-prepare:' + session.user.id + ':' + business.id + ':' + storeId;
  const base = '/businesses/' + business.id;
  useEffect(() => {
    if (!storeId) return; const controller = new AbortController();
    void Promise.all([request(base + '/products?limit=200', operations.products, { signal: controller.signal }), request(base + '/stores/' + storeId + '/cash-sessions', finance.sessions, { signal: controller.signal }), request(base + '/terminals', schemas.terminals, { signal: controller.signal }), request(base + '/stores/' + storeId + '/offline-leases', z.array(leaseSchema), { signal: controller.signal })]).then(([items, cash, tills, prepared]) => { if (!controller.signal.aborted) { setProducts(items); setSessions(cash.filter((item) => !item.closed && item.actor_user_id === session.user.id)); setTerminals(tills.filter((terminal) => terminal.store_id === storeId)); setLeases(prepared); } }).catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Preparation could not load.'); });
    return () => controller.abort();
  }, [base, storeId, session.user.id]);
  async function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy.current) return; const form = new FormData(event.currentTarget); busy.current = true; setPending(true); setError(''); setNotice('');
    let command: PendingCommand | null;
    try {
      if (!('serviceWorker' in navigator) || !import.meta.env.PROD) throw new Error('Offline checkout needs the built application. Open http://127.0.0.1:8080 after the pilot preview starts.');
      const registration = await Promise.race([navigator.serviceWorker.ready, new Promise<never>((_, reject) => window.setTimeout(() => reject(new Error('Offline application cache is unavailable. Reload online and retry.')), 10000))]);
      if (!registration.active) throw new Error('Offline cache has not finished installing. Reload online.');
      const passphrase = String(form.get('passphrase')); if (passphrase.length < 12) throw new Error('Use a device passphrase of at least 12 characters.');
      command = loadPending(storageKey);
      if (!command) {
        command = { route: base + '/stores/' + storeId + '/offline-leases', key: crypto.randomUUID(), body: { terminal_id: String(form.get('terminal_id')), cash_session_id: String(form.get('cash_session_id')), products: Object.entries(quantities).filter(([, quantity]) => quantity && quantity !== '0').map(([product_id, quantity]) => ({ product_id, quantity })), reason: String(form.get('reason')), confirmed: form.has('confirmed') } };
        persistPending(storageKey, command);
      }
      const lease = await request(command.route, leaseSchema, { method: 'POST', csrf: session.csrf_token, idempotencyKey: command.key, body: command.body });
      const profiles = await listProfiles();
      if (!profiles.some((profile) => profile.id === lease.id)) await createProfile(lease, business.id, business.name + ' / ' + (stores.find((store) => store.id === storeId)?.name ?? storeId), passphrase);
      localStorage.removeItem(storageKey);
      const persistent = navigator.storage?.persist ? await navigator.storage.persist() : false;
      setNotice('Till prepared and encrypted on this device. ' + (persistent ? 'Persistent storage granted.' : 'Browser storage can be evicted; export an encrypted journal after each sale batch.'));
      setLeases((current) => [...current.filter((item) => item.id !== lease.id), lease]);
    } catch (error) {
      if (error instanceof ApiError && [401, 403, 404, 409, 422].includes(error.status)) localStorage.removeItem(storageKey);
      setError(error instanceof Error ? error.message : 'Preparation failed.');
    } finally { busy.current = false; setPending(false); }
  }
  async function resume(lease: Lease, passphrase: string) {
    setPending(true); setError('');
    try { if (!passphrase || passphrase.length < 12) throw new Error('Enter the device passphrase first.'); if (!import.meta.env.PROD) throw new Error('Open the built application on port 8080.'); await navigator.serviceWorker.ready; await createProfile(lease, business.id, business.name + ' / recovered prepared till', passphrase); setNotice('Remaining server quota saved. Open the offline till.'); }
    catch (error) { setError(error instanceof Error ? error.message : 'Recovery failed.'); } finally { setPending(false); }
  }
  return <section className="workspace-card"><h4>Prepare an offline cash till</h4><p>Reserve a limited stock quota while online. This prevents other tills selling the same units. Fixed prices and cash only; synchronize and finalize before cash closing. Keep the device passphrase and encrypted backups safe.</p>
    <p><a href="/offline-pos">Open offline till</a></p>{!import.meta.env.PROD && <p>Use the built pilot application at <a href="http://127.0.0.1:8080">127.0.0.1:8080</a> for offline cache support.</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <label>Store<select value={storeId} disabled={pending} onChange={(event) => { setStoreId(event.target.value); setQuantities({}); }}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
    <form onSubmit={(event) => void prepare(event)}><label>Terminal<select name="terminal_id" required>{terminals.map((terminal) => <option key={terminal.id} value={terminal.id}>{terminal.name}</option>)}</select></label><label>Your open cash session<select name="cash_session_id" required>{sessions.map((cash) => <option key={cash.id} value={cash.id}>{cash.id} · Opening ₹{cash.opening_cash}</option>)}</select></label>
      <details open><summary>Stock quota by product (first 200 products)</summary>{products.filter((product) => product.active).map((product) => <label key={product.id}>{product.name} · {product.unit}<input aria-label={'Reserve ' + product.name} inputMode="decimal" value={quantities[product.id] ?? ''} onChange={(event) => setQuantities({ ...quantities, [product.id]: event.target.value })} pattern="[0-9]{1,5}(\.[0-9]{1,3})?" /></label>)}</details>
      <label>Preparation reason<input name="reason" required minLength={3} maxLength={500} /></label><label>Device passphrase<input name="passphrase" type="password" required minLength={12} autoComplete="new-password" /></label><label className="checkbox-label"><input name="confirmed" type="checkbox" required />I approve this stock reservation for my till.</label><button disabled={pending || !sessions.length}>Prepare offline till</button>
    </form>
    {leases.filter((lease) => !lease.sealed && lease.actor_user_id === session.user.id).map((lease) => <details key={lease.id}><summary>Prepared session {lease.id}</summary><p>If local preparation failed before any sale, recover its remaining server quota here. Restore an existing encrypted journal for completed unsynchronized sales; do not clone a working till.</p><form onSubmit={(event) => { event.preventDefault(); void resume(lease, String(new FormData(event.currentTarget).get('passphrase'))); }}><label>Recovery device passphrase<input type="password" name="passphrase" minLength={12} required /></label><button disabled={pending}>Recover preparation</button></form></details>)}
  </section>;
}
