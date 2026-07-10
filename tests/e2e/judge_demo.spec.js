// End-to-end against the real server (offline fake-Qwen mode).
// Default mode is the Conversation Workbench; the console is MEMORY LAB.
const { test, expect } = require('@playwright/test');

test.describe('conversation workbench (default)', () => {

  test('workbench loads: three panes, evidence tabs, mode switch', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/ENGRAM/);
    await expect(page.locator('#workspace')).toBeVisible();
    await expect(page.locator('#wsSess')).toBeVisible();
    await expect(page.locator('#evTabs button')).toHaveCount(4);
    await expect(page.locator('#modeSwitch button[data-mode="chat"]')).toHaveClass(/on/);
    // console panes are hidden in conversation mode
    await expect(page.locator('#proofBar')).toBeHidden();
  });

  test('chat turn: streamed reply, meta line, evidence replay, copy audit', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.click();
    await input.fill('I am vegetarian and I have a severe peanut allergy.');
    await input.press('Enter');

    const bot = page.locator('.msg.bot').last();
    await expect(bot.locator('.bubble')).toContainText(/\w/, { timeout: 30000 });
    const badge = bot.locator('.audit-badge');
    await expect(badge).toBeVisible({ timeout: 30000 });
    await expect(badge).toContainText(/tk/);

    // evidence pane reacted live; replay works from the badge too
    await badge.click();
    await expect(page.locator('#turnPanel #dQuery')).toContainText('peanut allergy');
    await expect(page.locator('#turnPanel .op-card').first()).toBeVisible();
    await expect(page.locator('#copyAudit')).toBeVisible();
  });

  test('evidence tabs: graph window, store filters, demo entry', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.fill('I work at Acme Robotics and I love hiking.');
    await input.press('Enter');
    await expect(page.locator('.msg.bot').last().locator('.audit-badge'))
      .toBeVisible({ timeout: 30000 });

    await page.locator('#evTabs button[data-ev="graph"]').click();
    await expect(page.locator('#graphPanel')).toBeVisible();
    const graphOpen = await page.evaluate(() =>
      document.body.classList.contains('graph-open'));
    expect(graphOpen).toBe(true);

    await page.locator('#evTabs button[data-ev="store"]').click();
    await expect(page.locator('#storeList .mi').first()).toBeVisible();
    await page.locator('#storeFilters button[data-f="active"]').click();
    await expect(page.locator('#storeList .mi').first()).toBeVisible();

    await page.locator('#evTabs button[data-ev="demo"]').click();
    await expect(page.locator('#demoMini .dm')).toHaveCount(5);
    await expect(page.locator('#demoRun')).toBeVisible();
    await expect(page.locator('#mcpCard')).toContainText('MCP READY');
  });

  test('IME composition Enter must not send', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.click();
    await input.fill('draft that must stay unsent');
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

  test('mode switch: lab shows the console, chat restores the workbench', async ({ page }) => {
    await page.goto('/');
    await page.locator('#modeSwitch button[data-mode="lab"]').click();
    await expect(page.locator('#workspace')).toBeHidden();
    await expect(page.locator('#proofBar')).toBeVisible();
    await expect(page.locator('main #inspector')).toBeVisible();
    await expect(page.locator('#dockBody #log')).toBeVisible();

    await page.locator('#modeSwitch button[data-mode="chat"]').click();
    await expect(page.locator('#workspace')).toBeVisible();
    await expect(page.locator('#wsConv #log')).toBeVisible();
    await expect(page.locator('#turnPanel')).toBeVisible();
  });

  test('streaming follows only near the bottom; pill appears otherwise', async ({ page }) => {
    await page.goto('/');
    const state = await page.evaluate(() => {
      const log = document.querySelector('#log');
      for (let i = 0; i < 40; i++) {
        const d = document.createElement('div');
        d.className = 'msg ' + (i % 2 ? 'bot' : 'user');
        d.textContent = 'filler message ' + i;
        log.appendChild(d);
      }
      log.scrollTop = 0;
      scrollLog();
      const hijacked = log.scrollTop > 5;
      const pillShown = document.querySelector('#newMsgPill').classList.contains('show');
      scrollLog(true);
      const followed = Math.abs(log.scrollHeight - log.scrollTop - log.clientHeight) < 5;
      const pillCleared = !document.querySelector('#newMsgPill').classList.contains('show');
      return { hijacked, pillShown, followed, pillCleared };
    });
    expect(state.hijacked).toBe(false);
    expect(state.pillShown).toBe(true);
    expect(state.followed).toBe(true);
    expect(state.pillCleared).toBe(true);
  });

  test('history pagination preserves the reading position', async ({ page }) => {
    await page.goto('/');
    const seeded = await page.evaluate(async () => {
      const uid = localStorage.getItem('engram_uid');
      const s = await (await fetch('/api/sessions', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: uid, title: 'long history'})})).json();
      for (let i = 0; i < 26; i++) {
        const r = await fetch('/api/chat', {method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({user_id: uid, session_id: s.id,
            message: 'filler turn number ' + i + ' about nothing much'})});
        await r.text();
      }
      return s.id;
    });
    await page.reload();
    await expect(page.locator('#wsSessList .ws-s')).toHaveCount(2, { timeout: 15000 });
    await page.locator('#wsSessList .ws-s[data-id="' + seeded + '"]').click();
    const earlier = page.locator('.load-earlier');
    await expect(earlier).toBeVisible({ timeout: 15000 });

    const anchorText = await page.evaluate(() => {
      const first = document.querySelector('#log .msg');
      return first.textContent.slice(0, 30);
    });
    await earlier.click();
    await expect(page.locator('#log .msg').first())
      .not.toHaveText(new RegExp('^' + anchorText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), { timeout: 10000 });
    const anchored = await page.evaluate(txt => {
      const log = document.querySelector('#log');
      const el = [...log.querySelectorAll('.msg')].find(m => m.textContent.startsWith(txt));
      if (!el) return false;
      const lr = log.getBoundingClientRect(), er = el.getBoundingClientRect();
      return er.bottom > lr.top - 4 && er.top < lr.bottom + 4;
    }, anchorText);
    expect(anchored).toBe(true);
  });

  test('stop preserves the partial response and marks it stopped', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.fill('Tell me something and I will interrupt you mid-stream.');
    await input.press('Enter');
    const send = page.locator('#send');
    await expect(send).toHaveClass(/stop/);           // button became Stop
    await send.click();                               // abort mid-stream
    await expect(send).not.toHaveClass(/stop/, { timeout: 10000 });
    await expect(page.locator('.msg.bot').last().locator('.bubble'))
      .toContainText('stopped', { timeout: 10000 });
  });

  test('server policy verdict: deny is enforced, audited, and replayable', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.fill('Standing rule: never restart the billing pods directly, always drain traffic first.');
    await input.press('Enter');
    await expect(page.locator('.msg.bot').last().locator('.audit-badge'))
      .toBeVisible({ timeout: 30000 });
    await input.fill('Latency is spiking — should I restart the billing pod right now?');
    await input.press('Enter');
    const gate = page.locator('.policy-gate').last();
    await expect(gate).toBeVisible({ timeout: 30000 });
    await expect(gate).toContainText('ACTION DENIED');
    await expect(gate).toContainText('server-side gate');
    // the verdict is persisted in the turn audit
    const badge = page.locator('.msg.bot').last().locator('.audit-badge');
    await expect(badge).toBeVisible({ timeout: 30000 });
    const mid = await badge.getAttribute('data-mid');
    const audit = await page.evaluate(async id => {
      const uid = localStorage.getItem('engram_uid');
      return await (await fetch('/api/turn_audit?user_id='+uid+'&message_id='+id)).json();
    }, mid);
    expect(audit.policy.decision).toBe('deny');
    expect(audit.policy.rule_memory_id).toBeTruthy();
    expect(audit.policy.enforcement).toContain('server-side');
  });

  test('graph label pins on click and clears on Escape', async ({ page }) => {
    await page.goto('/');
    const input = page.getByPlaceholder('Talk to your agent');
    await input.fill('I am vegetarian and I work at Acme Robotics.');
    await input.press('Enter');
    await expect(page.locator('.msg.bot').last().locator('.audit-badge'))
      .toBeVisible({ timeout: 30000 });
    await page.locator('#modeSwitch button[data-mode="lab"]').click();
    const pinned = await page.evaluate(() => {
      const n = [...nodes.values()][0];
      cv.dispatchEvent(new MouseEvent('click', {clientX: n.x, clientY: n.y, bubbles: true}));
      return [...nodes.values()].some(x => x.pinned);
    });
    expect(pinned).toBe(true);
    await page.keyboard.press('Escape');
    const cleared = await page.evaluate(() => ![...nodes.values()].some(x => x.pinned));
    expect(cleared).toBe(true);
    // that same Escape must NOT have exited the lab (pin layer absorbed it)
    await expect(page.locator('#proofBar')).toBeVisible();
  });

});

test.describe('memory lab', () => {

  test('lab chat dock drag-resize persists', async ({ page }) => {
    await page.goto('/?mode=lab');
    const grip = page.locator('#dockGrip');
    await expect(grip).toBeVisible();
    const before = await page.evaluate(() =>
      document.querySelector('#dockBody').getBoundingClientRect().height);
    const box = await grip.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y - 120, { steps: 6 });
    await page.mouse.up();
    const after = await page.evaluate(() =>
      document.querySelector('#dockBody').getBoundingClientRect().height);
    expect(after).toBeGreaterThan(before + 60);
    const saved = await page.evaluate(() => localStorage.getItem('engram_chat_h'));
    expect(saved).toMatch(/px/);
  });

  test('judge demo: opens the lab, 5/5 live-verified, mirrored to evidence', async ({ page }) => {
    await page.goto('/');
    await page.locator('#jdMini').click();      // from the workbench header
    await expect(page.locator('#proofBar')).toBeVisible();   // auto-switched to lab
    await expect(page.locator('#tlResults'))
      .toContainText('TRACK 1 REQUIREMENTS: 5 / 5 PASSED', { timeout: 150000 });
    await expect(page.locator('#tlResults'))
      .toContainText('active Acme employment claims: 0');
    // verdict mirrored into the workbench Demo tab
    await page.locator('#modeSwitch button[data-mode="chat"]').click();
    await page.locator('#evTabs button[data-ev="demo"]').click();
    await expect(page.locator('#demoVerdict')).toContainText('5 / 5 PASSED');
  });

});
