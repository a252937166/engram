// E2E config: boots the real server in offline fake-Qwen mode.
// Run locally:  npx playwright install chromium && npx playwright test
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: 'tests/e2e',
  timeout: 180000,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://127.0.0.1:8788',
  },
  projects: [
    {
      name: 'desktop',
      testIgnore: /responsive/,
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'laptop',
      testMatch: /responsive/,
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'mobile',
      testMatch: /responsive/,
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: {
    command: 'rm -f e2e-tmp.db e2e-tmp.db-wal e2e-tmp.db-shm && python3 backend/server.py',
    port: 8788,
    reuseExistingServer: false,
    env: {
      ENGRAM_FAKE_QWEN: '1',
      ENGRAM_FAKE_STREAM_DELAY: '0.06',
      ENGRAM_DB: 'e2e-tmp.db',
      ENGRAM_SEED_USER: '',
      ENGRAM_PORT: '8788',
    },
  },
});
