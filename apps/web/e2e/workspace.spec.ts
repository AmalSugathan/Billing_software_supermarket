import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('workspace: focused navigation, product entry and responsive checkout', async ({ page, context }, testInfo) => {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('signin.png'), fullPage: true });
  const origin = new URL(page.url()).origin;
  const registered = await context.request.post('/api/v1/auth/register', {
    headers: { Origin: origin }, data: { email: 'workspace-' + randomUUID() + '@example.com', password: randomUUID() + '-Workspace', display_name: 'DEMO workspace review' },
  });
  expect(registered.status()).toBe(201);
  const session = await registered.json() as { csrf_token: string };
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token };
  const created = await context.request.post('/api/v1/businesses', { headers, data: { name: 'DEMO supermarket', store_name: 'DEMO main store', store_address: 'Synthetic interface validation' } });
  expect(created.status()).toBe(201);
  await page.reload();
  const nav = page.getByRole('navigation', { name: 'Operation modules' });
  await nav.getByRole('button', { name: 'Products & barcodes' }).click();
  await expect(nav.getByRole('button', { name: 'Products & barcodes' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText(/PHASE 1|The development path|Development foundation/)).toHaveCount(0);
  await expect(page.getByLabel('Product name', { exact: true })).not.toBeVisible();
  await page.getByText('Add a product', { exact: true }).click();
  await page.getByLabel('Product name', { exact: true }).fill('DEMO rice packet');
  await page.getByLabel('SKU', { exact: true }).fill('DEMO-UI-RICE');
  for (const [label, amount] of [['Purchase price', '45'], ['Landed cost', '45'], ['Selling price', '55'], ['MRP', '60']]) {
    await page.getByLabel(new RegExp('^' + label + ' ')).fill(amount);
  }
  await page.getByRole('button', { name: 'Save product', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Product saved' })).toBeVisible();
  await page.getByText('Add a product', { exact: true }).click();
  await expect(page.getByRole('cell', { name: 'DEMO rice packet', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('catalog-desktop.png'), fullPage: true });
  await nav.getByRole('button', { name: 'Settings & access' }).click();
  await expect(page.getByRole('heading', { name: 'Staff access', exact: true })).toBeVisible();
  await page.getByLabel('Terminal name', { exact: true }).fill('DEMO counter');
  await page.getByRole('button', { name: 'Add terminal', exact: true }).click();
  await expect(page.getByText('DEMO counter', { exact: true })).toBeVisible();
  await nav.getByRole('button', { name: 'POS billing', exact: true }).click();
  await expect(page.getByText('Your bill is empty')).toBeVisible();
  await page.getByLabel('Product name, SKU or barcode').fill('DEMO-UI-RICE');
  await page.getByRole('button', { name: 'Find / add product' }).click();
  await expect(page.getByText('Total INR 55.00', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Complete sale', exact: true })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath('checkout-desktop.png'), fullPage: true });
  for (const width of [390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.getByRole('button', { name: 'Complete sale', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('checkout-' + width + '.png'), fullPage: true });
  }
  await nav.getByRole('button', { name: 'Invoice inbox', exact: true }).click();
  await expect(page.getByRole('list', { name: 'Invoice workflow' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('invoice-tablet.png'), fullPage: true });
  expect(failures).toEqual([]);
});
