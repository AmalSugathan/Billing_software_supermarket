import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('private invoice intake: lost response replay, source preview and no stock posting', async ({ page, context }) => {
  const failures: string[] = []; page.on('pageerror', (error) => failures.push(error.message));
  await page.goto('/');
  const origin = new URL(page.url()).origin;
  const registered = await context.request.post('/api/v1/auth/register', { headers: { Origin: origin }, data: { email: 'ocr-pilot-' + randomUUID() + '@example.com', password: randomUUID() + '-Pilot', display_name: 'DEMO invoice intake pilot' } });
  expect(registered.status()).toBe(201);
  const session = await registered.json() as { csrf_token: string };
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token };
  const created = await context.request.post('/api/v1/businesses', { headers, data: { name: 'DEMO private invoice pilot', store_name: 'DEMO invoice review', store_address: 'Synthetic automated test only' } });
  expect(created.status()).toBe(201);
  const business = await created.json() as { id: string };
  const base = '/api/v1/businesses/' + business.id;
  const storesResponse = await context.request.get(base + '/stores');
  const stores = await storesResponse.json() as { id: string }[];
  const path = base + '/stores/' + stores[0].id;
  await page.reload();
  await page.getByRole('button', { name: 'Invoice inbox', exact: true }).click();
  const availabilityResponse = await context.request.get(path + '/ocr-documents/provider');
  const availability = await availabilityResponse.json() as { message: string; provider_configured: boolean };
  await expect(page.getByRole('status').filter({ hasText: availability.message })).toBeVisible();
  const image = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 400; canvas.height = 200;
    const ctx = canvas.getContext('2d')!; ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, 400, 200); ctx.fillStyle = '#000000'; ctx.font = '20px sans-serif'; ctx.fillText('DEMO synthetic bill 50.00', 20, 60);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await page.getByLabel('Invoice image or PDF').setInputFiles({ name: 'DEMO-pilot-invoice.png', mimeType: 'image/png', buffer: Buffer.from(image, 'base64') });
  await page.getByLabel('Evidence origin').selectOption('synthetic');
  let dropped = false;
  const keys: string[] = [];
  await page.route('**/ocr-documents?filename=*', async (route) => {
    keys.push(route.request().headers()['idempotency-key']);
    if (!dropped) { const response = await route.fetch(); expect(response.status()).toBe(201); dropped = true; await route.abort('failed'); }
    else await route.continue();
  });
  await page.getByRole('button', { name: 'Save invoice privately', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(/Upload status is uncertain/);
  await page.getByRole('button', { name: 'Save invoice privately', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Invoice saved privately' })).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
  const preview = page.getByAltText('Supplier invoice source for human review');
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
  const runOcr = page.getByRole('button', { name: 'Run private OCR', exact: true });
  if (availability.provider_configured) await expect(runOcr).toBeEnabled();
  else await expect(runOcr).toBeDisabled();
  const documentsResponse = await context.request.get(path + '/ocr-documents');
  expect(documentsResponse.headers()['cache-control']).toBe('no-store');
  const documents = await documentsResponse.json() as { id: string; status: string; data_origin: string }[];
  expect(documents).toHaveLength(1); expect(documents[0]).toMatchObject({ status: 'uploaded', data_origin: 'synthetic' });
  const source = await context.request.get(path + '/ocr-documents/' + documents[0].id + '/content?original=true');
  expect(source.headers()['content-disposition']).toMatch(/^attachment/);
  expect(await source.body()).toEqual(Buffer.from(image, 'base64'));
  expect(await (await context.request.get(path + '/stock')).json()).toEqual([]);
  expect(await (await context.request.get(path + '/purchases')).json()).toEqual([]);
  expect(failures).toEqual([]);
});
