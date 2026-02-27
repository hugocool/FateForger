const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const errors = [];
  page.on('pageerror', e => errors.push('PAGE_ERR: ' + e.message));

  await page.goto('http://localhost:8765/constraint_flow.html');
  await page.waitForTimeout(1500);

  if (errors.length) console.log('JS errors:', errors);
  else console.log('No JS errors on load ✓');

  // How many d2-node elements got tagged?
  const nodeInfo = await page.evaluate(() => {
    const nodes = document.querySelectorAll('.d2-node[data-node-id]');
    return {
      count: nodes.length,
      ids: Array.from(nodes).map(n => n.dataset.nodeId),
      cursorNodes: Array.from(nodes).filter(n => n.style.cursor === 'pointer').map(n => n.dataset.nodeId)
    };
  });
  console.log('Tagged nodes:', nodeInfo.count);
  console.log('With cursor:pointer:', nodeInfo.cursorNodes);

  // Test clicking each container node
  for (const id of ['extraction', 'stores', 'prefetch', 'filters', 'postprocess']) {
    await page.evaluate((nodeId) => {
      const el = document.querySelector(`.d2-node[data-node-id="${nodeId}"]`);
      if (el) el.click();
    }, id);
    await page.waitForTimeout(150);
    const state = await page.evaluate(() => ({
      open: document.getElementById('detail-drawer').classList.contains('open'),
      nid: document.getElementById('drawer-node-id').textContent || ''
    }));
    console.log(`Click ${id}: drawer.open=${state.open}, nodeId="${state.nid}"`);
    // close for next test
    await page.evaluate(() => document.getElementById('detail-drawer').classList.remove('open'));
  }

  // Test step button
  await page.click('button.step-btn[data-step="2"]');
  await page.waitForTimeout(400);
  const title = await page.innerText('#step-title');
  console.log('Step 2 nav click → title:', title);

  await browser.close();
  console.log('Done.');
})().catch(e => console.error('FATAL:', e.message));
