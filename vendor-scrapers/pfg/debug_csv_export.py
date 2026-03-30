#!/usr/bin/env python3
"""PFG discovery: Dump date filter UI, export modal, and CSV format."""
import asyncio, json, csv, io
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

        # Navigate to Invoices via Playwright click (React/MUI)
        print("=== Navigating to Invoices ===")
        await page.locator('button:has-text("Invoices")').click()
        await page.wait_for_timeout(1500)
        menu_item = page.locator('.cf-menu-item:has-text("Invoices")').first
        if await menu_item.count() > 0:
            await menu_item.click()
        await page.wait_for_timeout(5000)
        print(f"URL: {page.url}")

        # ─── PHASE 1: Dump date filter UI ───
        print("\n\n=== PHASE 1: DATE FILTER UI ===")
        date_elements = await page.evaluate("""
            () => {
                const results = [];
                // Look for date-related inputs, buttons, selectors
                const selectors = [
                    'input[type="date"]', 'input[type="text"][placeholder*="date" i]',
                    '[class*="DatePicker"]', '[class*="datepicker"]', '[class*="date-picker"]',
                    '[class*="DateRange"]', '[class*="date-range"]', '[class*="daterange"]',
                    'button[class*="date" i]', '[class*="MuiDatePicker"]',
                    '[data-testid*="date" i]', '[aria-label*="date" i]',
                ];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        const rect = el.getBoundingClientRect();
                        results.push({
                            selector: sel,
                            tag: el.tagName,
                            cls: (el.className || '').substring(0, 120),
                            text: (el.innerText || el.value || '').substring(0, 100),
                            placeholder: el.placeholder || '',
                            type: el.type || '',
                            testid: el.getAttribute('data-testid') || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            visible: rect.width > 0 && rect.height > 0,
                            top: Math.round(rect.top),
                        });
                    }
                }
                return results;
            }
        """)
        if date_elements:
            for el in date_elements:
                vis = "VIS" if el['visible'] else "hid"
                print(f"  [{vis}] <{el['tag']}> sel={el['selector']} cls=\"{el['cls'][:80]}\"")
                print(f"    text=\"{el['text']}\" placeholder=\"{el['placeholder']}\" testid=\"{el['testid']}\"")
        else:
            print("  No date-specific elements found, scanning broader UI...")

        # Look for any buttons/dropdowns that might contain date range text
        date_buttons = await page.evaluate("""
            () => {
                const results = [];
                const buttons = document.querySelectorAll('button, [role="button"], [class*="MuiSelect"], [class*="MuiChip"]');
                for (const btn of buttons) {
                    const text = (btn.innerText || '').trim();
                    // Look for date-like text: "Last 30 days", "03/01/2026", date ranges
                    if (text.match(/\\d{1,2}\\/\\d{1,2}|last\\s+\\d+|days?|month|week|year|range|filter|period/i) ||
                        text.match(/\\d{4}/)) {
                        const rect = btn.getBoundingClientRect();
                        results.push({
                            tag: btn.tagName,
                            cls: (btn.className || '').substring(0, 120),
                            text: text.substring(0, 150),
                            testid: btn.getAttribute('data-testid') || '',
                            visible: rect.width > 0 && rect.height > 0,
                            top: Math.round(rect.top),
                            left: Math.round(rect.left),
                        });
                    }
                }
                return results;
            }
        """)
        print(f"\n  Date-related buttons/controls: {len(date_buttons)}")
        for btn in date_buttons:
            vis = "VIS" if btn['visible'] else "hid"
            print(f"  [{vis}] <{btn['tag']}> top={btn['top']} left={btn['left']} testid=\"{btn['testid']}\"")
            print(f"    cls=\"{btn['cls'][:80]}\"")
            print(f"    text=\"{btn['text']}\"")

        # Also dump ALL visible buttons on the page for context
        print("\n\n=== ALL VISIBLE BUTTONS ===")
        all_buttons = await page.evaluate("""
            () => {
                const results = [];
                const btns = document.querySelectorAll('button, [role="button"], a.MuiButton-root, [class*="MuiIconButton"]');
                for (const btn of btns) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const text = (btn.innerText || '').trim().substring(0, 80);
                        results.push({
                            tag: btn.tagName,
                            text: text || '(icon/empty)',
                            cls: (btn.className || '').substring(0, 100),
                            testid: btn.getAttribute('data-testid') || '',
                            ariaLabel: btn.getAttribute('aria-label') || '',
                            top: Math.round(rect.top),
                            left: Math.round(rect.left),
                        });
                    }
                }
                return results;
            }
        """)
        for btn in all_buttons:
            label = btn['ariaLabel'] or btn['testid'] or ''
            extra = f" aria=\"{label}\"" if label else ""
            extra += f" testid=\"{btn['testid']}\"" if btn['testid'] and btn['testid'] != label else ""
            print(f"  <{btn['tag']}> top={btn['top']} left={btn['left']}{extra}")
            print(f"    text=\"{btn['text']}\"")

        # ─── PHASE 2: Dump full page text for context ───
        print("\n\n=== FULL PAGE TEXT (first 2000 chars) ===")
        body = await page.evaluate("() => document.body.innerText.substring(0, 2000)")
        print(body)

        # ─── PHASE 3: Look for three-dot menus per invoice row ───
        print("\n\n=== THREE-DOT / MORE MENUS ===")
        more_menus = await page.evaluate("""
            () => {
                const results = [];
                // MUI MoreVert icon buttons
                const icons = document.querySelectorAll(
                    '[class*="MuiIconButton"], [aria-label*="more" i], [aria-label*="menu" i], ' +
                    '[data-testid*="more" i], [data-testid*="menu" i], [data-testid*="action" i]'
                );
                for (const icon of icons) {
                    const rect = icon.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        results.push({
                            tag: icon.tagName,
                            cls: (icon.className || '').substring(0, 100),
                            ariaLabel: icon.getAttribute('aria-label') || '',
                            testid: icon.getAttribute('data-testid') || '',
                            top: Math.round(rect.top),
                            left: Math.round(rect.left),
                            innerHTML: (icon.innerHTML || '').substring(0, 200),
                        });
                    }
                }
                return results;
            }
        """)
        print(f"  Found {len(more_menus)} icon buttons")
        for m in more_menus:
            print(f"  <{m['tag']}> top={m['top']} left={m['left']} aria=\"{m['ariaLabel']}\" testid=\"{m['testid']}\"")
            print(f"    cls=\"{m['cls'][:80]}\"")
            if 'svg' in m['innerHTML'].lower() or 'MoreVert' in m['innerHTML']:
                print(f"    (contains SVG icon)")

        # ─── PHASE 4: Click first three-dot menu to see dropdown options ───
        print("\n\n=== CLICK FIRST THREE-DOT MENU ===")
        if more_menus:
            # Click the first icon button that's in the invoice list area (top > 150)
            for m in more_menus:
                if m['top'] > 150:
                    try:
                        icon_btn = page.locator(f'[data-testid="{m["testid"]}"]').first if m['testid'] else None
                        if not icon_btn or await icon_btn.count() == 0:
                            # Try by position
                            icon_btn = page.locator('[class*="MuiIconButton"]').first
                            # Find the one at the right position
                            all_icons = page.locator('[class*="MuiIconButton"]')
                            for i in range(await all_icons.count()):
                                box = await all_icons.nth(i).bounding_box()
                                if box and abs(box['y'] - m['top']) < 10:
                                    icon_btn = all_icons.nth(i)
                                    break
                        await icon_btn.click()
                        await page.wait_for_timeout(1500)

                        # Dump popup menu
                        popup = await page.evaluate("""
                            () => {
                                const results = [];
                                const items = document.querySelectorAll(
                                    '[role="menuitem"], [role="menu"] li, [class*="MuiMenuItem"], ' +
                                    '[class*="MuiPopover"] li, [class*="MuiMenu"] li'
                                );
                                for (const item of items) {
                                    const rect = item.getBoundingClientRect();
                                    if (rect.width > 0 && rect.height > 0) {
                                        results.push({
                                            text: (item.innerText || '').trim().substring(0, 100),
                                            cls: (item.className || '').substring(0, 80),
                                            testid: item.getAttribute('data-testid') || '',
                                        });
                                    }
                                }
                                return results;
                            }
                        """)
                        print(f"  Popup menu items: {len(popup)}")
                        for item in popup:
                            print(f"    \"{item['text']}\" testid=\"{item['testid']}\"")
                        break
                    except Exception as e:
                        print(f"  Error clicking menu: {e}")
                        break
        else:
            print("  No three-dot menus found")

        # Dismiss any popup
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

        # ─── PHASE 5: Look for Export/CSV button ───
        print("\n\n=== EXPORT / CSV BUTTONS ===")
        export_btns = await page.evaluate("""
            () => {
                const results = [];
                const btns = document.querySelectorAll('button, [role="button"], a');
                for (const btn of btns) {
                    const text = (btn.innerText || '').trim().toLowerCase();
                    if (text.includes('export') || text.includes('csv') || text.includes('download')) {
                        const rect = btn.getBoundingClientRect();
                        results.push({
                            tag: btn.tagName,
                            text: (btn.innerText || '').trim().substring(0, 100),
                            cls: (btn.className || '').substring(0, 100),
                            testid: btn.getAttribute('data-testid') || '',
                            visible: rect.width > 0 && rect.height > 0,
                            disabled: btn.disabled || btn.classList.contains('Mui-disabled'),
                            top: Math.round(rect.top),
                        });
                    }
                }
                return results;
            }
        """)
        print(f"  Export-related buttons: {len(export_btns)}")
        for btn in export_btns:
            vis = "VIS" if btn['visible'] else "hid"
            dis = " DISABLED" if btn['disabled'] else ""
            print(f"  [{vis}{dis}] <{btn['tag']}> top={btn['top']} testid=\"{btn['testid']}\"")
            print(f"    text=\"{btn['text']}\"")

        # ─── PHASE 6: Select one invoice checkbox and check if Export enables ───
        print("\n\n=== SELECT ONE INVOICE + CHECK EXPORT ===")
        checkboxes = await page.evaluate("""
            () => {
                const results = [];
                const cbs = document.querySelectorAll(
                    'input[type="checkbox"], [class*="MuiCheckbox"], [role="checkbox"]'
                );
                for (const cb of cbs) {
                    const rect = cb.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const label = cb.closest('label') || cb.parentElement;
                        const row = cb.closest('tr, [class*="row" i], [class*="Row"]');
                        results.push({
                            tag: cb.tagName,
                            cls: (cb.className || '').substring(0, 100),
                            testid: cb.getAttribute('data-testid') || '',
                            ariaLabel: cb.getAttribute('aria-label') || '',
                            checked: cb.checked || cb.getAttribute('aria-checked') === 'true',
                            top: Math.round(rect.top),
                            left: Math.round(rect.left),
                            labelText: (label?.innerText || '').substring(0, 50),
                            rowText: (row?.innerText || '').substring(0, 100),
                        });
                    }
                }
                return results;
            }
        """)
        print(f"  Found {len(checkboxes)} visible checkboxes")
        for cb in checkboxes:
            chk = "CHECKED" if cb['checked'] else "unchecked"
            print(f"  [{chk}] top={cb['top']} left={cb['left']} testid=\"{cb['testid']}\" aria=\"{cb['ariaLabel']}\"")
            if cb['rowText']:
                print(f"    row: \"{cb['rowText'][:80]}\"")

        # Click the second checkbox (skip header "select all" if first)
        if len(checkboxes) >= 2:
            target_cb = checkboxes[1]  # Skip first (likely select-all header)
            print(f"\n  Clicking checkbox at top={target_cb['top']}...")
            try:
                if target_cb['testid']:
                    await page.locator(f'[data-testid="{target_cb["testid"]}"]').click()
                else:
                    # Click by position
                    all_cbs = page.locator('[class*="MuiCheckbox"], input[type="checkbox"]')
                    for i in range(await all_cbs.count()):
                        box = await all_cbs.nth(i).bounding_box()
                        if box and abs(box['y'] - target_cb['top']) < 10:
                            await all_cbs.nth(i).click()
                            break
                await page.wait_for_timeout(1500)
                print("  Clicked checkbox")

                # Re-check export buttons
                export_btns2 = await page.evaluate("""
                    () => {
                        const results = [];
                        const btns = document.querySelectorAll('button, [role="button"]');
                        for (const btn of btns) {
                            const text = (btn.innerText || '').trim().toLowerCase();
                            if (text.includes('export') || text.includes('csv') || text.includes('download')) {
                                results.push({
                                    text: (btn.innerText || '').trim(),
                                    disabled: btn.disabled || btn.classList.contains('Mui-disabled'),
                                    testid: btn.getAttribute('data-testid') || '',
                                });
                            }
                        }
                        return results;
                    }
                """)
                print(f"\n  Export buttons after selection:")
                for btn in export_btns2:
                    dis = "DISABLED" if btn['disabled'] else "ENABLED"
                    print(f"    [{dis}] \"{btn['text']}\" testid=\"{btn['testid']}\"")

                # ─── PHASE 7: Click Export to CSV and dump the modal ───
                print("\n\n=== CLICK EXPORT TO CSV ===")
                for btn in export_btns2:
                    if 'csv' in btn['text'].lower() or 'export' in btn['text'].lower():
                        if not btn['disabled']:
                            print(f"  Clicking: \"{btn['text']}\"")
                            await page.locator(f'button:has-text("{btn["text"]}")').first.click()
                            await page.wait_for_timeout(2000)

                            # Dump modal/dialog contents
                            modal = await page.evaluate("""
                                () => {
                                    const results = [];
                                    const dialogs = document.querySelectorAll(
                                        '[role="dialog"], [role="presentation"], [class*="MuiModal"], [class*="MuiDialog"], [class*="MuiDrawer"]'
                                    );
                                    for (const d of dialogs) {
                                        const rect = d.getBoundingClientRect();
                                        const text = (d.innerText || '').trim();
                                        if (rect.width > 0 && rect.height > 0 && text.length > 0) {
                                            // Also find all form controls within
                                            const inputs = [];
                                            for (const inp of d.querySelectorAll('input, [role="checkbox"], [class*="MuiSwitch"], [class*="MuiCheckbox"], button, select')) {
                                                inputs.push({
                                                    tag: inp.tagName,
                                                    type: inp.type || '',
                                                    text: (inp.innerText || inp.value || '').substring(0, 80),
                                                    cls: (inp.className || '').substring(0, 80),
                                                    testid: inp.getAttribute('data-testid') || '',
                                                    checked: inp.checked || inp.getAttribute('aria-checked') === 'true',
                                                    disabled: inp.disabled,
                                                });
                                            }
                                            results.push({
                                                tag: d.tagName,
                                                text: text.substring(0, 1500),
                                                childCount: d.children.length,
                                                inputs: inputs,
                                            });
                                        }
                                    }
                                    return results;
                                }
                            """)
                            print(f"  Modal/dialog elements: {len(modal)}")
                            for m in modal:
                                print(f"\n  <{m['tag']}> children={m['childCount']}")
                                print(f"  TEXT:\n{m['text'][:1500]}")
                                print(f"\n  FORM CONTROLS ({len(m['inputs'])}):")
                                for inp in m['inputs']:
                                    chk = " CHECKED" if inp['checked'] else ""
                                    dis = " DISABLED" if inp['disabled'] else ""
                                    print(f"    <{inp['tag']} type=\"{inp['type']}\">{chk}{dis} text=\"{inp['text'][:60]}\" testid=\"{inp['testid']}\"")
                            break
                        else:
                            print(f"  \"{btn['text']}\" is DISABLED even after selection")

            except Exception as e:
                print(f"  Error: {e}")

        # ─── PHASE 8: Try to download the CSV ───
        print("\n\n=== ATTEMPT CSV DOWNLOAD ===")
        try:
            download_btn = page.locator('[role="dialog"] button:has-text("Download"), [role="presentation"] button:has-text("Download")').first
            if await download_btn.count() > 0:
                async with page.expect_download(timeout=30000) as dl_info:
                    await download_btn.click()
                download = await dl_info.value
                dest = Path(f"./debug_export_{download.suggested_filename}")
                await download.save_as(str(dest))
                print(f"  Downloaded: {dest}")
                print(f"  Size: {dest.stat().st_size} bytes")

                # Read and print CSV headers + first rows
                with open(dest, 'r') as f:
                    content = f.read()
                print(f"\n  === CSV CONTENT (first 3000 chars) ===")
                print(content[:3000])

                # Parse and show structure
                reader = csv.reader(io.StringIO(content))
                rows = list(reader)
                if rows:
                    print(f"\n  === CSV HEADERS ===")
                    print(f"  {rows[0]}")
                    print(f"\n  === FIRST 5 DATA ROWS ===")
                    for row in rows[1:6]:
                        print(f"  {row}")
                    print(f"\n  Total rows: {len(rows)} (including header)")
            else:
                print("  No Download button found in dialog")
        except Exception as e:
            print(f"  Download error: {e}")

        await context.close()

asyncio.run(main())
