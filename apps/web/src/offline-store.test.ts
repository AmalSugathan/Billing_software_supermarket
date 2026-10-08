// @vitest-environment node
import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { beginFinalization, completeLocalSale, createProfile, exportProfile, importProfile, listProfiles, offlineQuote, readState, unlockProfile, updateState, type Lease } from './offline-store';
const password = 'Synthetic-passphrase-2026';
function lease(quantity = '8'): Lease { return { id: crypto.randomUUID(), store_id: crypto.randomUUID(), terminal_id: crypto.randomUUID(), account_id: crypto.randomUUID(), cash_session_id: crypto.randomUUID(), actor_user_id: crypto.randomUUID(), created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString(), synced_sequence: 0, sealed: false, products: [{ id: crypto.randomUUID(), name: 'DEMO biscuit', sku: 'DEMO-BISCUIT', unit: 'pcs', selling_price: '25.00', minimum_selling_price: '0', gst_rate: '5', hsn: null, quantity, barcodes: [] }] }; }
const lines = (prepared: Lease, quantity = '2') => [{ product_id: prepared.products[0].id, quantity }];
beforeEach(async () => { await new Promise<void>((resolve, reject) => { const removal = indexedDB.deleteDatabase('supermarket-pos-v1'); removal.onsuccess = () => resolve(); removal.onerror = () => reject(removal.error); }); });
describe('durable encrypted offline journal', () => {
  it('acknowledges saved receipts after commit and recovers them after reload', async () => {
    const prepared = lease(); const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    const saved = await completeLocalSale(prepared.id, key, lines(prepared), '100');
    expect(saved.sale.body.expected_total).toBe('50.00'); expect(saved.sale.body.sequence).toBe(1);
    const recovered = await unlockProfile(prepared.id, password); expect(recovered.state.sales[0]).toEqual(saved.sale);
    expect((await listProfiles())[0].id).toBe(prepared.id);
    const ciphertext = await exportProfile(prepared.id); expect(ciphertext).not.toContain('DEMO biscuit'); expect(ciphertext).not.toContain(saved.sale.body.client_sale_id); expect(ciphertext).not.toContain(password);
    await expect(unlockProfile(prepared.id, 'Incorrect-passphrase')).rejects.toThrow();
    await expect(importProfile(ciphertext)).rejects.toThrow();
    expect((await readState(prepared.id, key)).sales).toHaveLength(1);
  });
  it('serializes competing tabs and prevents quota overselling', async () => {
    const prepared = lease(); const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    const results = await Promise.allSettled([completeLocalSale(prepared.id, key, lines(prepared, '6'), '150'), completeLocalSale(prepared.id, key, lines(prepared, '6'), '150')]);
    expect(results.filter((result) => result.status === 'fulfilled')).toHaveLength(1);
    expect((await readState(prepared.id, key)).sales).toHaveLength(1);
    const second = await completeLocalSale(prepared.id, key, lines(prepared, '2'), '50'); expect(second.sale.body.sequence).toBe(2);
    await expect(completeLocalSale(prepared.id, key, lines(prepared, '1'), '25')).rejects.toThrow('quota');
  });
  it('retains blocked receipts and prevents new sales after expiry or finalization', async () => {
    const prepared = lease(); const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    await completeLocalSale(prepared.id, key, lines(prepared), '50');
    await updateState(prepared.id, key, (state) => ({ ...state, lease: { ...state.lease, expires_at: '2020-01-01T00:00:00Z' }, sales: state.sales.map((sale) => ({ ...sale, sync_error: 'Requires review' })) }));
    await expect(completeLocalSale(prepared.id, key, lines(prepared), '50')).rejects.toThrow('expired');
    expect((await unlockProfile(prepared.id, password)).state.sales[0].sync_error).toBe('Requires review');
    await updateState(prepared.id, key, (state) => ({ ...state, lease: { ...state.lease, expires_at: new Date(Date.now() + 60000).toISOString(), sealed: true } }));
    await expect(completeLocalSale(prepared.id, key, lines(prepared), '50')).rejects.toThrow('finalized');
  });
  it('protects ciphertext against modification and restores an encrypted backup', async () => {
    const prepared = lease(); const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    await completeLocalSale(prepared.id, key, lines(prepared), '50'); const backup = await exportProfile(prepared.id);
    await new Promise<void>((resolve) => { indexedDB.deleteDatabase('supermarket-pos-v1').onsuccess = () => resolve(); });
    const modified = JSON.parse(backup) as { record: { business_id: string } }; modified.record.business_id = 'another-business'; await importProfile(JSON.stringify(modified));
    await expect(unlockProfile(prepared.id, password)).rejects.toThrow();
    await new Promise<void>((resolve) => { indexedDB.deleteDatabase('supermarket-pos-v1').onsuccess = () => resolve(); });
    await importProfile(backup); expect((await unlockProfile(prepared.id, password)).state.sales).toHaveLength(1);
  });
  it('blocks other tabs during uncertain finalization and persists its original command', async () => {
    const prepared = lease(); const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    await completeLocalSale(prepared.id, key, lines(prepared), '50');
    await expect(beginFinalization(prepared.id, key, 'Synthetic close')).rejects.toThrow('Synchronize');
    await updateState(prepared.id, key, (state) => ({ ...state, sales: state.sales.map((sale) => ({ ...sale, posted: true })) }));
    const finishing = await beginFinalization(prepared.id, key, 'Synthetic close');
    await expect(completeLocalSale(prepared.id, key, lines(prepared), '50')).rejects.toThrow('finalized');
    expect((await unlockProfile(prepared.id, password)).state.finalization).toEqual(finishing.finalization);
    expect((await beginFinalization(prepared.id, key, 'Another reason')).finalization).toEqual(finishing.finalization);
  });
  it('uses exact paise and quantity arithmetic and rejects invalid tender or fractional pieces', async () => {
    const prepared = lease(); expect(offlineQuote(prepared, lines(prepared))).toMatchObject({ total: '50.00', taxable: '47.62', cgst: '1.19', sgst: '1.19' });
    expect(() => offlineQuote(prepared, lines(prepared, '0.5'))).toThrow('quantity');
    prepared.products[0].unit = 'kg'; prepared.products[0].selling_price = '60'; expect(offlineQuote(prepared, lines(prepared, '2.5')).total).toBe('150.00');
    const { key } = await createProfile(prepared, 'business', 'DEMO till', password);
    await expect(completeLocalSale(prepared.id, key, lines(prepared), '1')).rejects.toThrow('below'); expect((await readState(prepared.id, key)).sales).toHaveLength(0);
  });
});
