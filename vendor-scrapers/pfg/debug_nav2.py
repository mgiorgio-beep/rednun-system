#!/usr/bin/env python3
"""PFG debug #2: MUI-specific. Click Invoices via Playwright API, dump MUI elements."""
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
            cookies = state.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)

        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        print(f"URL: {page.url}")
        print(f"Title: {await page.title()}")

        # Snapshot body text hash before clicking
        body_before = await page.evaluate("() => document.body.innerText.length")
        print(f"Body length before: {body_before}")

        # ─── Test 1: Click "Invoices" MUI button via Playwright API ───
        print("\n=== CLICKING 'Invoices' BUTTON VIA PLAYWRIGHT ===")
        invoices_btn = await page.query_selector('button:has-text("Invoices")')
        if invoices_btn:
            print(f"  Found button: {await invoices_btn.inner_text()}")
            await invoices_btn.click()
            await page.wait_for_timeout(3000)

            body_after = await page.evaluate("() => document.body.innerText.length")
            print(f"  Body length after click: {body_after} (delta: {body_after - body_before})")
            print(f"  URL after click: {page.url}")
            print(f"  Title after click: {await page.title()}")

            # Dump all MUI-specific elements that might be a menu/popover
            print("\n=== MUI MENU / POPOVER ELEMENTS ===")
            mui = await page.evaluate("""
                () => {
                    const results = [];
                    // MUI renders menus in portals at body end
                    const portals = document.querySelectorAll(
                        '[role="presentation"], [role="menu"], [role="listbox"], ' +
                        '[class*="MuiPopover"], [class*="MuiMenu"], [class*="MuiPaper"], ' +
                        '[class*="MuiModal"], [class*="MuiDrawer"], [class*="MuiDialog"]'
                    );
                    for (const el of portals) {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const text = (el.innerText || '').trim();
                        results.push({
                            tag: el.tagName,
                            role: el.getAttribute('role') || '',
                            cls: (el.className || '').substring(0, 120),
                            visible: rect.width > 0 && rect.height > 0 && style.display !== 'none',
                            text: text.substring(0, 500).replace(/\\n/g, ' | '),
                            childCount: el.children.length,
                        });
                    }
                    return results;
                }
            """)
            for m in mui:
                vis = "VIS" if m['visible'] else "hid"
                print(f"  [{vis}] <{m['tag']} role=\"{m['role']}\" class=\"{m['cls'][:80]}\"> children={m['childCount']}")
                if m['text']:
                    print(f"    text: \"{m['text'][:300]}\"")

            # Dump all role="menuitem" elements
            print("\n=== MENU ITEMS ===")
            items = await page.evaluate("""
                () => {
                    const results = [];
                    const items = document.querySelectorAll('[role="menuitem"], [class*="MuiMenuItem"]');
                    for (const item of items) {
                        const rect = item.getBoundingClientRect();
                        results.push({
                            tag: item.tagName,
                            text: (item.innerText || '').trim().substring(0, 100),
                            cls: (item.className || '').substring(0, 80),
                            visible: rect.width > 0 && rect.height > 0,
                            href: (item.href || item.querySelector('a')?.href || '').substring(0, 100),
                        });
                    }
                    return results;
                }
            """)
            for item in items:
                vis = "VIS" if item['visible'] else "hid"
                print(f"  [{vis}] <{item['tag']} class=\"{item['cls'][:60]}\"> \"{item['text']}\"")
                if item['href']:
                    print(f"    href: {item['href']}")

            # Check if maybe the page content changed (SPA view update)
            print("\n=== PAGE CONTENT AFTER CLICK ===")
            sample = await page.evaluate("() => document.body.innerText.substring(0, 1500)")
            print(sample[:1500])
        else:
            print("  No 'Invoices' button found!")

        # ─── Test 2: "Select location(s)" button ───
        print("\n\n=== CLICKING 'Select location(s)' BUTTON ===")
        # Navigate back to home first
        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        loc_btn = await page.query_selector('button:has-text("Select location")')
        if loc_btn:
            print(f"  Found button: {await loc_btn.inner_text()}")
            await loc_btn.click()
            await page.wait_for_timeout(3000)

            # Dump what appeared (MUI dialog/modal/popover)
            print("\n=== AFTER 'Select location(s)' CLICK ===")
            elements = await page.evaluate("""
                () => {
                    const results = [];
                    const portals = document.querySelectorAll(
                        '[role="presentation"], [role="dialog"], [role="menu"], [role="listbox"], ' +
                        '[class*="MuiPopover"], [class*="MuiMenu"], [class*="MuiModal"], ' +
                        '[class*="MuiDialog"], [class*="MuiDrawer"], [class*="MuiPaper"]'
                    );
                    for (const el of portals) {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const text = (el.innerText || '').trim();
                        if (text.length > 0) {
                            results.push({
                                tag: el.tagName,
                                role: el.getAttribute('role') || '',
                                cls: (el.className || '').substring(0, 120),
                                visible: rect.width > 0 && rect.height > 0 && style.display !== 'none',
                                text: text.substring(0, 800).replace(/\\n/g, ' | '),
                            });
                        }
                    }
                    return results;
                }
            """)
            for e in elements:
                vis = "VIS" if e['visible'] else "hid"
                print(f"  [{vis}] <{e['tag']} role=\"{e['role']}\"> class=\"{e['cls'][:80]}\"")
                print(f"    text: \"{e['text'][:500]}\"")

            # Also check for checkboxes (location selection might use checkboxes)
            checks = await page.evaluate("""
                () => {
                    const results = [];
                    const cbs = document.querySelectorAll('input[type="checkbox"], [role="checkbox"], [class*="MuiCheckbox"]');
                    for (const cb of cbs) {
                        const label = cb.closest('label') || cb.parentElement;
                        const text = (label?.innerText || '').trim();
                        results.push({
                            text: text.substring(0, 100),
                            checked: cb.checked || cb.getAttribute('aria-checked') === 'true',
                            cls: (cb.className || '').substring(0, 60),
                        });
                    }
                    return results;
                }
            """)
            if checks:
                print("\n=== CHECKBOXES (location selection) ===")
                for c in checks:
                    state = "CHECKED" if c['checked'] else "unchecked"
                    print(f"  [{state}] \"{c['text']}\"")
        else:
            print("  No 'Select location(s)' button found!")

        # ─── Test 3: Click company name button (top-left) ───
        print("\n\n=== CLICKING COMPANY NAME BUTTON ===")
        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        company_btn = await page.query_selector('button:has-text("Red Nun")')
        if company_btn:
            btn_text = await company_btn.inner_text()
            print(f"  Found button: {repr(btn_text[:80])}")
            await company_btn.click()
            await page.wait_for_timeout(3000)

            # Dump all new MUI elements
            elements2 = await page.evaluate("""
                () => {
                    const results = [];
                    const portals = document.querySelectorAll(
                        '[role="presentation"], [role="dialog"], [role="menu"], [role="listbox"], ' +
                        '[class*="MuiPopover"], [class*="MuiMenu"], [class*="MuiModal"], ' +
                        '[class*="MuiDialog"], [class*="MuiDrawer"], [class*="MuiPaper"]'
                    );
                    for (const el of portals) {
                        const text = (el.innerText || '').trim();
                        if (text.length > 0) {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            results.push({
                                tag: el.tagName,
                                role: el.getAttribute('role') || '',
                                cls: (el.className || '').substring(0, 120),
                                visible: rect.width > 0 && rect.height > 0 && style.display !== 'none',
                                text: text.substring(0, 800).replace(/\\n/g, ' | '),
                            });
                        }
                    }
                    return results;
                }
            """)
            for e in elements2:
                vis = "VIS" if e['visible'] else "hid"
                print(f"  [{vis}] <{e['tag']} role=\"{e['role']}\"> class=\"{e['cls'][:80]}\"")
                print(f"    text: \"{e['text'][:500]}\"")

            # Also check for radio buttons or list items with company names
            radios = await page.evaluate("""
                () => {
                    const results = [];
                    const all = document.querySelectorAll('input[type="radio"], [role="radio"], [class*="MuiRadio"]');
                    for (const r of all) {
                        const label = r.closest('label') || r.parentElement;
                        results.push({
                            text: (label?.innerText || '').trim().substring(0, 100),
                            checked: r.checked || r.getAttribute('aria-checked') === 'true',
                        });
                    }
                    return results;
                }
            """)
            if radios:
                print("\n=== RADIO BUTTONS (company selection) ===")
                for r in radios:
                    state = "SELECTED" if r['checked'] else "unselected"
                    print(f"  [{state}] \"{r['text']}\"")
        else:
            print("  No company button found!")

        await context.close()

asyncio.run(main())
