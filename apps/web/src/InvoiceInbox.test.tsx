import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import InvoiceInbox from './InvoiceInbox';
const business = { id: 'business-a', name: 'DEMO', currency: 'INR', timezone: 'Asia/Kolkata', role: 'OWNER', capabilities: ['purchases.manage', 'actions.approve'], all_stores: true };
const session = { user: { id: 'owner', email: 'owner@example.com', display_name: 'DEMO' }, csrf_token: 'csrf-test' };
const stores = [{ id: 'store-a', name: 'Main', address: '' }];
const id = '12345678-1234-4234-8234-123456789abc';
const document = { id, store_id: id, filename: 'DEMO.jpg', mime_type: 'image/jpeg', data_origin: 'synthetic', sha256: 'a'.repeat(64), byte_size: 100, page_count: 1, created_at: '2026-10-08T08:00:00Z', status: 'uploaded', attempt_id: null, error_code: null, evidence: null };
const availability = { upload_enabled: true, provider_configured: false, provider_model: 'PaddleOCR-VL-1.6', health_verified: false, semantic_matching_available: false, message: 'PaddleOCR service is not configured.' };
const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => vi.unstubAllGlobals());
describe('private invoice inbox', () => {
  it('exposes genuine unavailable OCR and keeps intake separate from financial posting', async () => {
    const fetchMock = vi.fn(async (path: string) => path.endsWith('/provider') ? json(availability) : path.endsWith('/' + id) ? json(document) : path.endsWith('/content') ? new Response('synthetic', { headers: { 'Content-Type': 'application/pdf' } }) : json([document]));
    vi.stubGlobal('fetch', fetchMock);
    render(<InvoiceInbox business={business} session={session} stores={stores} suppliers={[]} />);
    expect(await screen.findByText(/PaddleOCR service is not configured/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /DEMO.jpg/ }));
    expect(await screen.findByRole('button', { name: 'Run private OCR' })).toBeDisabled();
    expect(screen.getByText(/OCR confidence is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download original bill' })).toHaveAttribute('href', expect.stringContaining('/content?original=true'));
    expect(fetchMock.mock.calls.every(([path]) => !path.includes('/purchases') && !path.includes('/stock'))).toBe(true);
  });
  it('renders provider text as plain evidence and requires verified mapping approval', async () => {
    const reviewed = { ...document, status: 'review_required', evidence: { provider_model: 'PaddleOCR-VL-1.6', document_class: 'unclassified', classification_confidence: null, review_required: true, pages: [{ page_number: 1, markdown: '<script>private untrusted OCR</script>', blocks: [] }] } };
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/provider') ? json({ ...availability, provider_configured: true }) : path.endsWith('/' + id) ? json(reviewed) : path.endsWith('/content') ? new Response('synthetic', { headers: { 'Content-Type': 'application/pdf' } }) : json([document])));
    const { container } = render(<InvoiceInbox business={business} session={session} stores={stores} suppliers={[]} />);
    await userEvent.click(await screen.findByRole('button', { name: /DEMO.jpg/ }));
    expect(await screen.findByText('<script>private untrusted OCR</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByRole('button', { name: 'Approve supplier description mapping' })).toBeDisabled();
    expect(screen.getByLabelText('I checked the product, pack size and unit against this invoice.')).toBeRequired();
  });
  it('returns a background processing state and polls without reposting or reloading source bytes', async () => {
    let started = false;
    const completed = { ...document, status: 'review_required', evidence: { provider_model: 'PaddleOCR-VL-1.6', document_class: 'unclassified', classification_confidence: null, review_required: true, pages: [{ page_number: 1, markdown: 'Unreviewed synthetic extracted bill', blocks: [] }] } };
    const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/provider')) return json({ ...availability, provider_configured: true, health_verified: true });
      if (path.endsWith('/process')) { started = true; return json({ ...document, status: 'processing' }); }
      if (path.endsWith('/' + id)) return json(started ? completed : document);
      if (path.endsWith('/content')) return new Response('synthetic', { headers: { 'Content-Type': 'application/pdf' } });
      expect(options?.method).not.toBe('POST'); return json([document]);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<InvoiceInbox business={business} session={session} stores={stores} suppliers={[]} />);
    await userEvent.click(await screen.findByRole('button', { name: /DEMO.jpg/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Run private OCR' }));
    expect(await screen.findByText('Unreviewed synthetic extracted bill', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([path]) => path.endsWith('/process'))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([path]) => path.endsWith('/content'))).toHaveLength(1);
  });

});
