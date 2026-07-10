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
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: 'python3 backend/server.py',
    port: 8788,
    reuseExistingServer: false,
    env: {
      ENGRAM_FAKE_QWEN: '1',
      ENGRAM_DB: 'e2e-tmp.db',
      ENGRAM_SEED_USER: '',
      ENGRAM_PORT: '8788',
    },
  },
});
