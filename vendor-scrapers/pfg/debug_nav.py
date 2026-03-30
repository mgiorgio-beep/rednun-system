#!/usr/bin/env python3
"""Quick debug: dump PFG company dropdown items and nav items."""
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
                print(f"Injected {len(cookies)} cookies")

        await page.goto("https://www.customerfirstsolutions.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        print(f"\nURL: {page.url}")
        print(f"Title: {await page.title()}")

        # Dump all nav/menu items
        print("\n=== ALL NAV/MENU LINKS ===")
        nav_items = await page.evaluate("""
            () => {
                const results = [];
                const els = document.querySelectorAll('nav a, nav button, header a, header button, [class*="nav"] a, [class*="nav"] button, [class*="menu"] a, [class*="menu"] button, a[href], button');
                for (const el of els) {
                    const text = (el.innerText || '').trim();
                    if (!text || text.length > 200) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 120).replace(/\\n/g, ' | '),
                        href: (el.href || '').substring(0, 100),
                        cls: (el.className || '').substring(0, 80),
                        top: Math.round(rect.top),
                        left: Math.round(rect.left),
                    });
                }
                return results;
            }
        """)
        for item in nav_items[:40]:
            print(f"  <{item['tag']} class=\"{item['cls']}\"> top={item['top']} left={item['left']}")
            print(f"    text: \"{item['text']}\"")
            if item['href']:
                print(f"    href: {item['href']}")

        # Click the company switcher to open dropdown
        print("\n=== CLICKING COMPANY SWITCHER ===")
        clicked = await page.evaluate("""
            () => {
                const candidates = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = el.innerText?.trim() || '';
                    if (text.includes('Red Nun') && el.children.length < 3 && text.length < 100) {
                        const rect = el.getBoundingClientRect();
                        if (rect.top < 100) {
                            candidates.push({el, text, right: rect.right, top: rect.top});
                        }
                    }
                });
                candidates.sort((a, b) => (window.innerWidth - a.right) - (window.innerWidth - b.right));
                if (candidates.length > 0) {
                    candidates[0].el.click();
                    return candidates[0].text;
                }
                const headerBtns = document.querySelectorAll('header button, nav button, [class*="header"] button');
                for (const btn of headerBtns) {
                    if (btn.innerText?.includes('Red Nun')) { btn.click(); return btn.innerText.trim(); }
                }
                return null;
            }
        """)
        print(f"  Clicked: {repr(clicked)}")
        await page.wait_for_timeout(2000)

        # Dump dropdown contents
        print("\n=== DROPDOWN / POPOVER CONTENTS ===")
        dropdown = await page.evaluate("""
            () => {
                const results = [];
                const containers = document.querySelectorAll(
                    '[class*="dropdown"], [class*="popover"], [class*="menu"], [role="listbox"], [role="menu"], ul, [class*="modal"], [class*="overlay"], [class*="panel"]'
                );
                for (const container of containers) {
                    const rect = container.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    const items = container.querySelectorAll('li, a, button, option, [role="option"], div, label, input[type="radio"]');
                    for (const item of items) {
                        const text = (item.innerText || item.textContent || '').trim();
                        if (text.length < 3 || text.length > 300) continue;
                        const iRect = item.getBoundingClientRect();
                        results.push({
                            containerClass: (container.className || '').substring(0, 60),
                            tag: item.tagName,
                            text: text.substring(0, 200).replace(/\\n/g, ' | '),
                            cls: (item.className || '').substring(0, 60),
                            visible: iRect.width > 0 && iRect.height > 0,
                            type: item.type || '',
                        });
                    }
                }
                return results;
            }
        """)
        seen = set()
        for item in dropdown:
            key = item['text'][:80]
            if key in seen:
                continue
            seen.add(key)
            vis = "VISIBLE" if item['visible'] else "hidden"
            print(f"  [{vis}] <{item['tag']} class=\"{item['cls']}\"> in container \"{item['containerClass']}\"")
            print(f"    text: \"{item['text']}\"")

        # Also dump all text containing company-like names
        print("\n=== ALL 'Red Nun' / 'Chatham' / 'Dennis' TEXT ON PAGE ===")
        matches = await page.evaluate("""
            () => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const text = (el.innerText || '').trim();
                    if (text.length < 3 || text.length > 200) continue;
                    const lower = text.toLowerCase();
                    if ((lower.includes('red nun') || lower.includes('chatham') || lower.includes('dennis')) && el.children.length < 3) {
                        const rect = el.getBoundingClientRect();
                        results.push({
                            tag: el.tagName, text: text.substring(0, 200).replace(/\\n/g, ' | '),
                            cls: (el.className || '').substring(0, 60),
                            visible: rect.width > 0 && rect.height > 0,
                        });
                    }
                }
                return results;
            }
        """)
        seen2 = set()
        for m in matches[:30]:
            key = m['text'][:80]
            if key in seen2: continue
            seen2.add(key)
            vis = "VIS" if m['visible'] else "hid"
            print(f"  [{vis}] <{m['tag']}> \"{m['text']}\"")

        await context.close()

asyncio.run(main())
