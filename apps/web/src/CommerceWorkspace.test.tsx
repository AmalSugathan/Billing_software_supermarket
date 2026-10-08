import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CommerceWorkspace from './CommerceWorkspace';
import { addOne, persistPending } from './commerce-api';

const business = { id: 'business-a', name: 'DEMO', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER', capabilities: ['sales.create', 'actions.approve', 'finance.read', 'cash.sessions'], all_stores: true };
const session = { user: { id: 'owner', email: 'owner@example.com', display_name: 'DEMO' }, csrf_token: 'test-csrf' };
const stores = [{ id: 'store-a', name: 'Main', address: '' }];
const cash = { id: 'cash-a', store_id: 'store-a', terminal_id: 'terminal-a', name: 'DEMO drawer', kind: 'cash', opening_amount: '1000.00', balance: '1000.00' };
const cashSession = { id: 'session-a', account_id: cash.id, store_id: 'store-a', actor_user_id: 'owner', opening_cash: '1000.00', expected_cash: '1000.00', actual_cash: null, variance: null, closed: false, created_at: '2026-10-08T08:00:00Z' };
const product = { id: 'product-a', sku: 'DEMO-1', name: 'Synthetic biscuit', unit: 'pcs', category_id: null, brand_id: null, supplier_id: null, purchase_price: '20', landed_cost: '20', selling_price: '25', mrp: '25', minimum_selling_price: '0', target_margin: '20', gst_rate: '0', hsn: null, reorder_level: '0', reorder_quantity: '0', batch_tracking: false, expiry_tracking: false, active: true, barcodes: [], alternate_names: [] };
const quote = { gross_total: '25.00', discount: '0.00', taxable_total: '25.00', cgst: '0.00', sgst: '0.00', total: '25.00', lines: [{ line_number: 1, product_id: product.id, product_name: product.name, sku: product.sku, unit: product.unit, hsn: null, quantity: '1', unit_price: '25', gross: '25', discount: '0', gst_rate: '0', taxable: '25', cgst: '0', sgst: '0', total: '25' }] };
const receipt = { ...quote, id: 'sale-a', store_id: 'store-a', terminal_id: 'terminal-a', invoice_number: 'POS-DEMO', actor_user_id: 'owner', created_at: '2026-10-08T08:00:00Z', payments: [{ id: 'pay-a', account_id: cash.id, movement_id: 'move-a', method: 'cash', amount: '25' }] };
const payable = { purchase_id: 'purchase-a', supplier_id: 'supplier-a', supplier_name: 'DEMO supplier', invoice_number: 'DEMO-INV', total: '504', paid: '200', outstanding: '304' };
const storageKey = 'supermarket-command:owner:business-a:store-a:pos';
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
function reads(path: string) {
  if (path.endsWith('/accounts')) return json([cash]);
  if (path.includes('/cash-sessions')) return json([cashSession]);
  if (path.endsWith('/terminals')) return json([{ id: 'terminal-a', store_id: 'store-a', name: 'DEMO terminal' }]);
  if (path.includes('/products/resolve-barcode')) return json({ detail: 'Not found' }, 404);
  if (path.includes('/products?q=')) return json([product]);
  if (path.includes('/supplier-payables')) return json([payable]);
  return json([]);
}
function mount(mode: 'pos' | 'suppliers' = 'pos', overrides = business) { return render(<CommerceWorkspace business={overrides} session={session} stores={stores} mode={mode} onRecorded={vi.fn(async () => {})} />); }

describe('POS and invoice payments', () => {
  it('recovers a lost checkout response after reload with the original decimal payload and key', async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/sales/preview')) return json(quote);
      if (path.endsWith('/sales') && options?.method === 'POST') { attempts++; if (attempts === 1) throw new TypeError('Lost committed response'); return json(receipt, 201); }
      return reads(path);
    });
    vi.stubGlobal('fetch', fetchMock); const initial = mount();
    await screen.findByRole('option', { name: 'DEMO terminal' });
    await userEvent.type(screen.getByLabelText('Product name, SKU or barcode'), 'DEMO-1');
    await userEvent.click(screen.getByRole('button', { name: 'Find / add product' }));
    await screen.findByText(/Total INR 25.00/);
    await userEvent.click(screen.getByRole('button', { name: 'Add payment / split' }));
    await userEvent.click(screen.getByLabelText(/I confirm the goods/));
    await userEvent.click(screen.getByRole('button', { name: 'Complete sale' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the store service');
    expect(screen.getByLabelText('Product name, SKU or barcode')).toBeDisabled();
    const saved = JSON.parse(localStorage.getItem(storageKey)!);
    expect(saved.body).toMatchObject({ lines: [{ product_id: product.id, quantity: '1', expected_price: '25' }], payments: [{ amount: '25.00', method: 'cash', cash_session_id: cashSession.id }] });
    initial.unmount(); mount();
    await userEvent.click(await screen.findByRole('button', { name: 'Recover submitted transaction' }));
    await screen.findByText('Sale completed: POS-DEMO');
    expect(localStorage.getItem(storageKey)).toBeNull();
    const posts = fetchMock.mock.calls.filter(([path, options]) => path.endsWith('/sales') && options?.method === 'POST');
    expect(posts).toHaveLength(2); expect(posts[0][1]?.headers).toEqual(posts[1][1]?.headers); expect(posts[0][1]?.body).toEqual(posts[1][1]?.body);
    expect(screen.getByRole('button', { name: 'Print / reprint receipt' })).toBeEnabled();
  });

  it('requires a fresh quote and payment confirmation after a cart edit', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/sales/preview') ? json(quote) : reads(path)));
    mount(); await screen.findByRole('option', { name: 'DEMO terminal' });
    await userEvent.type(screen.getByLabelText('Product name, SKU or barcode'), 'DEMO-1'); await userEvent.click(screen.getByRole('button', { name: 'Find / add product' }));
    await screen.findByText(/Total INR 25.00/); await userEvent.click(screen.getByRole('button', { name: 'Add payment / split' })); await userEvent.click(screen.getByLabelText(/I confirm the goods/));
    await userEvent.clear(screen.getByLabelText('Quantity for Synthetic biscuit')); await userEvent.type(screen.getByLabelText('Quantity for Synthetic biscuit'), '2');
    expect(screen.getByLabelText(/I confirm the goods/)).not.toBeChecked(); expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled();
    expect(screen.queryByLabelText('Received amount (INR)')).not.toBeInTheDocument();
  });

  it('records only owner-approved supplier payments and shows outstanding separately', async () => {
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/supplier-payments') && options?.method === 'POST') return json({ id: 'supplier-pay-a', purchase_id: payable.purchase_id, supplier_id: payable.supplier_id, account_id: cash.id, movement_id: 'move-a', amount: '100', reference: 'DEMO-PAY', reason: 'Synthetic payment evidence', actor_user_id: 'owner', created_at: '2026-10-08T08:00:00Z' }, 201);
      return reads(path);
    });
    vi.stubGlobal('fetch', fetchMock); mount('suppliers');
    await screen.findByText('DEMO supplier'); expect(screen.getByText('INR 304')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Purchase invoice'), payable.purchase_id); await userEvent.selectOptions(screen.getByLabelText('Source account'), cash.id); await userEvent.selectOptions(screen.getByLabelText('Open cash session'), cashSession.id);
    await userEvent.type(screen.getByLabelText('Paid amount (INR)'), '100'); await userEvent.type(screen.getByLabelText('Payment reference'), 'DEMO-PAY'); await userEvent.type(screen.getByLabelText('Reason / payment evidence'), 'Synthetic payment evidence');
    await userEvent.click(screen.getByLabelText(/I approve this invoice allocation/)); await userEvent.click(screen.getByRole('button', { name: 'Record supplier payment' }));
    await screen.findByText('Supplier payment recorded: DEMO-PAY');
    const posted = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST'); expect(JSON.parse(String(posted?.[1]?.body))).toMatchObject({ purchase_id: payable.purchase_id, amount: '100', confirmed: true, cash_session_id: cashSession.id });
  });

  it('keeps a purchase manager read-only and rejects corrupted recovery storage', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => reads(path)));
    const view = mount('suppliers', { ...business, role: 'PURCHASE_MANAGER', capabilities: ['purchases.manage'] }); await screen.findByText('DEMO supplier'); expect(screen.queryByRole('button', { name: 'Record supplier payment' })).not.toBeInTheDocument();
    view.unmount(); localStorage.setItem(storageKey, '{broken'); mount();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Find / add product' })).toBeDisabled());
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('retains unresolved recovery in its original store and uses exact quantity arithmetic', () => {
    expect(addOne('99999999999.999')).toBe('100000000000.999'); expect(() => addOne('1.0001')).toThrow();
    const command = { route: '/businesses/business-a/stores/store-a/sales', key: crypto.randomUUID(), body: { confirmed: true } };
    persistPending(storageKey, command); expect(JSON.parse(localStorage.getItem(storageKey)!)).toEqual(command);
  });
});
