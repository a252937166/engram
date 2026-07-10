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

  test('chat workspace: three panes, chat works, exit restores console', async ({ page }) => {
    await page.goto('/');
    await page.locator('#dockFocus').click();
    await expect(page.locator('#workspace')).toBeVisible();
    await expect(page.locator('#wsSessList .ws-s')).toHaveCount(1);
    // inspector lives in the evidence pane while the workspace is open
    await expect(page.locator('#wsEvid #inspector')).toBeVisible();

    const input = page.getByPlaceholder('Talk to your agent');
    await input.fill('Remember that I always deploy on Tuesdays.');
    await input.press('Enter');
    const bot = page.locator('#wsConv .msg.bot').last();
    await expect(bot.locator('.bubble')).toContainText(/\w/, { timeout: 30000 });
    // the evidence pane reacted to the live turn
    await expect(page.locator('#wsEvid #dQuery')).toContainText('Tuesdays');

    await page.locator('#wsBack').click();
    await expect(page.locator('#workspace')).toBeHidden();
    await expect(page.locator('#dockBody #log')).toBeVisible();
    await expect(page.locator('main #inspector')).toBeVisible();
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

  test('streaming follows only near the bottom; pill appears otherwise', async ({ page }) => {
    await page.goto('/');
    const state = await page.evaluate(() => {
      const log = document.querySelector('#log');
      // build a tall history so the log actually scrolls
      for (let i = 0; i < 40; i++) {
        const d = document.createElement('div');
        d.className = 'msg ' + (i % 2 ? 'bot' : 'user');
        d.textContent = 'filler message ' + i;
        log.appendChild(d);
      }
      // reader scrolled up -> appended content must NOT hijack the viewport
      log.scrollTop = 0;
      scrollLog();
      const hijacked = log.scrollTop > 5;
      const pillShown = document.querySelector('#newMsgPill')
        .classList.contains('show');
      // reader jumps back down -> follow again, pill clears
      scrollLog(true);
      const followed = Math.abs(
        log.scrollHeight - log.scrollTop - log.clientHeight) < 5;
      const pillCleared = !document.querySelector('#newMsgPill')
        .classList.contains('show');
      return { hijacked, pillShown, followed, pillCleared };
    });
    expect(state.hijacked).toBe(false);
    expect(state.pillShown).toBe(true);
    expect(state.followed).toBe(true);
    expect(state.pillCleared).toBe(true);
  });

  test('history pagination preserves the reading position', async ({ page }) => {
    await page.goto('/');
    // create 26 turns (52 rows) straight through the API - fast in fake mode
    const seeded = await page.evaluate(async () => {
      const uid = localStorage.getItem('engram_uid');
      const s = await (await fetch('/api/sessions', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: uid, title: 'long history'})})).json();
      for (let i = 0; i < 26; i++) {
        // read the body to completion - the SSE stream IS the turn; the
        // fetch promise alone resolves on headers, before anything persists
        const r = await fetch('/api/chat', {method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({user_id: uid, session_id: s.id,
            message: 'filler turn number ' + i + ' about nothing much'})});
        await r.text();
      }
      return s.id;
    });
    await page.reload();
    await expect(page.locator('#sessSel option')).toHaveCount(2, { timeout: 15000 });
    await page.locator('#sessSel').selectOption(seeded);
    const earlier = page.locator('.load-earlier');
    await expect(earlier).toBeVisible({ timeout: 15000 });

    const anchorText = await page.evaluate(() => {
      const log = document.querySelector('#log');
      const first = log.querySelector('.msg');
      return first.textContent.slice(0, 30);
    });
    await earlier.click();
    await expect(page.locator('#log .msg').first())
      .not.toHaveText(new RegExp('^' + anchorText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), { timeout: 10000 });
    // the previously-first message is still inside the viewport (no jump)
    const anchored = await page.evaluate(txt => {
      const log = document.querySelector('#log');
      const el = [...log.querySelectorAll('.msg')]
        .find(m => m.textContent.startsWith(txt));
      if (!el) return false;
      const lr = log.getBoundingClientRect(), er = el.getBoundingClientRect();
      return er.bottom > lr.top - 4 && er.top < lr.bottom + 4;
    }, anchorText);
    expect(anchored).toBe(true);
  });

  test('chat dock drag-resize persists', async ({ page }) => {
    await page.goto('/');
    const grip = page.locator('#dockGrip');
    const before = await page.evaluate(() =>
      document.querySelector('#dockBody').getBoundingClientRect().height);
    const box = await grip.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y - 120, { steps: 6 });
    await page.mouse.up();
    const after = await page.evaluate(() =>
      document.querySelector('#dockBody').getBoundingClientRect().height);
    expect(after).toBeGreaterThan(before + 60);
    const saved = await page.evaluate(() => localStorage.getItem('engram_chat_h'));
    expect(saved).toMatch(/px/);
  });

});
