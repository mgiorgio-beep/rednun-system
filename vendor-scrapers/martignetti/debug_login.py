#!/usr/bin/env python3
"""Debug: Examine Martignetti login page and test auto-login."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

PORTAL_URL = "https://martignettiexchange.com/profile/login?backurl=/profile/invoices/"

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
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("=" * 60)
        print("STEP 1: Navigate to portal (fresh session, no cookies)")
        print("=" * 60)

        try:
            resp = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
            print(f"  Status: {resp.status if resp else 'no response'}")
        except Exception as e:
            print(f"  Navigation error: {e}")

        await page.wait_for_timeout(3000)

        print(f"  URL: {page.url}")
        print(f"  Title: {await page.title()}")

        # Dump body text
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 2000) || 'NO BODY'")
        print(f"\n  BODY TEXT (first 2000 chars):\n{body}")

        # Dump all form elements
        forms = await page.evaluate("""
            () => {
                const forms = document.querySelectorAll('form');
                const inputs = document.querySelectorAll('input, select, textarea');
                const buttons = document.querySelectorAll('button, input[type="submit"], [role="button"]');

                return {
                    formCount: forms.length,
                    forms: Array.from(forms).map(f => ({
                        action: f.action,
                        method: f.method,
                        id: f.id,
                        className: f.className,
                    })),
                    inputs: Array.from(inputs).map(i => ({
                        type: i.type,
                        name: i.name,
                        id: i.id,
                        placeholder: i.placeholder,
                        className: i.className?.substring(0, 80),
                        visible: i.getBoundingClientRect().width > 0,
                        value: i.type === 'hidden' ? i.value?.substring(0, 50) : '',
                    })),
                    buttons: Array.from(buttons).map(b => ({
                        tag: b.tagName,
                        type: b.type,
                        text: b.innerText?.trim()?.substring(0, 50),
                        id: b.id,
                        className: b.className?.substring(0, 80),
                        visible: b.getBoundingClientRect().width > 0,
                    })),
                };
            }
        """)

        print(f"\n{'=' * 60}")
        print(f"FORMS ({forms['formCount']})")
        for f in forms['forms']:
            print(f"  <form action='{f['action']}' method='{f['method']}' id='{f['id']}'>")

        print(f"\nINPUTS ({len(forms['inputs'])})")
        for i in forms['inputs']:
            vis = "V" if i['visible'] else "H"
            print(f"  [{vis}] <input type='{i['type']}' name='{i['name']}' id='{i['id']}' placeholder='{i['placeholder']}'>")

        print(f"\nBUTTONS ({len(forms['buttons'])})")
        for b in forms['buttons']:
            vis = "V" if b['visible'] else "H"
            print(f"  [{vis}] <{b['tag']} type='{b['type']}'> \"{b['text']}\" id='{b['id']}'")

        # Check for iframes
        iframes = await page.evaluate("""
            () => Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src?.substring(0, 150),
                id: f.id,
                name: f.name,
                width: f.getBoundingClientRect().width,
                height: f.getBoundingClientRect().height,
            }))
        """)
        if iframes:
            print(f"\nIFRAMES ({len(iframes)})")
            for f in iframes:
                print(f"  <iframe src='{f['src']}' id='{f['id']}' {f['width']}x{f['height']}>")

        await page.screenshot(path="/tmp/mart_login_page.png", full_page=True)
        print(f"\nScreenshot saved to /tmp/mart_login_page.png")

        print(f"\n{'=' * 60}")
        print("STEP 2: Try auto-login")
        print("=" * 60)

        user = os.environ.get("MARTIGNETTI_USER", "")
        pw_val = os.environ.get("MARTIGNETTI_PASS", "")
        print(f"  User: {user}")
        print(f"  Pass: {'*' * len(pw_val)} ({len(pw_val)} chars)")

        if user and pw_val:
            email_filled = False
            for sel in [
                'input[name="USER_LOGIN"]', 'input[name="user_login"]',
                'input[type="email"]', 'input[name="email"]', 'input[name="username"]',
                'input[name="user"]', 'input[name="login"]', 'input[id="email"]',
                'input[id="username"]', 'input[placeholder*="mail"]',
                'input[placeholder*="user"]', 'input[type="text"]',
            ]:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.fill(user)
                    print(f"  Filled email via: {sel}")
                    email_filled = True
                    break
            if not email_filled:
                print("  [WARN] No email field found!")

            pw_filled = False
            for sel in ['input[type="password"]', 'input[name="USER_PASSWORD"]', 'input[name="user_password"]']:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.fill(pw_val)
                    print(f"  Filled password via: {sel}")
                    pw_filled = True
                    break
            if not pw_filled:
                print("  [WARN] No password field found!")

            if email_filled and pw_filled:
                submitted = False
                for sel in [
                    'button[type="submit"]', 'input[type="submit"]',
                    'input[value="Log In"]', 'input[value="Sign In"]',
                    'button:has-text("Log In")', 'button:has-text("Sign In")',
                    'button:has-text("Login")', 'button:has-text("Submit")',
                ]:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        print(f"  Clicked submit via: {sel}")
                        submitted = True
                        break

                if not submitted:
                    print("  No submit button found — pressing Enter")
                    await page.keyboard.press("Enter")

                await page.wait_for_timeout(8000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except:
                    pass

                print(f"\n  POST-LOGIN:")
                print(f"  URL: {page.url}")
                print(f"  Title: {await page.title()}")

                body2 = await page.evaluate("() => document.body?.innerText?.substring(0, 1500) || 'NO BODY'")
                print(f"\n  BODY TEXT:\n{body2}")

                await page.screenshot(path="/tmp/mart_after_login.png", full_page=True)
                print(f"\n  Screenshot saved to /tmp/mart_after_login.png")

        await browser.close()

asyncio.run(main())
