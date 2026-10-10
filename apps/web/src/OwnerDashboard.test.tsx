import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import OwnerDashboard from './OwnerDashboard';
import { rupees } from './insights-api';

const business = { id: 'business', name: 'DEMO store', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER', capabilities: ['finance.read', 'inventory.read'], all_stores: true };
const stores = [{ id: 'first', name: 'First store', address: '' }, { id: 'second', name: 'Second store', address: '' }];
const data = {
  store_id: 'first', start: '2026-10-10', end: '2026-10-10', timezone: 'Asia/Kolkata', basis: 'Server posting date', generated_at: '2026-10-10T04:00:00Z',
  metrics: { revenue: '100.00', billed: '105.00', cogs: '60.00', expenses: '10.00', stock_loss: '0.00', money_in: '105.00', money_out: '10.00', opening_funds: '500.00', cash_variance: '-5.00', variance_absolute: '5.00', sale_count: 1, credit_count: 0, variance_count: 1, estimated_gross_profit: '40.00', after_recorded_expenses: '30.00', net_cash_flow: '95.00' },
  snapshot: { recorded_balance: '590.00', supplier_outstanding: '50.00', inventory_value: '400.00', low_stock_count: 1, expiring_batch_count: 0, expired_batch_count: 0, stocked_product_count: 2 },
  daily: [{ date: '2026-10-10', revenue: '100.00', money_in: '105.00', money_out: '10.00', net_cash_flow: '95.00' }],
  briefing: [{ priority: 'high', title: 'Review reorder levels', detail: 'One product at reorder level.', module: 'inventory' }],
  briefing_method: 'rules_based', limitations: ['Unsynchronized offline sales are absent.'],
};
const json = (value: unknown) => new Response(JSON.stringify(value));
afterEach(() => vi.unstubAllGlobals());
it('shows server metrics distinctly and opens relevant records without changing finances', async () => {
  const fetchMock = vi.fn(async () => json(data)); vi.stubGlobal('fetch', fetchMock);
  const navigate = vi.fn(); render(<OwnerDashboard business={business} stores={stores} onNavigate={navigate} />);
  const totals = await screen.findByLabelText('Recorded business totals');
  expect(within(totals).getByText('\u20b9100.00')).toBeVisible();
  expect(within(totals).getByText('\u20b940.00')).toBeVisible();
  expect(screen.getByText(/Rules-based checks/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Open related records' }));
  expect(navigate).toHaveBeenCalledWith('inventory');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it('does not keep stale financial data or replace a failed load with zeros', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.includes('/second/') ? new Response(JSON.stringify({ detail: 'Database unavailable' }), { status: 503 }) : json(data)));
  render(<OwnerDashboard business={business} stores={stores} onNavigate={vi.fn()} />);
  await screen.findByLabelText('Recorded business totals');
  await userEvent.selectOptions(screen.getByLabelText('Dashboard store'), 'second');
  expect(await screen.findByRole('alert')).toHaveTextContent('Business data is unavailable');
  expect(screen.queryByLabelText('Recorded business totals')).not.toBeInTheDocument();
});
it('ignores a previous store response that completes after switching stores', async () => {
  let complete: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.includes('/first/') ? new Promise<Response>((resolve) => { complete = resolve; }) : json({ ...data, store_id: 'second', metrics: { ...data.metrics, revenue: '200.00' } })));
  render(<OwnerDashboard business={business} stores={stores} onNavigate={vi.fn()} />);
  await userEvent.selectOptions(screen.getByLabelText('Dashboard store'), 'second');
  const totals = await screen.findByLabelText('Recorded business totals');
  expect(within(totals).getByText('\u20b9200.00')).toBeVisible();
  complete(json(data));
  await waitFor(() => expect(within(totals).queryByText('\u20b9100.00')).not.toBeInTheDocument());
});
it('formats large Decimal strings without floating-point money conversion', () => {
  expect(rupees('9007199254740993.01')).toBe('\u20b99,00,71,99,25,47,40,993.01');
  expect(rupees('-0.50')).toBe('-\u20b90.50');
});
