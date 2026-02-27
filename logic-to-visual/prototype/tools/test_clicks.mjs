import { chromium } from '/Users/hugoevers/VScode-projects/admonish-1/.playwright-mcp/node_modules/playwright-core/index.js';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

page.on('console', m => console.log('[browser]', m.type(), m.text()));
page.on('pageerror', e => console.error('[page error]', e.message));

await page.goto('http://localhost:8765/constraint_flow.html');
await page.waitForTimeout(1500);

// Test 1: step button click updates sidebar
const step1Btn = page.locator('button.step-btn[data-step="1"]').first();
await step1Btn.click();
await page.waitForTimeout(500);
const stepTitle = await page.locator('#step-title').textContent();
console.log('Step 1 click → title:', stepTitle);

// Test 2: how many .d2-node elements are tagged
const nodeInfo = await page.evaluate(() => {
  const nodes = document.querySelectorAll('.d2-node[data-node-id]');
  return {
    count: nodes.length,
    ids: Array.from(nodes).map(n => n.dataset.nodeId),
  };
});
console.log('Tagged nodes:', nodeInfo.count, nodeInfo.ids);

// Test 3: dispatch click on stores node
await page.evaluate(() => {
  const el = document.querySelector('.d2-node[data-node-id="stores"]');
  if (el) {
    el.click();  // uses the click() method vs dispatchEvent
  }
});
await page.waitForTimeout(300);

const drawerState = await page.evaluate(() => ({
  open: document.getElementById('detail-drawer').classList.contains('open'),
  nodeId: document.getElementById('drawer-node-id').textContent,
  bodySnippet: document.getElementById('drawer-body').innerHTML.substring(0, 100),
}));
console.log('Drawer after stores click:', JSON.stringify(drawerState, null, 2));

// Test 4: also try clicking on extraction container
await page.evaluate(() => {
  const el = document.querySelector('.d2-node[data-node-id="extraction"]');
  if (el) el.click();
});
await page.waitForTimeout(200);
const drawerState2 = await page.evaluate(() => ({
  open: document.getElementById('detail-drawer').classList.contains('open'),
  nodeId: document.getElementById('drawer-node-id').textContent,
}));
console.log('Drawer after extraction click:', JSON.stringify(drawerState2));

// Test 5: check if goStep modifies the detail drawer (it shouldn't auto-open it)
await page.evaluate(() => {
  window.goStep(2, null);
});
await page.waitForTimeout(500);
const afterGoStep = await page.evaluate(() => ({
  drawerOpen: document.getElementById('detail-drawer').classList.contains('open'),
  sidebarTitle: document.getElementById('step-title').textContent,
}));
console.log('After goStep(2):', JSON.stringify(afterGoStep));

await browser.close();
console.log('DONE');
