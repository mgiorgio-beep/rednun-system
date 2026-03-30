#!/usr/bin/env python3
"""
Craft Collective Invoice Scraper — Red Nun Vendor Scrapers
==========================================================
Automates downloading new invoices and credits from termsync.com as PDFs.

Login: mgiorgio@rednun.com
Location: Dennis ONLY

Account picker: After login, TermSync may show multiple accounts:
  - Craft Collective Dennis ← SELECT THIS ONE
  - Craft Collective Chatham (no orders, skip)
  - Atlantic Beverage Distributors (Dennis only, skip for now)

Flow:
  1. Login → account picker (if shown) → select Craft Collective Dennis
  2. Hover "Invoices" dropdown → "Invoice Listing" → invoice list
  3. Filter to current year only (skip old 2025 invoices)
  4. Click invoice number → invoice detail page
  5. Under "Available Actions" → click "View Invoice PDF" → new tab with PDF
  6. Download PDF from new tab, close tab, return to list
  7. Also scrape "Credits Listing" from the same Invoices dropdown

Downloaded PDFs are copied to /opt/rednun/invoice_images/ for OCR pipeline.

Requirements:
    pip install playwright requests
    playwright install chromium

Usage:
    python scraper.py

Cron (Beelink):
    0 7 * * * cd ~/vendor-scrapers/craft-collective && /opt/rednun/venv/bin/python3 scraper.py >> scraper.log 2>&1
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

PORTAL_URL = "https://www.termsync.com/"
VENDOR_NAME = "Craft Collective Inc"
LOCATION = "dennis"  # Dennis ONLY — scrape Craft Collective Dennis account only
# Account picker: TermSync shows multiple accounts after login.
# Select the Dennis account; skip Chatham (no orders) and Atlantic Beverage.
ACCOUNT_SELECT_KEYWORDS = ["craft collective", "dennis"]  # must match ALL (case-insensitive)
ACCOUNT_SKIP_KEYWORDS = ["chatham", "atlantic", "logout", "unable to load"]  # skip if ANY match
CURRENT_YEAR = datetime.now().year  # Filter: only download invoices from this year

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
                    if key and val:
                        os.environ[key] = val


def log(msg):
    print(f"[CraftCollective] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


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


def get_dashboard_existing_invoices() -> set:
    """Query dashboard API for already-imported Craft Collective invoice numbers."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": "Craft Collective"},
            timeout=10,
        )
        if r.status_code == 200:
            nums = set(r.json().get("invoice_numbers", []))
            log(f"Dashboard has {len(nums)} existing Craft Collective invoices")
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
    """Send email alert when Craft Collective / TermSync session has expired."""
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
        <h2 style="color:#f59e0b;margin:0 0 8px 0;">Craft Collective Session Expired</h2>
        <p style="margin:0;">The Craft Collective / TermSync invoice scraper cannot access the portal because the login session has expired.</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Vendor</td>
        <td style="padding:8px 0;">{VENDOR_NAME}</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Portal</td>
        <td style="padding:8px 0;">termsync.com</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Login</td>
        <td style="padding:8px 0;">mgiorgio@rednun.com</td>
    </tr>
    <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:8px 0;color:#94a3b8;">Detected</td>
        <td style="padding:8px 0;">{now}</td>
    </tr>
    </table>
    <div style="margin-top:20px;padding:12px;background:#0f172a;border-radius:8px;">
        <strong style="color:#94a3b8;">To fix:</strong>
        <pre style="color:#38bdf8;margin:8px 0 0 0;font-size:13px;">1. On your Windows PC:
   cd ~/vendor-scrapers/craft-collective
   python export_session.py

2. Log in as mgiorgio@rednun.com when the browser opens

3. Close the browser after login completes

4. Transfer the session:
   scp -r -P 2222 browser_profile/ rednun@ssh.rednun.com:~/vendor-scrapers/craft-collective/browser_profile/</pre>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">
        The scraper will automatically resume on the next cron run once the session is refreshed.
    </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[Red Nun] Craft Collective session expired — login needed"
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


# ─── ACCOUNT PICKER ──────────────────────────────────────────────────────────


async def handle_account_picker(page) -> bool:
    """
    If TermSync shows an account picker after login, select the Craft Collective
    Dennis account. Returns True if an account was selected (or no picker shown),
    False if the correct account could not be found.
    """
    await page.wait_for_timeout(2000)

    # Only run account picker if we're on the account selection page
    if "available_companies" not in page.url and "switch" not in page.url.lower():
        log("Not on account picker page — already logged into an account")
        return True

    # Detect if an account picker / selector is visible
    picker_result = await page.evaluate("""
        () => {
            // Look for account selection UI — cards, list items, buttons, radio buttons, links
            // that contain account/company names
            const candidates = document.querySelectorAll(
                'a, button, [role="button"], [role="option"], [role="listitem"], ' +
                'li, tr, .card, [class*="account"], [class*="company"], ' +
                '[class*="select"], [class*="choose"], label, div[onclick]'
            );

            const accounts = [];
            const seen = new Set();
            for (const el of candidates) {
                const text = (el.innerText || el.textContent || '').trim();
                // Skip very short or very long text, or nav/footer items
                if (text.length < 5 || text.length > 200) continue;
                // Must look like an account name (not a generic nav link)
                const lower = text.toLowerCase();
                if (lower.includes('craft collective') || lower.includes('atlantic beverage') ||
                    lower.includes('dennis') || lower.includes('chatham')) {
                    const key = text.substring(0, 80);
                    if (!seen.has(key)) {
                        seen.add(key);
                        accounts.push({
                            text: text.substring(0, 200),
                            tag: el.tagName,
                            clickable: !!(el.href || el.onclick || el.tagName === 'BUTTON' ||
                                         el.getAttribute('role') === 'button' ||
                                         el.getAttribute('role') === 'option')
                        });
                    }
                }
            }
            return accounts;
        }
    """)

    if not picker_result or len(picker_result) == 0:
        log("No account picker detected — proceeding")
        return True

    log(f"Account picker detected — {len(picker_result)} accounts found:")
    for acct in picker_result:
        log(f"  [{acct['tag']}] {acct['text']}")

    # Find and click the correct account (Craft Collective Dennis)
    # TermSync account links are <a> tags with text like:
    #   "Craft Collective Homegrown\nLog in as Red Nun Dennis Port (726)"
    # Prioritize <a> tags with "log in as" text to avoid clicking sidebar/profile elements
    selected = await page.evaluate("""
        (config) => {
            const selectKw = config.selectKeywords;  // must match ALL
            const skipKw = config.skipKeywords;       // skip if ANY match

            // Priority 1: <a> tags with "log in as" — the actual account selector links
            const links = document.querySelectorAll('a');
            for (const el of links) {
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!text.includes('log in as')) continue;
                if (skipKw.some(kw => text.includes(kw))) continue;
                const matchCount = selectKw.filter(kw => text.includes(kw)).length;
                if (matchCount >= 1) {
                    el.click();
                    return { found: true, text: el.innerText.trim().substring(0, 150), score: matchCount, method: 'log-in-as-link' };
                }
            }

            // Priority 2: Any clickable element matching keywords (broader search)
            const candidates = document.querySelectorAll(
                'a, button, [role="button"], [role="option"]'
            );
            let bestMatch = null;
            let bestScore = -1;
            for (const el of candidates) {
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (text.length < 5 || text.length > 120) continue;
                if (skipKw.some(kw => text.includes(kw))) continue;
                const matchCount = selectKw.filter(kw => text.includes(kw)).length;
                if (matchCount > bestScore) {
                    bestScore = matchCount;
                    bestMatch = el;
                }
            }
            if (bestMatch && bestScore >= 1) {
                const text = (bestMatch.innerText || bestMatch.textContent || '').trim();
                bestMatch.click();
                return { found: true, text: text.substring(0, 150), score: bestScore, method: 'keyword-match' };
            }

            return { found: false, score: bestScore };
        }
    """, {"selectKeywords": ACCOUNT_SELECT_KEYWORDS, "skipKeywords": ACCOUNT_SKIP_KEYWORDS})

    if not selected["found"]:
        log("[ERROR] Could not find Craft Collective Dennis account in picker")
        return False

    log(f"Selected account: \"{selected['text']}\" (keyword score: {selected['score']}, via {selected.get('method', '?')})")

    # Wait for the account to load — the click navigates to the account's home page
    await page.wait_for_timeout(3000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Verify we left the account picker page
    if "available_companies" in page.url:
        log("  [WARN] Still on account picker page — waiting longer...")
        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    log(f"  Post-selection URL: {page.url}")
    return True


# ─── SESSION HEALTH CHECK ────────────────────────────────────────────────────


async def check_session_health(page) -> bool:
    """Returns True if session is valid, False if login page detected."""
    login_indicators = [
        'input[type="password"]',
        'form[action*="login"]',
        'form[action*="signin"]',
        'form[action*="auth"]',
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
    if "login" in url or "signin" in url or "/auth" in url:
        log(f"Session expired — redirected to login URL: {page.url}")
        return False

    return True


async def auto_login(page) -> bool:
    """Attempt auto-login when session has expired. Reads credentials from ~/vendor-scrapers/.env."""
    load_scraper_env()

    username = os.getenv("CRAFT_USER", "")
    password = os.getenv("CRAFT_PASS", "")
    if not username or not password:
        log("No credentials found in .env (CRAFT_USER/CRAFT_PASS) — cannot auto-login")
        return False

    log(f"Attempting auto-login as {username}...")
    try:
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        email_field = await page.query_selector(
            'input[type="email"], input[name="email"], input[name="username"], '
            'input[name="login"], input[id="email"], input[id="username"], '
            'input[name="UserName"]'
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


# ─── NAVIGATION ──────────────────────────────────────────────────────────────


async def navigate_to_listing(page, listing_type: str) -> bool:
    """
    Navigate to Invoice Listing or Credits Listing.
    TermSync nav: "Invoices" is a dropdown LI (class subnav_dropdown).
    Uses Playwright hover() + native click for proper navigation.
    """
    target_text = "Invoice Listing" if listing_type == "invoice" else "Credits Listing"
    log(f"Navigating to '{target_text}'...")

    # Step 1: Try Playwright hover on the dropdown LI to open CSS dropdown,
    # then use Playwright native click (not JS evaluate) to actually navigate
    dropdown_li = page.locator('li.subnav_dropdown').first
    if await dropdown_li.count() > 0:
        await dropdown_li.hover()
        await page.wait_for_timeout(1500)

        # Use Playwright locator to click the target link (native click triggers navigation)
        target_link = page.locator(f'a:has-text("{target_text}")').first
        if await target_link.count() > 0:
            await target_link.click()
            log(f"  Clicked '{target_text}' via Playwright native click")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            log(f"  URL: {page.url}")
            return True

    # Step 2: Fallback — direct navigation to known TermSync URLs
    if listing_type == "invoice":
        target_link = page.locator('a[href*="/payments"]').first
        if await target_link.count() > 0:
            await target_link.click()
            log(f"  Clicked Invoices link directly")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            log(f"  URL: {page.url}")
            return True

    if listing_type == "credit":
        target_link = page.locator('a[href*="/credits"]').first
        if await target_link.count() > 0:
            await target_link.click()
            log(f"  Clicked Credits link directly")
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            log(f"  URL: {page.url}")
            return True
        log(f"  No separate credits listing found — credits may be on invoices page")
        return False

    log(f"  [WARN] Could not navigate to '{target_text}'")
    return False


# ─── INVOICE LIST ────────────────────────────────────────────────────────────


def is_current_year(date_str: str) -> bool:
    """Check if a date string (MM/DD/YYYY or similar) is from the current year."""
    if not date_str:
        return True  # Include if we can't parse the date
    try:
        # Try common date formats
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.year == CURRENT_YEAR
            except ValueError:
                continue
        # Check if the year string appears
        if str(CURRENT_YEAR) in date_str:
            return True
        if str(CURRENT_YEAR - 1) in date_str:
            return False
    except Exception:
        pass
    return True  # Include if unparseable


async def scrape_listing(page, listing_type: str) -> list[dict]:
    """
    Scrape invoice or credit rows from the current listing page.
    listing_type: 'invoice' or 'credit' (for tracking key prefix)

    Returns list of dicts with invoice_number, date, amount, row_index, pdf_url.
    pdf_url is the direct download path (e.g. /payments/{id}/download_invoice_pdf)
    or empty string if no PDF is available (shows "Request" instead of "View").
    """
    items = await page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();

            // Strategy 1: Table rows
            const rows = document.querySelectorAll('table tbody tr, table tr');
            for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
                const row = rows[rowIdx];
                const cells = row.querySelectorAll('td');
                if (cells.length < 2) continue;

                const text = row.innerText || '';

                // Look for invoice/credit number — typically a link in the first few cells
                let invNum = null;
                const links = row.querySelectorAll('a');
                for (const link of links) {
                    const lt = link.innerText?.trim() || '';
                    // Invoice numbers: digits, possibly with dashes or prefixes
                    if (lt.match(/^[A-Z0-9][-A-Z0-9]{3,}$/i) || lt.match(/^\\d{4,}$/)) {
                        invNum = lt;
                        break;
                    }
                }

                // Fallback: look for a number pattern in the row text
                if (!invNum) {
                    const numMatch = text.match(/\\b([A-Z]*\\d{5,}[A-Z0-9]*)\\b/);
                    if (numMatch) invNum = numMatch[1];
                }

                if (!invNum || seen.has(invNum)) continue;
                seen.add(invNum);

                // Extract dates and amounts
                const dates = text.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g) || [];
                const amounts = text.match(/-?\\$[\\d,]+\\.\\d{2}/g) || [];

                // Find PDF download link (last column — "View" with download_invoice_pdf or download_credit_pdf href)
                let pdfUrl = '';
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('download_invoice_pdf') || href.includes('download_credit_pdf')) {
                        pdfUrl = href;
                        break;
                    }
                }

                results.push({
                    invoice_number: invNum,
                    date: dates[0] || '',
                    amount: amounts[0] ? amounts[0].replace('$', '').replace(',', '') : '',
                    row_index: rowIdx,
                    row_text: text.substring(0, 400),
                    pdf_url: pdfUrl,
                });
            }

            // Strategy 2: scan all links if table approach found nothing
            if (results.length === 0) {
                const allLinks = document.querySelectorAll('a');
                for (const link of allLinks) {
                    const lt = link.innerText?.trim() || '';
                    if ((lt.match(/^[A-Z0-9][-A-Z0-9]{4,}$/i) || lt.match(/^\\d{5,}$/)) && !seen.has(lt)) {
                        seen.add(lt);
                        results.push({
                            invoice_number: lt,
                            date: '',
                            amount: '',
                            row_index: -1,
                            row_text: '',
                            pdf_url: '',
                        });
                    }
                }
            }

            return results;
        }
    """)
    return items


# ─── DOWNLOAD ────────────────────────────────────────────────────────────────


async def _download_pdf_via_requests(context, pdf_url: str, dest: Path) -> bool:
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
            log(f"    [OK] Downloaded PDF via requests ({len(r.content):,} bytes): {dest.name}")
            return True
        else:
            log(f"    [WARN] requests.get returned {r.status_code}, {len(r.content)} bytes")
            return False
    except Exception as e:
        log(f"    [WARN] requests download failed: {e}")
        return False


async def download_invoice_pdf(page, invoice_number: str, doc_type: str, pdf_url: str = '') -> Path | None:
    """
    Download a single invoice/credit PDF using the direct download URL
    from the listing page (e.g. /payments/{id}/download_invoice_pdf).

    If pdf_url is empty, the invoice has no PDF available (shows "Request" on the portal).

    doc_type: 'invoice' or 'credit' (for filename)
    """
    try:
        if not pdf_url:
            log(f"    [SKIP] #{invoice_number} — no PDF available (Request only)")
            return None

        date_stamp = datetime.now().strftime("%Y%m%d")
        safe_num = invoice_number.replace("/", "-").replace("\\", "-")
        dest = DOWNLOAD_DIR / f"craft_{doc_type}_{LOCATION}_{safe_num}_{date_stamp}.pdf"
        if dest.exists():
            seq = 2
            while dest.exists():
                dest = DOWNLOAD_DIR / f"craft_{doc_type}_{LOCATION}_{safe_num}_{date_stamp}_{seq}.pdf"
                seq += 1

        # Make absolute URL
        if pdf_url.startswith('/'):
            full_url = f"https://www.termsync.com{pdf_url}"
        else:
            full_url = pdf_url

        # Download via requests using browser cookies
        pdf_saved = await _download_pdf_via_requests(page.context, full_url, dest)

        if pdf_saved and dest.exists() and dest.stat().st_size > 1000:
            return dest

        # Clean up empty/tiny files
        if dest.exists() and dest.stat().st_size <= 1000:
            dest.unlink()

        log(f"    [FAIL] Could not save PDF for #{invoice_number}")
        return None

    except Exception as e:
        log(f"    [ERROR] #{invoice_number} — {e}")
        return None


# ─── SCRAPE A LISTING ────────────────────────────────────────────────────────


async def scrape_listing_page(page, listing_type: str, already_downloaded: set) -> list[str]:
    """
    Scrape and download all new items from a listing page (invoices or credits).
    Returns list of downloaded tracking keys.
    """
    items = await scrape_listing(page, listing_type)
    label = "invoices" if listing_type == "invoice" else "credits"
    log(f"Found {len(items)} {label} on page")

    # Filter to current year only
    current_year_items = [inv for inv in items if is_current_year(inv.get("date", ""))]
    skipped_old = len(items) - len(current_year_items)
    if skipped_old:
        log(f"  Skipped {skipped_old} pre-{CURRENT_YEAR} {label}")
    log(f"  Current year {label}: {len(current_year_items)}")

    # Filter to new only — use type prefix for tracking key
    new_items = [
        inv for inv in current_year_items
        if f"{listing_type}_{inv['invoice_number']}" not in already_downloaded
    ]
    log(f"  New {label}: {len(new_items)}")

    if not new_items:
        log(f"  All {label} caught up!")
        return []

    # Download each
    downloaded = []
    for i, inv in enumerate(new_items):
        inv_num = inv["invoice_number"]
        log(f"[{i + 1}/{len(new_items)}] {listing_type.title()} #{inv_num} "
            f"({inv.get('date', '?')}, {inv.get('amount', '?')})")

        pdf_path = await download_invoice_pdf(page, inv_num, listing_type, pdf_url=inv.get('pdf_url', ''))

        if pdf_path and pdf_path.exists():
            tracking_key = f"{listing_type}_{inv_num}"
            downloaded.append(tracking_key)
            already_downloaded.add(tracking_key)
            save_downloaded_invoices(already_downloaded)

            # Copy to OCR pipeline
            copy_to_ocr_pipeline(pdf_path)

        await page.wait_for_timeout(1500)

    return downloaded


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    load_env()

    log(f"{'=' * 60}")
    log(f"Craft Collective Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")
    log(f"Location: {LOCATION} only")
    log(f"Year filter: {CURRENT_YEAR}")

    # Ensure directories exist
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Load previously downloaded (local tracking)
    already_downloaded = load_downloaded_invoices()
    log(f"Previously downloaded (local): {len(already_downloaded)} entries")

    # Check dashboard API for already-imported invoices
    dashboard_existing = get_dashboard_existing_invoices()
    for inv_num in dashboard_existing:
        already_downloaded.add(f"invoice_{inv_num}")
        already_downloaded.add(f"credit_{inv_num}")
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

        # Navigate to TermSync
        log("\nNavigating to TermSync portal...")
        try:
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            log("[ERROR] TermSync portal timed out")
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
                update_vendor_session_status("active")

        # Handle account picker (Craft Collective Dennis vs Chatham vs Atlantic Beverage)
        acct_ok = await handle_account_picker(page)
        if not acct_ok:
            log("\n[ERROR] Could not select Craft Collective Dennis account.")
            log("  TermSync may have changed its account picker layout.")
            update_vendor_session_status("expired", failure_reason="account_picker_failed")
            await context.close()
            sys.exit(1)

        # Debug: page info
        page_info = await page.evaluate("""
            () => ({
                url: location.href,
                title: document.title,
                bodyLength: document.body.innerText.length,
                sampleText: document.body.innerText.substring(0, 600)
            })
        """)
        log(f"Page: {page_info['title']}")
        log(f"URL: {page_info['url']}")

        total_downloaded = []

        # ── Part 1: Invoice Listing ───────────────────────────────
        log(f"\n{'─' * 60}")
        log("INVOICE LISTING")
        log(f"{'─' * 60}")

        nav_ok = await navigate_to_listing(page, "invoice")
        if nav_ok:
            downloaded = await scrape_listing_page(page, "invoice", already_downloaded)
            total_downloaded.extend(downloaded)
        else:
            log("[ERROR] Could not navigate to Invoice Listing")

        # ── Part 2: Credits Listing ───────────────────────────────
        log(f"\n{'─' * 60}")
        log("CREDITS LISTING")
        log(f"{'─' * 60}")

        nav_ok = await navigate_to_listing(page, "credit")
        if nav_ok:
            downloaded = await scrape_listing_page(page, "credit", already_downloaded)
            total_downloaded.extend(downloaded)
        else:
            log("[ERROR] Could not navigate to Credits Listing")

        # Update session status
        update_vendor_session_status("healthy", invoices_scraped=len(total_downloaded))

        # Summary
        inv_count = sum(1 for k in total_downloaded if k.startswith("invoice_"))
        cred_count = sum(1 for k in total_downloaded if k.startswith("credit_"))
        log(f"\n{'=' * 60}")
        log("SUMMARY")
        log(f"{'=' * 60}")
        log(f"  Invoices downloaded: {inv_count}")
        log(f"  Credits downloaded:  {cred_count}")
        log(f"  Total downloaded:    {len(total_downloaded)}")
        log(f"  Total tracked:       {len(already_downloaded)}")
        if total_downloaded:
            log(f"  New: {', '.join(total_downloaded)}")
        log(f"{'=' * 60}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
