#!/usr/bin/env python3
"""
VTInfo Session Export — Red Nun Vendor Scrapers

Run on the Beelink via X-forwarding (ssh -Y) to capture a logged-in VTInfo session.
One login covers both L. Knife & Son and Colonial Wholesale Beverage.

IMPORTANT: Do NOT close the browser window. Press Enter in the terminal instead.

Usage (from MobaXterm or ssh -Y):
    cd ~/vendor-scrapers/vtinfo
    /opt/rednun/venv/bin/python3 export_session.py

Requirements:
    pip install playwright
    playwright install chromium
"""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VTINFO_URL = "https://apps.vtinfo.com/retailer-portal/"
BROWSER_PROFILE_DIR = Path("./browser_profile")
STORAGE_STATE_FILE = Path("./storage_state.json")


def main():
    print("=" * 60)
    print("VTInfo Session Export (L. Knife + Colonial)")
    print("=" * 60)
    print()
    print(f"Portal: {VTINFO_URL}")
    print(f"Profile: {BROWSER_PROFILE_DIR.absolute()}")
    print()
    print("Instructions:")
    print("  1. A browser window will open to the VTInfo portal")
    print("  2. Log in with your credentials")
    print("  3. You should land on the vendor selection screen")
    print("  4. Come back HERE and press Enter (do NOT close the browser)")
    print()
    print("NOTE: One login covers BOTH L. Knife and Colonial.")
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

        print("Opening VTInfo portal...")
        page.goto(VTINFO_URL, wait_until="domcontentloaded", timeout=60000)

        print()
        print(">>> Browser is open. Log in, wait for vendor selection screen.")
        print(">>> Then come back here and press ENTER.")
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
