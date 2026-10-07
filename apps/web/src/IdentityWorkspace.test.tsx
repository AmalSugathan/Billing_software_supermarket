import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import IdentityWorkspace from './IdentityWorkspace';

afterEach(() => vi.unstubAllGlobals());
const account = { user: { id: 'test-owner', display_name: 'Test Owner', email: 'owner@example.com' }, csrf_token: 'test-csrf' };
const ownerBusiness = { id: 'test-business', name: 'Test supermarket', currency: 'INR', timezone: 'Asia/Kolkata',
  role: 'OWNER', capabilities: ['business.read', 'stores.manage', 'staff.manage', 'audit.read'], all_stores: true };
const testStores = [{ id: 'test-store', name: 'Main store', address: 'Isolated test fixture' }];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('identity and business setup', () => {
  it('registers an account and creates the first business with session CSRF verification', async () => {
    let signedIn = false; let created = false;
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/auth/session')) return signedIn ? json(account) : json({ detail: 'Sign in required' }, 401);
      if (path.endsWith('/auth/register')) { signedIn = true; return json(account, 201); }
      if (path.endsWith('/businesses') && options?.method === 'POST') { created = true; return json(ownerBusiness, 201); }
      if (path.endsWith('/businesses')) return json(created ? [ownerBusiness] : []);
      if (path.endsWith('/stores')) return json(testStores);
      return json([]);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<IdentityWorkspace />);
    await userEvent.click(await screen.findByRole('button', { name: 'New here? Create an account' }));
    await userEvent.type(screen.getByLabelText('Your name'), 'Test Owner');
    await userEvent.type(screen.getByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-test-password');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await userEvent.type(await screen.findByLabelText('Business name'), 'Test supermarket');
    await userEvent.type(screen.getByLabelText('First store name'), 'Main store');
    await userEvent.click(screen.getByRole('button', { name: 'Create business and store' }));
    expect(await screen.findByText('Isolated test fixture')).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([path, options]) => path.endsWith('/businesses') && options?.method === 'POST');
    expect(call?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': 'test-csrf' });
    expect(call?.[1]?.credentials).toBe('same-origin');
    expect(call?.[1]?.body).toContain('Main store');
  });

  it('does not expose owner controls to a cashier and forgets the workspace on sign-out', async () => {
    const cashier = { ...ownerBusiness, role: 'CASHIER', capabilities: ['business.read', 'sales.create'], all_stores: false };
    const fetchMock = vi.fn(async (path: string) => {
      if (path.endsWith('/auth/session')) return json(account);
      if (path.endsWith('/businesses')) return json([cashier]);
      if (path.endsWith('/stores')) return json(testStores);
      if (path.endsWith('/auth/logout')) return new Response(null, { status: 204 });
      return json([]);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<IdentityWorkspace />);
    await screen.findByText('Isolated test fixture');
    expect(screen.queryByRole('button', { name: 'Add store' })).not.toBeInTheDocument();
    expect(screen.queryByText('Staff access')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/members') || path.endsWith('/audit'))).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText('Isolated test fixture')).not.toBeInTheDocument();
  });

  it('shows a genuine registration failure without advancing onboarding', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/auth/session')
      ? json({ detail: 'Sign in required' }, 401) : json({ detail: 'Database unavailable' }, 503)));
    render(<IdentityWorkspace />);
    await userEvent.type(await screen.findByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-test-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable');
    expect(screen.queryByLabelText('Business name')).not.toBeInTheDocument();
  });

  it('requires a valid session contract instead of accepting an invented successful response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ user: 'invalid' })));
    render(<IdentityWorkspace />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('could not be verified'));
    expect(screen.queryByLabelText('Business name')).not.toBeInTheDocument();
  });
});
