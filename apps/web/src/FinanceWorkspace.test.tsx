import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import FinanceWorkspace from './FinanceWorkspace';

const business = { id: 'business-a', name: 'DEMO', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER', capabilities: ['actions.approve', 'expenses.manage', 'finance.read', 'cash.sessions'], all_stores: true };
const session = { user: { id: 'owner', email: 'owner@example.com', display_name: 'DEMO' }, csrf_token: 'test-csrf' };
const stores = [{ id: 'store-a', name: 'Main', address: '' }];
const cash = { id: 'cash-a', store_id: 'store-a', terminal_id: 'terminal-a', name: 'DEMO drawer', kind: 'cash', opening_amount: '1000.00', balance: '950.00' };
const cashSession = { id: 'session-a', account_id: cash.id, store_id: 'store-a', actor_user_id: 'owner', opening_cash: '1000.00', expected_cash: '950.00', actual_cash: null, variance: null, closed: false, created_at: '2026-10-08T08:00:00Z' };
const expense = { id: 'expense-a', store_id: 'store-a', account_id: cash.id, movement_id: 'movement-a', reference: 'DEMO-001', expense_date: '2026-10-08', category: 'delivery', description: 'Synthetic delivery', amount: '50.00', actor_user_id: 'owner', created_at: '2026-10-08T08:00:00Z', reversed: false };
const closing = { id: 'closing-a', cash_session_id: cashSession.id, expected_cash: '950.00', actual_cash: '940.00', variance: '-10.00', movement_id: 'variance-a', reason: 'Synthetic shortage for review', actor_user_id: 'owner', created_at: '2026-10-08T08:00:00Z' };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => vi.unstubAllGlobals());
function mount(mode: 'expenses' | 'cash', overrides = business) { render(<FinanceWorkspace business={overrides} session={session} stores={stores} mode={mode} onRecorded={vi.fn(async () => {})} />); }

function reads(path: string) {
  if (path.endsWith('/accounts')) return json([cash]);
  if (path.endsWith('/cash-sessions')) return json([cashSession]);
  return json([]);
}

describe('expense and cashier finance flows', () => {
  it('records owner-reviewed opening differences separately from receipts or expenses', async () => {
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/opening-variance') && options?.method === 'POST') return json({ id: 'opening-difference', account_id: cash.id, cash_session_id: null, kind: 'opening_variance', amount: '-10.00', resource_id: 'opening-difference', reason: 'Synthetic opening shortage', actor_user_id: 'owner', source: 'human', human_approved: true, created_at: '2026-10-08T08:00:00Z' }, 201);
      if (path.endsWith('/accounts')) return json([{ ...cash, balance: '1000.00' }]);
      return json([]);
    });
    vi.stubGlobal('fetch', fetchMock); mount('cash');
    await screen.findByText('Reconcile cash before opening');
    await userEvent.click(screen.getByText('Reconcile cash before opening'));
    await userEvent.selectOptions(screen.getByLabelText('Drawer to reconcile'), cash.id);
    await userEvent.type(screen.getByLabelText('Actual opening count (INR)'), '990.00');
    await userEvent.type(screen.getByLabelText('Opening difference explanation'), 'Synthetic opening shortage');
    await userEvent.click(screen.getByLabelText(/I approve recording this opening count difference/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm opening cash adjustment' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Opening cash difference recorded separately from receipts and expenses.');
    const posted = fetchMock.mock.calls.find(([path]) => path.endsWith('/opening-variance'));
    expect(JSON.parse(String(posted?.[1]?.body))).toMatchObject({ expected_cash: '1000.00', actual_cash: '990.00', confirmed: true });
  });

  it('posts an expense with decimal strings and retries a lost response without a second key', async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/expenses') && options?.method === 'POST') { attempts++; if (attempts === 1) throw new TypeError('Lost committed response'); return json(expense, 201); }
      return reads(path);
    });
    vi.stubGlobal('fetch', fetchMock); mount('expenses');
    await screen.findByRole('button', { name: 'Confirm paid expense' });
    await userEvent.selectOptions(screen.getByLabelText('Expense payment account'), cash.id);
    await userEvent.selectOptions(screen.getByLabelText('Open drawer session'), cashSession.id);
    await userEvent.type(screen.getByLabelText('Expense reference'), 'DEMO-001');
    await userEvent.type(screen.getByLabelText('Expense date'), '2026-10-08');
    await userEvent.type(screen.getByLabelText('Expense category'), 'delivery');
    await userEvent.type(screen.getByLabelText('Paid amount (INR)'), '50.00');
    await userEvent.type(screen.getByLabelText('Expense description'), 'Synthetic delivery');
    await userEvent.click(screen.getByLabelText(/I reviewed this paid expense/));
    await userEvent.clear(screen.getByLabelText('Paid amount (INR)'));
    await userEvent.type(screen.getByLabelText('Paid amount (INR)'), '50.00');
    expect(screen.getByLabelText(/I reviewed this paid expense/)).not.toBeChecked();
    await userEvent.click(screen.getByLabelText(/I reviewed this paid expense/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm paid expense' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the store service');
    expect(screen.getByLabelText('Expense reference')).toHaveValue('DEMO-001');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm paid expense' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Expense posted. Reference: expense-a');
    const calls = fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(calls).toHaveLength(2);
    expect(calls[0][1]?.headers).toEqual(calls[1][1]?.headers);
    expect(calls[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': 'test-csrf', 'Idempotency-Key': expect.any(String) });
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ amount: '50.00', cash_session_id: cashSession.id, confirmed: true });
  });

  it('closes cash using the recorded expectation and shows variance as a review item', async () => {
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => path.endsWith('/close') && options?.method === 'POST' ? json(closing, 201) : reads(path));
    vi.stubGlobal('fetch', fetchMock); mount('cash');
    await screen.findByRole('button', { name: 'Confirm session closing' });
    await userEvent.selectOptions(screen.getByLabelText('Session to close'), cashSession.id);
    await userEvent.type(screen.getByLabelText('Actual closing cash (INR)'), '940.00');
    await userEvent.type(screen.getByLabelText('Count explanation and variance reason'), closing.reason);
    await userEvent.click(screen.getByLabelText(/I confirm the actual cash count/));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm session closing' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Cash variance: INR -10.00. Recorded for review.');
    const posted = fetchMock.mock.calls.find(([path]) => path.endsWith('/close'));
    expect(JSON.parse(String(posted?.[1]?.body))).toMatchObject({ expected_cash: '950.00', actual_cash: '940.00', confirmed: true });
  });

  it('clears closing approval when financial records refresh and limits cashier controls', async () => {
    let refreshed = false;
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      if (path.endsWith('/accounts')) return json([cash]);
      if (path.endsWith('/cash-sessions')) { const current = refreshed; refreshed = true; return json([{ ...cashSession, expected_cash: current ? '900.00' : '950.00' }]); }
      return json([]);
    }));
    mount('cash', { ...business, role: 'CASHIER', capabilities: ['business.read', 'cash.sessions'], all_stores: false });
    await screen.findByRole('button', { name: 'Confirm session closing' });
    expect(screen.queryByText('Add a payment account')).not.toBeInTheDocument();
    expect(screen.queryByText('Record other money in or out')).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Session to close'), cashSession.id);
    await userEvent.click(screen.getByLabelText(/I confirm the actual cash count/));
    await userEvent.click(screen.getByRole('button', { name: 'Refresh financial records' }));
    await waitFor(() => expect(screen.getByText('Expected cash: INR 900.00')).toBeInTheDocument());
    expect(screen.getByLabelText(/I confirm the actual cash count/)).not.toBeChecked();
  });
});
