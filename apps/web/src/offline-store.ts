import { z } from 'zod';
const decimalSchema = z.string().regex(/^\d+(\.\d+)?$/);
const product = z.object({ id: z.string(), name: z.string(), sku: z.string(), unit: z.string(), selling_price: decimalSchema, minimum_selling_price: decimalSchema, gst_rate: decimalSchema, hsn: z.string().nullable(), quantity: decimalSchema, barcodes: z.array(z.string()) });
export const leaseSchema = z.object({ id: z.string(), store_id: z.string(), terminal_id: z.string(), account_id: z.string(), cash_session_id: z.string(), actor_user_id: z.string(), created_at: z.string(), expires_at: z.string(), sealed: z.boolean(), synced_sequence: z.number().int(), products: z.array(product) });
export type Lease = z.infer<typeof leaseSchema>;
const saleLine = z.object({ product_id: z.string(), quantity: decimalSchema, expected_price: decimalSchema, discount: z.literal('0') });
const body = z.object({ client_sale_id: z.string().uuid(), sequence: z.number().int(), completed_at: z.string(), lines: z.array(saleLine), expected_total: decimalSchema, received_cash: decimalSchema, reason: z.string(), confirmed: z.literal(true) });
const localSale = z.object({ body, posted: z.boolean(), sync_error: z.string(), recovery_reason: z.string().optional() });
const stateSchema = z.object({ lease: leaseSchema, business_id: z.string(), label: z.string(), sales: z.array(localSale), finalization: z.object({ key: z.string().uuid(), body: z.object({ last_sequence: z.number().int(), confirmed: z.literal(true), reason: z.string() }) }).optional() });
export type OfflineState = z.infer<typeof stateSchema>;
export type LocalSale = z.infer<typeof localSale>;
export type LocalLine = { product_id: string; quantity: string };
export type ProfileMeta = { id: string; business_id: string; store_id: string; actor_user_id: string; label: string };
type Stored = ProfileMeta & { version: number; salt: string; iv: string; ciphertext: string };
const storedSchema = z.object({ id: z.string(), business_id: z.string(), store_id: z.string(), actor_user_id: z.string(), label: z.string(), version: z.number().int(), salt: z.string(), iv: z.string(), ciphertext: z.string() });
const encoder = new TextEncoder();
const encode = (data: Uint8Array) => { let source = ''; for (let offset = 0; offset < data.length; offset += 32768) source += String.fromCharCode(...data.subarray(offset, offset + 32768)); return btoa(source); };
const decode = (data: string) => Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
const associated = (record: ProfileMeta & { version: number }) => encoder.encode([record.id, record.business_id, record.store_id, record.actor_user_id, record.version].join('|'));

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open('supermarket-pos-v1', 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore('profiles', { keyPath: 'id' });
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error ?? new Error('Offline storage could not open.'));
    opening.onblocked = () => reject(new Error('Close older till tabs before opening storage.'));
  });
}
async function records(id?: string): Promise<Stored[]> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('profiles', 'readonly');
    const read = id ? transaction.objectStore('profiles').get(id) : transaction.objectStore('profiles').getAll();
    let result: unknown;
    read.onsuccess = () => { result = read.result; };
    transaction.oncomplete = () => { db.close(); try { resolve(id ? result === undefined ? [] : [storedSchema.parse(result)] : z.array(storedSchema).parse(result)); } catch (error) { reject(error); } };
    transaction.onerror = transaction.onabort = () => { db.close(); reject(transaction.error ?? new Error('Offline storage read failed.')); };
  });
}
async function write(record: Stored, expectedVersion?: number): Promise<boolean> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('profiles', 'readwrite', { durability: 'strict' });
    const store = transaction.objectStore('profiles'); let conflict = false;
    if (expectedVersion === undefined) store.add(record);
    else {
      const read = store.get(record.id);
      read.onsuccess = () => {
        const previous = storedSchema.safeParse(read.result);
        if (!previous.success || previous.data.version !== expectedVersion) { conflict = true; transaction.abort(); }
        else store.put(record);
      };
    }
    transaction.oncomplete = () => { db.close(); resolve(true); };
    transaction.onabort = () => { db.close(); if (conflict) resolve(false); else reject(transaction.error ?? new Error('Offline sale was not saved.')); };
    transaction.onerror = () => { /* onabort handles failed writes; never acknowledge them */ };
  });
}
async function derive(passphrase: string, salt: Uint8Array<ArrayBuffer>) {
  if (passphrase.length < 12) throw new Error('Use an offline passphrase of at least 12 characters.');
  const material = await crypto.subtle.importKey('raw', encoder.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 310000 }, material, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
async function encrypt(record: Omit<Stored, 'iv' | 'ciphertext'>, state: OfflineState, key: CryptoKey): Promise<Stored> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: associated(record) }, key, encoder.encode(JSON.stringify(state)));
  return { ...record, iv: encode(iv), ciphertext: encode(new Uint8Array(ciphertext)) };
}
async function decrypt(record: Stored, key: CryptoKey): Promise<OfflineState> {
  const plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: decode(record.iv), additionalData: associated(record) }, key, decode(record.ciphertext));
  const state = stateSchema.parse(JSON.parse(new TextDecoder().decode(plaintext)));
  if (state.business_id !== record.business_id || state.lease.id !== record.id || state.lease.store_id !== record.store_id || state.lease.actor_user_id !== record.actor_user_id) throw new Error('Offline profile scope failed validation.');
  return state;
}
export async function listProfiles(): Promise<ProfileMeta[]> { return (await records()).map(({ id, business_id, store_id, actor_user_id, label }) => ({ id, business_id, store_id, actor_user_id, label })); }
export async function createProfile(lease: Lease, businessId: string, label: string, passphrase: string) {
  const salt = crypto.getRandomValues(new Uint8Array(16)); const key = await derive(passphrase, salt);
  const state: OfflineState = { lease, business_id: businessId, label, sales: [] };
  const record = await encrypt({ id: lease.id, business_id: businessId, store_id: lease.store_id, actor_user_id: lease.actor_user_id, label, version: 1, salt: encode(salt) }, state, key);
  await write(record); return { key, state };
}
export async function unlockProfile(id: string, passphrase: string) {
  const record = (await records(id))[0]; if (!record) throw new Error('Offline profile is unavailable.');
  const key = await derive(passphrase, decode(record.salt)); return { key, state: await decrypt(record, key) };
}
export async function readState(id: string, key: CryptoKey) {
  const record = (await records(id))[0]; if (!record) throw new Error('Offline profile is unavailable.');
  return decrypt(record, key);
}
export async function updateState(id: string, key: CryptoKey, mutate: (state: OfflineState) => OfflineState) {
  for (let attempt = 0; attempt < 10; attempt++) {
    const previous = (await records(id))[0]; if (!previous) throw new Error('Offline profile is unavailable.');
    const state = mutate(await decrypt(previous, key)); const next = await encrypt({ ...previous, version: previous.version + 1 }, state, key);
    if (await write(next, previous.version)) return state;
  }
  throw new Error('Another till tab is writing. Retry after closing it.');
}
export function units(value: string, places: number): bigint {
  if (!new RegExp('^\\d{1,12}(\\.\\d{1,' + places + '})?$').test(value)) throw new Error('Use a valid decimal amount or quantity.');
  const [whole, fraction = ''] = value.split('.'); return BigInt(whole) * 10n ** BigInt(places) + BigInt(fraction.padEnd(places, '0'));
}
export function decimal(value: bigint, places: number): string { const factor = 10n ** BigInt(places); return (value / factor).toString() + '.' + (value % factor).toString().padStart(places, '0'); }
const round = (numerator: bigint, denominator: bigint) => (numerator * 2n + denominator) / (denominator * 2n);
export function offlineQuote(lease: Lease, lines: LocalLine[]) {
  if (!lines.length || lines.length > 200 || new Set(lines.map((line) => line.product_id)).size !== lines.length) throw new Error('Add each product once.');
  const calculated = lines.map((line) => {
    const product = lease.products.find((item) => item.id === line.product_id); if (!product) throw new Error('Product was not prepared for this till.');
    const quantity = units(line.quantity, 3); if (quantity <= 0n || ['pcs', 'pack'].includes(product.unit) && quantity % 1000n !== 0n) throw new Error('Enter a valid positive quantity for the product unit.');
    const total = round(units(product.selling_price, 2) * quantity, 1000n);
    const taxable = round(total * 10000n, 10000n + units(product.gst_rate, 2)); const tax = total - taxable; const cgst = round(tax, 2n);
    return { product, quantity, total, taxable, cgst, sgst: tax - cgst };
  });
  const sum = (field: 'total' | 'taxable' | 'cgst' | 'sgst') => calculated.reduce((amount, line) => amount + line[field], 0n);
  if (sum('total') <= 0n || sum('total') > 99999999999999n) throw new Error('Bill total is outside the supported range.');
  return { lines: calculated, total: decimal(sum('total'), 2), taxable: decimal(sum('taxable'), 2), cgst: decimal(sum('cgst'), 2), sgst: decimal(sum('sgst'), 2) };
}
export async function completeLocalSale(id: string, key: CryptoKey, lines: LocalLine[], receivedCash: string) {
  const updated = await updateState(id, key, (state) => {
    if (state.lease.sealed || Date.now() >= Date.parse(state.lease.expires_at)) throw new Error('Preparation expired or finalized. Synchronize saved receipts; prepare again online for new sales.');
    const quote = offlineQuote(state.lease, lines);
    if (units(receivedCash, 2) < units(quote.total, 2)) throw new Error('Cash received is below the bill total.');
    for (const line of quote.lines) {
      const spent = state.sales.flatMap((sale) => sale.body.lines).filter((item) => item.product_id === line.product.id).reduce((quantity, item) => quantity + units(item.quantity, 3), 0n);
      if (spent + line.quantity > units(line.product.quantity, 3)) throw new Error('Prepared stock quota exceeded for ' + line.product.name);
    }
    const sale: LocalSale = { posted: false, sync_error: '', body: { client_sale_id: crypto.randomUUID(), sequence: state.lease.synced_sequence + state.sales.length + 1, completed_at: new Date().toISOString(), lines: lines.map((line) => ({ ...line, expected_price: state.lease.products.find((item) => item.id === line.product_id)!.selling_price, discount: '0' })), expected_total: quote.total, received_cash: receivedCash, reason: 'Cashier-confirmed durable offline receipt', confirmed: true } };
    return { ...state, sales: [...state.sales, sale] };
  });
  return { state: updated, sale: updated.sales[updated.sales.length - 1] };
}
export async function beginFinalization(id: string, key: CryptoKey, reason: string) {
  if (reason.trim().length < 3) throw new Error('Enter a finalization reason.');
  return updateState(id, key, (state) => {
    if (state.finalization) return state;
    if (state.sales.some((sale) => !sale.posted)) throw new Error('Synchronize every saved receipt before finalizing.');
    return { ...state, lease: { ...state.lease, sealed: true }, finalization: { key: crypto.randomUUID(), body: { last_sequence: state.lease.synced_sequence + state.sales.length, confirmed: true, reason } } };
  });
}
export async function exportProfile(id: string) { const record = (await records(id))[0]; if (!record) throw new Error('Profile unavailable.'); return JSON.stringify({ format: 'supermarket-encrypted-pos-v1', record }); }
export async function importProfile(source: string) {
  const backup = z.object({ format: z.literal('supermarket-encrypted-pos-v1'), record: storedSchema }).parse(JSON.parse(source));
  await write(backup.record); return backup.record.id;
}
