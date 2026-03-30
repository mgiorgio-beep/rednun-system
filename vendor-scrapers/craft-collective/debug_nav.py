#!/usr/bin/env python3
"""Quick debug: dump Craft Collective nav/dropdown items after account selection."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

ACCOUNT_SELECT_KEYWORDS = ["craft collective", "dennis"]
ACCOUNT_SKIP_KEYWORDS = ["chatham", "atlantic"]

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
                print(f"Injected {len(cookies)} cookies")

        await page.goto("https://www.termsync.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        print(f"\nURL: {page.url}")
        print(f"Title: {await page.title()}")

        # Handle account picker
        print("\n=== ACCOUNT PICKER ===")
        selected = await page.evaluate("""
            (config) => {
                const selectKw = config.selectKeywords;
                const skipKw = config.skipKeywords;
                const candidates = document.querySelectorAll(
                    'a, button, [role="button"], [role="option"], [role="listitem"], li, tr, .card, [class*="account"], [class*="company"], [class*="select"], [class*="choose"], label, div[onclick]'
                );
                let bestMatch = null; let bestScore = -1;
                for (const el of candidates) {
                    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (text.length < 5 || text.length > 200) continue;
                    if (skipKw.some(kw => text.includes(kw))) continue;
                    const matchCount = selectKw.filter(kw => text.includes(kw)).length;
                    if (matchCount > bestScore) { bestScore = matchCount; bestMatch = el; }
                }
                if (bestMatch && bestScore >= 1) {
                    const text = (bestMatch.innerText || bestMatch.textContent || '').trim();
                    bestMatch.click();
                    return { found: true, text: text.substring(0, 150), score: bestScore };
                }
                return { found: false, score: bestScore };
            }
        """, {"selectKeywords": ACCOUNT_SELECT_KEYWORDS, "skipKeywords": ACCOUNT_SKIP_KEYWORDS})
        print(f"  Selected: {selected}")

        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        print(f"\nURL after account: {page.url}")
        print(f"Title: {await page.title()}")

        # Dump all nav items
        print("\n=== ALL NAV/MENU ITEMS ===")
        nav = await page.evaluate("""
            () => {
                const results = [];
                const els = document.querySelectorAll('nav a, nav li, nav button, header a, header li, [class*="nav"] a, [class*="nav"] li, [class*="menu"] a, [class*="menu"] li, a[href], .navbar a, .navbar li, .nav a, .nav li, ul.nav a, ul.nav li');
                for (const el of els) {
                    const text = (el.innerText || '').trim();
                    if (!text || text.length > 200) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    results.push({
                        tag: el.tagName, text: text.substring(0, 120).replace(/\\n/g, ' | '),
                        cls: (el.className || '').substring(0, 80),
                        href: (el.href || '').substring(0, 120),
                        top: Math.round(rect.top), left: Math.round(rect.left),
                    });
                }
                return results;
            }
        """)
        seen = set()
        for item in nav:
            key = f"{item['tag']}:{item['text'][:60]}"
            if key in seen: continue
            seen.add(key)
            print(f"  <{item['tag']} class=\"{item['cls']}\"> top={item['top']} left={item['left']}")
            print(f"    text: \"{item['text']}\"")
            if item['href']:
                print(f"    href: {item['href']}")

        # Try hovering "Invoices" and dump what appears
        print("\n=== HOVERING 'INVOICES' NAV ITEM ===")
        hovered = await page.evaluate("""
            () => {
                const navItems = document.querySelectorAll('nav a, nav li, header a, header li, [class*="nav"] a, [class*="nav"] li, [class*="menu"] a, [class*="menu"] li, a[href*="invoice"], li[class*="dropdown"]');
                for (const item of navItems) {
                    const text = item.innerText?.trim() || '';
                    if (text === 'Invoices' || text === 'INVOICES' || text.toLowerCase() === 'invoices') {
                        item.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                        item.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                        return {found: true, tag: item.tagName, text: text, cls: (item.className || '').substring(0, 80)};
                    }
                }
                return {found: false};
            }
        """)
        print(f"  Hover result: {hovered}")
        await page.wait_for_timeout(2000)

        # Now dump dropdowns/submenus that appeared
        print("\n=== DROPDOWN/SUBMENU CONTENTS AFTER HOVER ===")
        dropdowns = await page.evaluate("""
            () => {
                const results = [];
                // Check all potential dropdown containers
                const containers = document.querySelectorAll(
                    '[class*="dropdown"], [class*="submenu"], [class*="menu"], [role="menu"], ul.dropdown-menu, ul[class*="sub"], div[class*="drop"], .open ul, .show ul, li.open ul, li.show ul'
                );
                for (const container of containers) {
                    const rect = container.getBoundingClientRect();
                    const style = getComputedStyle(container);
                    const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    const items = container.querySelectorAll('a, button, li, [role="menuitem"]');
                    for (const item of items) {
                        const text = (item.innerText || '').trim();
                        if (!text || text.length > 200) continue;
                        results.push({
                            containerClass: (container.className || '').substring(0, 80),
                            containerVisible: visible,
                            containerDisplay: style.display,
                            tag: item.tagName,
                            text: text.substring(0, 150).replace(/\\n/g, ' | '),
                            href: (item.href || '').substring(0, 120),
                            cls: (item.className || '').substring(0, 60),
                        });
                    }
                }
                return results;
            }
        """)
        seen2 = set()
        for item in dropdowns:
            key = f"{item['text'][:60]}"
            if key in seen2: continue
            seen2.add(key)
            vis = "VIS" if item['containerVisible'] else f"hid({item['containerDisplay']})"
            print(f"  [{vis}] container=\"{item['containerClass']}\"")
            print(f"    <{item['tag']} class=\"{item['cls']}\"> \"{item['text']}\"")
            if item['href']:
                print(f"    href: {item['href']}")

        # Also try clicking Invoices (not just hovering)
        print("\n=== CLICKING 'INVOICES' ===")
        await page.evaluate("""
            () => {
                const navItems = document.querySelectorAll('nav a, nav li, header a, header li, [class*="nav"] a, [class*="nav"] li, [class*="menu"] a, [class*="menu"] li');
                for (const item of navItems) {
                    const text = item.innerText?.trim() || '';
                    if (text === 'Invoices' || text === 'INVOICES' || text.toLowerCase() === 'invoices') {
                        item.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        await page.wait_for_timeout(2000)

        # Dump after click
        print("\n=== AFTER CLICK — DROPDOWN/SUBMENU ===")
        dropdowns2 = await page.evaluate("""
            () => {
                const results = [];
                const containers = document.querySelectorAll(
                    '[class*="dropdown"], [class*="submenu"], [class*="menu"], [role="menu"], ul.dropdown-menu, ul[class*="sub"], div[class*="drop"], .open ul, .show ul, li.open ul, li.show ul'
                );
                for (const container of containers) {
                    const rect = container.getBoundingClientRect();
                    const style = getComputedStyle(container);
                    const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    const items = container.querySelectorAll('a, button, li, [role="menuitem"]');
                    for (const item of items) {
                        const text = (item.innerText || '').trim();
                        if (!text || text.length > 200) continue;
                        const iRect = item.getBoundingClientRect();
                        results.push({
                            containerClass: (container.className || '').substring(0, 80),
                            containerVisible: visible,
                            tag: item.tagName,
                            text: text.substring(0, 150).replace(/\\n/g, ' | '),
                            href: (item.href || '').substring(0, 120),
                            itemVisible: iRect.width > 0 && iRect.height > 0,
                        });
                    }
                }
                // Also check the full page for anything with 'listing' or 'invoice' text
                const all = document.querySelectorAll('a, button, li');
                for (const el of all) {
                    const text = (el.innerText || '').trim().toLowerCase();
                    if (text.includes('listing') || text.includes('invoice') || text.includes('credit')) {
                        const rect = el.getBoundingClientRect();
                        results.push({
                            containerClass: 'FULL-PAGE-SCAN',
                            containerVisible: true,
                            tag: el.tagName,
                            text: (el.innerText || '').trim().substring(0, 150).replace(/\\n/g, ' | '),
                            href: (el.href || '').substring(0, 120),
                            itemVisible: rect.width > 0 && rect.height > 0,
                        });
                    }
                }
                return results;
            }
        """)
        seen3 = set()
        for item in dropdowns2:
            key = f"{item['containerClass']}:{item['text'][:60]}"
            if key in seen3: continue
            seen3.add(key)
            vis = "VIS" if item.get('itemVisible', item['containerVisible']) else "hid"
            print(f"  [{vis}] container=\"{item['containerClass']}\"")
            print(f"    <{item['tag']}> \"{item['text']}\"")
            if item.get('href'):
                print(f"    href: {item['href']}")

        # Dump current URL
        print(f"\nFinal URL: {page.url}")

        await context.close()

asyncio.run(main())
