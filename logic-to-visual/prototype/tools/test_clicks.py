"""
Headless browser test for constraint_flow.html click behavior.
Usage: python3 docs/test_clicks.py
"""

import asyncio

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGE ERROR: {e}"))
        page.on(
            "console",
            lambda m: (
                print(f"  [browser {m.type}] {m.text}") if m.type == "error" else None
            ),
        )

        await page.goto("http://localhost:8765/constraint_flow.html")
        await page.wait_for_timeout(1500)

        if errors:
            print("JS errors detected:", errors)
        else:
            print("No JS errors on load ✓")

        # Test 1: Step button click
        await page.click('button.step-btn[data-step="1"]')
        await page.wait_for_timeout(400)
        title = await page.inner_text("#step-title")
        print(f"Step 1 click → sidebar title: {title!r}")

        # Test 2: Count tagged nodes
        node_info = await page.evaluate(
            """() => {
            const nodes = document.querySelectorAll('.d2-node[data-node-id]');
            return {
                count: nodes.length,
                ids: Array.from(nodes).map(n => n.dataset.nodeId)
            };
        }"""
        )
        print(f"Tagged d2-node count: {node_info['count']}")
        print(f"Node IDs found: {node_info['ids']}")

        # Test 3: Click stores node via JS dispatch
        await page.evaluate(
            """() => {
            const el = document.querySelector('.d2-node[data-node-id="stores"]');
            if (el) el.click();
            else console.error('stores node not found!');
        }"""
        )
        await page.wait_for_timeout(300)
        drawer = await page.evaluate(
            """() => ({
            open: document.getElementById('detail-drawer').classList.contains('open'),
            nodeId: document.getElementById('drawer-node-id').textContent,
            bodyLen: document.getElementById('drawer-body').innerHTML.length
        })"""
        )
        print(f"After stores.click(): drawer={drawer}")

        # Test 4: Click stores.notion_db
        await page.evaluate(
            """() => {
            const el = document.querySelector('.d2-node[data-node-id="stores.notion_db"]');
            if (el) el.click();
            else console.error('stores.notion_db not found!');
        }"""
        )
        await page.wait_for_timeout(200)
        drawer2 = await page.evaluate(
            """() => ({
            open: document.getElementById('detail-drawer').classList.contains('open'),
            nodeId: document.getElementById('drawer-node-id').textContent,
        })"""
        )
        print(f"After stores.notion_db.click(): drawer={drawer2}")

        # Test 5: Step 1 after clicking should auto-trigger, does it dim stores?
        lit_stores = await page.evaluate(
            """() => {
            const el = document.querySelector('.d2-node[data-node-id="stores"]');
            return el ? {
                classes: el.className,
                opacity: el.style.opacity,
                cursor: el.style.cursor
            } : null;
        }"""
        )
        print(f"stores node classes/style: {lit_stores}")

        await browser.close()
        print("\nAll tests complete.")


asyncio.run(main())

asyncio.run(main())
