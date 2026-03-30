#!/usr/bin/env python3
"""
Craft Collective / TermSync Session Export — Red Nun Vendor Scrapers

Run on the Beelink via X-forwarding (ssh -Y) to capture a logged-in session.
Login: mgiorgio@rednun.com

IMPORTANT: Do NOT close the browser window. Press Enter in the terminal instead.

Usage (from MobaXterm or ssh -Y):
    cd ~/vendor-scrapers/craft-collective
    /opt/rednun/venv/bin/python3 export_session.py

Requirements:
    pip install playwright
    playwright install chromium
"""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://www.termsync.com/"
BROWSER_PROFILE_DIR = Path("./browser_profile")
STORAGE_STATE_FILE = Path("./storage_state.json")


def main():
    print("=" * 60)
    print("Craft Collective / TermSync Session Export")
    print("=" * 60)
    print()
    print(f"Portal: {PORTAL_URL}")
    print(f"Login:  mgiorgio@rednun.com")
    print(f"Profile: {BROWSER_PROFILE_DIR.absolute()}")
    print()
    print("Instructions:")
    print("  1. A browser window will open to TermSync")
    print("  2. Log in as mgiorgio@rednun.com")
    print("  3. Wait for the vendor home screen to load")
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

        print("Opening TermSync portal...")
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)

        print()
        print(">>> Browser is open. Log in as mgiorgio@rednun.com,")
        print(">>> wait for the home screen, then press ENTER here.")
        print()

        try:
            input("Press ENTER after you have logged in successfully... ")
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted — saving session anyway...")

        print()
        print("Saving session...")

        try:
            state = context.storage_state()
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            cookie_count = len(state.get("cookies", []))
            print(f"  Saved {cookie_count} cookies to {STORAGE_STATE_FILE}")
        except Exception as e:
            print(f"  [WARN] storage_state() failed: {e}")

        time.sleep(2)

        try:
            context.close()
            print("  Browser closed gracefully")
        except Exception as e:
            print(f"  [WARN] context.close() error: {e}")

    cookies_path = BROWSER_PROFILE_DIR / "Default" / "Cookies"
    if cookies_path.exists():
        size = cookies_path.stat().st_size
        print(f"  Cookies file: {size:,} bytes", end="")
        if size > 20480:
            print(" — OK (has cookies)")
        else:
            print(" — WARNING: may be empty (only schema)")
    else:
        print("  WARNING: Cookies file not found!")

    print()
    print("Session saved! The scraper should now be able to use this session.")
    print()


if __name__ == "__main__":
    main()
