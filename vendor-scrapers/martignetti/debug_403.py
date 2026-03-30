#!/usr/bin/env python3
"""Debug: Investigate Martignetti 403 — try different approaches."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

PORTAL_URL = "https://martignettiexchange.com/profile/login?backurl=/profile/invoices/"
PORTAL_BASE = "https://martignettiexchange.com"

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip()


async def main():
    async with async_playwright() as pw:
        # Test 1: Default headless browser
        print("=" * 60)
        print("TEST 1: Default headless Chromium")
        print("=" * 60)
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        resp = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  Status: {resp.status if resp else 'none'}")
        print(f"  URL: {page.url}")
        headers = (await resp.all_headers()) if resp else {}
        print(f"  Response headers: {json.dumps(dict(list(headers.items())[:10]), indent=2)}")
        await browser.close()

        # Test 2: With real user-agent
        print(f"\n{'=' * 60}")
        print("TEST 2: Real Chrome user-agent")
        print("=" * 60)
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        resp = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  Status: {resp.status if resp else 'none'}")
        print(f"  URL: {page.url}")
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || 'NO BODY'")
        print(f"  Body: {body[:200]}")
        await browser.close()

        # Test 3: Try the base domain first
        print(f"\n{'=' * 60}")
        print("TEST 3: Base domain (no /profile/login path)")
        print("=" * 60)
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        resp = await page.goto(PORTAL_BASE, wait_until="domcontentloaded", timeout=30000)
        print(f"  Status: {resp.status if resp else 'none'}")
        print(f"  URL: {page.url}")
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || 'NO BODY'")
        print(f"  Body: {body[:300]}")
        await browser.close()

        # Test 4: With existing cookies from persistent profile
        print(f"\n{'=' * 60}")
        print("TEST 4: Persistent browser profile (existing cookies)")
        print("=" * 60)
        profile_dir = Path("./browser_profile")
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Also inject storage_state cookies
        ss_file = Path("./storage_state.json")
        if ss_file.exists():
            data = json.loads(ss_file.read_text())
            cookies = data if isinstance(data, list) else data.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
                print(f"  Injected {len(cookies)} cookies")

        resp = await page.goto(PORTAL_BASE, wait_until="domcontentloaded", timeout=30000)
        print(f"  Base domain status: {resp.status if resp else 'none'}")
        print(f"  URL: {page.url}")
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || 'NO BODY'")
        print(f"  Body: {body[:300]}")

        # Try login page with profile
        print(f"\n  Navigating to login page...")
        resp2 = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  Login page status: {resp2.status if resp2 else 'none'}")
        print(f"  URL: {page.url}")
        body2 = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || 'NO BODY'")
        print(f"  Body: {body2[:300]}")
        await context.close()

        # Test 5: Headed mode (non-headless) — some sites detect headless
        print(f"\n{'=' * 60}")
        print("TEST 5: Non-headless mode with user-agent")
        print("=" * 60)
        browser = await pw.chromium.launch(
            headless=False,
            args=["--window-position=-10000,-10000"],  # off-screen
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        resp = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  Status: {resp.status if resp else 'none'}")
        print(f"  URL: {page.url}")
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || 'NO BODY'")
        print(f"  Body: {body[:300]}")

        # If we get the login page, dump forms
        forms = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll('input');
                return Array.from(inputs).map(i => ({
                    type: i.type, name: i.name, id: i.id,
                    placeholder: i.placeholder,
                    visible: i.getBoundingClientRect().width > 0,
                }));
            }
        """)
        if forms:
            print(f"  INPUTS ({len(forms)}):")
            for i in forms:
                vis = "V" if i['visible'] else "H"
                print(f"    [{vis}] type={i['type']} name={i['name']} id={i['id']} placeholder={i['placeholder']}")

        await page.screenshot(path="/tmp/mart_test5.png", full_page=True)
        await browser.close()

        # Test 6: curl-style request to see headers
        print(f"\n{'=' * 60}")
        print("TEST 6: Raw HTTP via requests library")
        print("=" * 60)
        import requests as req
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            r = req.get(PORTAL_BASE, headers=headers, timeout=15, allow_redirects=True)
            print(f"  Base: status={r.status_code}, url={r.url}")
            print(f"  Body: {r.text[:300]}")
        except Exception as e:
            print(f"  Error: {e}")

        try:
            r2 = req.get(PORTAL_URL, headers=headers, timeout=15, allow_redirects=True)
            print(f"  Login: status={r2.status_code}, url={r2.url}")
            print(f"  Body: {r2.text[:300]}")
        except Exception as e:
            print(f"  Error: {e}")


asyncio.run(main())
