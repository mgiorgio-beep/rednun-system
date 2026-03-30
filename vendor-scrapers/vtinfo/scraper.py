#!/usr/bin/env python3
"""
VTInfo Invoice Scraper — Red Nun Vendor Scrapers
=================================================
Automates downloading new invoices from the VIP Retailer Portal
(apps.vtinfo.com/retailer-portal/) as CSV files.

Handles both vendors under the same login:
  - L. Knife & Son, Inc.
  - Colonial Wholesale Beverage

Flow per vendor:
  1. Select vendor from post-login vendor list
  2. Select location from top-right dropdown
  3. Click "View and Pay Invoices" tab
  4. Click each invoice number to open detail view
  5. Click cloud download icon (top-left) to get CSV
  6. Repeat for all new invoices, then switch vendor

Downloaded CSVs are saved to ./downloads/ and POSTed to the dashboard
import-csv endpoint for auto-import. If import fails (parser not yet
built for VTInfo format), CSVs remain in downloads for future processing.

Requirements:
    pip install playwright requests
    playwright install chromium

Usage:
    python scraper.py

Cron (Beelink):
    0 7 * * * cd ~/vendor-scrapers/vtinfo && /opt/rednun/venv/bin/python3 scraper.py >> scraper.log 2>&1
"""

import asyncio
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ─── CONFIG ──────────────────────────────────────────────────────────────────

VTINFO_URL = "https://apps.vtinfo.com/retailer-portal/"
MIN_YEAR = 2026  # Only download invoices from 2026 onward

# Vendors to scrape — as they appear in the VTInfo vendor selector.
# Keys = display text to match in vendor list, values = dashboard vendor name.
VENDORS = {
    "L Knife": "L. Knife & Son, Inc.",  # Portal shows "L Knife/Craft MA/Seaboard"
    "Colonial": "Colonial Wholesale Beverage",
}

# Locations to scrape per vendor — match text from the dropdown.
# VTInfo shows "RED NUN BAR & GRILL (AR034)" and "RED NUN DENNISPORT (AR035)".
# Keys = substring to match in dropdown text, values = dashboard location slug.
LOCATIONS = {
    "BAR & GRILL": "chatham",
    "DENNISPORT": "dennis",
}

# Directories
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
BROWSER_PROFILE_DIR = Path(os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"))

# Tracking file for already-downloaded invoices
DOWNLOADED_LOG = DATA_DIR / "downloaded_invoices.json"

# Browser settings
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "500"))

# Dashboard API
DASHBOARD_API = os.getenv("DASHBOARD_API", "http://127.0.0.1:8080")

# Email alerts
ALERT_RECIPIENT = "mgiorgio@rednun.com"
ENV_PATH = "/opt/rednun/.env"
SCRAPERS_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


# ─── HELPERS ─────────────────────────────────────────────────────────────────


def load_env():
    """Load environment variables from .env file if available."""
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val


def load_scraper_env():
    """Load credentials from vendor-scrapers/.env (separate from main app .env)."""
    if os.path.exists(SCRAPERS_ENV_PATH):
        with open(SCRAPERS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val:
                        os.environ[key] = val


def log(msg):
    print(f"[VTInfo] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def is_invoice_year_ok(date_str: str) -> bool:
    """Return True if the invoice date is from MIN_YEAR or later. Includes unparseable dates."""
    if not date_str:
        return True
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).year >= MIN_YEAR
        except ValueError:
            continue
    return True


def load_downloaded_invoices() -> set:
    """Load the set of invoice keys we've already downloaded."""
    if DOWNLOADED_LOG.exists():
        try:
            with open(DOWNLOADED_LOG, "r") as f:
                data = json.load(f)
                return set(data.get("invoices", []))
        except Exception:
            pass
    return set()


def save_downloaded_invoices(invoice_set: set):
    """Persist the set of downloaded invoice keys."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DOWNLOADED_LOG, "w") as f:
        json.dump({
            "invoices": sorted(invoice_set),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)


def get_dashboard_existing_invoices(vendor_name: str) -> set:
    """Query dashboard API for already-imported invoice numbers for a vendor."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": vendor_name},
            timeout=10,
        )
        if r.status_code == 200:
            nums = set(r.json().get("invoice_numbers", []))
            log(f"  Dashboard has {len(nums)} existing {vendor_name} invoices")
            return nums
        else:
            log(f"  [WARN] Dashboard API returned {r.status_code} for {vendor_name}")
    except requests.exceptions.ConnectionError:
        log("  [WARN] Dashboard API not reachable — skipping dedup check")
    except Exception as e:
        log(f"  [WARN] Dashboard dedup check failed: {e}")
    return set()


def update_vendor_session_status(vendor_name, status, failure_reason=None, invoices_scraped=0):
    """Update vendor_session_status table via dashboard API."""
    try:
        r = requests.post(
            f"{DASHBOARD_API}/api/vendor-sessions/update",
            json={
                "vendor_name": vendor_name,
                "status": status,
                "failure_reason": failure_reason,
                "invoices_scraped_last_run": invoices_scraped,
            },
            timeout=10,
        )
        if r.status_code == 200:
            log(f"  Session status updated: {vendor_name} → {status}")
    except Exception as e:
        log(f"  [WARN] Could not update session status for {vendor_name}: {e}")


def try_import_csv(csv_path: Path, location: str, vendor_slug: str = "") -> bool:
    """Try to import a downloaded CSV via the dashboard import-csv endpoint.
    Returns True if successfully imported, False otherwise."""
    try:
        params = {"location": location}
        if vendor_slug:
            params["vendor"] = f"vtinfo_{vendor_slug}"
        with open(csv_path, "rb") as f:
            r = requests.post(
                f"{DASHBOARD_API}/api/invoices/import-csv",
                files={"file": (csv_path.name, f, "text/csv")},
                params=params,
                timeout=30,
            )
        if r.status_code == 200:
            data = r.json()
            log(f"    [IMPORTED] {data.get('status')}: {data.get('message', '')}")
            return True
        elif r.status_code == 409:
            data = r.json()
            log(f"    [DUP] {data.get('message', 'Duplicate')}")
            return True  # Already imported counts as success
        else:
            log(f"    [IMPORT-SKIP] HTTP {r.status_code} — CSV saved for future import")
            return False
    except requests.exceptions.ConnectionError:
        log(f"    [IMPORT-SKIP] Dashboard not reachable — CSV saved locally")
        return False
    except Exception as e:
        log(f"    [IMPORT-SKIP] {e} — CSV saved locally")


def try_import_pdf(pdf_path: Path, location: str) -> bool:
    """Try to import a downloaded PDF invoice via the dashboard scan endpoint.
    The PDF goes through OCR to extract all items (more complete than CSV export).
    Returns True if successfully imported, False otherwise."""
    try:
        with open(pdf_path, "rb") as f:
            r = requests.post(
                f"{DASHBOARD_API}/api/invoices/scan",
                files={"file": (pdf_path.name, f, "application/pdf")},
                params={"location": location},
                timeout=120,  # OCR can take longer
            )
        if r.status_code == 200:
            data = r.json()
            inv_id = data.get('invoice_id', '')
            status = data.get('status', '')
            inv_data = data.get('data', {})
            vendor = inv_data.get('vendor_name', '')
            inv_num = inv_data.get('invoice_number', '')
            total = inv_data.get('total', 0)
            items = inv_data.get('total_line_items', 0)
            log(f"    [IMPORTED] {status}: {vendor} #{inv_num}: {items} items, ${total} (id={inv_id})")
            return True
        elif r.status_code == 409:
            data = r.json()
            log(f"    [DUP] {data.get('message', 'Duplicate')}")
            return True
        else:
            try:
                data = r.json()
                msg = data.get('error', data.get('message', ''))
            except Exception:
                msg = r.text[:200]
            log(f"    [IMPORT-SKIP] HTTP {r.status_code}: {msg}")
            return False
    except requests.exceptions.ConnectionError:
        log(f"    [IMPORT-SKIP] Dashboard not reachable — PDF saved locally")
        return False
    except Exception as e:
        log(f"    [IMPORT-SKIP] {e} — PDF saved locally")
        return False


def send_session_expired_alert():
    """Send email alert when VTInfo session has expired."""
    load_env()
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        log("[ERROR] Cannot send alert — GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set")
        return False

    now = datetime.now().strftime("%I:%M %p on %A, %B %d")
    vendors = ", ".join(VENDORS.values())

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;background:#020617;color:#e2e8f0;padding:20px;margin:0;">
<div style="max-width:600px;margin:0 auto;">
    <div style="background:#451a03;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin-bottom:20px;">
        <h2 style="color:#f59e0b;margin:0 0 8px 0;">VTInfo Session Expired</h2>
        <p style="margin:0;">The VTInfo invoice scraper cannot access the portal because the login session has expired.</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Vendors</td>
        <td style="padding:8px 0;">{vendors}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Portal</td>
        <td style="padding:8px 0;">{VTINFO_URL}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Detected</td>
        <td style="padding:8px 0;">{now}</td>
    </tr>
    </table>
    <div style="margin-top:20px;padding:12px;background:#0f172a;border-radius:8px;">
        <strong style="color:#94a3b8;">To fix:</strong>
        <pre style="color:#38bdf8;margin:8px 0 0 0;font-size:13px;">1. On your Windows PC:
   cd ~/vendor-scrapers/vtinfo
   python export_session.py

2. Log in when the browser opens

3. Close the browser after login completes

4. Transfer the session:
   scp -r -P 2222 browser_profile/ rednun@ssh.rednun.com:~/vendor-scrapers/vtinfo/browser_profile/</pre>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">
        One login covers both L. Knife and Colonial. The scraper will automatically resume on the next cron run.
    </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[Red Nun] VTInfo session expired — login needed"
    msg["From"] = gmail_user
    msg["To"] = ALERT_RECIPIENT
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, [ALERT_RECIPIENT], msg.as_string())
        log(f"Session expired alert sent to {ALERT_RECIPIENT}")
        return True
    except Exception as e:
        log(f"[ERROR] Could not send alert: {e}")
        return False


# ─── SESSION HEALTH CHECK ────────────────────────────────────────────────────


async def check_session_health(page):
    """Returns True if session is valid, False if login page detected."""
    login_indicators = [
        'input[type="password"]',
        'form[action*="login"]',
        'form[action*="signin"]',
        '#login', '.login-form', '#signin',
        'button:has-text("Log In")', 'button:has-text("Sign In")',
    ]
    for selector in login_indicators:
        try:
            el = await page.query_selector(selector)
            if el:
                log(f"Session expired — detected login indicator: {selector}")
                return False
        except Exception:
            pass

    url = page.url.lower()
    if "login" in url or "signin" in url or "auth" in url:
        log(f"Session expired — redirected to login URL: {page.url}")
        return False

    return True


async def auto_login(page) -> bool:
    """Attempt auto-login when session has expired. Reads credentials from ~/vendor-scrapers/.env."""
    load_scraper_env()

    username = os.getenv("VTINFO_USER", "")
    password = os.getenv("VTINFO_PASS", "")
    if not username or not password:
        log("No credentials found in .env (VTINFO_USER/VTINFO_PASS) — cannot auto-login")
        return False

    log(f"Attempting auto-login as {username}...")
    try:
        await page.goto(VTINFO_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        email_field = await page.query_selector(
            'input[type="email"], input[name="email"], input[name="username"], '
            'input[name="login"], input[id="email"], input[id="username"], '
            'input[name="USER_LOGIN"], input[name="user_login"]'
        )
        password_field = await page.query_selector('input[type="password"]')

        if not password_field:
            log("  No password field found on page — cannot auto-login")
            return False

        if email_field:
            await email_field.click()
            await email_field.fill(username)
            log(f"  Filled username: {username}")

        await password_field.click()
        await password_field.fill(password)
        log("  Filled password")

        submitted = False
        for btn_selector in [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Log In")', 'button:has-text("Sign In")',
            'button:has-text("Login")', 'button:has-text("Submit")',
            'input[value="Log In"]', 'input[value="Sign In"]',
        ]:
            btn = await page.query_selector(btn_selector)
            if btn:
                await btn.click()
                submitted = True
                log(f"  Clicked submit: {btn_selector}")
                break
        if not submitted:
            await password_field.press("Enter")
            log("  Pressed Enter in password field")

        await page.wait_for_timeout(8000)

        session_ok = await check_session_health(page)
        if session_ok:
            log("  Auto-login SUCCEEDED!")
            try:
                cookies = await page.context.cookies()
                with open(Path("./storage_state.json"), "w") as f:
                    json.dump({"cookies": cookies}, f, indent=2)
                log("  Saved new session state")
            except Exception as e:
                log(f"  [WARN] Could not save session state: {e}")
            return True
        else:
            log("  Auto-login FAILED — still on login page")
            return False
    except Exception as e:
        log(f"  Auto-login error: {e}")
        return False


# ─── VENDOR SELECTOR ─────────────────────────────────────────────────────────


async def _wait_for_retailer_selector(page, timeout_ms: int = 30000):
    """Wait for the Angular #retailer_selector nav element to render.
    After vendor selection, the portal takes several seconds to load the
    dashboard and render the nav bar with the location switcher.
    """
    log("  Waiting for retailer selector to render...")
    try:
        await page.wait_for_selector('#retailer_selector a', timeout=timeout_ms)
        loc_text = await page.locator('#retailer_selector a').first.inner_text()
        log(f"  Retailer selector ready: {loc_text.replace('arrow_drop_down', '').strip()}")
    except Exception:
        # Fallback: try waiting for any nav link with RED NUN
        try:
            await page.wait_for_selector('nav a:has-text("RED NUN")', timeout=10000)
            log("  Retailer selector ready (fallback: nav RED NUN link)")
        except Exception:
            log("  [WARN] Retailer selector did not render within timeout")


async def select_vendor(page, vendor_display_name: str) -> bool:
    """
    Select a vendor from the post-login vendor selection screen.
    Uses Playwright click for Angular Material compatibility.
    Returns True if vendor was selected successfully.
    """
    log(f"  Selecting vendor: {vendor_display_name}")

    # Use Playwright locator for proper Angular event dispatch
    vendor_link = page.locator(f'a:has-text("{vendor_display_name}"), button:has-text("{vendor_display_name}")').first
    if await vendor_link.count() > 0:
        text = (await vendor_link.inner_text()).strip()[:100]
        await vendor_link.click()
        log(f"  Selected vendor: \"{text}\"")
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # Wait for Angular nav bar to fully render after vendor switch
        # The #retailer_selector (location switcher) takes time to appear
        await _wait_for_retailer_selector(page)
        return True

    # Fallback: broader text search
    all_links = page.locator('a, button, [role="button"]')
    count = await all_links.count()
    lower = vendor_display_name.lower()
    for i in range(count):
        text = (await all_links.nth(i).inner_text()).strip()
        if lower in text.lower() and len(text) < 200:
            await all_links.nth(i).click()
            log(f"  Selected vendor: \"{text[:100]}\" (fallback)")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await _wait_for_retailer_selector(page)
            return True

    log(f"  [WARN] Could not find vendor \"{vendor_display_name}\" in vendor list")
    return False


# ─── LOCATION SELECTOR ───────────────────────────────────────────────────────


async def select_location(page, location_name: str) -> bool:
    """
    Select a location from the VTInfo nav bar.
    Clicking the location link (#retailer_selector) opens a "Switch Retailer"
    dialog with mat-list-item links for each location.
    Returns True if location was selected successfully.
    """
    log(f"  Selecting location: {location_name}")

    # Check if already on this location
    loc_link = await page.evaluate("""
        () => {
            const sel = document.querySelector('#retailer_selector a');
            if (sel) return (sel.innerText || '').replace('arrow_drop_down', '').trim();
            // Fallback: any nav link with RED NUN
            const nav = document.querySelector('nav.navigation') || document;
            const links = nav.querySelectorAll('a');
            for (const a of links) {
                const text = (a.innerText || '').trim();
                if (text.includes('RED NUN') && text.includes('(A')) {
                    return text.replace('arrow_drop_down', '').trim();
                }
            }
            return '';
        }
    """)

    loc_lower = location_name.lower()
    if loc_lower in loc_link.lower():
        log(f"  Already on {location_name} ({loc_link})")
        return True

    # Click the retailer selector to open the "Switch Retailer" dialog
    # First dismiss any lingering overlay/backdrop from previous interactions
    await page.evaluate("""
        () => {
            const backdrops = document.querySelectorAll('.cdk-overlay-backdrop');
            for (const bd of backdrops) bd.click();
        }
    """)
    await page.wait_for_timeout(500)

    # Wait for #retailer_selector to render (Angular may need time after vendor switch)
    try:
        await page.wait_for_selector('#retailer_selector a', timeout=10000)
    except Exception:
        pass
    loc_btn = page.locator('#retailer_selector a').first
    if await loc_btn.count() == 0:
        # Fallback: nav link with RED NUN and arrow_drop_down
        loc_btn = page.locator('nav a:has-text("arrow_drop_down"):has-text("RED NUN")').first
    if await loc_btn.count() == 0:
        log("  [WARN] Could not find location selector in nav bar")
        return False

    await loc_btn.click()
    await page.wait_for_timeout(2000)

    # The "Switch Retailer" dialog opens as a mat-dialog with mat-list-item links
    # Look for the target location in the dialog
    target = page.locator(f'.mat-dialog-container a:has-text("{location_name}")').first
    if await target.count() > 0:
        text = (await target.inner_text()).strip()
        await target.click()
        log(f"  Selected location: \"{text}\"")
        # Wait for page to reload after location switch
        await page.wait_for_timeout(4000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # Wait for the invoices button to render
        try:
            await page.wait_for_selector('#invoices', timeout=10000)
        except Exception:
            pass
        return True

    # Fallback: search all overlay links for the location name
    result = await page.evaluate("""
        (locName) => {
            const lower = locName.toLowerCase();
            const items = document.querySelectorAll(
                '.cdk-overlay-pane a, .mat-list-item, .mat-dialog-container a, ' +
                '.cdk-overlay-pane button, [role="menuitem"]'
            );
            const available = [];
            for (const item of items) {
                const text = (item.innerText || '').trim();
                if (text.length > 3 && text.length < 200) {
                    available.push(text.substring(0, 100));
                    if (text.toLowerCase().includes(lower)) {
                        item.click();
                        return {found: true, text: text.substring(0, 100)};
                    }
                }
            }
            return {found: false, available: available};
        }
    """, location_name)

    if result.get("found"):
        log(f"  Selected location: \"{result['text']}\"")
        await page.wait_for_timeout(3000)
        return True

    log(f"  [WARN] Could not select location \"{location_name}\"")
    if result.get("available"):
        log(f"  Available in dialog: {result['available']}")

    # Close the dialog
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    return False


# ─── INVOICE LIST ────────────────────────────────────────────────────────────


async def navigate_to_invoices(page) -> bool:
    """Click the 'View and Pay Invoices' button to see the invoice list."""
    log("  Navigating to 'View and Pay Invoices'...")

    # Wait for the button to render (Angular may still be loading)
    try:
        await page.wait_for_selector('#invoices', timeout=10000)
    except Exception:
        pass

    # Primary: button with id="invoices" (Angular Material button)
    # Page has duplicate id="invoices" elements (responsive layout) — find the visible one
    inv_btns = page.locator('#invoices')
    btn_count = await inv_btns.count()
    for idx in range(btn_count):
        btn = inv_btns.nth(idx)
        if await btn.is_visible():
            await btn.click()
            log("  Clicked 'View and Pay Invoices' button (id=invoices, visible instance)")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            return True

    # Fallback: text-based search using Playwright locators
    for selector in [
        'button:has-text("View and Pay Invoices")',
        'a:has-text("View and Pay Invoices")',
        'button:has-text("Invoices")',
    ]:
        el = page.locator(selector).first
        if await el.count() > 0:
            await el.click()
            log(f"  Clicked invoice button via: {selector}")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            return True

    log("  [WARN] Could not find 'View and Pay Invoices' button")
    return True  # Continue anyway — might already be on invoices page


async def scrape_invoice_list(page) -> list[dict]:
    """
    Scrape invoice numbers and metadata from the invoice list.
    Returns list of dicts with invoice_number, date, amount.
    """
    invoices = await page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();

            // Strategy 1: Table rows with invoice-like numbers
            const rows = document.querySelectorAll('table tbody tr, tr, [class*="row"]');
            for (const row of rows) {
                const text = row.innerText || '';
                // Look for clickable invoice number (link inside the row)
                const links = row.querySelectorAll('a');
                for (const link of links) {
                    const linkText = link.innerText?.trim() || '';
                    // Invoice numbers are typically 6-10 digits
                    const numMatch = linkText.match(/^\\d{5,10}$/);
                    if (numMatch) {
                        const invNum = numMatch[0];
                        if (seen.has(invNum)) continue;
                        seen.add(invNum);

                        // Extract dates and amounts from the row
                        const dates = text.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g)
                            || text.match(/\\d{4}-\\d{2}-\\d{2}/g)
                            || text.match(/[A-Z][a-z]{2}\\s+\\d{1,2},?\\s+\\d{4}/g)
                            || [];
                        const amounts = text.match(/-?\\$[\\d,]+\\.\\d{2}/g) || [];

                        // VTInfo row: [DUE DATE] [INV#] [INVOICE DATE] [AMOUNT]
                        // The second date is the actual invoice date
                        const invDate = dates.length >= 2 ? dates[1] : (dates[0] || '');

                        results.push({
                            invoice_number: invNum,
                            date: invDate,
                            amount: amounts[0] ? amounts[0].replace('$', '').replace(',', '') : '',
                            row_text: text.substring(0, 300),
                        });
                    }
                }
            }

            // Strategy 2: Look for any numeric links on the page
            if (results.length === 0) {
                const allLinks = document.querySelectorAll('a');
                for (const link of allLinks) {
                    const text = link.innerText?.trim() || '';
                    const numMatch = text.match(/^\\d{5,10}$/);
                    if (numMatch && !seen.has(numMatch[0])) {
                        seen.add(numMatch[0]);
                        results.push({
                            invoice_number: numMatch[0],
                            date: '',
                            amount: '',
                            row_text: '',
                        });
                    }
                }
            }

            return results;
        }
    """)
    return invoices


# ─── DOWNLOAD ────────────────────────────────────────────────────────────────


async def download_invoice_pdf(page, invoice_number: str, vendor_slug: str, location: str, invoice_date: str = '') -> Path | None:
    """
    Download a single invoice as PDF from VTInfo:
    1. Click the invoice number link to open detail view
    2. Extract invoice date from the detail page
    3. Click the PDF download button (right of the CSV button)
    4. Navigate back to invoice list

    The PDF contains ALL line items (the CSV export is often incomplete).
    """
    try:
        # ── Step 1: Click the invoice number to open detail ────────
        clicked = await page.evaluate("""
            (invoiceNum) => {
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    if (link.innerText?.trim() === invoiceNum) {
                        link.click();
                        return true;
                    }
                }
                return false;
            }
        """, invoice_number)

        if not clicked:
            log(f"    [WARN] Could not find link for invoice #{invoice_number}")
            return None

        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # ── Step 2: Extract invoice date from detail page ─────────
        if not invoice_date:
            try:
                detail_date = await page.evaluate("""
                    () => {
                        const body = document.body.innerText || '';
                        const patterns = [
                            /(?:Invoice\\s*Date|Date)[:\\s]*?(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})/i,
                            /(?:Invoice\\s*Date|Date)[:\\s]*?(\\d{4}-\\d{2}-\\d{2})/i,
                            /(?:Invoice\\s*Date|Date)[:\\s]*?([A-Z][a-z]{2}\\s+\\d{1,2},?\\s+\\d{4})/i,
                        ];
                        for (const pat of patterns) {
                            const m = body.match(pat);
                            if (m) return m[1];
                        }
                        const top = body.substring(0, 2000);
                        const m = top.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{4}/);
                        return m ? m[0] : '';
                    }
                """)
                if detail_date:
                    invoice_date = detail_date.strip()
                    log(f"    Extracted date from detail page: {invoice_date}")
            except Exception:
                pass

        # ── Step 3: Click PDF download button ──────────────────────
        # The PDF button is right next to the CSV download button.
        # First, log what icons/buttons are available for debugging.
        icons_info = await page.evaluate("""
            () => {
                const info = [];
                // Check all material icons
                const matIcons = document.querySelectorAll('i.material-icons, mat-icon, .mat-icon, i');
                for (const icon of matIcons) {
                    const text = (icon.innerText || icon.textContent || '').trim();
                    const rect = icon.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && text) {
                        info.push({text: text, tag: icon.tagName, cls: icon.className, x: Math.round(rect.left), y: Math.round(rect.top)});
                    }
                }
                // Check buttons/links near top
                const btns = document.querySelectorAll('a, button, [role="button"]');
                for (const btn of btns) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.top < 200 && rect.width > 0) {
                        const text = (btn.innerText || '').trim().substring(0, 50);
                        const title = btn.title || '';
                        const aria = btn.getAttribute('aria-label') || '';
                        if (text || title || aria) {
                            info.push({text: text, title: title, aria: aria, tag: btn.tagName, x: Math.round(rect.left), y: Math.round(rect.top)});
                        }
                    }
                }
                return info;
            }
        """)
        log(f"    Detail page icons/buttons: {json.dumps(icons_info[:15])}")

        # Use Playwright native click — more reliable for Angular apps
        pdf_icon = page.locator('mat-icon:text-is("insert_drive_file"), i:text-is("insert_drive_file")').first
        if await pdf_icon.count() == 0:
            log("    [WARN] insert_drive_file icon not found")
            await page.go_back()
            await page.wait_for_timeout(1000)
            return None

        # Click the parent <a> or <button>, or the icon itself
        pdf_btn = pdf_icon.locator('xpath=ancestor::a | ancestor::button').first
        if await pdf_btn.count() == 0:
            pdf_btn = pdf_icon

        async with page.expect_download(timeout=45000) as download_info:
            await pdf_btn.click()
            log(f"    Click result: native click on insert_drive_file")

        download = await download_info.value

        # ── Save the PDF ──────────────────────────────────────────
        date_stamp = ''
        if invoice_date:
            for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%b %d, %Y', '%b %d %Y'):
                try:
                    date_stamp = datetime.strptime(invoice_date.strip(), fmt).strftime('%Y%m%d')
                    break
                except ValueError:
                    continue
        if not date_stamp:
            date_stamp = datetime.now().strftime("%Y%m%d")

        dest = DOWNLOAD_DIR / f"vtinfo_{vendor_slug}_{location}_{invoice_number}_{date_stamp}.pdf"
        if dest.exists():
            seq = 2
            while dest.exists():
                dest = DOWNLOAD_DIR / f"vtinfo_{vendor_slug}_{location}_{invoice_number}_{date_stamp}_{seq}.pdf"
                seq += 1
        await download.save_as(str(dest))

        log(f"    [OK] Invoice #{invoice_number} -> {dest.name}")

        # Navigate back to invoice list
        await page.go_back()
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        return dest

    except PlaywrightTimeoutError:
        log(f"    [TIMEOUT] Invoice #{invoice_number} — download timed out")
        # Try to go back to invoice list
        try:
            await page.go_back()
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        return None
    except Exception as e:
        log(f"    [ERROR] Invoice #{invoice_number} — {e}")
        try:
            await page.go_back()
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        return None


# ─── SCRAPE VENDOR + LOCATION ────────────────────────────────────────────────


async def scrape_vendor_location(
    page,
    vendor_display: str,
    vendor_db_name: str,
    location_display: str,
    location_slug: str,
    already_downloaded: set,
) -> list[str] | None:
    """Scrape and download invoices for one vendor + location combo.
    Returns list of downloaded keys on success, None on failure."""
    log(f"\n{'─' * 60}")
    log(f"VENDOR: {vendor_db_name} | LOCATION: {location_display} ({location_slug})")
    log(f"{'─' * 60}")

    # Select location from dropdown
    loc_ok = await select_location(page, location_display)
    if not loc_ok:
        log(f"  [ERROR] Could not select location {location_display} — skipping to avoid scraping wrong location")
        return None

    # Navigate to invoice list
    nav_ok = await navigate_to_invoices(page)
    if not nav_ok:
        log("  [ERROR] Could not navigate to invoices")
        return None

    # Debug page info
    page_info = await page.evaluate("""
        () => {
            return {
                url: location.href,
                title: document.title,
                bodyLength: document.body.innerText.length,
                sampleText: document.body.innerText.substring(0, 600)
            };
        }
    """)
    log(f"  Page: {page_info['title']}")
    log(f"  URL: {page_info['url']}")

    # Verify location via URL (more reliable than nav text)
    url = page_info['url']
    if location_display.upper() == "BAR & GRILL":
        # Chatham codes: AR034 (L Knife), R2560 (Colonial)
        if 'AR035' in url or 'R2565' in url:
            log(f"  [WARN] URL suggests DENNISPORT but expected BAR & GRILL — retrying location switch")
            loc_ok = await select_location(page, location_display)
            if loc_ok:
                nav_ok = await navigate_to_invoices(page)
    elif location_display.upper() == "DENNISPORT":
        # Dennis codes: AR035 (L Knife), R2565 (Colonial)
        if 'AR034' in url or 'R2560' in url:
            log(f"  [WARN] URL suggests BAR & GRILL but expected DENNISPORT — retrying location switch")
            loc_ok = await select_location(page, location_display)
            if loc_ok:
                nav_ok = await navigate_to_invoices(page)

    # Wait for invoice table to render (Angular loads data async)
    for attempt in range(3):
        has_rows = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (/^\\d{5,10}$/.test((a.innerText || '').trim())) return true;
                }
                return false;
            }
        """)
        if has_rows:
            break
        log(f"  Waiting for invoice table to load (attempt {attempt + 1})...")
        await page.wait_for_timeout(3000)

    # Scrape invoice list
    invoices = await scrape_invoice_list(page)
    log(f"  Found {len(invoices)} invoices on page")
    if invoices:
        sample = invoices[0]
        log(f"  Sample row: #{sample.get('invoice_number')} date='{sample.get('date')}' amount='{sample.get('amount')}'")
        log(f"  Row text: {sample.get('row_text', '')[:200]}")

    # Create vendor slug for filenames and tracking keys
    vendor_slug = vendor_display.lower().replace(" ", "").replace(".", "")

    # Filter to current year (2026+) only
    current_year_invoices = [inv for inv in invoices if is_invoice_year_ok(inv.get("date", ""))]
    skipped_old = len(invoices) - len(current_year_invoices)
    if skipped_old:
        log(f"  Skipped {skipped_old} pre-{MIN_YEAR} invoices")

    # Filter to new invoices only — use vendor+invnum as key
    new_invoices = [
        inv for inv in current_year_invoices
        if f"{vendor_slug}_{inv['invoice_number']}" not in already_downloaded
    ]
    log(f"  New invoices: {len(new_invoices)}")

    if not new_invoices:
        log("  All caught up!")
        return []

    # Download each new invoice
    downloaded = []
    for i, inv in enumerate(new_invoices):
        inv_num = inv["invoice_number"]
        log(f"  [{i + 1}/{len(new_invoices)}] Invoice #{inv_num} ({inv.get('date', '?')}, {inv.get('amount', '?')})")

        pdf_path = await download_invoice_pdf(page, inv_num, vendor_slug, location_slug, invoice_date=inv.get("date", ""))

        if pdf_path and pdf_path.exists():
            tracking_key = f"{vendor_slug}_{inv_num}"
            downloaded.append(tracking_key)
            already_downloaded.add(tracking_key)
            save_downloaded_invoices(already_downloaded)

            # Try to import via dashboard API
            try_import_pdf(pdf_path, location_slug)

        await page.wait_for_timeout(1500)

    return downloaded


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    load_env()

    log(f"{'=' * 60}")
    log(f"VTInfo Invoice Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")
    log(f"Vendors: {', '.join(VENDORS.values())}")

    # Ensure directories exist
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Load previously downloaded invoices (local tracking)
    already_downloaded = load_downloaded_invoices()
    log(f"Previously downloaded (local): {len(already_downloaded)} entries")

    # Check dashboard API for already-imported invoices (both vendors)
    for vendor_display, vendor_db_name in VENDORS.items():
        dashboard_existing = get_dashboard_existing_invoices(vendor_db_name)
        vendor_slug = vendor_display.lower().replace(" ", "").replace(".", "")
        for inv_num in dashboard_existing:
            already_downloaded.add(f"{vendor_slug}_{inv_num}")
    log(f"Combined dedup set: {len(already_downloaded)} entries")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            accept_downloads=True,
            viewport={"width": 1600, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # Inject cookies from storage_state.json if available (backup for persistent context)
        storage_state_file = Path("./storage_state.json")
        if storage_state_file.exists():
            try:
                with open(storage_state_file) as f:
                    state = json.load(f)
                cookies = state.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    log(f"Injected {len(cookies)} cookies from storage_state.json")
            except Exception as e:
                log(f"[WARN] Could not load storage_state.json: {e}")

        # Navigate to VTInfo portal
        log("\nNavigating to VTInfo portal...")
        try:
            await page.goto(VTINFO_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            log("[ERROR] VTInfo portal timed out")
            for vn in VENDORS.values():
                update_vendor_session_status(vn, "expired", failure_reason="timeout")
            await context.close()
            sys.exit(1)

        # Check session health — try auto-login if expired
        session_ok = await check_session_health(page)
        if not session_ok:
            log("\nSESSION EXPIRED — attempting auto-login...")
            login_ok = await auto_login(page)
            if not login_ok:
                for vn in VENDORS.values():
                    update_vendor_session_status(vn, "expired", failure_reason="session_expired")
                send_session_expired_alert()
                await context.close()
                sys.exit(1)
            else:
                for vn in VENDORS.values():
                    update_vendor_session_status(vn, "active")

        # Process each vendor
        total_downloaded = []
        vendor_counts = {}
        location_failures = []  # track failed locations

        for vendor_display, vendor_db_name in VENDORS.items():
            log(f"\n{'=' * 60}")
            log(f"VENDOR: {vendor_db_name}")
            log(f"{'=' * 60}")

            # Select this vendor from the vendor list
            vendor_ok = await select_vendor(page, vendor_display)
            if not vendor_ok:
                log(f"[ERROR] Could not select vendor {vendor_display}")
                update_vendor_session_status(vendor_db_name, "expired", failure_reason="vendor_select_failed")
                for _, loc_slug in LOCATIONS.items():
                    location_failures.append(f"{vendor_db_name}/{loc_slug}")
                continue

            vendor_downloaded = []
            vendor_failed_locations = []

            # Scrape each location
            for location_display, location_slug in LOCATIONS.items():
                downloaded = await scrape_vendor_location(
                    page, vendor_display, vendor_db_name,
                    location_display, location_slug,
                    already_downloaded,
                )
                if downloaded is None:
                    vendor_failed_locations.append(location_slug)
                    location_failures.append(f"{vendor_db_name}/{location_slug}")
                else:
                    vendor_downloaded.extend(downloaded)
                    total_downloaded.extend(downloaded)

            vendor_counts[vendor_db_name] = len(vendor_downloaded)
            if vendor_failed_locations:
                reason = f"location_failed: {', '.join(vendor_failed_locations)}"
                update_vendor_session_status(vendor_db_name, "expired", failure_reason=reason,
                                             invoices_scraped=len(vendor_downloaded))
            else:
                update_vendor_session_status(vendor_db_name, "healthy", invoices_scraped=len(vendor_downloaded))

            # Navigate back to portal home for next vendor selection
            log("  Returning to portal home for next vendor...")
            try:
                await page.goto(VTINFO_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        # Summary
        log(f"\n{'=' * 60}")
        log(f"SUMMARY")
        log(f"{'=' * 60}")
        for vn, count in vendor_counts.items():
            log(f"  {vn}: {count} downloaded")
        log(f"  Total downloaded: {len(total_downloaded)}")
        log(f"  Total tracked:    {len(already_downloaded)}")
        if total_downloaded:
            log(f"  New: {', '.join(total_downloaded)}")
        if location_failures:
            log(f"  FAILED locations: {', '.join(location_failures)}")
        log(f"{'=' * 60}")

        await context.close()

        # Exit with non-zero if any location failed
        if location_failures:
            log(f"Exiting with code 1 due to {len(location_failures)} location failure(s)")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
