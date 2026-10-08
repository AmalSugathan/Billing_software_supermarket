import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './e2e', timeout: 90000, workers: 1, retries: 0, reporter: 'list', use: { baseURL: process.env.PILOT_WEB_URL ?? 'http://127.0.0.1:8080', browserName: 'chromium', ...(process.env.PILOT_BROWSER_CHANNEL ? { channel: process.env.PILOT_BROWSER_CHANNEL } : {}), trace: 'retain-on-failure' } });
