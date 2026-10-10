import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import OperationsWorkspace from './OperationsWorkspace';

afterEach(() => vi.unstubAllGlobals());
const business = { id: 'business-a', name: 'Test store', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER',
  capabilities: ['catalog.manage', 'purchases.manage', 'inventory.read', 'actions.approve'], all_stores: true };
const session = { user: { id: 'owner', email: 'owner@example.com', display_name: 'Test Owner' }, csrf_token: 'test-csrf' };
const stores = [{ id: 'store-a', name: 'Main store', address: '' }];
const item = { id: 'product-a', sku: 'RICE', name: 'Rice 1kg', unit: 'kg', category_id: null, brand_id: null, supplier_id: null,
  purchase_price: '18.50', landed_cost: '19.00', selling_price: '25.00', mrp: '30.00', minimum_selling_price: '0.00',
  target_margin: '0.00', gst_rate: '0.00', hsn: null, reorder_level: '0.000', reorder_quantity: '0.000',
  batch_tracking: false, expiry_tracking: false, active: true, barcodes: ['890001'], alternate_names: [] };
const movement = { id: 'movement-a', store_id: 'store-a', product_id: 'product-a', batch_id: 'batch-a', kind: 'opening',
  quantity: '1.125', unit_cost: '18.50', reason: 'Physical count from signed stock sheet', actor_user_id: 'owner',
  source: 'human', human_approved: true, created_at: '2026-10-07T08:00:00Z' };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const onRecorded = vi.fn(async () => {});
function mount(overrides = business) { render(<OperationsWorkspace business={overrides} session={session} stores={stores} onRecorded={onRecorded} />); }

describe('real operations API flows', () => {
  it('reviews matching products and sends prices as decimal strings with explicit distinct-product confirmation', async () => {
    let created = false;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.includes('/products/matches')) return json([{ id: 'similar', sku: 'RICE-OTHER', name: 'Rice 2kg', similarity: 87 }]);
      if (path.endsWith('/products') && options?.method === 'POST') { created = true; return json(item, 201); }
      if (path.includes('/products?')) return json(created ? [item] : []);
      return json([]);
    });
    vi.stubGlobal('fetch', fetchMock); mount();
    await screen.findByText(/No products match/);
    await userEvent.click(screen.getByText('Add a product'));
    await userEvent.type(screen.getByLabelText('Product name'), 'Rice 1kg');
    await userEvent.type(screen.getByLabelText('SKU'), 'RICE');
    for (const [label, amount] of [['Purchase price (₹)', '18.50'], ['Landed cost (₹)', '19.00'], ['Selling price (₹)', '25.00'], ['MRP (₹)', '30.00']]) {
      await userEvent.clear(screen.getByLabelText(label)); await userEvent.type(screen.getByLabelText(label), amount);
    }
    await userEvent.click(screen.getByRole('button', { name: 'Check existing products' }));
    expect(await screen.findByText(/87% similarity/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/I reviewed these matches/));
    await userEvent.click(screen.getByRole('button', { name: 'Save product' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Product saved. Stock has not changed.');
    const submitted = fetchMock.mock.calls.find(([path, options]) => path.endsWith('/products') && options?.method === 'POST');
    expect(JSON.parse(String(submitted?.[1]?.body))).toMatchObject({ purchase_price: '18.50', confirm_distinct_product: true });
    expect(submitted?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': 'test-csrf' });
    expect(screen.getByLabelText('Product name')).toHaveValue('');
  });

  it('uses one idempotency key when retrying a stock posting whose first response was lost', async () => {
    let posts = 0;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.includes('/products?')) return json([item]);
      if (path.endsWith('/opening-stock')) { posts++; if (posts === 1) throw new TypeError('Response lost after server commit'); return json(movement, 201); }
      if (path.includes('/stock-movements')) return json(posts ? [movement] : []);
      if (path.includes('/stock?')) return json([{ product_id: item.id, sku: item.sku, name: item.name, unit: item.unit,
        quantity: posts ? '1.125' : '0', opening_value: posts ? '20.81250' : '0' }]);
      void options; return json([]);
    });
    vi.stubGlobal('fetch', fetchMock); mount();
    await screen.findByText(item.name); await userEvent.click(screen.getByRole('button', { name: 'Inventory' }));
    await screen.findByRole('table', { name: 'Recorded inventory' });
    await userEvent.click(screen.getByText('Record opening stock'));
    await userEvent.selectOptions(screen.getByLabelText('Opening product'), item.id);
    await userEvent.type(screen.getByLabelText('Counted quantity'), '1.125');
    await userEvent.clear(screen.getByLabelText('Opening unit cost (₹)')); await userEvent.type(screen.getByLabelText('Opening unit cost (₹)'), '18.50');
    await userEvent.type(screen.getByLabelText('Count source and reason'), movement.reason);
    await userEvent.click(screen.getByLabelText(/I confirm the physical count/));
    await userEvent.clear(screen.getByLabelText('Counted quantity'));
    await userEvent.type(screen.getByLabelText('Counted quantity'), '1.125');
    expect(screen.getByLabelText(/I confirm the physical count/)).not.toBeChecked();
    await userEvent.click(screen.getByLabelText(/I confirm the physical count/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm opening stock' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the store service');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm opening stock' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Opening stock recorded. Movement reference: movement-a'));
    const calls = fetchMock.mock.calls.filter(([path]) => path.endsWith('/opening-stock'));
    expect(calls).toHaveLength(2);
    expect(calls[0][1]?.headers).toEqual(calls[1][1]?.headers);
    expect(calls[0][1]?.headers).toMatchObject({ 'Idempotency-Key': expect.any(String) });
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ quantity: '1.125', unit_cost: '18.50', confirmed: true });
    expect(screen.getByLabelText('Opening product')).toHaveValue('');
  });

  it('lets a scanner submit barcode lookup using Enter and keeps cashier controls limited', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.includes('/resolve-barcode') ? json({ ...item, purchase_price: null, landed_cost: null, target_margin: null }) : json([])));
    mount({ ...business, role: 'CASHIER', capabilities: ['business.read', 'sales.create'], all_stores: false });
    await screen.findByText(/No products match/);
    expect(screen.queryByRole('button', { name: 'Inventory' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save product' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Suppliers' })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Scan or enter barcode'), '890001{Enter}');
    expect(await screen.findByRole('status')).toHaveTextContent('Barcode found: Rice 1kg · ₹25.00');
  });

  it('records a real supplier API response and preserves input on a genuine duplicate rejection', async () => {
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => path.endsWith('/suppliers') && options?.method === 'POST'
      ? json({ detail: 'Supplier name or GSTIN already exists' }, 409) : json([]));
    vi.stubGlobal('fetch', fetchMock); mount(); await screen.findByText(/No products match/);
    await userEvent.click(screen.getByRole('button', { name: 'Suppliers' }));
    await userEvent.click(screen.getByText('Add a supplier'));
    await userEvent.type(screen.getByLabelText('Supplier name'), 'Registered supplier');
    await userEvent.click(screen.getByRole('button', { name: 'Save supplier' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Supplier name or GSTIN already exists');
    expect(screen.getByLabelText('Supplier name')).toHaveValue('Registered supplier');
    expect(screen.queryByText('Supplier saved. No purchase or payment has been created.')).not.toBeInTheDocument();
  });
});
