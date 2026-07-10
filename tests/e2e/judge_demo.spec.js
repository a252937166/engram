// End-to-end: the Memory Decision Console against the real server
// (offline fake-Qwen mode — deterministic, no API key, no network).
const { test, expect } = require('@playwright/test');

test.describe('memory decision console', () => {

  test('console loads: proof bar, constellation, judge demo entry', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/ENGRAM/);
    await expect(page.locator('#proofBar .pc')).toHaveCount(5);
    await expect(page.locator('#jdBtn')).toBeVisible();
    await expect(page.locator('canvas#space')).toBeVisible();
  });

  test('chat turn: streamed reply, audit badge, inspector replay', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.click();
    const msg = 'I am vegetarian and I have a severe peanut allergy.';
    await input.fill(msg);
    await input.press('Enter');

    const bot = page.locator('.msg.bot').last();
    await expect(bot.locator('.bubble')).toContainText(/\w/, { timeout: 30000 });
    const badge = bot.locator('.audit-badge');
    await expect(badge).toBeVisible({ timeout: 30000 });
    await expect(badge).toContainText(/tk/);

    // replay the frozen decision into the Inspector
    await badge.click();
    await expect(page.locator('#dQuery')).toContainText('peanut allergy');

    // memory ops crystallized (typed memories extracted from the turn)
    await expect(page.locator('#dOp .op-card').first()).toBeVisible();
  });

  test('IME composition Enter must not send', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.click();
    await input.fill('draft that must stay unsent');
    // Simulated composition Enter (keyCode 229 path)
    await page.evaluate(() => {
      const el = document.querySelector('#input');
      const e = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
      Object.defineProperty(e, 'keyCode', { get: () => 229 });
      el.dispatchEvent(e);
    });
    await page.waitForTimeout(400);
    await expect(page.locator('.msg.user')).toHaveCount(0);
    await expect(input).toHaveValue('draft that must stay unsent');
  });

  test('judge demo: 5/5 live-verified against the store', async ({ page }) => {
    await page.goto('/');
    await page.locator('#jdBtn').click();
    await expect(page.locator('#tlResults'))
      .toContainText('TRACK 1 REQUIREMENTS: 5 / 5 PASSED', { timeout: 150000 });
    // the belief-revision check is verified against the live store
    await expect(page.locator('#tlResults'))
      .toContainText('active Acme employment claims: 0');
  });

});
