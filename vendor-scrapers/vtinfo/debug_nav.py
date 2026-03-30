#!/usr/bin/env python3
"""Quick debug: dump VTInfo vendor list after login."""
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

        await page.goto("https://apps.vtinfo.com/retailer-portal/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        print(f"\nURL: {page.url}")
        print(f"Title: {await page.title()}")

        # Dump full page text (first 2000 chars)
        print("\n=== PAGE TEXT (first 2000 chars) ===")
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 2000) || ''")
        print(body)

        # Dump all clickable elements
        print("\n=== ALL CLICKABLE ELEMENTS ===")
        clickables = await page.evaluate("""
            () => {
                const results = [];
                const els = document.querySelectorAll('a, button, [role="button"], [role="option"], li, .card, [class*="vendor"], [class*="item"], [class*="company"]');
                for (const el of els) {
                    const text = (el.innerText || '').trim();
                    if (!text || text.length > 200) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 150).replace(/\\n/g, ' | '),
                        cls: (el.className || '').substring(0, 80),
                        href: (el.href || '').substring(0, 100),
                        top: Math.round(rect.top),
                    });
                }
                return results;
            }
        """)
        for item in clickables[:40]:
            print(f"  <{item['tag']} class=\"{item['cls']}\"> top={item['top']}")
            print(f"    text: \"{item['text']}\"")
            if item['href']:
                print(f"    href: {item['href']}")

        # Specifically look for vendor-related text
        print("\n=== VENDOR-RELATED TEXT ('knife', 'colonial', 'beverage', 'wine', 'liquor') ===")
        vendors = await page.evaluate("""
            () => {
                const results = [];
                const keywords = ['knife', 'colonial', 'beverage', 'wine', 'liquor', 'vendor', 'supplier', 'distributor'];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const text = (el.innerText || '').trim();
                    if (text.length < 3 || text.length > 200) continue;
                    const lower = text.toLowerCase();
                    if (keywords.some(kw => lower.includes(kw)) && el.children.length < 3) {
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
        seen = set()
        for v in vendors[:20]:
            key = v['text'][:80]
            if key in seen: continue
            seen.add(key)
            vis = "VIS" if v['visible'] else "hid"
            print(f"  [{vis}] <{v['tag']}> \"{v['text']}\"")

        await context.close()

asyncio.run(main())
