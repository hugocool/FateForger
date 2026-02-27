/**
 * Headless click test for constraint_flow.html
 * Run: node docs/headless_test.cjs
 * (Uses npx playwright context if @playwright/test not installed)
 */

const http = require('http');
const { execSync, spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// Find playwright
let playwrightPath;
try {
  playwrightPath = require.resolve('playwright-core');
} catch (e) {
  // Not in node_modules — try npx cache
  const cacheDir = process.env.npm_config_cache || path.join(process.env.HOME, '.npm', '_npx');
  const found = execSync(`find "${cacheDir}" -name "playwright-core" -type d 2>/dev/null | head -1`).toString().trim();
  if (found) playwrightPath = found;
}

if (!playwrightPath) {
  console.error('playwright-core not found. Install with: npm install playwright-core');
  process.exit(1);
}

const { chromium } = require(playwrightPath);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('[console.error]', msg.text());
  });

  await page.goto('http://localhost:8765/constraint_flow.html');
  await page.waitForTimeout(2000);

  if (errors.length) {
    console.log('⚠️  JS errors:', errors);
  } else {
    console.log('✅ No JS errors');
  }

  const info = await page.evaluate(() => {
    const nodes = document.querySelectorAll('.d2-node[data-node-id]');
    const hasDrawer = !!document.getElementById('detail-drawer');
    const drawerOpen = document.getElementById('detail-drawer')?.classList.contains('open');
    const navBtns = document.querySelectorAll('.step-btn').length;
    return {
      taggedNodeCount: nodes.length,
      nodeIds: Array.from(nodes).map(n => n.dataset.nodeId),
      hasDrawer,
      drawerOpen,
      navBtnCount: navBtns,
    };
  });
  console.log('Tagged nodes:', info.taggedNodeCount);
  console.log('Has drawer:', info.hasDrawer, '  Open:', info.drawerOpen);
  console.log('Nav buttons:', info.navBtnCount);
  console.log('Node IDs:', info.nodeIds.join(', '));

  // Test step 1 click
  await page.click('button.step-btn[data-step="1"]');
  await page.waitForTimeout(400);
  const step1Title = await page.evaluate(() => document.getElementById('step-title').textContent);
  console.log('\nStep 1 nav click → title:', JSON.stringify(step1Title));

  // Test node click
  for (const nodeId of ['extraction', 'stores', 'postprocess']) {
    const clickResult = await page.evaluate((id) => {
      const el = document.querySelector(`.d2-node[data-node-id="${id}"]`);
      if (!el) return { error: `node ${id} not found` };
      el.click();
      return { cursor: el.style.cursor, classes: el.className };
    }, nodeId);
    await page.waitForTimeout(200);
    const drawerOpen = await page.evaluate(() =>
      document.getElementById('detail-drawer').classList.contains('open')
    );
    const drawerNodeId = await page.evaluate(() =>
      document.getElementById('drawer-node-id').textContent
    );
    console.log(`Click "${nodeId}": cursor=${clickResult.cursor}, drawer.open=${drawerOpen}, drawer.nodeId="${drawerNodeId}"`);
    // Reset drawer
    await page.evaluate(() => document.getElementById('detail-drawer').classList.remove('open'));
    await page.waitForTimeout(100);
  }

  await browser.close();
  console.log('\nDone.');
})().catch(e => {
  console.error('FATAL:', e.message);
  process.exit(1);
});
