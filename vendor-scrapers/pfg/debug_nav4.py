#!/usr/bin/env python3
"""PFG debug #4: Use Playwright click() (not evaluate) for React/MUI event handling."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./browser_profile", headless=True, viewport={"width": 1600, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        sf = Path("./storage_state.json")
        if sf.exists():
            with open(sf) as f:
                state = json.load(f)
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])

        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        print(f"Start: URL={page.url} body={await page.evaluate('() => document.body.innerText.length')}")

        # Test 1: Click Invoices top nav via Playwright click()
        print("\n=== Click 'Invoices' top nav ===")
        await page.click('button:has-text("Invoices")')
        await page.wait_for_timeout(2000)

        # Dump what appeared (the cf-menu-item dropdown)
        items = await page.evaluate("""
            () => {
                const results = [];
                const menuItems = document.querySelectorAll('.cf-menu-item, [class*="cf-menu"]');
                for (const el of menuItems) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        text: (el.innerText || '').trim().substring(0, 80),
                        cls: (el.className || '').substring(0, 80),
                        top: Math.round(rect.top),
                        visible: rect.width > 0 && rect.height > 0,
                    });
                }
                return results;
            }
        """)
        print(f"  Menu items found: {len(items)}")
        for item in items:
            vis = "VIS" if item['visible'] else "hid"
            print(f"  [{vis}] top={item['top']} \"{item['text']}\"")

        # Test 2: Click the "Invoices" menu item via Playwright click() (NOT evaluate)
        print("\n=== Click 'Invoices' menu item via Playwright ===")
        try:
            # Use locator for precise clicking
            menu_item = page.locator('.cf-menu-item:has-text("Invoices")').first
            if await menu_item.count() > 0:
                await menu_item.click()
                print("  Clicked via .cf-menu-item locator")
            else:
                # Fallback: click the LI or text
                await page.click('li:has-text("Invoices"):not([class*="MuiButton"])')
                print("  Clicked via li locator")
        except Exception as e:
            print(f"  Error: {e}")
            # Try direct text click below the header
            try:
                invoices_items = page.locator('text="Invoices"')
                count = await invoices_items.count()
                print(f"  Found {count} 'Invoices' text elements")
                for i in range(count):
                    box = await invoices_items.nth(i).bounding_box()
                    if box and box['y'] > 70:  # Below header
                        print(f"  Clicking element {i} at y={box['y']}")
                        await invoices_items.nth(i).click()
                        break
            except Exception as e2:
                print(f"  Fallback error: {e2}")

        # Wait for SPA to load content
        await page.wait_for_timeout(10000)

        print(f"\n  URL: {page.url}")
        print(f"  Title: {await page.title()}")
        body_len = await page.evaluate("() => document.body.innerText.length")
        print(f"  Body length: {body_len}")

        # Dump page content (first 3000 chars)
        body = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
        print(f"\n=== PAGE CONTENT ===")
        print(body[:3000])

        # Check for tables
        table_info = await page.evaluate("""
            () => ({
                tables: document.querySelectorAll('table').length,
                rows: document.querySelectorAll('table tbody tr, tr').length,
                divRows: document.querySelectorAll('[class*="row"], [class*="Row"]').length,
                invoiceMentions: (document.body.innerText.match(/\\d{6,10}/g) || []).length,
            })
        """)
        print(f"\n  Tables: {table_info['tables']}, Rows: {table_info['rows']}, DivRows: {table_info['divRows']}, Invoice numbers: {table_info['invoiceMentions']}")

        # Test 3: Try direct URL paths
        print("\n\n=== Test direct URL paths ===")
        for path in ['/invoices', '/invoice', '/billing', '/billing/invoices', '/ap', '/ap/invoices']:
            url = f"https://www.customerfirstsolutions.com{path}"
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            status = resp.status if resp else "?"
            title = await page.title()
            body_len = await page.evaluate("() => document.body.innerText.length")
            print(f"  {url} -> {status} | {title} | body={body_len}")
            if body_len > 700:
                sample = await page.evaluate("() => document.body.innerText.substring(0, 500)")
                print(f"    {sample[:200]}")
            await page.wait_for_timeout(1000)

        await context.close()

asyncio.run(main())
