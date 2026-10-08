import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PurchaseWorkspace from './PurchaseWorkspace';

const business = { id: 'business-a', name: 'DEMO', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER', capabilities: ['purchases.manage'], all_stores: true };
const session = { user: { id: 'owner', email: 'owner@example.com', display_name: 'DEMO' }, csrf_token: 'csrf-test' };
const stores = [{ id: 'store-a', name: 'Main', address: '' }];
const suppliers = [{ id: 'supplier-a', name: 'DEMO supplier', gstin: null, phone: '', address: '', payment_terms_days: 0, active: true }];
const product = { id: 'product-a', sku: 'DEMO-BISCUIT', name: 'DEMO biscuit', unit: 'pcs', category_id: null, brand_id: null, supplier_id: null, purchase_price: '10.00', landed_cost: '10.00', selling_price: '25.00', mrp: '25.00', minimum_selling_price: '0.00', target_margin: '0.00', gst_rate: '5.00', hsn: null, reorder_level: '0.000', reorder_quantity: '0.000', batch_tracking: false, expiry_tracking: false, active: true, barcodes: [], alternate_names: [] };
const preview = { taxable_total: '480.00', cgst: '12.00', sgst: '12.00', igst: '0.00', round_off: '0', invoice_total: '504.00', lines: [{ line_number: 1, product_id: product.id, product_name: product.name, sku: product.sku, stock_unit: 'pcs', stock_quantity: '48.000', taxable_value: '480.00', cgst: '12.00', sgst: '12.00', igst: '0.00', line_total: '504.00', unit_cost: '10.000000' }] };
const purchase = { ...preview, id: 'purchase-a', store_id: stores[0].id, supplier_id: suppliers[0].id, supplier_name: suppliers[0].name, supplier_gstin: null, invoice_number: 'DEMO-001', invoice_date: '2026-10-08', tax_mode: 'exclusive', tax_kind: 'intra', review_reason: 'Reviewed synthetic invoice', actor_user_id: 'owner', human_approved: true, source: 'human', created_at: '2026-10-08T08:00:00Z' };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());

async function fill() {
  await screen.findByRole('option', { name: /DEMO biscuit/ });
  await userEvent.selectOptions(screen.getByLabelText('Purchase supplier'), suppliers[0].id);
  await userEvent.type(screen.getByLabelText('Supplier invoice number'), 'DEMO-001');
  await userEvent.type(screen.getByLabelText('Supplier invoice date'), '2026-10-08');
  await userEvent.type(screen.getByLabelText('Printed invoice total (INR)'), '504.00');
  await userEvent.type(screen.getByLabelText('Review notes and rounding explanation'), 'Reviewed synthetic invoice');
  await userEvent.selectOptions(screen.getByLabelText('Product for line 1'), product.id);
  await userEvent.type(screen.getByLabelText('Supplier description 1'), 'Biscuit carton');
  await userEvent.selectOptions(screen.getByLabelText('Purchase unit 1'), 'carton');
  await userEvent.type(screen.getByLabelText('Invoice quantity 1'), '2');
  await userEvent.clear(screen.getByLabelText('Stock units per purchase unit 1'));
  await userEvent.type(screen.getByLabelText('Stock units per purchase unit 1'), '24');
  await userEvent.type(screen.getByLabelText('Rate per purchase unit (INR) 1'), '240.00');
  await userEvent.type(screen.getByLabelText('Invoice GST rate (%) 1'), '5');
  await userEvent.type(screen.getByLabelText('Pack conversion evidence 1'), 'Synthetic bill states 24 pieces per carton');
}

function mount(onRecorded = vi.fn(async () => {})) {
  render(<PurchaseWorkspace business={business} session={session} stores={stores} suppliers={suppliers} onRecorded={onRecorded} />);
}

describe('reviewed purchases', () => {
  it('reviews carton conversion before posting and retries a lost response with the same key', async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.includes('/products?')) return json([product]);
      if (path.endsWith('/preview')) return json(preview);
      if (options?.method === 'POST') { attempts++; if (attempts === 1) throw new TypeError('Lost response after commit'); return json(purchase, 201); }
      return json([]);
    });
    vi.stubGlobal('fetch', fetchMock); mount(); await fill();
    await userEvent.click(screen.getByRole('button', { name: 'Review purchase totals' }));
    expect(await screen.findByRole('table', { name: 'Purchase review quantities and amounts' })).toHaveTextContent('48.000 pcs');
    expect(screen.getByRole('button', { name: 'Confirm and post purchase' })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([path, options]) => options?.method === 'POST' && !path.endsWith('/preview'))).toHaveLength(0);
    await userEvent.click(screen.getByLabelText(/I reviewed this invoice/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and post purchase' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the store service');
    expect(screen.getByLabelText('Supplier invoice number')).toHaveValue('DEMO-001');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and post purchase' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Purchase posted. Inventory updated. Reference: purchase-a. No payment recorded.'));
    const calls = fetchMock.mock.calls.filter(([path, options]) => options?.method === 'POST' && !path.endsWith('/preview'));
    expect(calls).toHaveLength(2);
    expect(calls[0][1]?.headers).toEqual(calls[1][1]?.headers);
    expect(calls[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-test', 'Idempotency-Key': expect.any(String) });
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ confirmed: true, invoice_total: '504.00', lines: [{ purchase_quantity: '2', units_per_purchase: '24', unit_rate: '240.00' }] });
    expect(screen.getByLabelText('Supplier invoice number')).toHaveValue('');
  });

  it('invalidates reviewed totals and approval when invoice details change', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.includes('/products?') ? json([product]) : path.endsWith('/preview') ? json(preview) : json([])));
    mount(); await fill();
    await userEvent.click(screen.getByRole('button', { name: 'Review purchase totals' }));
    await screen.findByRole('table', { name: 'Purchase review quantities and amounts' });
    await userEvent.click(screen.getByLabelText(/I reviewed this invoice/));
    await userEvent.clear(screen.getByLabelText('Stock units per purchase unit 1'));
    expect(screen.queryByLabelText(/I reviewed this invoice/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm and post purchase' })).not.toBeInTheDocument();
  });

  it('keeps posting success visible when audit refresh fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => path.includes('/products?') ? json([product]) : path.endsWith('/preview') ? json(preview) : options?.method === 'POST' ? json(purchase, 201) : json([])));
    mount(vi.fn(async () => { throw new Error('Audit unavailable'); })); await fill();
    await userEvent.click(screen.getByRole('button', { name: 'Review purchase totals' }));
    await screen.findByRole('table', { name: 'Purchase review quantities and amounts' });
    await userEvent.click(screen.getByLabelText(/I reviewed this invoice/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and post purchase' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Purchase posted. Inventory updated.');
    expect(await screen.findByRole('alert')).toHaveTextContent('Purchase posted successfully; audit refresh failed');
  });
});
