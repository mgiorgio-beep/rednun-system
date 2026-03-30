#!/usr/bin/env python3
"""Debug: Check Martignetti login form fields with proper user-agent."""
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
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, user_agent=UA)
        page = await context.new_page()

        resp = await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        print(f"Status: {resp.status}, URL: {page.url}")
        print(f"Title: {await page.title()}")

        # Dump all form elements
        forms = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll('input, select, textarea');
                const buttons = document.querySelectorAll('button, input[type="submit"], [role="button"]');
                return {
                    inputs: Array.from(inputs).map(i => ({
                        type: i.type, name: i.name, id: i.id,
                        placeholder: i.placeholder,
                        className: i.className?.substring(0, 80),
                        visible: i.getBoundingClientRect().width > 0,
                        form_action: i.form?.action?.substring(0, 100) || '',
                    })),
                    buttons: Array.from(buttons).map(b => ({
                        tag: b.tagName, type: b.type,
                        text: b.innerText?.trim()?.substring(0, 50),
                        id: b.id, className: b.className?.substring(0, 80),
                        visible: b.getBoundingClientRect().width > 0,
                    })),
                };
            }
        """)

        print(f"\nINPUTS ({len(forms['inputs'])}):")
        for i in forms['inputs']:
            vis = "V" if i['visible'] else "H"
            print(f"  [{vis}] type={i['type']:12s} name={i['name']:20s} id={i['id']:20s} ph={i['placeholder']:20s} form={i['form_action']}")

        print(f"\nBUTTONS ({len(forms['buttons'])}):")
        for b in forms['buttons']:
            vis = "V" if b['visible'] else "H"
            print(f"  [{vis}] <{b['tag']} type='{b['type']}'> \"{b['text']}\" id={b['id']} class={b['className']}")

        # Dump body text
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 1500) || ''")
        print(f"\nBODY:\n{body}")

        # Try login
        user = os.environ.get("MARTIGNETTI_USER", "")
        pw_val = os.environ.get("MARTIGNETTI_PASS", "")
        print(f"\n{'='*60}\nTrying login: {user}")

        # Fill username
        for sel in ['input[name="USER_LOGIN"]', 'input[name="user_login"]', 'input[id="USER_LOGIN"]',
                     'input[type="email"]', 'input[name="email"]', 'input[name="username"]',
                     'input[placeholder*="mail"]', 'input[placeholder*="user"]']:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(user)
                print(f"  Filled user: {sel}")
                break

        # Fill password
        for sel in ['input[name="USER_PASSWORD"]', 'input[name="user_password"]',
                     'input[type="password"]']:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(pw_val)
                print(f"  Filled pass: {sel}")
                break

        # Submit
        for sel in ['input[type="submit"]', 'input[value="Log In"]', 'button[type="submit"]',
                     'button:has-text("Log In")', 'button:has-text("Login")']:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                print(f"  Submit: {sel}")
                break

        await page.wait_for_timeout(8000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass

        print(f"\nPOST-LOGIN: {page.url}")
        print(f"Title: {await page.title()}")
        body2 = await page.evaluate("() => document.body?.innerText?.substring(0, 1500) || ''")
        print(f"Body:\n{body2}")

        await page.screenshot(path="/tmp/mart_after_login.png", full_page=True)
        await browser.close()

asyncio.run(main())
