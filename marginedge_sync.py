"""
MarginEdge Sync Module
Syncs product costs, categories, vendors, and invoice data from MarginEdge
into the local SQLite database for COGS and pour cost analysis.

Usage:
    python marginedge_sync.py              # Sync all data for both locations
    python marginedge_sync.py dennis       # Sync Dennis Port only
    python marginedge_sync.py chatham      # Sync Chatham only
    python marginedge_sync.py invoices 30  # Sync last 30 days of invoices
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from marginedge_client import MarginEdgeClient, UNIT_IDS, COGS_CATEGORY_TYPES

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "toast_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_me_tables():
    """Create MarginEdge-specific tables."""
    conn = get_connection()
    conn.executescript("""
        -- MarginEdge product/ingredient costs
        CREATE TABLE IF NOT EXISTS me_products (
            product_id TEXT NOT NULL,
            location TEXT NOT NULL,
            product_name TEXT,
            category_id TEXT,
            category_name TEXT,
            category_type TEXT,
            percent_allocation REAL DEFAULT 100,
            report_by_unit TEXT,
            latest_price REAL DEFAULT 0,
            tax_exempt INTEGER DEFAULT 0,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (product_id, location)
        );

        CREATE INDEX IF NOT EXISTS idx_me_products_category
            ON me_products(category_type);
        CREATE INDEX IF NOT EXISTS idx_me_products_location
            ON me_products(location);

        -- MarginEdge categories
        CREATE TABLE IF NOT EXISTS me_categories (
            category_id TEXT NOT NULL,
            location TEXT NOT NULL,
            category_name TEXT,
            category_type TEXT,
            accounting_code TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (category_id, location)
        );

        -- MarginEdge vendors
        CREATE TABLE IF NOT EXISTS me_vendors (
            vendor_id TEXT NOT NULL,
            location TEXT NOT NULL,
            vendor_name TEXT,
            central_vendor_id TEXT,
            account_numbers TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (vendor_id, location)
        );

        -- MarginEdge invoices (order headers)
        CREATE TABLE IF NOT EXISTS me_invoices (
            order_id TEXT NOT NULL,
            location TEXT NOT NULL,
            vendor_id TEXT,
            vendor_name TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            created_date TEXT,
            order_total REAL DEFAULT 0,
            status TEXT,
            payment_account TEXT,
            customer_number TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_id, location)
        );

        CREATE INDEX IF NOT EXISTS idx_me_invoices_date
            ON me_invoices(location, invoice_date);
        CREATE INDEX IF NOT EXISTS idx_me_invoices_vendor
            ON me_invoices(vendor_id);

        -- MarginEdge invoice line items (from order detail)
        CREATE TABLE IF NOT EXISTS me_invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            location TEXT NOT NULL,
            product_id TEXT,
            product_name TEXT,
            category_id TEXT,
            category_type TEXT,
            quantity REAL DEFAULT 0,
            unit TEXT,
            unit_price REAL DEFAULT 0,
            total_price REAL DEFAULT 0,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_me_invoice_items_order
            ON me_invoice_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_me_invoice_items_category
            ON me_invoice_items(category_type);
        CREATE INDEX IF NOT EXISTS idx_me_invoice_items_date
            ON me_invoice_items(location, order_id);

        -- COGS summary by period (aggregated view for dashboard)
        CREATE TABLE IF NOT EXISTS me_cogs_summary (
            location TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            category_type TEXT NOT NULL,
            total_cost REAL DEFAULT 0,
            invoice_count INTEGER DEFAULT 0,
            item_count INTEGER DEFAULT 0,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (location, period_start, category_type)
        );

        -- MarginEdge sync log
        CREATE TABLE IF NOT EXISTS me_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            data_type TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            record_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error_message TEXT
        );
    """)
    conn.commit()
    conn.close()
    logger.info("MarginEdge tables initialized")


# ------------------------------------------------------------------
# Sync Functions
# ------------------------------------------------------------------

def sync_categories(client, location, unit_id):
    """Sync categories from MarginEdge."""
    logger.info(f"Syncing categories for {location} (unit {unit_id})...")
    categories = client.get_categories(unit_id)

    conn = get_connection()
    cursor = conn.cursor()
    count = 0

    for c in categories:
        cursor.execute("""
            INSERT OR REPLACE INTO me_categories
            (category_id, location, category_name, category_type,
             accounting_code, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            c.get("categoryId"),
            location,
            c.get("categoryName"),
            c.get("categoryType"),
            c.get("accountingCode"),
            datetime.now().isoformat(),
        ))
        count += 1

    conn.commit()
    conn.close()
    logger.info(f"  Stored {count} categories for {location}")
    return count


def sync_products(client, location, unit_id):
    """Sync products with their category info and latest prices."""
    logger.info(f"Syncing products for {location} (unit {unit_id})...")

    # Get categories first for mapping
    categories = client.get_categories(unit_id)
    cat_map = {c["categoryId"]: c for c in categories}

    # Get all products
    products = client.get_products(unit_id)

    conn = get_connection()
    cursor = conn.cursor()
    count = 0

    for p in products:
        # Get primary category
        cat_id = None
        cat_name = None
        cat_type = None
        pct_alloc = 100.0

        if p.get("categories"):
            cat_ref = p["categories"][0]
            cat_id = cat_ref.get("categoryId")
            pct_alloc = cat_ref.get("percentAllocation", 100.0)
            if cat_id in cat_map:
                cat_name = cat_map[cat_id].get("categoryName")
                cat_type = cat_map[cat_id].get("categoryType")

        cursor.execute("""
            INSERT OR REPLACE INTO me_products
            (product_id, location, product_name, category_id, category_name,
             category_type, percent_allocation, report_by_unit, latest_price,
             tax_exempt, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p.get("companyConceptProductId"),
            location,
            p.get("productName"),
            cat_id,
            cat_name,
            cat_type,
            pct_alloc,
            p.get("reportByUnit"),
            p.get("latestPrice", 0),
            1 if p.get("taxExempt") else 0,
            datetime.now().isoformat(),
        ))
        count += 1

    conn.commit()
    conn.close()
    logger.info(f"  Stored {count} products for {location}")
    return count


def sync_vendors(client, location, unit_id):
    """Sync vendor list."""
    logger.info(f"Syncing vendors for {location} (unit {unit_id})...")
    vendors = client.get_vendors(unit_id)

    conn = get_connection()
    cursor = conn.cursor()
    count = 0

    for v in vendors:
        # Collect account numbers
        accounts = v.get("vendorAccounts", [])
        acct_nums = ", ".join(a.get("vendorAccountNumber", "") for a in accounts if a.get("vendorAccountNumber"))

        cursor.execute("""
            INSERT OR REPLACE INTO me_vendors
            (vendor_id, location, vendor_name, central_vendor_id,
             account_numbers, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            v.get("vendorId"),
            location,
            v.get("vendorName"),
            v.get("centralVendorId"),
            acct_nums,
            datetime.now().isoformat(),
        ))
        count += 1

    conn.commit()
    conn.close()
    logger.info(f"  Stored {count} vendors for {location}")
    return count


def sync_invoices(client, location, unit_id, days_back=30):
    """Sync invoices (order headers) from MarginEdge."""
    logger.info(f"Syncing invoices for {location} (last {days_back} days)...")

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    orders = client.get_orders(unit_id, start_date=start_date, end_date=end_date)

    conn = get_connection()
    cursor = conn.cursor()
    count = 0

    for o in orders:
        cursor.execute("""
            INSERT OR REPLACE INTO me_invoices
            (order_id, location, vendor_id, vendor_name, invoice_number,
             invoice_date, created_date, order_total, status,
             payment_account, customer_number, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            o.get("orderId"),
            location,
            o.get("vendorId"),
            o.get("vendorName"),
            o.get("invoiceNumber"),
            o.get("invoiceDate"),
            o.get("createdDate"),
            o.get("orderTotal", 0),
            o.get("status"),
            o.get("paymentAccount"),
            o.get("customerNumber"),
            datetime.now().isoformat(),
        ))
        count += 1

    conn.commit()
    conn.close()
    logger.info(f"  Stored {count} invoices for {location}")

    # Sync line items for each invoice
    sync_invoice_items(client, location, unit_id, [o.get("orderId") for o in orders])

    return count


def sync_invoice_items(client, location, unit_id, order_ids):
    """Sync invoice line items with product category mapping."""
    logger.info(f"Syncing invoice line items for {location} ({len(order_ids)} invoices)...")

    # Build product -> category map from local DB
    conn = get_connection()
    prod_rows = conn.execute(
        "SELECT product_id, category_id, category_type FROM me_products WHERE location = ?",
        (location,)
    ).fetchall()
    prod_cat = {r["product_id"]: (r["category_id"], r["category_type"]) for r in prod_rows}

    # Skip invoices we already have line items for
    existing = conn.execute(
        "SELECT DISTINCT order_id FROM me_invoice_items WHERE location = ?",
        (location,)
    ).fetchall()
    existing_ids = {r["order_id"] for r in existing}

    new_ids = [oid for oid in order_ids if str(oid) not in existing_ids]
    if not new_ids:
        logger.info(f"  All {len(order_ids)} invoices already have line items")
        conn.close()
        return 0

    logger.info(f"  Fetching detail for {len(new_ids)} new invoices (skipping {len(existing_ids)} existing)...")

    cursor = conn.cursor()
    total_items = 0
    import time

    for i, oid in enumerate(new_ids):
        try:
            detail = client.get_order_detail(oid, unit_id)
            line_items = detail.get("lineItems", [])

            for li in line_items:
                prod_id = li.get("companyConceptProductId")
                cat_id = li.get("categoryId")
                cat_type = None

                # Look up category from product mapping
                if prod_id and prod_id in prod_cat:
                    cat_id = cat_id or prod_cat[prod_id][0]
                    cat_type = prod_cat[prod_id][1]

                cursor.execute("""
                    INSERT OR REPLACE INTO me_invoice_items
                    (order_id, location, product_id, product_name,
                     category_id, category_type, quantity, unit,
                     unit_price, total_price, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(oid),
                    location,
                    prod_id,
                    li.get("vendorItemName"),
                    cat_id,
                    cat_type,
                    li.get("quantity", 0),
                    None,
                    li.get("unitPrice", 0),
                    li.get("linePrice", 0),
                    datetime.now().isoformat(),
                ))
                total_items += 1

            # Rate limit: 1 request per second
            time.sleep(1.0)

            # Commit every 10 invoices
            if (i + 1) % 10 == 0:
                conn.commit()
                logger.info(f"  Progress: {i+1}/{len(new_ids)} invoices, {total_items} items...")

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg:
                logger.warning(f"  Rate limited at invoice {i+1}/{len(new_ids)}, waiting 30s...")
                conn.commit()
                time.sleep(30)
                # Retry this one
                try:
                    detail = client.get_order_detail(oid, unit_id)
                    for li in detail.get("lineItems", []):
                        prod_id = li.get("companyConceptProductId")
                        cat_id = li.get("categoryId")
                        cat_type = None
                        if prod_id and prod_id in prod_cat:
                            cat_id = cat_id or prod_cat[prod_id][0]
                            cat_type = prod_cat[prod_id][1]
                        cursor.execute("""
                            INSERT OR REPLACE INTO me_invoice_items
                            (order_id, location, product_id, product_name,
                             category_id, category_type, quantity, unit,
                             unit_price, total_price, synced_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(oid), location, prod_id, li.get("vendorItemName"),
                            cat_id, cat_type, li.get("quantity", 0), None,
                            li.get("unitPrice", 0), li.get("linePrice", 0),
                            datetime.now().isoformat(),
                        ))
                        total_items += 1
                    time.sleep(1.0)
                except Exception as e2:
                    logger.warning(f"  Retry failed for order {oid}: {e2}")
            else:
                logger.warning(f"  Could not fetch detail for order {oid}: {e}")
            continue

    conn.commit()
    conn.close()
    logger.info(f"  Stored {total_items} line items for {location}")
    return total_items


def build_cogs_summary(location, period_start, period_end):
    """
    Build COGS summary from invoices + product category data.
    Aggregates invoice totals by category type for a given period.
    """
    logger.info(f"Building COGS summary for {location} ({period_start} to {period_end})...")

    conn = get_connection()
    cursor = conn.cursor()

    # Aggregate invoice totals by vendor, then map vendors to categories
    # For now, use a simpler approach: sum invoice totals and use
    # vendor-to-category heuristics based on known vendor types
    #
    # Better approach when we have invoice line items:
    # JOIN me_invoice_items with me_products to get category-level COGS

    # Get invoices in the period
    cursor.execute("""
        SELECT vendor_name, SUM(order_total) as total, COUNT(*) as cnt
        FROM me_invoices
        WHERE location = ? AND invoice_date >= ? AND invoice_date <= ?
        GROUP BY vendor_name
        ORDER BY total DESC
    """, (location, period_start, period_end))

    vendor_totals = cursor.fetchall()

    # Known vendor -> category mappings for Red Nun
    # (This can be made configurable later)
    vendor_category_hints = {
        # Liquor/Wine distributors
        "southern glazer": "LIQUOR",
        "l. knife": "LIQUOR",
        "martignetti": "LIQUOR",
        "atlantic beverage": "LIQUOR",
        "horizon beverage": "LIQUOR",
        # Beer distributors
        "colonial wholesale": "BEER",
        "craft collective": "BEER",
        "cape cod beer": "BEER",
        # Food broadline
        "us foods": "FOOD",
        "reinhart": "FOOD",
        "performance food": "FOOD",
        "chefs warehouse": "FOOD",
        "cape fish": "FOOD",
        "sysco": "FOOD",
        # Non-COGS (uniforms, linens, cleaning, etc.)
        "cintas": "NON_COGS",
        "unifirst": "NON_COGS",
        "cozzini": "NON_COGS",
        "rooter": "NON_COGS",
        "dennisport village": "NON_COGS",
        "caron group": "NON_COGS",
        "robert b. our": "NON_COGS",
        "marginedge": "NON_COGS",
    }

    category_totals = {}
    for row in vendor_totals:
        vname = (row["vendor_name"] or "").lower()
        matched = "OTHER"
        for hint, cat in vendor_category_hints.items():
            if hint in vname:
                matched = cat
                break
        if matched not in category_totals:
            category_totals[matched] = {"total": 0, "invoices": 0}
        category_totals[matched]["total"] += row["total"]
        category_totals[matched]["invoices"] += row["cnt"]

    # Store summary - clear old data first
    cursor.execute("DELETE FROM me_cogs_summary WHERE location = ?", (location,))
    for cat_type, data in category_totals.items():
        cursor.execute("""
            INSERT OR REPLACE INTO me_cogs_summary
            (location, period_start, period_end, category_type,
             total_cost, invoice_count, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            location, period_start, period_end, cat_type,
            data["total"], data["invoices"],
            datetime.now().isoformat(),
        ))

    conn.commit()
    conn.close()

    logger.info(f"  COGS summary for {location}:")
    for cat, data in sorted(category_totals.items()):
        logger.info(f"    {cat:15} ${data['total']:,.2f} ({data['invoices']} invoices)")

    return category_totals


# ------------------------------------------------------------------
# Full Sync
# ------------------------------------------------------------------

def sync_location(client, location, unit_id, invoice_days=30):
    """Run full sync for one location."""
    log_id = log_sync_start(location, "full")

    try:
        results = {}
        results["categories"] = sync_categories(client, location, unit_id)
        results["products"] = sync_products(client, location, unit_id)
        results["vendors"] = sync_vendors(client, location, unit_id)
        results["invoices"] = sync_invoices(client, location, unit_id, invoice_days)

        # Build COGS summary for last 30 days
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=invoice_days)).strftime("%Y-%m-%d")
        results["cogs"] = build_cogs_summary(location, start_date, end_date)

        total = sum(v for v in results.values() if isinstance(v, int))
        log_sync_complete(log_id, total, "success")

        logger.info(f"\n  === {location.upper()} sync complete ===")
        logger.info(f"  Categories: {results['categories']}")
        logger.info(f"  Products:   {results['products']}")
        logger.info(f"  Vendors:    {results['vendors']}")
        logger.info(f"  Invoices:   {results['invoices']}")

        return results

    except Exception as e:
        log_sync_complete(log_id, 0, "error", str(e))
        logger.error(f"Sync failed for {location}: {e}")
        raise


def sync_all(invoice_days=30):
    """Sync both locations."""
    client = MarginEdgeClient()

    print("\n" + "=" * 60)
    print("  MarginEdge Data Sync")
    print("=" * 60)

    for location, unit_id in UNIT_IDS.items():
        print(f"\n--- Syncing {location.upper()} (unit {unit_id}) ---")
        sync_location(client, location, unit_id, invoice_days)

    print("\n" + "=" * 60)
    print("  MarginEdge Sync Complete!")
    print("=" * 60)

    # Print summary
    print_cogs_summary()


# ------------------------------------------------------------------
# Reporting
# ------------------------------------------------------------------

def print_cogs_summary():
    """Print current COGS data from the database."""
    conn = get_connection()
    cursor = conn.cursor()

    print("\n--- Product Cost Summary ---")
    for location in ["dennis", "chatham"]:
        cursor.execute("""
            SELECT category_type, COUNT(*) as cnt,
                   ROUND(AVG(latest_price), 2) as avg_price,
                   ROUND(SUM(latest_price), 2) as total_catalog_value
            FROM me_products
            WHERE location = ? AND category_type IN ('LIQUOR','BEER','WINE','FOOD','NA_BEVERAGES')
            GROUP BY category_type
            ORDER BY category_type
        """, (location,))
        rows = cursor.fetchall()
        if rows:
            print(f"\n  {location.upper()}:")
            for r in rows:
                print(f"    {r['category_type']:15} {r['cnt']:3} items, "
                      f"avg ${r['avg_price']:.2f}, catalog ${r['total_catalog_value']:.2f}")

    print("\n--- Invoice Spending (Last 30 Days) ---")
    for location in ["dennis", "chatham"]:
        cursor.execute("""
            SELECT category_type, total_cost, invoice_count
            FROM me_cogs_summary
            WHERE location = ?
            ORDER BY total_cost DESC
        """, (location,))
        rows = cursor.fetchall()
        if rows:
            total = sum(r["total_cost"] for r in rows)
            print(f"\n  {location.upper()} — Total: ${total:,.2f}")
            for r in rows:
                pct = (r["total_cost"] / total * 100) if total > 0 else 0
                print(f"    {r['category_type']:15} ${r['total_cost']:>10,.2f} "
                      f"({pct:5.1f}%) — {r['invoice_count']} invoices")

    conn.close()


# ------------------------------------------------------------------
# Sync Logging
# ------------------------------------------------------------------

def log_sync_start(location, data_type):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO me_sync_log (location, data_type, started_at, status)
        VALUES (?, ?, ?, 'running')
    """, (location, data_type, datetime.now().isoformat()))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def log_sync_complete(log_id, count, status, error=None):
    conn = get_connection()
    conn.execute("""
        UPDATE me_sync_log
        SET completed_at = ?, record_count = ?, status = ?, error_message = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), count, status, error, log_id))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize tables
    init_me_tables()

    args = sys.argv[1:]

    if not args:
        # Default: sync everything
        sync_all(invoice_days=30)

    elif args[0] in ("dennis", "chatham"):
        location = args[0]
        unit_id = UNIT_IDS[location]
        days = int(args[1]) if len(args) > 1 else 30
        client = MarginEdgeClient()
        sync_location(client, location, unit_id, days)
        print_cogs_summary()

    elif args[0] == "invoices":
        days = int(args[1]) if len(args) > 1 else 30
        client = MarginEdgeClient()
        for location, unit_id in UNIT_IDS.items():
            sync_invoices(client, location, unit_id, days)
        print_cogs_summary()

    elif args[0] == "summary":
        print_cogs_summary()

    else:
        print("Usage:")
        print("  python marginedge_sync.py              # Full sync both locations")
        print("  python marginedge_sync.py dennis        # Sync Dennis Port only")
        print("  python marginedge_sync.py chatham       # Sync Chatham only")
        print("  python marginedge_sync.py invoices 60   # Sync invoices (last N days)")
        print("  python marginedge_sync.py summary       # Print COGS summary")
