import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

afterEach(() => vi.unstubAllGlobals());

function responses(ready: boolean) {
  return vi.fn(async (path: string) => new Response(
    JSON.stringify({ status: path.endsWith('/live') ? 'alive' : ready ? 'ready' : 'unavailable' }),
    { status: path.endsWith('/live') || ready ? 200 : 503 },
  ));
}

describe('development environment status', () => {
  it('reports API availability separately from an unmigrated database and can retry', async () => {
    const fetchMock = responses(false);
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    const status = screen.getByRole('status', { name: 'Environment health' });
    expect(await within(status).findByText('available')).toBeInTheDocument();
    expect(within(status).getByText('unavailable')).toBeInTheDocument();
    fetchMock.mockImplementation(responses(true));
    await userEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await within(status).findAllByText('available')).toHaveLength(2);
    expect(screen.getByText(/Billing, purchases and expenses are not available yet/)).toBeInTheDocument();
  });

  it('does not show success when the server is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    expect(await within(screen.getByRole('status', { name: 'Environment health' })).findAllByText('unavailable')).toHaveLength(2);
  });

  it('rejects an invalid health payload even with a successful HTTP status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sales: 100 }))));
    render(<App />);
    expect(await within(screen.getByRole('status', { name: 'Environment health' })).findAllByText('unavailable')).toHaveLength(2);
  });
});
