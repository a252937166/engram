// Narrow-viewport behaviour (runs under the `laptop` 1024x768 and
// `mobile` 390x844 projects): the console collapses into tabs, the chat
// tab is fully usable, and the engine panes stay reachable.
const { test, expect } = require('@playwright/test');

test('tabs layout: chat is usable, engine panes reachable', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#tabBar')).toBeVisible();

  // chat tab: full-height dock, send a message end-to-end
  await page.locator('#tabBar button[data-tab="chat"]').click();
  const input = page.getByPlaceholder('Talk to your agent');
  await expect(input).toBeVisible();
  await input.fill('I am vegetarian and I love hiking.');
  await input.press('Enter');
  const bot = page.locator('.msg.bot').last();
  await expect(bot.locator('.bubble')).toContainText(/\w/, { timeout: 30000 });
  await expect(bot.locator('.audit-badge')).toBeVisible({ timeout: 30000 });

  // decision tab shows the inspector that just reacted to the turn
  await page.locator('#tabBar button[data-tab="decision"]').click();
  await expect(page.locator('#inspector')).toBeVisible();
  await expect(page.locator('#dQuery')).toContainText('hiking');

  // graph tab shows the constellation canvas
  await page.locator('#tabBar button[data-tab="graph"]').click();
  await expect(page.locator('#center')).toBeVisible();
});
