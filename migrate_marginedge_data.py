"""
MarginEdge Data Migration Script
Imports vendors, products, and invoices from MarginEdge into the new inventory management system.

Usage:
    python migrate_marginedge_data.py              # Migrate last 30 days
    python migrate_marginedge_data.py --sync       # Sync fresh data from MarginEdge first
    python migrate_marginedge_data.py --days 60    # Migrate last 60 days
"""

import os
import sys
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "toast_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def migrate_vendors():
    """Migrate vendors from me_vendors to vendors table."""
    logger.info("Migrating vendors...")
    conn = get_connection()
    cursor = conn.cursor()

    # Get all unique vendors from me_vendors (both locations)
    vendors = conn.execute("""
        SELECT DISTINCT vendor_name,
               MAX(vendor_id) as vendor_id,
               MAX(central_vendor_id) as central_vendor_id,
               MAX(account_numbers) as account_numbers,
               CASE
                   WHEN vendor_name LIKE '%glazer%' OR vendor_name LIKE '%martignetti%'
                        OR vendor_name LIKE '%knife%' OR vendor_name LIKE '%atlantic bev%'
                        OR vendor_name LIKE '%horizon bev%' THEN 'LIQUOR'
                   WHEN vendor_name LIKE '%colonial%' OR vendor_name LIKE '%craft collective%'
                        OR vendor_name LIKE '%beer%' THEN 'BEER'
                   WHEN vendor_name LIKE '%us foods%' OR vendor_name LIKE '%reinhart%'
                        OR vendor_name LIKE '%sysco%' OR vendor_name LIKE '%performance food%'
                        OR vendor_name LIKE '%chefs warehouse%' OR vendor_name LIKE '%fish%' THEN 'FOOD'
                   WHEN vendor_name LIKE '%cintas%' OR vendor_name LIKE '%unifirst%' THEN 'SUPPLIES'
                   ELSE 'OTHER'
               END as category
        FROM me_vendors
        WHERE vendor_name IS NOT NULL AND vendor_name != ''
        GROUP BY vendor_name
    """).fetchall()

    count = 0
    for v in vendors:
        # Check if vendor already exists
        existing = conn.execute(
            "SELECT id FROM vendors WHERE name = ?",
            (v['vendor_name'],)
        ).fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO vendors (name, category, account_number, notes, active)
                VALUES (?, ?, ?, ?, 1)
            """, (
                v['vendor_name'],
                v['category'],
                v['account_numbers'],
                f"MarginEdge ID: {v['vendor_id']}, Central ID: {v['central_vendor_id']}"
            ))
            count += 1
        else:
            # Update existing vendor with additional info
            cursor.execute("""
                UPDATE vendors
                SET category = COALESCE(category, ?),
                    account_number = COALESCE(account_number, ?),
                    notes = COALESCE(notes, '') || ' ' || ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                v['category'],
                v['account_numbers'],
                f"MarginEdge ID: {v['vendor_id']}",
                existing['id']
            ))

    conn.commit()
    logger.info(f"  Migrated {count} new vendors ({len(vendors) - count} already existed)")

    # Return vendor name -> id mapping
    vendor_map = {}
    for row in conn.execute("SELECT id, name FROM vendors").fetchall():
        vendor_map[row['name']] = row['id']

    conn.close()
    return vendor_map


def migrate_products(vendor_map):
    """Migrate products from me_products to products table."""
    logger.info("Migrating products...")
    conn = get_connection()
    cursor = conn.cursor()

    # Get all unique products from me_products (prefer dennis location for pricing)
    products = conn.execute("""
        SELECT
            product_name,
            category_type,
            report_by_unit,
            MAX(latest_price) as latest_price,
            MAX(product_id) as me_product_id,
            GROUP_CONCAT(DISTINCT location) as locations
        FROM me_products
        WHERE product_name IS NOT NULL
            AND category_type IN ('LIQUOR', 'BEER', 'WINE', 'NA_BEVERAGES', 'FOOD')
        GROUP BY product_name, category_type, report_by_unit
        ORDER BY product_name
    """).fetchall()

    count = 0
    category_map = {
        'LIQUOR': 'LIQUOR',
        'BEER': 'BEER',
        'WINE': 'WINE',
        'NA_BEVERAGES': 'NA_BEVERAGES',
        'FOOD': 'FOOD'
    }

    for p in products:
        # Check if product already exists
        existing = conn.execute(
            "SELECT id FROM products WHERE name = ? AND category = ?",
            (p['product_name'], category_map.get(p['category_type'], 'OTHER'))
        ).fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO products (
                    name, category, unit, current_price,
                    notes, active
                )
                VALUES (?, ?, ?, ?, ?, 1)
            """, (
                p['product_name'],
                category_map.get(p['category_type'], 'OTHER'),
                p['report_by_unit'],
                p['latest_price'],
                f"MarginEdge ID: {p['me_product_id']}, Locations: {p['locations']}"
            ))
            count += 1

    conn.commit()
    logger.info(f"  Migrated {count} new products ({len(products) - count} already existed)")

    # Return product name -> id mapping
    product_map = {}
    for row in conn.execute("SELECT id, name FROM products").fetchall():
        product_map[row['name']] = row['id']

    conn.close()
    return product_map


def migrate_invoices(vendor_map, days_back=30):
    """Migrate invoices from me_invoices to invoice_scans table."""
    logger.info(f"Migrating invoices (last {days_back} days)...")
    conn = get_connection()
    cursor = conn.cursor()

    # Calculate date range
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Get invoices from me_invoices (MarginEdge uses CLOSED status, not approved)
    invoices = conn.execute("""
        SELECT
            i.*,
            (SELECT SUM(total_price) FROM me_invoice_items
             WHERE order_id = i.order_id AND location = i.location) as calculated_total,
            (SELECT COUNT(*) FROM me_invoice_items
             WHERE order_id = i.order_id AND location = i.location) as item_count
        FROM me_invoices i
        WHERE i.invoice_date >= ? AND i.invoice_date <= ?
            AND i.status IN ('CLOSED', 'approved')
        ORDER BY i.invoice_date DESC, i.vendor_name
    """, (start_date, end_date)).fetchall()

    count = 0
    for inv in invoices:
        vendor_id = vendor_map.get(inv['vendor_name'])

        # Use invoice_number if available, otherwise use order_id as reference
        inv_ref = inv['invoice_number'] if inv['invoice_number'] else f"ME-{inv['order_id']}"

        # Check if invoice already exists
        existing = conn.execute(
            "SELECT id FROM scanned_invoices WHERE invoice_number = ? AND vendor_name = ?",
            (inv_ref, inv['vendor_name'])
        ).fetchone()

        if not existing:
            # Use calculated total from line items if available, otherwise use order total
            total = inv['calculated_total'] if inv['calculated_total'] else inv['order_total']

            cursor.execute("""
                INSERT INTO scanned_invoices (
                    invoice_number, vendor_name, invoice_date,
                    total, location, status,
                    notes, raw_extraction, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?, CURRENT_TIMESTAMP)
            """, (
                inv_ref,
                inv['vendor_name'],
                inv['invoice_date'],
                total,
                inv['location'],
                f"Imported from MarginEdge",
                f"MarginEdge Order ID: {inv['order_id']}, {inv['item_count']} line items"
            ))
            count += 1

    conn.commit()
    logger.info(f"  Migrated {count} new invoices ({len(invoices) - count} already existed)")
    conn.close()
    return count


def migrate_invoice_items():
    """Migrate invoice line items from me_invoice_items to scanned_invoice_items."""
    logger.info("Migrating invoice line items...")
    conn = get_connection()
    cursor = conn.cursor()

    # Get mapping of invoice numbers to scanned_invoices.id
    # Invoice numbers may be actual numbers or "ME-{order_id}" format
    invoice_map = {}
    for row in conn.execute("""
        SELECT id, invoice_number, vendor_name, notes
        FROM scanned_invoices
        WHERE invoice_number IS NOT NULL
    """).fetchall():
        key = f"{row['invoice_number']}:{row['vendor_name']}"
        invoice_map[key] = row['id']

        # Also map by order_id if this is a MarginEdge import
        if row['notes'] and 'MarginEdge Order ID:' in row['notes']:
            try:
                order_id = row['notes'].split('Order ID: ')[1].split(',')[0]
                alt_key = f"ME-{order_id}:{row['vendor_name']}"
                invoice_map[alt_key] = row['id']
            except:
                pass

    # Get line items from me_invoice_items
    items = conn.execute("""
        SELECT
            mii.*,
            mi.invoice_number,
            mi.vendor_name,
            mi.order_id
        FROM me_invoice_items mii
        JOIN me_invoices mi ON mii.order_id = mi.order_id AND mii.location = mi.location
        ORDER BY mii.order_id
    """).fetchall()

    count = 0
    skipped = 0

    for item in items:
        # Find matching scanned invoice - try both invoice_number and ME-{order_id}
        inv_ref = item['invoice_number'] if item['invoice_number'] else f"ME-{item['order_id']}"
        key = f"{inv_ref}:{item['vendor_name']}"
        invoice_id = invoice_map.get(key)

        if not invoice_id:
            skipped += 1
            continue

        # Check if item already exists
        existing = conn.execute("""
            SELECT id FROM scanned_invoice_items
            WHERE invoice_id = ? AND product_name = ? AND unit_price = ?
        """, (invoice_id, item['product_name'], item['unit_price'])).fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO scanned_invoice_items (
                    invoice_id, product_name, quantity, unit,
                    unit_price, total_price, category_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_id,
                item['product_name'],
                item['quantity'],
                item['unit'],
                item['unit_price'],
                item['total_price'],
                item['category_type']
            ))
            count += 1

    conn.commit()
    logger.info(f"  Migrated {count} line items ({skipped} skipped - no matching invoice)")
    conn.close()
    return count


def print_summary():
    """Print summary of migrated data."""
    conn = get_connection()

    print("\n" + "=" * 60)
    print("  MIGRATION SUMMARY")
    print("=" * 60)

    # Vendors by category
    print("\n📦 VENDORS BY CATEGORY:")
    vendors = conn.execute("""
        SELECT category, COUNT(*) as count
        FROM vendors
        WHERE active = 1
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()
    for v in vendors:
        print(f"  {v['category']:15} {v['count']:3} vendors")

    # Products by category
    print("\n🏷️  PRODUCTS BY CATEGORY:")
    products = conn.execute("""
        SELECT category, COUNT(*) as count,
               ROUND(AVG(current_price), 2) as avg_price
        FROM products
        WHERE active = 1
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()
    for p in products:
        print(f"  {p['category']:15} {p['count']:4} products (avg ${p['avg_price']:.2f})")

    # Invoices by location and date range
    print("\n📄 INVOICES:")
    invoices = conn.execute("""
        SELECT
            location,
            COUNT(*) as count,
            ROUND(SUM(total), 2) as total_amount,
            MIN(invoice_date) as earliest,
            MAX(invoice_date) as latest
        FROM scanned_invoices
        WHERE status = 'confirmed'
        GROUP BY location
    """).fetchall()
    for i in invoices:
        print(f"  {i['location'].upper():8} {i['count']:3} invoices, "
              f"${i['total_amount']:>10,.2f} ({i['earliest']} to {i['latest']})")

    # Invoice items summary
    items = conn.execute("""
        SELECT COUNT(*) as count,
               ROUND(SUM(total_price), 2) as total
        FROM scanned_invoice_items
    """).fetchone()
    if items and items['count'] > 0:
        print(f"\n📦 INVOICE LINE ITEMS: {items['count']} items totaling ${items['total']:,.2f}")

    # Top vendors by spend
    print("\n💰 TOP VENDORS (Last 30 days):")
    top_vendors = conn.execute("""
        SELECT
            vendor_name,
            COUNT(*) as invoice_count,
            ROUND(SUM(total), 2) as total_spent
        FROM scanned_invoices
        WHERE status = 'confirmed'
            AND invoice_date >= date('now', '-30 days')
        GROUP BY vendor_name
        ORDER BY total_spent DESC
        LIMIT 10
    """).fetchall()
    for tv in top_vendors:
        print(f"  {tv['vendor_name'][:30]:30} {tv['invoice_count']:2} inv  ${tv['total_spent']:>10,.2f}")

    print("\n" + "=" * 60)

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate MarginEdge data to inventory management system")
    parser.add_argument("--sync", action="store_true", help="Sync fresh data from MarginEdge API first")
    parser.add_argument("--days", type=int, default=30, help="Number of days of invoice history to migrate")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    print("\n" + "=" * 60)
    print("  MarginEdge → Inventory Management Migration")
    print("=" * 60)

    # Sync from MarginEdge API if requested
    if args.sync:
        print("\n⏳ Syncing fresh data from MarginEdge API...")
        try:
            from marginedge_sync import sync_all, init_me_tables
            init_me_tables()
            sync_all(invoice_days=args.days)
        except Exception as e:
            logger.error(f"Failed to sync from MarginEdge: {e}")
            print("\n⚠️  Sync failed, but will attempt to migrate existing data...")

    # Run migrations
    try:
        print("\n⏳ Migrating data...")
        vendor_map = migrate_vendors()
        product_map = migrate_products(vendor_map)
        invoice_count = migrate_invoices(vendor_map, args.days)
        item_count = migrate_invoice_items()

        print("\n✅ Migration complete!")
        print_summary()

        print("\n🎉 You can now view the data at:")
        print("   • Management Hub: http://159.65.180.102:8080/manage")
        print("   • Invoices:       http://159.65.180.102:8080/invoices")

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
