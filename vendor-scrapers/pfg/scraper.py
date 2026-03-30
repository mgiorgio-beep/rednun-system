#!/usr/bin/env python3
"""
PFG Invoice Scraper — Red Nun Vendor Scrapers
==============================================
Automates downloading new invoices from customerfirstsolutions.com as CSV files.
Supports both Red Nun locations (Chatham + Dennis Port) via location switcher.

Primary flow: CSV export (structured data, imported directly via dashboard API).
Fallback: PDF download via three-dot kebab menu per invoice.

Requirements:
    pip install playwright requests
    playwright install chromium

Usage:
    python scraper.py

Cron (Beelink):
    0 7 * * * cd ~/vendor-scrapers/pfg && /opt/rednun/venv/bin/python3 scraper.py >> scraper.log 2>&1
"""

import asyncio
import json
import os
import shutil
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ─── CONFIG ──────────────────────────────────────────────────────────────────

PFG_URL = "https://www.customerfirstsolutions.com/"
VENDOR_NAME = "Performance Foodservice"
MIN_YEAR = 2026  # Only download invoices from 2026 onward

# Companies to scrape — names as they appear in the PFG location switcher
COMPANIES = {
    "Red Nun Bar & Grill Chat": "chatham",
    "Red Nun Dennis Port": "dennis",
}

# Directories
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
BROWSER_PROFILE_DIR = Path(os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"))

# Where to copy PDFs for the dashboard OCR pipeline (fallback only)
INVOICE_IMAGES_DIR = Path(os.getenv("INVOICE_IMAGES_DIR", "/opt/rednun/invoice_images"))

# Tracking file for already-downloaded invoices
DOWNLOADED_LOG = DATA_DIR / "downloaded_invoices.json"

# Browser settings
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "500"))

# Dashboard API — for checking already-imported invoices and CSV import
DASHBOARD_API = os.getenv("DASHBOARD_API", "http://127.0.0.1:8080")

# Email alerts (loaded from /opt/rednun/.env if available)
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
    print(f"[PFG] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


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
    """Load the set of invoice numbers we've already downloaded."""
    if DOWNLOADED_LOG.exists():
        try:
            with open(DOWNLOADED_LOG, "r") as f:
                data = json.load(f)
                return set(data.get("invoices", []))
        except Exception:
            pass
    return set()


def save_downloaded_invoices(invoice_set: set):
    """Persist the set of downloaded invoice numbers."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DOWNLOADED_LOG, "w") as f:
        json.dump({
            "invoices": sorted(invoice_set),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)


def get_dashboard_existing_invoices() -> set:
    """Query dashboard API for already-imported PFG invoice numbers."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": VENDOR_NAME},
            timeout=10,
        )
        if r.status_code == 200:
            nums = set(r.json().get("invoice_numbers", []))
            log(f"Dashboard has {len(nums)} existing {VENDOR_NAME} invoices")
            return nums
        else:
            log(f"[WARN] Dashboard API returned {r.status_code} — skipping dedup check")
    except requests.exceptions.ConnectionError:
        log("[WARN] Dashboard API not reachable — skipping dedup check")
    except Exception as e:
        log(f"[WARN] Dashboard dedup check failed: {e}")
    return set()


def update_vendor_session_status(status, failure_reason=None, invoices_scraped=0):
    """Update vendor_session_status table via dashboard API."""
    try:
        r = requests.post(
            f"{DASHBOARD_API}/api/vendor-sessions/update",
            json={
                "vendor_name": VENDOR_NAME,
                "status": status,
                "failure_reason": failure_reason,
                "invoices_scraped_last_run": invoices_scraped,
            },
            timeout=10,
        )
        if r.status_code == 200:
            log(f"Session status updated: {status}")
        else:
            log(f"[WARN] Session status update returned {r.status_code}")
    except Exception as e:
        log(f"[WARN] Could not update session status: {e}")


def send_session_expired_alert():
    """Send email alert when PFG session has expired."""
    load_env()
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        log("[ERROR] Cannot send alert — GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set")
        return False

    now = datetime.now().strftime("%I:%M %p on %A, %B %d")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;background:#020617;color:#e2e8f0;padding:20px;margin:0;">
<div style="max-width:600px;margin:0 auto;">
    <div style="background:#451a03;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin-bottom:20px;">
        <h2 style="color:#f59e0b;margin:0 0 8px 0;">PFG Session Expired</h2>
        <p style="margin:0;">The PFG invoice scraper cannot access the portal because the login session has expired.</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Vendor</td>
        <td style="padding:8px 0;">{VENDOR_NAME}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Portal</td>
        <td style="padding:8px 0;">{PFG_URL}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Detected</td>
        <td style="padding:8px 0;">{now}</td>
    </tr>
    </table>
    <div style="margin-top:20px;padding:12px;background:#0f172a;border-radius:8px;">
        <strong style="color:#94a3b8;">To fix:</strong>
        <pre style="color:#38bdf8;margin:8px 0 0 0;font-size:13px;">1. On your Windows PC:
   cd ~/vendor-scrapers/pfg
   python export_session.py

2. Log in when the browser opens

3. Close the browser after login completes

4. Transfer the session:
   scp -r -P 2222 browser_profile/ rednun@ssh.rednun.com:~/vendor-scrapers/pfg/browser_profile/</pre>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">
        The scraper will automatically resume on the next cron run once the session is refreshed.
    </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Red Nun] PFG session expired — login needed"
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

    # Also check URL for login redirects
    url = page.url.lower()
    if "login" in url or "signin" in url or "auth" in url:
        log(f"Session expired — redirected to login URL: {page.url}")
        return False

    return True


async def auto_login(page) -> bool:
    """Attempt auto-login when session has expired. Reads credentials from ~/vendor-scrapers/.env."""
    load_scraper_env()

    username = os.getenv("PFG_USER", "")
    password = os.getenv("PFG_PASS", "")
    if not username or not password:
        log("No credentials found in .env (PFG_USER/PFG_PASS) — cannot auto-login")
        return False

    log(f"Attempting auto-login as {username}...")
    try:
        await page.goto(PFG_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # PFG uses Azure B2C login — field id="signInName" is type="text", not "email"
        email_field = await page.query_selector(
            'input#signInName, input[type="email"], input[name="email"], '
            'input[name="username"], input[name="login"], input[id="email"], '
            'input[id="username"], input[type="text"]:not([name=""])'
        )
        password_field = await page.query_selector('input[type="password"]')

        if not password_field:
            log("  No password field found on page — cannot auto-login")
            return False

        if email_field:
            await email_field.click()
            await email_field.fill(username)
            log(f"  Filled username: {username}")
        else:
            log("  [WARN] No username field found — trying password-only")

        await password_field.click()
        await password_field.fill(password)
        log("  Filled password")

        submitted = False
        for btn_selector in [
            'button#next',  # PFG-specific submit button id
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Sign in")', 'button:has-text("Log In")',
            'button:has-text("Sign In")', 'button:has-text("Login")',
            'button:has-text("Submit")',
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


# ─── LOCATION SWITCHER ──────────────────────────────────────────────────────


async def select_location(page, company_name: str, location: str) -> bool:
    """
    Select a location using the top-left location menu (map icon).
    testid="location-menu-btn" opens a dropdown; click the target location.
    Returns True if successful.
    """
    log(f"  Selecting location: {company_name}...")

    target_lower = company_name.lower()

    # Check if already on this location via the button text
    loc_btn = page.locator('[data-testid="location-menu-btn"]')
    if await loc_btn.count() > 0:
        btn_text = (await loc_btn.inner_text()).strip().lower()
        if target_lower in btn_text:
            log(f"  Already on {company_name}")
            return True

    # Click to open location dropdown
    await loc_btn.click()
    await page.wait_for_timeout(1500)

    # Find and click target location in the dropdown
    loc_menu = page.locator('[data-testid="location-menu"]')
    if await loc_menu.count() == 0:
        log("  [WARN] Location menu did not open")
        await page.keyboard.press("Escape")
        return False

    # Get all clickable items in the location menu
    menu_items = await loc_menu.evaluate("""
        el => {
            const results = [];
            const divs = el.querySelectorAll('div, li, a, button, [role="menuitem"]');
            for (const item of divs) {
                const text = (item.innerText || '').trim().toLowerCase();
                const rect = item.getBoundingClientRect();
                if (rect.height > 20 && rect.height < 200 && rect.width > 100 && text.length > 10) {
                    results.push({
                        text: text.substring(0, 120),
                        top: Math.round(rect.top),
                        height: Math.round(rect.height),
                    });
                }
            }
            return results;
        }
    """)

    # Find the target location item by Y-position matching
    target_y = None
    for item in menu_items:
        if target_lower in item["text"]:
            target_y = item["top"]
            break

    if target_y is None:
        log(f"  [WARN] Could not find \"{company_name}\" in location menu")
        await page.keyboard.press("Escape")
        return False

    # Click the target location using coordinates
    box = await loc_menu.bounding_box()
    if box:
        click_x = box["x"] + box["width"] / 2
        click_y = target_y + 30  # Middle of the item
        await page.mouse.click(click_x, click_y)
        log(f"  Switched to {company_name}")
        await page.wait_for_timeout(3000)
        return True

    await page.keyboard.press("Escape")
    return False


# ─── INVOICE NAVIGATION ─────────────────────────────────────────────────────


async def navigate_to_invoices(page):
    """
    Navigate to the invoice list page via the top nav dropdown.
    PFG uses React/MUI — must use Playwright click() (not evaluate) for proper
    event dispatch.
    """
    log("  Navigating to Invoices...")

    # Dismiss any lingering dialogs/modals first
    for _ in range(3):
        dialog = page.locator('[role="dialog"], [role="presentation"]')
        if await dialog.count() > 0:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        else:
            break
    # If still blocked, remove via JS
    await page.evaluate("""
        () => {
            document.querySelectorAll('[role="presentation"]').forEach(el => {
                if (el.querySelector('.MuiDialog-container')) el.remove();
            });
            document.querySelectorAll('.MuiBackdrop-root').forEach(el => el.remove());
        }
    """)

    invoices_btn = page.locator('button:has-text("Invoices")')
    if await invoices_btn.count() == 0:
        log("  [WARN] Could not find 'Invoices' nav button")
        return False

    await invoices_btn.click()
    await page.wait_for_timeout(1500)

    menu_item = page.locator('.cf-menu-item:has-text("Invoices")').first
    if await menu_item.count() > 0:
        await menu_item.click()
        log("  Clicked 'Invoices' menu item")
    else:
        all_invoices = page.locator('text="Invoices"')
        count = await all_invoices.count()
        clicked = False
        for i in range(count):
            box = await all_invoices.nth(i).bounding_box()
            if box and box['y'] > 70:
                await all_invoices.nth(i).click()
                log(f"  Clicked 'Invoices' at y={int(box['y'])}")
                clicked = True
                break
        if not clicked:
            log("  [WARN] Could not find 'Invoices' in dropdown menu")
            return False

    await page.wait_for_timeout(5000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    log(f"  URL: {page.url}")
    return True


# ─── DATE FILTER ─────────────────────────────────────────────────────────────


async def expand_date_range(page) -> bool:
    """
    Expand the invoice date filter from default (e.g., 'Last 30 days') to all of 2026.
    Clicks the date dropdown button and looks for a broader date range option.
    Returns True if successfully expanded.
    """
    log("  Expanding date range...")

    # Click the date dropdown button
    date_btn = page.locator('[data-testid="invoices-date-dropdown-select-btn"]')
    if await date_btn.count() == 0:
        log("  [WARN] No date dropdown button found")
        return False

    await date_btn.click()
    await page.wait_for_timeout(2000)

    # Look for the date range input and try to find/select a broader range
    # Strategy 1: Look for MUI menu items with date range options
    broader_options = [
        "Year to Date", "Year to date", "YTD",
        "All", "All time", "All invoices",
        "Last 365 days", "Last 12 months", "Last year",
        "Custom", "Custom Range", "Custom range",
    ]
    for option_text in broader_options:
        option = page.locator(f'[role="menuitem"]:has-text("{option_text}"), '
                              f'[role="option"]:has-text("{option_text}"), '
                              f'li:has-text("{option_text}")')
        if await option.count() > 0:
            await option.first.click()
            log(f"  Selected date range: {option_text}")
            await page.wait_for_timeout(3000)
            return True

    # Strategy 2: Look for any MUI select/dropdown options that appeared
    options = await page.evaluate("""
        () => {
            const results = [];
            const items = document.querySelectorAll(
                '[role="menuitem"], [role="option"], [class*="MuiMenuItem"], li[class*="MuiList"]'
            );
            for (const item of items) {
                const rect = item.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    results.push({
                        text: (item.innerText || '').trim().substring(0, 80),
                        cls: (item.className || '').substring(0, 60),
                    });
                }
            }
            return results;
        }
    """)
    if options:
        log(f"  Date dropdown options: {[o['text'] for o in options]}")
        # Pick the broadest available option
        for opt in options:
            text = opt['text'].lower()
            if any(k in text for k in ['year', 'all', '365', '12 month', 'ytd']):
                await page.locator(f'text="{opt["text"]}"').first.click()
                log(f"  Selected: {opt['text']}")
                await page.wait_for_timeout(3000)
                return True

    # Strategy 3: If the date input is a text field, try clearing it and typing a date range
    date_input = page.locator('input[value="Last 30 days"], input[value*="Last"]')
    if await date_input.count() > 0:
        try:
            await date_input.click()
            await date_input.fill("")
            await page.wait_for_timeout(500)
            # Look for options that appeared after clearing
            log("  Cleared date input — looking for options...")
        except Exception:
            pass

    # Dismiss and continue with default range
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    log("  [WARN] Could not expand date range — using default")
    return False


# ─── INVOICE LIST SCRAPING ───────────────────────────────────────────────────


async def scrape_invoice_list(page) -> list[dict]:
    """
    Scrape invoice rows from the current page of the PFG invoice list.
    PFG uses div-based rows (MUI), not table rows.
    Returns a list of dicts with invoice_number, date, amount, doc_type.
    """
    invoices = await page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();
            const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(l => l);

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                if (!line.match(/^\\d{6,10}$/)) continue;
                const invNum = line;
                if (seen.has(invNum)) continue;
                seen.add(invNum);

                let docType = '', date = '', amount = '';
                for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                    const next = lines[j];
                    if (next === 'Invoice' || next === 'Credit') {
                        docType = next.toLowerCase();
                    } else if (next.match(/^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/)) {
                        date = next;
                    } else if (next.match(/^-?\\$[\\d,]+\\.\\d{2}$/)) {
                        amount = next.replace('$', '').replace(',', '');
                    }
                }

                results.push({
                    invoice_number: invNum,
                    date: date,
                    amount: amount,
                    doc_type: docType,
                });
            }
            return results;
        }
    """)
    return invoices


async def scrape_all_invoice_numbers(page) -> list[dict]:
    """Scrape all invoice numbers across all pagination pages."""
    all_invoices = []
    page_num = 1

    while True:
        invoices = await scrape_invoice_list(page)
        all_invoices.extend(invoices)
        log(f"  Page {page_num}: {len(invoices)} invoices (total: {len(all_invoices)})")

        # Look for next page button (MUI pagination)
        next_btn = page.locator(
            'button[aria-label="Go to next page"], '
            'button[aria-label="next page"], '
            '[class*="MuiPagination"] button:last-child'
        )
        if await next_btn.count() > 0:
            is_disabled = await next_btn.first.evaluate("el => el.disabled")
            if is_disabled:
                break
            await next_btn.first.click()
            await page.wait_for_timeout(3000)
            page_num += 1
        else:
            break

    return all_invoices


# ─── CSV EXPORT ──────────────────────────────────────────────────────────────


async def select_invoices_by_number(page, invoice_numbers: list[str]) -> int:
    """
    Check individual checkboxes for specific invoices.
    Iterates each checkbox, evaluates its invoice number inline, scrolls into view, clicks.
    Returns count of successfully selected.
    """
    selected = 0
    target_set = set(invoice_numbers)

    all_cbs = page.locator('[data-testid="invoices-table-checkbox"]')
    cb_count = await all_cbs.count()

    for i in range(cb_count):
        cb = all_cbs.nth(i)
        # Determine which invoice this checkbox belongs to
        inv_num = await cb.evaluate("""
            el => {
                let parent = el;
                for (let j = 0; j < 10; j++) {
                    parent = parent?.parentElement;
                    if (!parent) return null;
                    // Look for invoice number pattern in the row text
                    const text = (parent.innerText || '');
                    const match = text.match(/(\\d{6,10})/);
                    if (match) return match[1];
                }
                return null;
            }
        """)
        if inv_num and inv_num in target_set:
            is_checked = await cb.evaluate(
                "el => el.querySelector('input')?.checked || false"
            )
            if not is_checked:
                await cb.scroll_into_view_if_needed()
                await cb.click()
                selected += 1
                log(f"    Checked #{inv_num}")
                await page.wait_for_timeout(300)

    return selected


async def export_csv(page, location: str) -> Path | None:
    """
    Click 'Export to CSV', configure the export modal, download the CSV.
    Returns path to downloaded CSV file or None on failure.
    """
    log("  Exporting to CSV...")

    # Click "Export to CSV" button
    export_btn = page.locator('[data-testid="invoices-export-invoices-button"]')
    if await export_btn.count() == 0:
        log("  [WARN] No 'Export to CSV' button found")
        return None

    is_disabled = await export_btn.evaluate("el => el.disabled")
    if is_disabled:
        log("  [WARN] 'Export to CSV' button is disabled (no invoices selected?)")
        return None

    await export_btn.click()
    await page.wait_for_timeout(2000)

    # Verify modal opened
    modal = page.locator('[role="dialog"], [role="presentation"]')
    if await modal.count() == 0:
        log("  [WARN] Export modal did not open")
        return None

    # Step 1: Ensure "Display column headers" is ON
    # The toggle with testid="switch-invoice-export-modal" should be checked
    headers_switch = page.locator('[data-testid="switch-invoice-export-modal"]')
    if await headers_switch.count() > 0:
        is_checked = await headers_switch.evaluate("""
            el => {
                const input = el.querySelector('input[type="checkbox"]') || el.closest('label')?.querySelector('input');
                return input?.checked || false;
            }
        """)
        if not is_checked:
            await headers_switch.click()
            log("    Turned ON 'Display column headers'")
            await page.wait_for_timeout(500)

    # Step 2: Click "Select all" for FIELDS (not invoices)
    select_all_btn = page.locator('[role="dialog"] button:has-text("Select all"), '
                                   '[role="presentation"] button:has-text("Select all")').first
    if await select_all_btn.count() > 0:
        await select_all_btn.click()
        log("    Selected all fields")
        await page.wait_for_timeout(1000)
    else:
        log("  [WARN] Could not find 'Select all' button in export modal")

    # Step 3: Click "Download"
    download_btn = page.locator('[role="dialog"] button:has-text("Download"), '
                                 '[role="presentation"] button:has-text("Download")').first
    if await download_btn.count() == 0:
        log("  [WARN] No Download button found in modal")
        await page.keyboard.press("Escape")
        return None

    is_disabled = await download_btn.evaluate("el => el.disabled")
    if is_disabled:
        log("  [WARN] Download button is disabled (no fields selected?)")
        await page.keyboard.press("Escape")
        return None

    try:
        async with page.expect_download(timeout=30000) as dl_info:
            await download_btn.click()
        download = await dl_info.value

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = DOWNLOAD_DIR / f"pfg_{location}_{timestamp}.csv"
        await download.save_as(str(dest))
        log(f"  Downloaded CSV: {dest.name} ({dest.stat().st_size} bytes)")

        # Close the export modal so it doesn't block subsequent actions
        await page.wait_for_timeout(1000)
        # Try multiple dismiss strategies
        for attempt in range(5):
            modal_still = page.locator('[data-testid="modal-invoice-export"]')
            if await modal_still.count() == 0:
                break
            if attempt == 0:
                await page.keyboard.press("Escape")
            elif attempt == 1:
                # Click the MUI backdrop
                backdrop = page.locator('.MuiBackdrop-root').first
                if await backdrop.count() > 0:
                    await backdrop.click(force=True)
            elif attempt == 2:
                # Try clicking a close button
                close_btn = page.locator('[data-testid="modal-invoice-export"] button[aria-label="close"], '
                                          '[data-testid="modal-invoice-export"] .MuiDialogTitle-root button').first
                if await close_btn.count() > 0:
                    await close_btn.click()
                else:
                    await page.keyboard.press("Escape")
            else:
                # Nuclear: remove the modal from DOM
                await page.evaluate("""
                    () => {
                        const modal = document.querySelector('[data-testid="modal-invoice-export"]');
                        if (modal) modal.remove();
                        // Also remove any backdrop
                        const backdrops = document.querySelectorAll('.MuiBackdrop-root');
                        backdrops.forEach(b => b.remove());
                    }
                """)
            await page.wait_for_timeout(500)

        return dest

    except PlaywrightTimeoutError:
        log("  [ERROR] CSV download timed out")
        await page.keyboard.press("Escape")
        return None
    except Exception as e:
        log(f"  [ERROR] CSV download failed: {e}")
        await page.keyboard.press("Escape")
        return None


def import_csv_to_dashboard(csv_path: Path, location: str) -> dict:
    """
    Import a PFG CSV via the dashboard API.
    Returns response dict with import results.
    """
    log(f"  Importing CSV to dashboard (location={location})...")
    try:
        with open(csv_path, 'rb') as f:
            r = requests.post(
                f"{DASHBOARD_API}/api/invoices/import-csv",
                files={"file": (csv_path.name, f, "text/csv")},
                params={"location": location, "vendor": "pfg"},
                timeout=60,
            )
        if r.status_code == 200:
            data = r.json()
            imported = data.get("count", 0)
            dupes = data.get("duplicates", 0)
            log(f"  Import result: {imported} imported, {dupes} duplicates")
            return data
        else:
            log(f"  [ERROR] Import failed: HTTP {r.status_code} — {r.text[:200]}")
            return {"error": r.text[:200]}
    except Exception as e:
        log(f"  [ERROR] Import failed: {e}")
        return {"error": str(e)}


# ─── PDF FALLBACK ────────────────────────────────────────────────────────────


async def pdf_fallback_download(page, invoice_number: str, location: str) -> Path | None:
    """
    Download a single invoice PDF via the three-dot (kebab) menu.
    Flow: find kebab button for this row → click → "Download invoice" → popup → Download.
    """
    log(f"    PDF fallback for #{invoice_number}...")

    try:
        # Find the row containing this invoice number, then its kebab button
        # Each row has testid like "invoice-header-{uuid}"
        # Kebab buttons have testid="invoices-kebab-button"
        inv_text = page.locator(f'text="{invoice_number}"').first
        if await inv_text.count() == 0:
            log(f"    [WARN] Invoice #{invoice_number} not found on page")
            return None

        # Get the bounding box of the invoice number to find the right kebab
        inv_box = await inv_text.bounding_box()
        if not inv_box:
            return None

        # Find the kebab button at the same Y position
        kebabs = page.locator('[data-testid="invoices-kebab-button"]')
        kebab_count = await kebabs.count()
        clicked_kebab = False

        for i in range(kebab_count):
            box = await kebabs.nth(i).bounding_box()
            if box and abs(box['y'] - inv_box['y']) < 30:
                await kebabs.nth(i).click()
                clicked_kebab = True
                break

        if not clicked_kebab:
            log(f"    [WARN] Could not find kebab menu for #{invoice_number}")
            return None

        await page.wait_for_timeout(1500)

        # Click "Download invoice" from the popup menu
        dl_item = page.locator('[data-testid="invoice-grid-menu-item-0"], '
                                '[role="menuitem"]:has-text("Download invoice")')
        if await dl_item.count() == 0:
            log(f"    [WARN] No 'Download invoice' menu item")
            await page.keyboard.press("Escape")
            return None

        await dl_item.first.click()
        await page.wait_for_timeout(3000)

        # A popup/dialog with Print and Download should appear
        # Look for a Download button in the popup
        try:
            async with page.expect_download(timeout=30000) as dl_info:
                popup_dl = page.locator('[role="dialog"] button:has-text("Download"), '
                                         'button:has-text("Download"):not([data-testid="invoices-download-pdf-invoices-button"])')
                if await popup_dl.count() > 0:
                    await popup_dl.first.click()
                else:
                    log(f"    [WARN] No Download button in invoice popup")
                    await page.keyboard.press("Escape")
                    return None

            download = await dl_info.value
            date_stamp = datetime.now().strftime("%Y%m%d")
            dest = DOWNLOAD_DIR / f"pfg_{location}_{invoice_number}_{date_stamp}.pdf"
            await download.save_as(str(dest))
            log(f"    [OK] PDF #{invoice_number} → {dest.name}")

            # Copy to OCR pipeline for PDF processing
            if INVOICE_IMAGES_DIR.exists():
                try:
                    shutil.copy2(str(dest), str(INVOICE_IMAGES_DIR / dest.name))
                    log(f"    Copied to OCR pipeline")
                except Exception:
                    pass

            return dest

        except PlaywrightTimeoutError:
            log(f"    [TIMEOUT] PDF download for #{invoice_number}")
            await page.keyboard.press("Escape")
            return None

    except Exception as e:
        log(f"    [ERROR] PDF fallback for #{invoice_number}: {e}")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return None


# ─── SCRAPE COMPANY ──────────────────────────────────────────────────────────


async def scrape_company(page, company_name: str, location: str, already_downloaded: set) -> list:
    """Scrape and export invoices for a single PFG location via CSV export."""
    log(f"\n{'─' * 60}")
    log(f"COMPANY: {company_name} ({location})")
    log(f"{'─' * 60}")

    # Dismiss any lingering modals/dialogs from previous run
    for _ in range(3):
        modal = page.locator('[role="dialog"], [role="presentation"]')
        if await modal.count() > 0:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        else:
            break

    # Step 1: Select this location via top-left location menu
    selected = await select_location(page, company_name, location)
    if not selected:
        log(f"  [WARN] Could not select location {company_name} — trying anyway")

    # Step 2: Navigate to invoice list
    nav_ok = await navigate_to_invoices(page)
    if not nav_ok:
        log("  [ERROR] Could not navigate to invoices page")
        return []

    # Step 3: Expand date range to show all of 2026
    await expand_date_range(page)

    # Step 4: Scrape all invoice numbers across all pages
    all_invoices = await scrape_all_invoice_numbers(page)
    log(f"  Total invoices found: {len(all_invoices)}")

    # Step 5: Filter by year and dedup
    current_year = [inv for inv in all_invoices if is_invoice_year_ok(inv.get("date", ""))]
    skipped_old = len(all_invoices) - len(current_year)
    if skipped_old:
        log(f"  Skipped {skipped_old} pre-{MIN_YEAR} invoices")

    new_invoices = [inv for inv in current_year if inv["invoice_number"] not in already_downloaded]
    log(f"  New invoices: {len(new_invoices)}")

    if not new_invoices:
        log("  All caught up!")
        return []

    for inv in new_invoices:
        log(f"    #{inv['invoice_number']} ({inv.get('date', '?')}, {inv.get('doc_type', '?')}, ${inv.get('amount', '?')})")

    # Step 6: Select only new invoices' checkboxes
    selected_count = await select_invoices_by_number(page, [inv["invoice_number"] for inv in new_invoices])
    log(f"  Selected {selected_count} invoices for export")

    if selected_count == 0:
        log("  [WARN] Could not select any invoices — trying PDF fallback")
        downloaded = []
        for inv in new_invoices:
            pdf = await pdf_fallback_download(page, inv["invoice_number"], location)
            if pdf:
                downloaded.append(inv["invoice_number"])
                already_downloaded.add(inv["invoice_number"])
                save_downloaded_invoices(already_downloaded)
            await page.wait_for_timeout(1500)
        return downloaded

    # Step 7: Export to CSV
    csv_path = await export_csv(page, location)
    if not csv_path:
        log("  [WARN] CSV export failed — trying PDF fallback for each invoice")
        downloaded = []
        for inv in new_invoices:
            pdf = await pdf_fallback_download(page, inv["invoice_number"], location)
            if pdf:
                downloaded.append(inv["invoice_number"])
                already_downloaded.add(inv["invoice_number"])
                save_downloaded_invoices(already_downloaded)
            await page.wait_for_timeout(1500)
        return downloaded

    # Step 8: Import CSV via dashboard API
    result = import_csv_to_dashboard(csv_path, location)

    # Track imported invoices
    imported = []
    if "invoices" in result:
        for inv_result in result["invoices"]:
            inv_num = inv_result.get("invoice_number", "")
            if inv_result.get("status") in ("auto_confirmed", "duplicate"):
                imported.append(inv_num)
                already_downloaded.add(inv_num)
    elif not result.get("error"):
        # Single-invoice response (shouldn't happen with PFG but handle it)
        inv_num = result.get("invoice_number", "")
        if inv_num:
            imported.append(inv_num)
            already_downloaded.add(inv_num)

    if imported:
        save_downloaded_invoices(already_downloaded)

    # Step 9: PDF fallback for any invoices that failed CSV import
    csv_imported_set = set(imported)
    failed_invoices = [inv for inv in new_invoices if inv["invoice_number"] not in csv_imported_set]
    if failed_invoices:
        log(f"  {len(failed_invoices)} invoices not in CSV — trying PDF fallback")
        for inv in failed_invoices:
            pdf = await pdf_fallback_download(page, inv["invoice_number"], location)
            if pdf:
                imported.append(inv["invoice_number"])
                already_downloaded.add(inv["invoice_number"])
                save_downloaded_invoices(already_downloaded)
            await page.wait_for_timeout(1500)

    return imported


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    load_env()

    log(f"{'=' * 60}")
    log(f"PFG Invoice Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")
    log(f"Companies: {', '.join(COMPANIES.keys())}")

    # Ensure directories exist
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Load previously downloaded invoices (local tracking)
    already_downloaded = load_downloaded_invoices()
    log(f"Previously downloaded (local): {len(already_downloaded)} invoices")

    # Check dashboard API for already-imported invoices
    dashboard_existing = get_dashboard_existing_invoices()
    already_downloaded.update(dashboard_existing)
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

        # Inject cookies from storage_state.json if available
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

        # Navigate to PFG
        log("\nNavigating to PFG portal...")
        try:
            await page.goto(PFG_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            log("[ERROR] PFG portal timed out")
            update_vendor_session_status("expired", failure_reason="timeout")
            await context.close()
            sys.exit(1)

        # Check session health — try auto-login if expired
        session_ok = await check_session_health(page)
        if not session_ok:
            log("\nSESSION EXPIRED — attempting auto-login...")
            login_ok = await auto_login(page)
            if not login_ok:
                update_vendor_session_status("expired", failure_reason="session_expired")
                send_session_expired_alert()
                await context.close()
                sys.exit(1)
            else:
                update_vendor_session_status("healthy")

        # Process each company
        total_downloaded = []
        for company_name, location in COMPANIES.items():
            downloaded = await scrape_company(page, company_name, location, already_downloaded)
            total_downloaded.extend(downloaded)

        # Update session status
        update_vendor_session_status("healthy", invoices_scraped=len(total_downloaded))

        # Summary
        log(f"\n{'=' * 60}")
        log(f"SUMMARY")
        log(f"{'=' * 60}")
        log(f"  Companies scraped:       {len(COMPANIES)}")
        log(f"  Successfully imported:   {len(total_downloaded)}")
        log(f"  Total tracked:           {len(already_downloaded)}")
        if total_downloaded:
            log(f"  New invoices: {', '.join(total_downloaded)}")
        log(f"{'=' * 60}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
