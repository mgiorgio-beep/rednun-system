#!/usr/bin/env python3
"""
Martignetti Invoice Scraper — Red Nun Vendor Scrapers
=====================================================
Automates downloading new invoices from martignettiexchange.com as PDFs.

Both Red Nun locations are in the same account/list:
  - "Red Nun" in customer column → Dennis
  - "Red Nun Bar & Grill" in customer column → Chatham

Flow:
  1. Login → lands directly on invoices list (no extra navigation)
  2. Each row has a PDF column with a paper icon → click to download
  3. Parse location from customer name in each row
  4. Repeat for all new invoices

Downloaded PDFs are copied to /opt/rednun/invoice_images/ for the OCR pipeline.

Requirements:
    pip install playwright requests
    playwright install chromium

Usage:
    python scraper.py

Cron (Beelink):
    0 7 * * * cd ~/vendor-scrapers/martignetti && /opt/rednun/venv/bin/python3 scraper.py >> scraper.log 2>&1
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

PORTAL_URL = "https://martignettiexchange.com/profile/login?backurl=/profile/invoices/"
INVOICES_URL = "https://martignettiexchange.com/profile/invoices/"
VENDOR_NAME = "Martignetti Companies"
MIN_YEAR = 2026  # Only download invoices from 2026 onward

# Location detection from customer name in invoice list.
# IMPORTANT: Check "Red Nun Bar & Grill" BEFORE "Red Nun" (longer match first).
LOCATION_MAP = [
    ("red nun bar & grill", "chatham"),
    ("red nun bar", "chatham"),
    ("red nun b&g", "chatham"),
    ("red nun chatham", "chatham"),
    ("red nun", "dennis"),  # Must be last — "Red Nun" alone = Dennis
]

# Directories
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
BROWSER_PROFILE_DIR = Path(os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"))

# Where to copy PDFs for the dashboard OCR pipeline
INVOICE_IMAGES_DIR = Path(os.getenv("INVOICE_IMAGES_DIR", "/opt/rednun/invoice_images"))

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
                    if key and val:  # Only set if value is non-empty
                        os.environ[key] = val


def log(msg):
    print(f"[Martignetti] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


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


def detect_location(customer_name: str) -> str:
    """Detect location from customer name. Returns 'chatham' or 'dennis'."""
    lower = customer_name.lower().strip()
    for pattern, location in LOCATION_MAP:
        if pattern in lower:
            return location
    return "dennis"  # Default to Dennis if unclear


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
    """Query dashboard API for already-imported Martignetti invoice numbers."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": "Martignetti"},
            timeout=10,
        )
        if r.status_code == 200:
            nums = set(r.json().get("invoice_numbers", []))
            log(f"Dashboard has {len(nums)} existing Martignetti invoices")
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
    except Exception as e:
        log(f"[WARN] Could not update session status: {e}")


def send_session_expired_alert():
    """Send email alert when Martignetti session has expired."""
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
        <h2 style="color:#f59e0b;margin:0 0 8px 0;">Martignetti Session Expired</h2>
        <p style="margin:0;">The Martignetti invoice scraper cannot access the portal because the login session has expired.</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Vendor</td>
        <td style="padding:8px 0;">{VENDOR_NAME}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Portal</td>
        <td style="padding:8px 0;">martignettiexchange.com</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Detected</td>
        <td style="padding:8px 0;">{now}</td>
    </tr>
    </table>
    <div style="margin-top:20px;padding:12px;background:#0f172a;border-radius:8px;">
        <strong style="color:#94a3b8;">To fix:</strong>
        <pre style="color:#38bdf8;margin:8px 0 0 0;font-size:13px;">1. On your Windows PC:
   cd ~/vendor-scrapers/martignetti
   python export_session.py

2. Log in when the browser opens

3. Close the browser after login completes

4. Transfer the session:
   scp -r -P 2222 browser_profile/ rednun@ssh.rednun.com:~/vendor-scrapers/martignetti/browser_profile/</pre>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">
        The scraper will automatically resume on the next cron run once the session is refreshed.
    </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[Red Nun] Martignetti session expired — login needed"
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


def copy_to_ocr_pipeline(pdf_path: Path) -> bool:
    """Copy a downloaded PDF to the dashboard invoice_images/ dir for OCR processing."""
    if not INVOICE_IMAGES_DIR.exists():
        log(f"  [WARN] OCR pipeline dir not found: {INVOICE_IMAGES_DIR}")
        return False
    try:
        dest = INVOICE_IMAGES_DIR / pdf_path.name
        shutil.copy2(str(pdf_path), str(dest))
        log(f"  Copied to OCR pipeline: {dest.name}")
        return True
    except Exception as e:
        log(f"  [WARN] Could not copy to OCR pipeline: {e}")
        return False


# ─── SESSION HEALTH CHECK ────────────────────────────────────────────────────


async def check_session_health(page) -> bool:
    """Returns True if session is valid, False if login page detected or access denied."""
    # Check for 403 / access-denied pages
    page_title = await page.title()
    page_text = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
    title_lower = page_title.lower()
    text_lower = page_text.lower()

    access_denied_signals = ["403", "access not allowed", "access denied", "forbidden", "not authorized"]
    for signal in access_denied_signals:
        if signal in title_lower or signal in text_lower:
            log(f"Session expired — access denied page detected: \"{page_title}\"")
            return False

    # Specific Martignetti login form indicators — only match the actual login form,
    # not nav links on the logged-in page that say "LOGIN"
    login_indicators = [
        '#Login_password',           # Martignetti login form password field
        '#Login_email',              # Martignetti login form email field
        'input[type="password"]',    # Generic fallback
    ]
    for selector in login_indicators:
        try:
            el = await page.query_selector(selector)
            if el:
                box = await el.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    log(f"Session expired — detected visible login field: {selector}")
                    return False
        except Exception:
            pass

    return True


async def auto_login(page) -> bool:
    """
    Attempt to log in via form fill when session has expired.
    Reads credentials from ~/vendor-scrapers/.env.
    Returns True if login succeeded, False otherwise.
    """
    load_scraper_env()

    username = os.getenv("MARTIGNETTI_USER", "")
    password = os.getenv("MARTIGNETTI_PASS", "")
    if not username or not password:
        log("No credentials found in .env (MARTIGNETTI_USER/MARTIGNETTI_PASS) — cannot auto-login")
        return False

    log(f"Attempting auto-login as {username}...")

    try:
        # Navigate to login page
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Look for login form fields
        email_field = await page.query_selector(
            'input[type="email"], input[name="email"], input[name="username"], '
            'input[name="login"], input[id="email"], input[id="username"], '
            'input[name="USER_LOGIN"], input[name="user_login"]'
        )
        password_field = await page.query_selector('input[type="password"]')

        if not password_field:
            log("  No password field found on page — cannot auto-login")
            return False

        # Fill email/username
        if email_field:
            await email_field.click()
            await email_field.fill(username)
            log(f"  Filled username: {username}")
        else:
            log("  No username field found — trying password only")

        # Fill password
        await password_field.click()
        await password_field.fill(password)
        log("  Filled password")

        # Submit — try multiple strategies
        submitted = False
        for btn_selector in [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'button:has-text("Login")',
            'button:has-text("Submit")',
            'input[value="Log In"]',
            'input[value="Sign In"]',
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

        # Wait for navigation/redirect
        await page.wait_for_timeout(8000)

        # Check if login succeeded
        session_ok = await check_session_health(page)
        if session_ok:
            log("  Auto-login SUCCEEDED!")
            # Save new session state
            try:
                storage_state_file = Path("./storage_state.json")
                cookies = await page.context.cookies()
                state = {"cookies": cookies}
                with open(storage_state_file, "w") as f:
                    json.dump(state, f, indent=2)
                log(f"  Saved new session state to {storage_state_file.name}")
            except Exception as e:
                log(f"  [WARN] Could not save session state: {e}")
            return True
        else:
            log("  Auto-login FAILED — still on login page")
            return False

    except Exception as e:
        log(f"  Auto-login error: {e}")
        return False


# ─── INVOICE LIST ────────────────────────────────────────────────────────────


async def scrape_invoice_list(page) -> list[dict]:
    """
    Scrape invoice rows from the Martignetti invoice list page.
    Each row contains: invoice number, date, amount, customer name, and a PDF icon.

    Returns list of dicts with invoice_number, date, amount, customer, location, row_index.
    """
    invoices = await page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();

            // Strategy 1: Table rows
            const rows = document.querySelectorAll('table tbody tr, table tr');
            for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
                const row = rows[rowIdx];
                const cells = row.querySelectorAll('td, th');
                if (cells.length < 3) continue;  // Skip header or empty rows

                const text = row.innerText || '';

                // Extract invoice number — Martignetti uses "US1-XXXXXXXXX" format
                const invMatch = text.match(/(US\\d*-\\d+[A-Z0-9]*)/i);
                // Also try plain numeric
                const numMatch = text.match(/\\b(\\d{6,12})\\b/);
                const invNum = invMatch ? invMatch[1] : (numMatch ? numMatch[1] : null);

                if (!invNum || seen.has(invNum)) continue;
                seen.add(invNum);

                // Extract dates (MM/DD/YYYY or similar)
                const dates = text.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g) || [];
                // Extract dollar amounts
                const amounts = text.match(/-?\\$[\\d,]+\\.\\d{2}/g) || [];

                // Extract customer name for location detection
                // Look through cells for one containing "Red Nun"
                let customer = '';
                for (const cell of cells) {
                    const ct = cell.innerText?.trim() || '';
                    if (ct.toLowerCase().includes('red nun')) {
                        customer = ct;
                        break;
                    }
                }

                // Check if this row has a PDF icon/link
                const hasPdf = row.querySelector(
                    'a[href*="pdf"], a[href*="PDF"], a[href*="download"], ' +
                    '[class*="pdf"], [class*="PDF"], [class*="document"], ' +
                    'img[src*="pdf"], img[alt*="pdf" i], img[alt*="PDF"], ' +
                    'i[class*="file"], i[class*="document"], ' +
                    'svg, .fa-file-pdf, .fa-file, .glyphicon-file'
                ) !== null;

                results.push({
                    invoice_number: invNum,
                    date: dates[0] || '',
                    amount: amounts[0] ? amounts[0].replace('$', '').replace(',', '') : '',
                    customer: customer,
                    has_pdf: hasPdf,
                    row_index: rowIdx,
                    row_text: text.substring(0, 400),
                });
            }

            return results;
        }
    """)
    return invoices


# ─── DOWNLOAD ────────────────────────────────────────────────────────────────


async def download_invoice_pdf(page, invoice: dict) -> Path | None:
    """
    Download a single invoice PDF from the "PDF" column link.
    Martignetti uses <a> links with href like:
      /documents/posted-invoice-document/Invoice_US1-103192691
    containing an SVG fa-file icon. The link serves a PDF document.
    We extract the href and download via a new page (not click+expect_download,
    because clicking navigates the current page away from the invoices table).
    """
    inv_num = invoice["invoice_number"]
    row_idx = invoice["row_index"]
    location = detect_location(invoice.get("customer", ""))

    try:
        # Extract the PDF document link href from the row (don't click it!)
        pdf_href = await page.evaluate("""
            (invoiceNum) => {
                const rows = document.querySelectorAll('table tbody tr, table tr');
                for (const row of rows) {
                    const text = row.innerText || '';
                    if (!text.includes(invoiceNum)) continue;

                    // Look for the document link in this row
                    // Martignetti PDF column has: <a href=".../documents/posted-invoice-document/Invoice_..."><svg class="fa-file"></a>
                    const links = row.querySelectorAll('a');
                    for (const link of links) {
                        const href = link.href || '';
                        if (href.includes('/documents/') || href.includes('posted-invoice') ||
                            href.toLowerCase().includes('invoice') && href.includes('/document')) {
                            return href;
                        }
                    }

                    // Fallback: any <a> containing an SVG fa-file icon
                    for (const link of links) {
                        const svg = link.querySelector('svg.fa-file, svg[class*="fa-file"]');
                        if (svg && link.href) {
                            return link.href;
                        }
                    }

                    break;  // Found the row but no PDF link
                }
                return null;
            }
        """, inv_num)

        if not pdf_href:
            log(f"  [WARN] No PDF link found for invoice #{inv_num}")
            return None

        log(f"  PDF link: {pdf_href[-60:]}")

        # Open the document URL in a new page and save the PDF
        date_stamp = datetime.now().strftime("%Y%m%d")
        safe_num = inv_num.replace("/", "-")
        dest = DOWNLOAD_DIR / f"martignetti_{location}_{safe_num}_{date_stamp}.pdf"
        if dest.exists():
            seq = 2
            while dest.exists():
                dest = DOWNLOAD_DIR / f"martignetti_{location}_{safe_num}_{date_stamp}_{seq}.pdf"
                seq += 1

        # The document URL triggers a file download (Content-Disposition: attachment),
        # so page.goto() raises "Download is starting". Use expect_download() to catch it.
        new_page = await page.context.new_page()
        pdf_saved = False
        try:
            # Method A: Navigate to PDF URL and catch the download event
            try:
                async with new_page.expect_download(timeout=30000) as dl_info:
                    # goto will raise "Download is starting" which is expected
                    try:
                        await new_page.goto(pdf_href, wait_until="domcontentloaded", timeout=30000)
                    except Exception as nav_err:
                        if "Download is starting" not in str(nav_err):
                            raise
                download = await dl_info.value
                await download.save_as(str(dest))
                pdf_saved = True
                log(f"  [OK] Downloaded: {dest.name}")
            except PlaywrightTimeoutError:
                log(f"  Download timed out — trying page.pdf() fallback")

            # Method B: Render page to PDF as fallback
            if not pdf_saved:
                try:
                    await new_page.pdf(path=str(dest))
                    pdf_saved = True
                    log(f"  [OK] Rendered to PDF: {dest.name}")
                except Exception:
                    pass

        finally:
            try:
                await new_page.close()
            except Exception:
                pass

        if dest.exists() and dest.stat().st_size > 100:
            return dest
        log(f"  [FAIL] Could not save PDF for #{inv_num}")
        return None

    except PlaywrightTimeoutError:
        log(f"  [TIMEOUT] Invoice #{inv_num} — download timed out (PDF icon click may not have triggered a download)")
        # Try to dismiss any popups
        try:
            await page.evaluate("""
                () => {
                    const backdrop = document.querySelector('[class*="backdrop"], [class*="overlay"], .modal-backdrop');
                    if (backdrop) backdrop.click();
                    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
                }
            """)
            await page.wait_for_timeout(500)
        except Exception:
            pass
        return None
    except Exception as e:
        log(f"  [ERROR] Invoice #{inv_num} — {e}")
        return None


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    load_env()

    log(f"{'=' * 60}")
    log(f"Martignetti Invoice Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")

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
        # IMPORTANT: Must set a real Chrome user-agent — Martignetti's Varnish CDN
        # blocks the default headless Chromium user-agent with a 403.
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            accept_downloads=True,
            viewport={"width": 1600, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
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

        # Navigate to portal — should land on invoices page if logged in
        log("\nNavigating to Martignetti portal...")
        try:
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            log("[ERROR] Martignetti portal timed out")
            update_vendor_session_status("expired", failure_reason="timeout")
            await context.close()
            sys.exit(1)

        # Check session health
        session_ok = await check_session_health(page)
        if not session_ok:
            log("\nSESSION EXPIRED — attempting auto-login...")
            login_ok = await auto_login(page)
            if not login_ok:
                log("Auto-login failed. Manual session refresh required.")
                log("  Run export_session.py on your Windows PC to refresh,")
                log("  then transfer browser_profile/ to the Beelink.")
                update_vendor_session_status("expired", failure_reason="session_expired")
                send_session_expired_alert()
                await context.close()
                sys.exit(1)
            else:
                update_vendor_session_status("active")

        # If we're not on the invoices page, try to navigate there
        if "/invoices" not in page.url.lower():
            log("Not on invoices page, navigating...")
            try:
                await page.goto(INVOICES_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass

        # Debug: page info
        page_info = await page.evaluate("""
            () => {
                return {
                    url: location.href,
                    title: document.title,
                    bodyLength: document.body.innerText.length,
                    sampleText: document.body.innerText.substring(0, 800)
                };
            }
        """)
        log(f"Page: {page_info['title']}")
        log(f"URL: {page_info['url']}")

        # Scrape invoice list
        invoices = await scrape_invoice_list(page)
        log(f"Found {len(invoices)} invoices on page")

        # Log location breakdown
        chatham_count = sum(1 for inv in invoices if detect_location(inv.get("customer", "")) == "chatham")
        dennis_count = sum(1 for inv in invoices if detect_location(inv.get("customer", "")) == "dennis")
        log(f"  Chatham: {chatham_count}, Dennis: {dennis_count}")

        # Filter to current year (2026+) only
        current_year_invoices = [inv for inv in invoices if is_invoice_year_ok(inv.get("date", ""))]
        skipped_old = len(invoices) - len(current_year_invoices)
        if skipped_old:
            log(f"Skipped {skipped_old} pre-{MIN_YEAR} invoices")

        # Filter to new only
        new_invoices = [
            inv for inv in current_year_invoices
            if inv["invoice_number"] not in already_downloaded
        ]
        log(f"New invoices: {len(new_invoices)}")

        if not new_invoices:
            log("All caught up!")
            update_vendor_session_status("healthy", invoices_scraped=0)
            await context.close()
            return

        # Download each new invoice
        downloaded = []
        for i, inv in enumerate(new_invoices):
            inv_num = inv["invoice_number"]
            location = detect_location(inv.get("customer", ""))
            log(f"[{i + 1}/{len(new_invoices)}] Invoice #{inv_num} "
                f"({inv.get('date', '?')}, {inv.get('amount', '?')}, {location})")

            pdf_path = await download_invoice_pdf(page, inv)

            if pdf_path and pdf_path.exists():
                downloaded.append(inv_num)
                already_downloaded.add(inv_num)
                save_downloaded_invoices(already_downloaded)

                # Copy to OCR pipeline
                copy_to_ocr_pipeline(pdf_path)

            await page.wait_for_timeout(1500)

        # Update session status
        update_vendor_session_status("healthy", invoices_scraped=len(downloaded))

        # Summary
        log(f"\n{'=' * 60}")
        log(f"SUMMARY")
        log(f"{'=' * 60}")
        log(f"  Successfully downloaded: {len(downloaded)}")
        log(f"  Total tracked:           {len(already_downloaded)}")
        if downloaded:
            log(f"  New invoices: {', '.join(downloaded)}")
        log(f"{'=' * 60}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
