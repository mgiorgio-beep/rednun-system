#!/usr/bin/env python3
"""Debug: Find PDF icons in Martignetti invoice table."""
import asyncio, json, os
from pathlib import Path
from playwright.async_api import async_playwright

PORTAL_URL = "https://martignettiexchange.com/profile/login?backurl=/profile/invoices/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip()

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 900}, user_agent=UA)
        page = await context.new_page()

        # Login
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.fill('input[type="email"]', os.environ.get("MARTIGNETTI_USER", ""))
        await page.fill('input[type="password"]', os.environ.get("MARTIGNETTI_PASS", ""))
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(8000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass

        print(f"URL: {page.url}")
        print(f"Title: {await page.title()}")

        # Dump first 3 invoice rows in detail
        rows_info = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('table tbody tr, table tr');
                const results = [];
                for (let i = 0; i < Math.min(rows.length, 5); i++) {
                    const row = rows[i];
                    const cells = row.querySelectorAll('td, th');
                    const cellData = [];
                    for (let j = 0; j < cells.length; j++) {
                        const cell = cells[j];
                        const links = cell.querySelectorAll('a');
                        const imgs = cell.querySelectorAll('img, svg, i, span[class*="icon"]');
                        const buttons = cell.querySelectorAll('button');

                        cellData.push({
                            index: j,
                            text: cell.innerText?.trim()?.substring(0, 60) || '',
                            html: cell.innerHTML?.substring(0, 300) || '',
                            links: Array.from(links).map(a => ({
                                text: a.innerText?.trim()?.substring(0, 40),
                                href: (a.href || '').substring(0, 100),
                                class: a.className?.substring(0, 60),
                            })),
                            images: Array.from(imgs).map(img => ({
                                tag: img.tagName,
                                src: (img.src || '').substring(0, 100),
                                class: (typeof img.className === 'string' ? img.className : img.className?.baseVal || '').substring(0, 60),
                                alt: img.alt || '',
                            })),
                            buttons: Array.from(buttons).map(b => ({
                                text: b.innerText?.trim()?.substring(0, 30),
                                class: b.className?.substring(0, 60),
                            })),
                        });
                    }
                    results.push({
                        rowIndex: i,
                        rowText: row.innerText?.trim()?.substring(0, 200),
                        cellCount: cells.length,
                        cells: cellData,
                    });
                }
                return results;
            }
        """)

        for row in rows_info:
            print(f"\n{'='*60}")
            print(f"ROW {row['rowIndex']} ({row['cellCount']} cells): {row['rowText'][:100]}")
            for cell in row['cells']:
                print(f"  Cell[{cell['index']}]: text=\"{cell['text']}\"")
                if cell['links']:
                    for l in cell['links']:
                        print(f"    <a> text=\"{l['text']}\" href={l['href']} class={l['class']}")
                if cell['images']:
                    for img in cell['images']:
                        print(f"    <{img['tag']}> src={img['src']} class={img['class']} alt={img['alt']}")
                if cell['buttons']:
                    for b in cell['buttons']:
                        print(f"    <button> text=\"{b['text']}\" class={b['class']}")
                if not cell['links'] and not cell['images'] and not cell['buttons'] and 'pdf' in cell['html'].lower():
                    print(f"    HTML (contains pdf): {cell['html'][:200]}")

        await browser.close()

asyncio.run(main())
