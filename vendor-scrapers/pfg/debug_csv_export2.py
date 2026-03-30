#!/usr/bin/env python3
"""PFG discovery #2: Click Select All fields -> Download CSV. Also explore date filter and kebab menu."""
import asyncio, json, csv, io
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./browser_profile", headless=True, viewport={"width": 1600, "height": 900},
            accept_downloads=True,
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

        # Navigate to Invoices
        print("=== Navigating to Invoices ===")
        await page.locator('button:has-text("Invoices")').click()
        await page.wait_for_timeout(1500)
        await page.locator('.cf-menu-item:has-text("Invoices")').first.click()
        await page.wait_for_timeout(5000)
        print(f"URL: {page.url}")

        # ─── TEST 1: Date filter dropdown ───
        print("\n\n=== TEST 1: DATE FILTER DROPDOWN ===")
        date_btn = page.locator('[data-testid="invoices-date-dropdown-select-btn"]')
        if await date_btn.count() > 0:
            await date_btn.click()
            await page.wait_for_timeout(2000)

            # Dump what appeared
            date_options = await page.evaluate("""
                () => {
                    const results = [];
                    // Look for popover/menu/dropdown
                    const containers = document.querySelectorAll(
                        '[role="presentation"], [role="menu"], [role="listbox"], ' +
                        '[class*="MuiPopover"], [class*="MuiMenu"], [class*="MuiPaper"]'
                    );
                    for (const c of containers) {
                        const rect = c.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            const text = (c.innerText || '').trim();
                            if (text.length > 0) {
                                // Get all clickable items within
                                const items = [];
                                for (const item of c.querySelectorAll('li, [role="menuitem"], [role="option"], button, a, div')) {
                                    const itext = (item.innerText || '').trim();
                                    const irect = item.getBoundingClientRect();
                                    if (itext && irect.width > 0 && irect.height > 0 && itext.length < 100) {
                                        items.push({
                                            tag: item.tagName,
                                            text: itext,
                                            cls: (item.className || '').substring(0, 80),
                                            testid: item.getAttribute('data-testid') || '',
                                        });
                                    }
                                }
                                results.push({
                                    text: text.substring(0, 500),
                                    items: items,
                                });
                            }
                        }
                    }
                    return results;
                }
            """)
            for container in date_options:
                print(f"  Container text: \"{container['text'][:200]}\"")
                print(f"  Items: {len(container['items'])}")
                for item in container['items']:
                    print(f"    <{item['tag']}> \"{item['text']}\" testid=\"{item['testid']}\"")

            # Also look for date input fields
            date_inputs = await page.evaluate("""
                () => {
                    const results = [];
                    const inputs = document.querySelectorAll('input');
                    for (const inp of inputs) {
                        const rect = inp.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top > 200) {
                            results.push({
                                type: inp.type,
                                value: inp.value || '',
                                placeholder: inp.placeholder || '',
                                cls: (inp.className || '').substring(0, 80),
                                testid: inp.getAttribute('data-testid') || '',
                                top: Math.round(rect.top),
                            });
                        }
                    }
                    return results;
                }
            """)
            print(f"\n  Visible inputs after date click: {len(date_inputs)}")
            for inp in date_inputs:
                print(f"    type={inp['type']} value=\"{inp['value']}\" placeholder=\"{inp['placeholder']}\" top={inp['top']} testid=\"{inp['testid']}\"")

            # Dismiss
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        else:
            print("  Date button not found")

        # ─── TEST 2: Kebab menu with proper wait ───
        print("\n\n=== TEST 2: KEBAB MENU ===")
        kebab = page.locator('[data-testid="invoices-kebab-button"]').first
        if await kebab.count() > 0:
            await kebab.click()
            await page.wait_for_timeout(3000)  # More time for React to render

            # Check for MUI Menu portal (rendered at body end)
            menu_items = await page.evaluate("""
                () => {
                    const results = [];
                    const items = document.querySelectorAll(
                        '[role="menuitem"], [class*="MuiMenuItem"], [role="menu"] li, ' +
                        '.MuiList-root li, [class*="cf-menu-item"]'
                    );
                    for (const item of items) {
                        const rect = item.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.push({
                                text: (item.innerText || '').trim(),
                                cls: (item.className || '').substring(0, 80),
                                testid: item.getAttribute('data-testid') || '',
                            });
                        }
                    }
                    // Also check for any popover/presentation layer
                    const popovers = document.querySelectorAll('[role="presentation"], [class*="MuiPopover"]');
                    for (const p of popovers) {
                        const rect = p.getBoundingClientRect();
                        const text = (p.innerText || '').trim();
                        if (rect.width > 0 && rect.height > 0 && text) {
                            results.push({text: 'POPOVER: ' + text.substring(0, 200), cls: '', testid: ''});
                        }
                    }
                    return results;
                }
            """)
            print(f"  Menu items found: {len(menu_items)}")
            for item in menu_items:
                print(f"    \"{item['text']}\" testid=\"{item['testid']}\"")

            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        else:
            print("  No kebab button found")

        # ─── TEST 3: Select one invoice -> Export to CSV -> Select All fields -> Download ───
        print("\n\n=== TEST 3: FULL CSV EXPORT FLOW ===")

        # Step 1: Click first invoice checkbox
        print("  Step 1: Click first invoice checkbox...")
        first_cb = page.locator('[data-testid="invoices-table-checkbox"]').first
        await first_cb.click()
        await page.wait_for_timeout(1000)
        print("  Checked first invoice")

        # Step 2: Click "Export to CSV"
        print("  Step 2: Click Export to CSV...")
        export_btn = page.locator('[data-testid="invoices-export-invoices-button"]')
        await export_btn.click()
        await page.wait_for_timeout(2000)
        print("  Opened export modal")

        # Step 3: Click "Select all" for fields
        print("  Step 3: Click Select All for fields...")
        select_all_btn = page.locator('[role="dialog"] button:has-text("Select all"), [role="presentation"] button:has-text("Select all")').first
        if await select_all_btn.count() > 0:
            await select_all_btn.click()
            await page.wait_for_timeout(1500)
            print("  Clicked Select All")

            # Check if Download is now enabled
            download_btn = page.locator('[role="dialog"] button:has-text("Download"), [role="presentation"] button:has-text("Download")').first
            is_disabled = await download_btn.evaluate("el => el.disabled")
            print(f"  Download button disabled: {is_disabled}")

            # Step 4: Download
            if not is_disabled:
                print("  Step 4: Downloading CSV...")
                try:
                    async with page.expect_download(timeout=30000) as dl_info:
                        await download_btn.click()
                    download = await dl_info.value
                    dest = Path(f"./debug_pfg_export.csv")
                    await download.save_as(str(dest))
                    print(f"  Downloaded: {dest} ({dest.stat().st_size} bytes)")

                    with open(dest, 'r') as f:
                        content = f.read()
                    print(f"\n  === CSV CONTENT (first 3000 chars) ===")
                    print(content[:3000])

                    reader = csv.reader(io.StringIO(content))
                    rows = list(reader)
                    if rows:
                        print(f"\n  === CSV HEADERS ({len(rows[0])} columns) ===")
                        for i, col in enumerate(rows[0]):
                            print(f"    [{i}] {col}")
                        print(f"\n  === FIRST 3 DATA ROWS ===")
                        for row in rows[1:4]:
                            print(f"  {row[:10]}...")  # First 10 columns
                        print(f"\n  Total rows: {len(rows)} (including header)")
                except Exception as e:
                    print(f"  Download error: {e}")
            else:
                print("  Download still disabled after Select All — dumping modal state")
                # Dump the Selected section to see what happened
                selected = await page.evaluate("""
                    () => {
                        const text = document.querySelector('[role="dialog"], [role="presentation"]')?.innerText || '';
                        return text.substring(0, 2000);
                    }
                """)
                print(f"  Modal text: {selected[:1000]}")
        else:
            print("  Select All button not found in modal")

        await context.close()

asyncio.run(main())
