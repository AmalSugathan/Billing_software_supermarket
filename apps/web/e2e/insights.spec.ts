import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('owner overview reconciles posted sales, costs and cash without an AI request', async ({ page, context }, testInfo) => {
  const failures: string[] = []; page.on('pageerror', (error) => failures.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto('/');
  const origin = new URL(page.url()).origin;
  const registered = await context.request.post('/api/v1/auth/register', { headers: { Origin: origin }, data: { email: 'insights-' + randomUUID() + '@example.com', password: randomUUID() + '-Owner', display_name: 'DEMO owner overview' } });
  expect(registered.status()).toBe(201);
  const session = await registered.json() as { csrf_token: string };
  const api = async (path: string, body?: unknown) => {
    const response = body === undefined ? await context.request.get('/api/v1' + path) : await context.request.post('/api/v1' + path, { headers: { Origin: origin, 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': randomUUID() }, data: body });
    expect(response.ok(), path + ': ' + await response.text()).toBeTruthy(); return await response.json();
  };
  const business = await api('/businesses', { name: 'DEMO owner dashboard', store_name: 'DEMO main store', store_address: 'Synthetic browser checks only' }) as { id: string };
  const base = '/businesses/' + business.id;
  const stores = await api(base + '/stores') as { id: string }[];
  const path = base + '/stores/' + stores[0].id;
  const terminal = await api(path + '/terminals', { name: 'DEMO counter' }) as { id: string };
  const product = await api(base + '/products', { sku: 'DEMO-OVERVIEW', name: 'DEMO rice packet', unit: 'pcs', purchase_price: '30', landed_cost: '30', selling_price: '50', mrp: '50', gst_rate: '0', reorder_level: '9', reorder_quantity: '10', barcodes: [], confirm_distinct_product: true }) as { id: string };
  await api(path + '/opening-stock', { product_id: product.id, quantity: '10', unit_cost: '30', reason: 'Synthetic reviewed count', confirmed: true });
  const account = await api(path + '/accounts', { name: 'DEMO drawer', kind: 'cash', terminal_id: terminal.id, opening_amount: '500', reason: 'Synthetic opening funds', confirmed: true }) as { id: string };
  const shift = await api(path + '/cash-sessions', { account_id: account.id, opening_cash: '500', reason: 'Synthetic cash count', confirmed: true }) as { id: string };
  await api(path + '/sales', { terminal_id: terminal.id, lines: [{ product_id: product.id, quantity: '2', expected_price: '50', discount: '0' }], bill_discount: '0', payments: [{ account_id: account.id, cash_session_id: shift.id, method: 'cash', amount: '100' }], confirmed: true });
  await api(path + '/expenses', { account_id: account.id, cash_session_id: shift.id, reference: 'DEMO-OVERVIEW-EXP', expense_date: '2026-10-10', category: 'delivery', description: 'Synthetic delivery expense', amount: '10', confirmed: true });
  await page.reload();
  await expect(page.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute('aria-current', 'page');
  const totals = page.getByLabel('Recorded business totals');
  await expect(totals.getByText('\u20b940.00', { exact: true })).toBeVisible();
  await expect(totals.getByText('\u20b9100.00', { exact: true })).toHaveCount(2);
  await expect(totals.getByText('\u20b910.00', { exact: true })).toBeVisible();
  await expect(page.getByText('\u20b9590.00', { exact: true })).toBeVisible();
  await expect(page.getByText('\u20b930.00', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review reorder levels', exact: true })).toBeVisible();
  await expect(page.getByText(/Rules-based checks/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('owner-desktop.png'), fullPage: true });
  for (const width of [390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(totals.getByText('\u20b940.00', { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('owner-' + width + '.png'), fullPage: true });
  }
  await page.getByRole('button', { name: 'Open related records', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Recorded inventory' })).toBeVisible();
  expect(failures).toEqual([]);
});
