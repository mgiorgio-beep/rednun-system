#!/usr/bin/env python3
"""
Southern Glazer's Invoice Scraper — Chatham
============================================
Login: mike@rednun.com → Chatham location
Browser profile: browser_profile_chatham/

Cron (Beelink):
    0 7 * * * cd ~/vendor-scrapers/southern-glazers && /opt/rednun/venv/bin/python3 scraper_chatham.py >> scraper_chatham.log 2>&1
"""

import asyncio
from pathlib import Path

from scraper_core import run_scraper

LOCATION = "chatham"
LOGIN_EMAIL = "mike@rednun.com"
BROWSER_PROFILE_DIR = Path("./browser_profile_chatham")
DOWNLOAD_DIR = Path("./downloads")
DATA_DIR = Path("./data")

if __name__ == "__main__":
    asyncio.run(run_scraper(
        location=LOCATION,
        login_email=LOGIN_EMAIL,
        browser_profile_dir=BROWSER_PROFILE_DIR,
        download_dir=DOWNLOAD_DIR,
        data_dir=DATA_DIR,
    ))
