#!/usr/bin/env python3
"""
Southern Glazer's Session Export — Chatham
==========================================
Run on the Beelink via X-forwarding (ssh -Y).
Login: mike@rednun.com

The SG portal (portal2.ftnirdc.com) uses sessionStorage for auth tokens,
NOT cookies. This script captures sessionStorage, localStorage, and cookies.

IMPORTANT: Do NOT close the browser window. Press Enter in the terminal instead.

Usage (from MobaXterm or ssh -Y):
    cd ~/vendor-scrapers/southern-glazers
    /opt/rednun/venv/bin/python3 export_session_chatham.py

Requirements:
    pip install playwright
    playwright install chromium
"""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://portal2.ftnirdc.com/en/72752"
BROWSER_PROFILE_DIR = Path("./browser_profile_chatham")
STORAGE_STATE_FILE = Path("./storage_state_chatham.json")


def main():
    print("=" * 60)
    print("Southern Glazer's Session Export — CHATHAM")
    print("=" * 60)
    print()
    print(f"Portal: {PORTAL_URL}")
    print(f"Login:  mike@rednun.com")
    print(f"Profile: {BROWSER_PROFILE_DIR.absolute()}")
    print()
    print("Instructions:")
    print("  1. A browser window will open to the Southern Glazer's portal")
    print("  2. Log in as mike@rednun.com (Chatham)")
    print("  3. Wait for the invoice list to FULLY load")
    print("  4. Come back HERE and press Enter (do NOT close the browser)")
    print()

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            slow_mo=300,
            viewport={"width": 1400, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()

        print("Opening Southern Glazer's portal...")
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)

        print()
        print(">>> Browser is open. Log in as mike@rednun.com,")
        print(">>> wait for invoices to FULLY load, then press ENTER here.")
        print()

        try:
            input("Press ENTER after you have logged in successfully... ")
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted — saving session anyway...")

        print()
        print("Saving session...")

        # Capture standard storage_state (cookies + localStorage)
        try:
            state = context.storage_state()
        except Exception as e:
            print(f"  [WARN] storage_state() failed: {e}")
            state = {"cookies": [], "origins": []}

        cookie_count = len(state.get("cookies", []))
        print(f"  Cookies: {cookie_count}")

        # Capture sessionStorage (NOT included in storage_state)
        session_storage = {}
        try:
            session_storage = page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        items[key] = sessionStorage.getItem(key);
                    }
                    return items;
                }
            """)
            print(f"  sessionStorage: {len(session_storage)} items")
            for key in session_storage:
                val_preview = str(session_storage[key])[:80]
                print(f"    {key}: {val_preview}...")
        except Exception as e:
            print(f"  [WARN] Could not capture sessionStorage: {e}")

        # Capture localStorage too (belt and suspenders)
        local_storage = {}
        try:
            local_storage = page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                }
            """)
            print(f"  localStorage: {len(local_storage)} items")
        except Exception as e:
            print(f"  [WARN] Could not capture localStorage: {e}")

        # Capture current URL (to know what page we're on)
        current_url = page.url
        print(f"  URL: {current_url}")

        # Save everything to JSON
        state["sessionStorage"] = session_storage
        state["localStorage_manual"] = local_storage
        state["captured_url"] = current_url
        state["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        with open(STORAGE_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        total_items = cookie_count + len(session_storage) + len(local_storage)
        print(f"  Total auth items saved: {total_items}")

        if cookie_count == 0 and len(session_storage) == 0:
            print()
            print("  WARNING: No cookies or sessionStorage found!")
            print("  The portal may use a different auth mechanism.")
            print("  Make sure you fully logged in before pressing Enter.")

        time.sleep(2)

        try:
            context.close()
            print("  Browser closed gracefully")
        except Exception as e:
            print(f"  [WARN] context.close() error: {e}")

    print()
    print("Session saved! Re-run the scraper to test.")
    print()


if __name__ == "__main__":
    main()
