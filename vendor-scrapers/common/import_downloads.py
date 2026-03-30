#!/usr/bin/env python3
"""
Vendor Invoice Import Script — Red Nun Analytics

Scans vendor scraper download directories for new CSV/PDF files,
imports them into the dashboard via the API, then archives processed files.

Designed to run on cron every 15 minutes:
    */15 * * * * cd ~/vendor-scrapers && python3 common/import_downloads.py

Handles:
    - US Foods CSV Full files (~/usfoods-scraper/downloads/)
    - PFG CSV exports + PDF fallbacks (~/vendor-scrapers/pfg/downloads/)
    - VTInfo CSVs — L. Knife & Colonial (~/vendor-scrapers/vtinfo/downloads/)
    - Southern Glazer's PDFs → OCR (~/vendor-scrapers/southern-glazers/downloads/)
    - Martignetti PDFs → OCR (~/vendor-scrapers/martignetti/downloads/)
    - Craft Collective PDFs → OCR (~/vendor-scrapers/craft-collective/downloads/)
"""

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ─── CONFIG ──────────────────────────────────────────────────────────────────

DASHBOARD_API = os.getenv("DASHBOARD_API", "http://127.0.0.1:8080")

# Download directories to scan, keyed by vendor
DOWNLOAD_DIRS = {
    "usfoods": Path(os.path.expanduser("~/usfoods-scraper/downloads")),
    "pfg": Path(os.path.expanduser("~/vendor-scrapers/pfg/downloads")),
    "vtinfo": Path(os.path.expanduser("~/vendor-scrapers/vtinfo/downloads")),
    "southern_glazers": Path(os.path.expanduser("~/vendor-scrapers/southern-glazers/downloads")),
    "martignetti": Path(os.path.expanduser("~/vendor-scrapers/martignetti/downloads")),
    "craft_collective": Path(os.path.expanduser("~/vendor-scrapers/craft-collective/downloads")),
}

# Map US Foods customer names → location
COMPANY_LOCATION_MAP = {
    "red nun chatham": "chatham",
    "chatham": "chatham",
    "red nun dennisport": "dennis",
    "dennisport": "dennis",
    "dennis port": "dennis",
}

LOG_PREFIX = "[import_downloads]"

# OCR timeout — PDFs through Claude Vision can take a while
OCR_TIMEOUT = 120


# ─── HELPERS ─────────────────────────────────────────────────────────────────


def log(msg):
    print(f"{LOG_PREFIX} {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def get_existing_invoice_numbers(vendor_name):
    """Query dashboard API for already-imported invoice numbers."""
    try:
        r = requests.get(
            f"{DASHBOARD_API}/api/invoices/existing",
            params={"vendor": vendor_name},
            timeout=10,
        )
        if r.status_code == 200:
            return set(r.json().get("invoice_numbers", []))
        else:
            log(f"  [WARN] /api/invoices/existing returned {r.status_code}")
            return set()
    except Exception as e:
        log(f"  [WARN] Could not check existing invoices: {e}")
        return set()


def detect_location_from_csv(csv_path):
    """Quick-read the CSV to detect location from CustomerName or ShipTo fields."""
    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            import csv
            reader = csv.reader(f)
            header = next(reader)

            col = {}
            for i, name in enumerate(header):
                name = name.strip()
                if name not in col:
                    col[name] = i

            first_row = next(reader, None)
            if not first_row:
                return None

            def get(name, default=''):
                idx = col.get(name)
                if idx is not None and idx < len(first_row):
                    return first_row[idx].strip()
                return default

            # Check ShipToZip first (most reliable)
            ship_zip = get('ShipToZip')
            if '02633' in ship_zip:
                return 'chatham'
            if '02639' in ship_zip:
                return 'dennis'

            # Check CustomerName
            customer = get('CustomerName', '').lower()
            for key, loc in COMPANY_LOCATION_MAP.items():
                if key in customer:
                    return loc

            # Check ShipToCity
            city = get('ShipToCity', '').lower()
            if 'chatham' in city:
                return 'chatham'
            if 'dennis' in city:
                return 'dennis'

    except Exception as e:
        log(f"  [WARN] Could not detect location from {csv_path.name}: {e}")

    return None


def detect_invoice_number_from_csv(csv_path):
    """Quick-read the CSV to get the invoice/credit number for dedup checking."""
    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            import csv
            reader = csv.reader(f)
            header = next(reader)

            col = {}
            for i, name in enumerate(header):
                name = name.strip()
                if name not in col:
                    col[name] = i

            first_row = next(reader, None)
            if not first_row:
                return None

            def get(name, default=''):
                idx = col.get(name)
                if idx is not None and idx < len(first_row):
                    return first_row[idx].strip()
                return default

            doc_type = get('DocumentType')
            if doc_type == 'CREDIT_MEMO':
                return get('CreditMemoNumber') or get('DocumentNumber')
            return get('DocumentNumber')

    except Exception:
        return None


def is_rednun_invoice(csv_path):
    """Check if the CSV is for a Red Nun location (not Knockout Pizza etc.)."""
    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            import csv
            reader = csv.reader(f)
            header = next(reader)

            col = {}
            for i, name in enumerate(header):
                name = name.strip()
                if name not in col:
                    col[name] = i

            first_row = next(reader, None)
            if not first_row:
                return False

            idx = col.get('CustomerName')
            if idx is not None and idx < len(first_row):
                customer = first_row[idx].strip().lower()
                return 'red nun' in customer

    except Exception:
        pass
    return False


def import_csv_file(csv_path, location=None, vendor=None, filename=None):
    """Import a single CSV file via the dashboard API."""
    params = {}
    if location:
        params["location"] = location
    if vendor:
        params["vendor"] = vendor
    if filename:
        params["filename"] = filename

    with open(csv_path, 'rb') as f:
        r = requests.post(
            f"{DASHBOARD_API}/api/invoices/import-csv",
            files={"file": (csv_path.name, f, "text/csv")},
            params=params,
            timeout=60,
        )

    return r


def upload_pdf_for_ocr(pdf_path, location=None):
    """Upload a PDF to the invoice scan endpoint for OCR processing."""
    params = {}
    if location:
        params["location"] = location

    mime = "application/pdf"
    with open(pdf_path, 'rb') as f:
        r = requests.post(
            f"{DASHBOARD_API}/api/invoices/scan",
            files={"file": (pdf_path.name, f, mime)},
            params=params,
            timeout=OCR_TIMEOUT,
        )

    return r


def archive_file(src_path, archive_dir):
    """Move a processed file to the archive directory."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / src_path.name
    if dest.exists():
        # Add timestamp to avoid overwriting
        stem = src_path.stem
        suffix = src_path.suffix
        ts = datetime.now().strftime("%H%M%S")
        dest = archive_dir / f"{stem}_{ts}{suffix}"
    shutil.move(str(src_path), str(dest))
    return dest


def extract_metadata_from_filename(filename):
    """Extract location and invoice number from standardized vendor filenames.

    Patterns:
        sg_{location}_{invnum}_{date}.pdf
        martignetti_{location}_{invnum}_{date}.pdf
        craft_invoice_{location}_{invnum}_{date}.pdf
        pfg_{location}_{invnum}_{date}.pdf
        vtinfo_{vendor}_{location}_{invnum}_{date}.csv
    """
    name = Path(filename).stem

    # Southern Glazer's: sg_chatham_645062_20260323
    m = re.match(r'sg_(chatham|dennis)_([^_]+)_(\d{8})', name)
    if m:
        return {'location': m.group(1), 'invoice_number': m.group(2)}

    # Martignetti: martignetti_chatham_US1-103221377_20260323
    m = re.match(r'martignetti_(chatham|dennis)_([^_]+)_(\d{8})', name)
    if m:
        return {'location': m.group(1), 'invoice_number': m.group(2)}

    # Craft Collective: craft_invoice_dennis_1022456_20260323
    m = re.match(r'craft_invoice_(chatham|dennis)_([^_]+)_(\d{8})', name)
    if m:
        return {'location': m.group(1), 'invoice_number': m.group(2)}

    # PFG PDF: pfg_chatham_721961_20260322
    m = re.match(r'pfg_(chatham|dennis)_([^_]+)_(\d{8})', name)
    if m:
        return {'location': m.group(1), 'invoice_number': m.group(2)}

    # VTInfo: vtinfo_colonial_chatham_542237_20260323
    m = re.match(r'vtinfo_(lknife|colonial)_(chatham|dennis)_(\d+)_(\d{8})', name)
    if m:
        return {
            'vendor_code': m.group(1),
            'location': m.group(2),
            'invoice_number': m.group(3),
        }

    return {}


# ─── VENDOR PROCESSORS ──────────────────────────────────────────────────────


def process_usfoods_downloads():
    """Process US Foods CSV files from the scraper downloads directory."""
    download_dir = DOWNLOAD_DIRS.get("usfoods")
    if not download_dir or not download_dir.exists():
        return 0, 0

    archive_dir = download_dir / "archived"
    csv_files = sorted(download_dir.glob("usfoods_*.csv"))

    if not csv_files:
        log("  No US Foods CSV files to process")
        return 0, 0

    log(f"  Found {len(csv_files)} CSV files")

    # Pre-fetch existing invoice numbers from dashboard
    existing = get_existing_invoice_numbers("US Foods")
    log(f"  Dashboard already has {len(existing)} US Foods invoices")

    imported = 0
    skipped = 0

    for csv_path in csv_files:
        # Skip non-Red-Nun invoices (e.g., Knockout Pizza)
        if not is_rednun_invoice(csv_path):
            log(f"  [SKIP] {csv_path.name} — not a Red Nun invoice")
            archive_file(csv_path, archive_dir / "skipped")
            skipped += 1
            continue

        # Check if already imported (by invoice number from CSV content)
        inv_num = detect_invoice_number_from_csv(csv_path)
        if inv_num and inv_num in existing:
            log(f"  [SKIP] {csv_path.name} — invoice #{inv_num} already imported")
            archive_file(csv_path, archive_dir)
            skipped += 1
            continue

        # Detect location from CSV content
        location = detect_location_from_csv(csv_path)

        log(f"  [IMPORT] {csv_path.name} (location={location or 'auto'}, inv#{inv_num or '?'})")

        try:
            r = import_csv_file(csv_path, location=location)

            if r.status_code == 200:
                data = r.json()
                log(f"    OK: {data.get('message', '')}")
                archive_file(csv_path, archive_dir)
                imported += 1
                if inv_num:
                    existing.add(inv_num)

            elif r.status_code == 409:
                data = r.json()
                log(f"    [DUP] {data.get('message', 'Duplicate')}")
                archive_file(csv_path, archive_dir)
                skipped += 1

            else:
                log(f"    [ERROR] HTTP {r.status_code}: {r.text[:200]}")
                skipped += 1

        except requests.exceptions.ConnectionError:
            log(f"    [ERROR] Dashboard API not reachable at {DASHBOARD_API}")
            return imported, skipped
        except Exception as e:
            log(f"    [ERROR] {e}")
            skipped += 1

        time.sleep(0.5)

    return imported, skipped


def process_pfg_downloads():
    """Process PFG CSV and PDF files."""
    download_dir = DOWNLOAD_DIRS.get("pfg")
    if not download_dir or not download_dir.exists():
        return 0, 0

    archive_dir = download_dir / "archived"
    imported = 0
    skipped = 0

    # PFG CSVs — structured data, import via CSV endpoint
    csv_files = sorted(download_dir.glob("pfg_*.csv"))
    if csv_files:
        log(f"  Found {len(csv_files)} PFG CSV files")
        for csv_path in csv_files:
            log(f"  [IMPORT] {csv_path.name}")
            try:
                r = import_csv_file(csv_path, vendor="pfg")

                if r.status_code == 200:
                    data = r.json()
                    count = data.get('count', 0)
                    dupes = data.get('duplicates', 0)
                    log(f"    OK: {count} imported, {dupes} duplicates")
                    archive_file(csv_path, archive_dir)
                    imported += count
                    skipped += dupes

                elif r.status_code == 409:
                    log(f"    [DUP] {r.json().get('message', 'Duplicate')}")
                    archive_file(csv_path, archive_dir)
                    skipped += 1

                else:
                    log(f"    [ERROR] HTTP {r.status_code}: {r.text[:200]}")
                    skipped += 1

            except requests.exceptions.ConnectionError:
                log(f"    [ERROR] Dashboard API not reachable")
                return imported, skipped
            except Exception as e:
                log(f"    [ERROR] {e}")
                skipped += 1

            time.sleep(0.5)

    # PFG PDFs — fallback downloads, need OCR
    pdf_files = sorted(download_dir.glob("pfg_*.pdf"))
    if pdf_files:
        log(f"  Found {len(pdf_files)} PFG PDF files")
        imp, skip = _process_pdf_batch(pdf_files, archive_dir, "Performance Foodservice")
        imported += imp
        skipped += skip

    if not csv_files and not pdf_files:
        log("  No PFG files to process")

    return imported, skipped


def process_vtinfo_downloads():
    """Process VTInfo CSV files (L. Knife & Son + Colonial Wholesale)."""
    download_dir = DOWNLOAD_DIRS.get("vtinfo")
    if not download_dir or not download_dir.exists():
        return 0, 0

    archive_dir = download_dir / "archived"
    csv_files = sorted(download_dir.glob("vtinfo_*.csv"))

    if not csv_files:
        log("  No VTInfo CSV files to process")
        return 0, 0

    log(f"  Found {len(csv_files)} VTInfo CSV files")

    # Pre-fetch existing invoice numbers for both vendors
    existing_lknife = get_existing_invoice_numbers("L. Knife & Son")
    existing_colonial = get_existing_invoice_numbers("Colonial Wholesale")
    log(f"  Dashboard has {len(existing_lknife)} L. Knife, {len(existing_colonial)} Colonial invoices")

    imported = 0
    skipped = 0

    for csv_path in csv_files:
        meta = extract_metadata_from_filename(csv_path.name)
        vendor_code = meta.get('vendor_code', 'colonial')
        location = meta.get('location')
        inv_num = meta.get('invoice_number')

        # Dedup check
        existing = existing_lknife if vendor_code == 'lknife' else existing_colonial
        if inv_num and inv_num in existing:
            log(f"  [SKIP] {csv_path.name} — #{inv_num} already imported")
            archive_file(csv_path, archive_dir)
            skipped += 1
            continue

        vendor_param = f"vtinfo_{vendor_code}"
        log(f"  [IMPORT] {csv_path.name} ({vendor_code}, {location})")

        try:
            r = import_csv_file(
                csv_path,
                location=location,
                vendor=vendor_param,
                filename=csv_path.name,
            )

            if r.status_code == 200:
                data = r.json()
                log(f"    OK: {data.get('message', '')}")
                archive_file(csv_path, archive_dir)
                imported += 1
                if inv_num:
                    existing.add(inv_num)

            elif r.status_code == 409:
                log(f"    [DUP] {r.json().get('message', 'Duplicate')}")
                archive_file(csv_path, archive_dir)
                skipped += 1

            else:
                log(f"    [ERROR] HTTP {r.status_code}: {r.text[:200]}")
                skipped += 1

        except requests.exceptions.ConnectionError:
            log(f"    [ERROR] Dashboard API not reachable")
            return imported, skipped
        except Exception as e:
            log(f"    [ERROR] {e}")
            skipped += 1

        time.sleep(0.5)

    return imported, skipped


def process_pdf_vendor(vendor_key, file_prefix, vendor_name):
    """Generic processor for PDF-only vendors (SG, Martignetti, Craft Collective).
    Uploads PDFs to the /api/invoices/scan endpoint for OCR via Claude Vision.
    """
    download_dir = DOWNLOAD_DIRS.get(vendor_key)
    if not download_dir or not download_dir.exists():
        return 0, 0

    archive_dir = download_dir / "archived"
    pdf_files = sorted(download_dir.glob(f"{file_prefix}*.pdf"))

    if not pdf_files:
        log(f"  No {vendor_name} PDF files to process")
        return 0, 0

    log(f"  Found {len(pdf_files)} {vendor_name} PDF files")
    return _process_pdf_batch(pdf_files, archive_dir, vendor_name)


def _process_pdf_batch(pdf_files, archive_dir, vendor_name):
    """Process a batch of PDFs through OCR. Returns (imported, skipped)."""
    # Pre-fetch existing invoice numbers
    existing = get_existing_invoice_numbers(vendor_name)
    log(f"  Dashboard has {len(existing)} {vendor_name} invoices")

    imported = 0
    skipped = 0

    for pdf_path in pdf_files:
        meta = extract_metadata_from_filename(pdf_path.name)
        location = meta.get('location')
        inv_num = meta.get('invoice_number')

        # Dedup check
        if inv_num and inv_num in existing:
            log(f"  [SKIP] {pdf_path.name} — #{inv_num} already imported")
            archive_file(pdf_path, archive_dir)
            skipped += 1
            continue

        log(f"  [OCR] {pdf_path.name} (location={location or 'auto'})")

        try:
            r = upload_pdf_for_ocr(pdf_path, location=location)

            if r.status_code == 200:
                data = r.json()
                status = data.get('status', '')
                inv_id = data.get('invoice_id', '')
                ocr_inv_num = data.get('invoice_number', inv_num or '?')
                total = data.get('total', 0)
                items = data.get('total_items', 0)
                log(f"    OK [{status}]: #{ocr_inv_num}, {items} items, ${total:.2f}")
                archive_file(pdf_path, archive_dir)
                imported += 1
                if inv_num:
                    existing.add(inv_num)
                if ocr_inv_num and ocr_inv_num != inv_num:
                    existing.add(str(ocr_inv_num))

            elif r.status_code == 409:
                log(f"    [DUP] {r.json().get('message', 'Duplicate')}")
                archive_file(pdf_path, archive_dir)
                skipped += 1

            else:
                log(f"    [ERROR] HTTP {r.status_code}: {r.text[:300]}")
                # Don't archive — leave for retry
                skipped += 1

        except requests.exceptions.Timeout:
            log(f"    [TIMEOUT] OCR took too long for {pdf_path.name}")
            skipped += 1
        except requests.exceptions.ConnectionError:
            log(f"    [ERROR] Dashboard API not reachable")
            return imported, skipped
        except Exception as e:
            log(f"    [ERROR] {e}")
            skipped += 1

        # Longer delay between OCR calls (Claude API rate limits)
        time.sleep(2)

    return imported, skipped


# ─── MAIN ────────────────────────────────────────────────────────────────────


def main():
    log("=== Import run started ===")

    total_imported = 0
    total_skipped = 0

    # US Foods CSVs
    log("Processing US Foods downloads...")
    imp, skip = process_usfoods_downloads()
    total_imported += imp
    total_skipped += skip

    # PFG CSVs + PDFs
    log("Processing PFG downloads...")
    imp, skip = process_pfg_downloads()
    total_imported += imp
    total_skipped += skip

    # VTInfo CSVs (L. Knife + Colonial)
    log("Processing VTInfo downloads...")
    imp, skip = process_vtinfo_downloads()
    total_imported += imp
    total_skipped += skip

    # Southern Glazer's PDFs → OCR
    log("Processing Southern Glazer's downloads...")
    imp, skip = process_pdf_vendor("southern_glazers", "sg_", "Southern Glazer's")
    total_imported += imp
    total_skipped += skip

    # Martignetti PDFs → OCR
    log("Processing Martignetti downloads...")
    imp, skip = process_pdf_vendor("martignetti", "martignetti_", "Martignetti")
    total_imported += imp
    total_skipped += skip

    # Craft Collective PDFs → OCR
    log("Processing Craft Collective downloads...")
    imp, skip = process_pdf_vendor("craft_collective", "craft_", "Craft Collective")
    total_imported += imp
    total_skipped += skip

    log(f"=== Done: {total_imported} imported, {total_skipped} skipped ===")


if __name__ == "__main__":
    main()
