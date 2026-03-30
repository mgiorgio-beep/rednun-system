#!/usr/bin/env python3
"""
Southern Glazer's Invoice Scraper — Core Logic
===============================================
Shared by scraper_chatham.py and scraper_dennis.py.

Portal: https://portal2.ftnirdc.com/en/72752
Two separate logins → two separate browser profiles:
  - mike@rednun.com → Chatham (browser_profile_chatham/)
  - mgiorgio@rednun.com → Dennis (browser_profile_dennis/)

Flow:
  1. Login → lands on invoice list page
  2. Click invoice number → opens NEW BROWSER TAB with PDF
  3. Download/save PDF from the new tab
  4. Close new tab, return to invoice list
  5. Repeat for each new invoice

Downloaded PDFs are copied to /opt/rednun/invoice_images/ for OCR pipeline.

Requirements:
    pip install playwright requests
    playwright install chromium
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

PORTAL_URL = "https://portal2.ftnirdc.com/en/72752"
VENDOR_NAME = "Southern Glazer's Beverage Company"
MIN_YEAR = 2026  # Only download invoices from 2026 onward

# Where to copy PDFs for the dashboard OCR pipeline
INVOICE_IMAGES_DIR = Path(os.getenv("INVOICE_IMAGES_DIR", "/opt/rednun/invoice_images"))

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
                    if key and val:  # Only set if value is non-empty
                        os.environ[key] = val


def log(location, msg):
    print(f"[SG-{location}] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


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


def load_downloaded_invoices(data_dir: Path) -> set:
    """Load the set of invoice numbers we've already downloaded."""
    log_path = data_dir / "downloaded_invoices.json"
    if log_path.exists():
        try:
            with open(log_path, "r") as f:
                data = json.load(f)
                return set(data.get("invoices", []))
        except Exception:
            pass
    return set()


def save_downloaded_invoices(data_dir: Path, invoice_set: set):
    """Persist the set of downloaded invoice numbers."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "downloaded_invoices.json"
    with open(log_path, "w") as f:
        json.dump({
            "invoices": sorted(invoice_set),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)


def get_dashboard_existing_invoices(location: str) -> set:
    """Query dashboard API for already-imported Southern Glazer's invoice numbers."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": "Southern Glazer"},
            timeout=10,
        )
        if r.status_code == 200:
            nums = set(r.json().get("invoice_numbers", []))
            log(location, f"Dashboard has {len(nums)} existing Southern Glazer's invoices")
            return nums
        else:
            log(location, f"[WARN] Dashboard API returned {r.status_code} — skipping dedup check")
    except requests.exceptions.ConnectionError:
        log(location, "[WARN] Dashboard API not reachable — skipping dedup check")
    except Exception as e:
        log(location, f"[WARN] Dashboard dedup check failed: {e}")
    return set()


def update_vendor_session_status(location, status, failure_reason=None, invoices_scraped=0):
    """Update vendor_session_status table via dashboard API.
    Uses location-specific vendor name so Chatham and Dennis are tracked separately."""
    session_name = f"{VENDOR_NAME} ({location})"
    try:
        r = requests.post(
            f"{DASHBOARD_API}/api/vendor-sessions/update",
            json={
                "vendor_name": session_name,
                "status": status,
                "failure_reason": failure_reason,
                "invoices_scraped_last_run": invoices_scraped,
            },
            timeout=10,
        )
        if r.status_code == 200:
            log(location, f"Session status updated: {status}")
        else:
            log(location, f"[WARN] Session status update returned {r.status_code}")
    except Exception as e:
        log(location, f"[WARN] Could not update session status: {e}")


def send_session_expired_alert(location: str, login_email: str):
    """Send email alert when Southern Glazer's session has expired."""
    load_env()
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        log(location, "[ERROR] Cannot send alert — GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set")
        return False

    now = datetime.now().strftime("%I:%M %p on %A, %B %d")
    profile_dir = f"browser_profile_{location}"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;background:#020617;color:#e2e8f0;padding:20px;margin:0;">
<div style="max-width:600px;margin:0 auto;">
    <div style="background:#451a03;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin-bottom:20px;">
        <h2 style="color:#f59e0b;margin:0 0 8px 0;">Southern Glazer's Session Expired ({location.title()})</h2>
        <p style="margin:0;">The Southern Glazer's scraper for <strong>{location.title()}</strong> cannot access the portal because the login session has expired.</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Vendor</td>
        <td style="padding:8px 0;">{VENDOR_NAME}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Location</td>
        <td style="padding:8px 0;">{location.title()}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Login</td>
        <td style="padding:8px 0;">{login_email}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Portal</td>
        <td style="padding:8px 0;">{PORTAL_URL}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Detected</td>
        <td style="padding:8px 0;">{now}</td>
    </tr>
    </table>
    <div style="margin-top:20px;padding:12px;background:#0f172a;border-radius:8px;">
        <strong style="color:#94a3b8;">To fix:</strong>
        <pre style="color:#38bdf8;margin:8px 0 0 0;font-size:13px;">1. On your Windows PC:
   cd ~/vendor-scrapers/southern-glazers
   python export_session_{location}.py

2. Log in as {login_email} when the browser opens

3. Close the browser after login completes

4. Transfer the session:
   scp -r -P 2222 {profile_dir}/ rednun@ssh.rednun.com:~/vendor-scrapers/southern-glazers/{profile_dir}/</pre>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">
        The scraper will automatically resume on the next cron run once the session is refreshed.
    </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Red Nun] Southern Glazer's ({location.title()}) session expired — login needed"
    msg["From"] = gmail_user
    msg["To"] = ALERT_RECIPIENT
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, [ALERT_RECIPIENT], msg.as_string())
        log(location, f"Session expired alert sent to {ALERT_RECIPIENT}")
        return True
    except Exception as e:
        log(location, f"[ERROR] Could not send alert: {e}")
        return False


def copy_to_ocr_pipeline(pdf_path: Path, location: str) -> bool:
    """Copy a downloaded PDF to the dashboard invoice_images/ dir for OCR processing."""
    if not INVOICE_IMAGES_DIR.exists():
        log(location, f"  [WARN] OCR pipeline dir not found: {INVOICE_IMAGES_DIR}")
        return False
    try:
        dest = INVOICE_IMAGES_DIR / pdf_path.name
        shutil.copy2(str(pdf_path), str(dest))
        log(location, f"  Copied to OCR pipeline: {dest.name}")
        return True
    except Exception as e:
        log(location, f"  [WARN] Could not copy to OCR pipeline: {e}")
        return False


# ─── SESSION HEALTH CHECK ────────────────────────────────────────────────────


async def check_session_health(page, location: str) -> bool:
    """Returns True if session is valid, False if login page detected."""
    # Check for a VISIBLE password field — most reliable indicator.
    # SG portal login page has input[name="lpassword"] visible.
    # Don't use generic "button with Login text" — invoice page may also have one.
    login_indicators = [
        'input[name="lpassword"]',       # SG-specific login password
        'input[name="luserName"]',       # SG-specific login username
        'input[type="password"]',        # Generic fallback
        'form[action*="signin"]',
        'form[action*="auth"]',
    ]
    for selector in login_indicators:
        try:
            el = await page.query_selector(selector)
            if el:
                # Verify element is actually visible (hidden registration forms etc.)
                box = await el.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    log(location, f"Session expired — detected visible login indicator: {selector}")
                    return False
        except Exception:
            pass

    # Don't check URL for "login" — SG portal URL doesn't include "login" when expired,
    # and the portal URL may contain it in other contexts.
    return True


async def auto_login(page, location: str, login_email: str) -> bool:
    """
    Attempt to log in via form fill when session has expired.
    Reads credentials from ~/vendor-scrapers/.env.
    Returns True if login succeeded, False otherwise.
    """
    load_scraper_env()

    # Map location to env var names
    env_key = f"SG_{location.upper()}_PASS"
    password = os.getenv(env_key, "")
    if not password:
        log(location, f"No password found in .env for {env_key} — cannot auto-login")
        return False

    log(location, f"Attempting auto-login as {login_email}...")

    try:
        # Navigate to portal login page
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Look for username field — SG portal uses input[name="luserName"]
        email_field = None
        for sel in [
            'input[name="luserName"]', 'input[id="luserName"]',
            'input[type="email"]', 'input[name="email"]',
            'input[name="username"]', 'input[name="login"]',
            'input[id="email"]', 'input[id="username"]',
            'input[placeholder*="ser"]', 'input[placeholder*="mail"]',
        ]:
            el = await page.query_selector(sel)
            if el:
                box = await el.bounding_box()
                if box and box["width"] > 0:
                    email_field = el
                    log(location, f"  Found username field: {sel}")
                    break

        # Look for password field — SG portal uses input[name="lpassword"]
        password_field = None
        for sel in ['input[name="lpassword"]', 'input[id="lpassword"]', 'input[type="password"]']:
            el = await page.query_selector(sel)
            if el:
                box = await el.bounding_box()
                if box and box["width"] > 0:
                    password_field = el
                    break

        if not password_field:
            log(location, "  No password field found on page — cannot auto-login")
            return False

        # Fill username
        if email_field:
            await email_field.click()
            await email_field.fill(login_email)
            log(location, f"  Filled username: {login_email}")
        else:
            log(location, "  [WARN] No username field found — trying password only")

        # Fill password
        await password_field.click()
        await password_field.fill(password)
        log(location, "  Filled password")

        # Submit — try multiple strategies
        submitted = False

        # Strategy 1: Click submit/login button
        # SG portal uses button#dologinbutton (type=submit, text="Login")
        for btn_selector in [
            'button#dologinbutton',
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'button:has-text("Login")',
            'button:has-text("Submit")',
        ]:
            btn = await page.query_selector(btn_selector)
            if btn:
                await btn.click()
                submitted = True
                log(location, f"  Clicked submit: {btn_selector}")
                break

        # Strategy 2: Press Enter in password field
        if not submitted:
            await password_field.press("Enter")
            submitted = True
            log(location, "  Pressed Enter in password field")

        # Wait for navigation/redirect
        await page.wait_for_timeout(8000)

        # Check if login succeeded (no more login indicators)
        session_ok = await check_session_health(page, location)
        if session_ok:
            log(location, "  Auto-login SUCCEEDED!")
            # Save the new session state
            try:
                storage_state_file = Path(f"./storage_state_{location}.json")
                cookies = await page.context.cookies()
                session_storage = await page.evaluate("() => { const d = {}; for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); d[k] = sessionStorage.getItem(k); } return d; }")
                local_storage = await page.evaluate("() => { const d = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); d[k] = localStorage.getItem(k); } return d; }")
                state = {"cookies": cookies, "sessionStorage": session_storage, "localStorage_manual": local_storage}
                with open(storage_state_file, "w") as f:
                    json.dump(state, f, indent=2)
                log(location, f"  Saved new session state to {storage_state_file.name}")
            except Exception as e:
                log(location, f"  [WARN] Could not save session state: {e}")
            return True
        else:
            log(location, "  Auto-login FAILED — still on login page")
            return False

    except Exception as e:
        log(location, f"  Auto-login error: {e}")
        return False


# ─── INVOICE LIST ────────────────────────────────────────────────────────────


async def scrape_invoice_list(page, location: str) -> list[dict]:
    """
    Scrape invoice numbers and metadata from the invoice list page.
    Returns list of dicts with invoice_number, date, amount.
    """
    invoices = await page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();

            // Strategy 1: Table rows with clickable invoice numbers
            const rows = document.querySelectorAll('table tbody tr, tr, [class*="row"]');
            for (const row of rows) {
                const text = row.innerText || '';
                // Look for invoice number links in the row
                const links = row.querySelectorAll('a');
                for (const link of links) {
                    const linkText = link.innerText?.trim() || '';
                    // Invoice numbers are typically 6-10 digits
                    const numMatch = linkText.match(/^\\d{5,10}$/);
                    if (numMatch) {
                        const invNum = numMatch[0];
                        if (seen.has(invNum)) continue;
                        seen.add(invNum);

                        const dates = text.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g) || [];
                        const amounts = text.match(/-?\\$[\\d,]+\\.\\d{2}/g) || [];

                        results.push({
                            invoice_number: invNum,
                            date: dates[0] || '',
                            amount: amounts[0] ? amounts[0].replace('$', '').replace(',', '') : '',
                            row_text: text.substring(0, 300),
                        });
                        break;
                    }
                }

                // Fallback: look for standalone numbers in row text that look like invoice numbers
                if (results.length === 0 || !seen.has(text.match(/\\b(\\d{6,10})\\b/)?.[1])) {
                    const numMatch = text.match(/\\b(\\d{6,10})\\b/);
                    if (numMatch && !seen.has(numMatch[1])) {
                        const invNum = numMatch[1];
                        seen.add(invNum);
                        const dates = text.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g) || [];
                        const amounts = text.match(/-?\\$[\\d,]+\\.\\d{2}/g) || [];
                        results.push({
                            invoice_number: invNum,
                            date: dates[0] || '',
                            amount: amounts[0] ? amounts[0].replace('$', '').replace(',', '') : '',
                            row_text: text.substring(0, 300),
                        });
                    }
                }
            }

            // Strategy 2: If nothing found, scan all links for numeric text
            if (results.length === 0) {
                const allLinks = document.querySelectorAll('a');
                for (const link of allLinks) {
                    const linkText = link.innerText?.trim() || '';
                    const numMatch = linkText.match(/^\\d{5,10}$/);
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


async def _download_pdf_via_requests(context, pdf_url: str, dest: Path, location: str) -> bool:
    """Download a PDF URL directly via requests using the browser's session cookies.

    Chrome's built-in PDF viewer intercepts PDF URLs, so Playwright's
    response.body() returns the viewer HTML instead of the raw PDF.
    Using requests with the browser cookies bypasses this entirely.
    """
    try:
        cookies = await context.cookies()
        session = requests.Session()
        for c in cookies:
            session.cookies.set(c['name'], c['value'], domain=c.get('domain', ''))

        r = session.get(pdf_url, timeout=60, stream=True, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        })

        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest, 'wb') as f:
                f.write(r.content)
            log(location, f"    [OK] Downloaded PDF via requests ({len(r.content):,} bytes): {dest.name}")
            return True
        else:
            log(location, f"    [WARN] requests.get returned {r.status_code}, {len(r.content)} bytes")
            return False
    except Exception as e:
        log(location, f"    [WARN] requests download failed: {e}")
        return False


async def download_invoice_pdf(page, context, invoice_number: str, location: str, download_dir: Path) -> Path | None:
    """
    Download a single invoice as PDF by extracting the InvoiceId from AngularJS
    scope data and calling the SG API directly via requests.

    SG portal uses AngularJS — the invoice link has:
        ng-click="grid.appScope.invoicelist.openStatement(row.entity.InvoiceId)"
    which calls /api/GetExternalInvoice?invoiceid={id}&locale=en&token={jwt}

    We extract the InvoiceId and JWT from the page's Angular scope and
    sessionStorage, then download the PDF directly via requests.
    """
    try:
        log(location, f"  Downloading invoice #{invoice_number}...")

        date_stamp = datetime.now().strftime("%Y%m%d")
        dest = download_dir / f"sg_{location}_{invoice_number}_{date_stamp}.pdf"
        if dest.exists():
            seq = 2
            while dest.exists():
                dest = download_dir / f"sg_{location}_{invoice_number}_{date_stamp}_{seq}.pdf"
                seq += 1

        # ── Extract InvoiceId from Angular scope ───────────────────
        invoice_data = await page.evaluate("""
            (invoiceNum) => {
                const links = document.querySelectorAll('[ng-click*="openStatement"]');
                for (const link of links) {
                    if (link.innerText?.trim() === invoiceNum || link.title === invoiceNum) {
                        try {
                            const scope = angular.element(link).scope();
                            if (scope?.row?.entity?.InvoiceId) {
                                return { invoiceId: scope.row.entity.InvoiceId };
                            }
                        } catch(e) {}
                    }
                }
                return null;
            }
        """, invoice_number)

        if not invoice_data:
            log(location, f"    [WARN] Could not extract InvoiceId for #{invoice_number}")
            return None

        invoice_id = invoice_data['invoiceId']

        # ── Get auth token from sessionStorage ─────────────────────
        token = await page.evaluate("""
            () => {
                const authData = sessionStorage.getItem('authorizationData');
                return authData || null;
            }
        """)

        if not token:
            log(location, f"    [WARN] No auth token in sessionStorage")
            return None

        # ── Build API URL and download via requests ────────────────
        api_url = f"https://portal2.ftnirdc.com/api/GetExternalInvoice?invoiceid={invoice_id}&locale=en&token={token}"

        cookies = await context.cookies()
        session = requests.Session()
        for c in cookies:
            session.cookies.set(c['name'], c['value'], domain=c.get('domain', ''))

        r = session.get(api_url, timeout=60, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer': 'https://portal2.ftnirdc.com/',
        })

        if r.status_code == 200 and len(r.content) > 1000:
            ct = r.headers.get('content-type', '')
            if 'pdf' in ct.lower() or r.content[:5] == b'%PDF-':
                with open(dest, 'wb') as f:
                    f.write(r.content)
                log(location, f"    [OK] Downloaded PDF ({len(r.content):,} bytes): {dest.name}")
                return dest
            else:
                log(location, f"    [WARN] Response is not PDF (content-type: {ct}, size: {len(r.content)})")
        else:
            log(location, f"    [WARN] API returned {r.status_code}, {len(r.content)} bytes")

        log(location, f"    [FAIL] Could not download PDF for #{invoice_number}")
        return None

    except Exception as e:
        log(location, f"    [ERROR] Invoice #{invoice_number} — {e}")
        return None


# ─── MAIN SCRAPE LOGIC ───────────────────────────────────────────────────────


async def run_scraper(
    location: str,
    login_email: str,
    browser_profile_dir: Path,
    download_dir: Path,
    data_dir: Path,
):
    """Run the scraper for a single location/login."""
    load_env()

    log(location, f"{'=' * 60}")
    log(location, f"Southern Glazer's Scraper — {location.title()}")
    log(location, f"Login: {login_email}")
    log(location, f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(location, f"{'=' * 60}")

    # Ensure directories exist
    download_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    browser_profile_dir.mkdir(parents=True, exist_ok=True)

    # Load previously downloaded invoices (local tracking)
    already_downloaded = load_downloaded_invoices(data_dir)
    log(location, f"Previously downloaded (local): {len(already_downloaded)} invoices")

    # Check dashboard API for already-imported invoices
    dashboard_existing = get_dashboard_existing_invoices(location)
    already_downloaded.update(dashboard_existing)
    log(location, f"Combined dedup set: {len(already_downloaded)} entries")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_dir),
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            accept_downloads=True,
            viewport={"width": 1600, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # Load storage_state JSON if available (backup for persistent context)
        storage_state_file = Path(f"./storage_state_{location}.json")
        saved_state = None
        if storage_state_file.exists():
            try:
                with open(storage_state_file) as f:
                    saved_state = json.load(f)
                # Inject cookies before navigation
                cookies = saved_state.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    log(location, f"Injected {len(cookies)} cookies from {storage_state_file.name}")
            except Exception as e:
                log(location, f"[WARN] Could not load {storage_state_file.name}: {e}")

        # Navigate to portal
        log(location, "\nNavigating to Southern Glazer's portal...")
        try:
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            log(location, "[ERROR] Portal timed out")
            update_vendor_session_status(location, "expired", failure_reason="timeout")
            await context.close()
            sys.exit(1)

        # Inject sessionStorage and localStorage (origin-bound, must happen AFTER navigation)
        if saved_state:
            session_storage = saved_state.get("sessionStorage", {})
            if session_storage:
                try:
                    await page.evaluate(
                        "(items) => { for (const [k,v] of Object.entries(items)) sessionStorage.setItem(k, v); }",
                        session_storage,
                    )
                    log(location, f"Injected {len(session_storage)} sessionStorage items")
                except Exception as e:
                    log(location, f"[WARN] sessionStorage injection failed: {e}")

            local_storage = saved_state.get("localStorage_manual", {})
            if local_storage:
                try:
                    await page.evaluate(
                        "(items) => { for (const [k,v] of Object.entries(items)) localStorage.setItem(k, v); }",
                        local_storage,
                    )
                    log(location, f"Injected {len(local_storage)} localStorage items")
                except Exception as e:
                    log(location, f"[WARN] localStorage injection failed: {e}")

            # Reload so the portal picks up injected tokens
            if session_storage or local_storage:
                log(location, "Reloading page after storage injection...")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                except PlaywrightTimeoutError:
                    log(location, "[WARN] Reload timed out, continuing anyway...")

        # Check session health
        session_ok = await check_session_health(page, location)
        if not session_ok:
            log(location, "\nSESSION EXPIRED — attempting auto-login...")
            login_ok = await auto_login(page, location, login_email)
            if not login_ok:
                log(location, "Auto-login failed. Manual session refresh required.")
                log(location, f"  Run export_session_{location}.py on your Windows PC to refresh,")
                log(location, f"  then transfer browser_profile_{location}/ to the Beelink.")
                update_vendor_session_status(location, "expired", failure_reason="session_expired")
                send_session_expired_alert(location, login_email)
                await context.close()
                sys.exit(1)
            else:
                update_vendor_session_status(location, "active")

        # Debug: page info
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
        log(location, f"Page: {page_info['title']}")
        log(location, f"URL: {page_info['url']}")

        # Scrape invoice list
        invoices = await scrape_invoice_list(page, location)
        log(location, f"Found {len(invoices)} invoices on page")

        # Filter to current year (2026+) only
        current_year_invoices = [inv for inv in invoices if is_invoice_year_ok(inv.get("date", ""))]
        skipped_old = len(invoices) - len(current_year_invoices)
        if skipped_old:
            log(location, f"Skipped {skipped_old} pre-{MIN_YEAR} invoices")

        # Skip rows with no date (summary/statement rows, not real invoices)
        current_year_invoices = [inv for inv in current_year_invoices if inv.get("date")]
        # Filter to new only
        new_invoices = [
            inv for inv in current_year_invoices
            if inv["invoice_number"] not in already_downloaded
        ]
        log(location, f"New invoices: {len(new_invoices)}")

        if not new_invoices:
            log(location, "All caught up!")
            update_vendor_session_status(location, "healthy", invoices_scraped=0)
            await context.close()
            return

        # Download each new invoice
        downloaded = []
        for i, inv in enumerate(new_invoices):
            inv_num = inv["invoice_number"]
            log(location, f"[{i + 1}/{len(new_invoices)}] Invoice #{inv_num} "
                         f"({inv.get('date', '?')}, {inv.get('amount', '?')})")

            pdf_path = await download_invoice_pdf(page, context, inv_num, location, download_dir)

            if pdf_path and pdf_path.exists():
                downloaded.append(inv_num)
                already_downloaded.add(inv_num)
                save_downloaded_invoices(data_dir, already_downloaded)

                # Copy to OCR pipeline
                copy_to_ocr_pipeline(pdf_path, location)

            await page.wait_for_timeout(1500)

        # Update session status
        update_vendor_session_status(location, "healthy", invoices_scraped=len(downloaded))

        # Summary
        log(location, f"\n{'=' * 60}")
        log(location, f"SUMMARY — {location.title()}")
        log(location, f"{'=' * 60}")
        log(location, f"  Successfully downloaded: {len(downloaded)}")
        log(location, f"  Total tracked:           {len(already_downloaded)}")
        if downloaded:
            log(location, f"  New invoices: {', '.join(downloaded)}")
        log(location, f"{'=' * 60}")

        await context.close()
