#!/usr/bin/env python3
"""PFG debug #3: Click Invoices top nav -> click Invoices sub-tab -> dump content."""
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

        # Step 1: Click "Invoices" in top nav
        print("=== Step 1: Click top nav Invoices ===")
        btn = await page.query_selector('button:has-text("Invoices")')
        if btn:
            await btn.click()
            await page.wait_for_timeout(5000)
            print(f"URL: {page.url}")

        # Step 2: Find all clickable "Invoices" text on page and click the SUB one
        print("\n=== Step 2: Find Invoices sub-tab and click it ===")
        all_invoices = await page.evaluate("""
            () => {
                const results = [];
                const els = document.querySelectorAll('a, button, [role="tab"], li, span, div');
                for (const el of els) {
                    const text = (el.innerText || '').trim();
                    if (text === 'Invoices' || text === 'INVOICES') {
                        const rect = el.getBoundingClientRect();
                        results.push({
                            tag: el.tagName,
                            cls: (el.className || '').substring(0, 80),
                            top: Math.round(rect.top),
                            left: Math.round(rect.left),
                            visible: rect.width > 0 && rect.height > 0,
                            href: (el.href || '').substring(0, 100),
                            clickable: el.tagName === 'A' || el.tagName === 'BUTTON' || !!el.onclick,
                        });
                    }
                }
                return results;
            }
        """)
        for item in all_invoices:
            vis = "VIS" if item['visible'] else "hid"
            print(f"  [{vis}] <{item['tag']} class=\"{item['cls'][:60]}\"> top={item['top']} left={item['left']}")
            if item['href']:
                print(f"    href: {item['href']}")

        # Click the sub-tab (not the top nav one — look for one NOT in the header)
        clicked_sub = False
        for item in all_invoices:
            if item['visible'] and item['top'] > 80:  # Below the header
                print(f"\n  Clicking sub-tab at top={item['top']}...")
                sub_el = await page.evaluate("""
                    (targetTop) => {
                        const els = document.querySelectorAll('a, button, [role="tab"], li, span, div');
                        for (const el of els) {
                            const text = (el.innerText || '').trim();
                            if (text === 'Invoices' || text === 'INVOICES') {
                                const rect = el.getBoundingClientRect();
                                if (Math.round(rect.top) === targetTop && rect.width > 0) {
                                    el.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }
                """, item['top'])
                if sub_el:
                    clicked_sub = True
                    break

        if clicked_sub:
            await page.wait_for_timeout(5000)
            print(f"\n  URL after sub-tab click: {page.url}")
            print(f"  Title: {await page.title()}")

            # Dump page content
            body = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
            print(f"\n=== PAGE CONTENT AFTER SUB-TAB CLICK ===")
            print(body[:3000])

            # Check for invoice tables/rows
            table_info = await page.evaluate("""
                () => {
                    const tables = document.querySelectorAll('table');
                    const rows = document.querySelectorAll('table tbody tr, tr');
                    return {
                        tables: tables.length,
                        rows: rows.length,
                        bodyLen: document.body.innerText.length,
                    };
                }
            """)
            print(f"\n  Tables: {table_info['tables']}, Rows: {table_info['rows']}, Body: {table_info['bodyLen']}")
        else:
            print("  No sub-tab found to click")

        # Step 3: Also test location switching via "Select location(s)"
        print("\n\n=== Step 3: Switch to Chatham via location dialog ===")
        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        loc_btn = await page.query_selector('button:has-text("Select location")')
        if loc_btn:
            await loc_btn.click()
            await page.wait_for_timeout(2000)

            # Uncheck Dennis, check Chatham
            checkboxes = await page.query_selector_all('[class*="MuiCheckbox"], input[type="checkbox"]')
            print(f"  Found {len(checkboxes)} checkboxes")

            # Dump checkbox labels for clarity
            for i, cb in enumerate(checkboxes):
                label = await cb.evaluate("(el) => (el.closest('label') || el.parentElement)?.innerText?.trim()?.substring(0, 80) || ''")
                checked = await cb.evaluate("(el) => el.checked || el.querySelector('input')?.checked || el.getAttribute('aria-checked') === 'true' || false")
                print(f"  [{i}] {'CHECKED' if checked else 'unchecked'}: \"{label}\"")

        await context.close()

asyncio.run(main())
