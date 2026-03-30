"""
MarginEdge API Client
Pulls product costs, categories, vendors, and invoice data from MarginEdge
for real-time COGS and pour cost analysis.

API Docs: https://developer.marginedge.com
Base URL: https://api.marginedge.com/public
Auth: API key passed via x-api-key header
Pagination: cursor-based via 'nextPage' token
"""

import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BASE_URL = os.getenv("MARGINEDGE_BASE_URL", "https://api.marginedge.com/public")
API_KEY = os.getenv("MARGINEDGE_API_KEY", "")

# MarginEdge restaurant unit IDs (from /restaurantUnits endpoint)
UNIT_IDS = {
    "dennis": int(os.getenv("ME_UNIT_DENNIS", "361358284")),
    "chatham": int(os.getenv("ME_UNIT_CHATHAM", "449542588")),
}

# Category types that matter for COGS analysis
COGS_CATEGORY_TYPES = {"LIQUOR", "BEER", "WINE", "NA_BEVERAGES", "FOOD", "RETAIL"}


class MarginEdgeClient:
    """Client for the MarginEdge Public API."""

    def __init__(self):
        if not API_KEY:
            logger.warning("MARGINEDGE_API_KEY not set in .env file")
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": API_KEY,
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Core Request Method (cursor-based pagination)
    # ------------------------------------------------------------------

    def _get(self, endpoint, params=None, result_key=None, paginate=True):
        """
        Make a GET request to the MarginEdge API.
        Handles cursor-based pagination via 'nextPage' token.

        Args:
            endpoint: API path (e.g. '/products')
            params: Query parameters dict
            result_key: Key in response JSON containing the results list
                        (e.g. 'products', 'categories', 'restaurants')
            paginate: Whether to auto-paginate through all pages
        """
        url = f"{BASE_URL}{endpoint}"
        all_results = []
        params = dict(params or {})
        seen_ids = set()  # Detect duplicate pages (pagination end)
        page_num = 0

        while True:
            page_num += 1
            try:
                logger.debug(f"GET {url} params={params} (page {page_num})")
                resp = self.session.get(url, params=params, timeout=30)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited. Waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Extract results from the response
                if result_key and isinstance(data, dict):
                    results = data.get(result_key, [])
                elif isinstance(data, list):
                    results = data
                elif isinstance(data, dict) and not result_key:
                    return data
                else:
                    results = []

                if not results:
                    break

                # Check for duplicate page (pagination looped back)
                first_id = None
                for r in results:
                    if isinstance(r, dict):
                        first_id = (r.get("companyConceptProductId")
                                    or r.get("categoryId")
                                    or r.get("id")
                                    or str(r))
                        break
                if first_id and first_id in seen_ids:
                    logger.debug("Duplicate page detected, stopping pagination")
                    break
                if first_id:
                    seen_ids.add(first_id)

                all_results.extend(results)

                # Check for next page cursor
                if not paginate or not isinstance(data, dict):
                    break

                next_page = data.get("nextPage")
                if not next_page:
                    break

                params["page"] = next_page
                time.sleep(0.2)

            except requests.exceptions.HTTPError as e:
                logger.error(f"MarginEdge API error: {e} - {resp.text[:500]}")
                raise
            except requests.exceptions.RequestException as e:
                logger.error(f"MarginEdge request failed: {e}")
                raise

        logger.debug(f"Retrieved {len(all_results)} total results from {endpoint}")
        return all_results

    # ------------------------------------------------------------------
    # Restaurant Units
    # ------------------------------------------------------------------

    def get_restaurant_units(self):
        """
        Get all restaurant units accessible with this API key.
        Response: {"restaurants": [{"id": 123, "name": "..."}]}
        """
        resp = self.session.get(f"{BASE_URL}/restaurantUnits", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        units = data.get("restaurants", [])
        logger.info(f"Found {len(units)} restaurant units")
        for u in units:
            logger.info(f"  - {u.get('name', '?')} (ID: {u.get('id', '?')})")
        return units

    # ------------------------------------------------------------------
    # Products (Ingredients with costs)
    # ------------------------------------------------------------------

    def get_products(self, restaurant_unit_id):
        """
        Get all products for a restaurant unit.

        Response per product:
        {
            "companyConceptProductId": "460099732",
            "centralProductId": "607173197",
            "productName": "13 Celsius Sauvignon Blanc",
            "categories": [{"categoryId": "1210", "percentAllocation": 100.0}],
            "itemCount": 0,
            "taxExempt": false,
            "reportByUnit": "Bottle",
            "latestPrice": 8.50
        }
        """
        products = self._get(
            "/products",
            params={"restaurantUnitId": restaurant_unit_id},
            result_key="products",
        )
        logger.info(f"Retrieved {len(products)} products for unit {restaurant_unit_id}")
        return products

    def get_cogs_products(self, restaurant_unit_id, categories_map=None):
        """
        Get only COGS-relevant products (food, liquor, beer, wine, NA bev).
        Returns dict grouped by type.
        """
        if not categories_map:
            cats = self.get_categories(restaurant_unit_id)
            categories_map = {c["categoryId"]: c for c in cats}

        all_products = self.get_products(restaurant_unit_id)

        grouped = {
            "liquor": [], "beer": [], "wine": [],
            "na_bev": [], "food": [], "retail": [], "other": [],
        }

        type_mapping = {
            "LIQUOR": "liquor", "BEER": "beer", "WINE": "wine",
            "NA_BEVERAGES": "na_bev", "FOOD": "food", "RETAIL": "retail",
        }

        for p in all_products:
            placed = False
            for cat_ref in p.get("categories", []):
                cat_id = cat_ref.get("categoryId")
                cat_info = categories_map.get(cat_id, {})
                cat_type = cat_info.get("categoryType", "OTHER")
                group = type_mapping.get(cat_type, "other")
                if group != "other":
                    grouped[group].append(p)
                    placed = True
                    break
            if not placed:
                grouped["other"].append(p)

        for k, v in grouped.items():
            if v:
                logger.info(f"  {k}: {len(v)} products")

        return grouped

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def get_categories(self, restaurant_unit_id):
        """
        Get all categories for a restaurant unit.

        Response per category:
        {
            "categoryId": "1203",
            "categoryName": "Liquor",
            "categoryType": "LIQUOR",
            "accountingCode": null
        }
        """
        categories = self._get(
            "/categories",
            params={"restaurantUnitId": restaurant_unit_id},
            result_key="categories",
        )
        logger.info(f"Retrieved {len(categories)} categories for unit {restaurant_unit_id}")
        return categories

    # ------------------------------------------------------------------
    # Vendors
    # ------------------------------------------------------------------

    def get_vendors(self, restaurant_unit_id):
        """Get all vendors for a restaurant unit."""
        vendors = self._get(
            "/vendors",
            params={"restaurantUnitId": restaurant_unit_id},
            result_key="vendors",
        )
        logger.info(f"Retrieved {len(vendors)} vendors for unit {restaurant_unit_id}")
        return vendors

    def get_vendor_items(self, restaurant_unit_id, vendor_id):
        """Get all items from a specific vendor."""
        items = self._get(
            f"/vendors/{vendor_id}/vendorItems",
            params={"restaurantUnitId": restaurant_unit_id},
            result_key="vendorItems",
        )
        logger.info(f"Retrieved {len(items)} items for vendor {vendor_id}")
        return items

    # ------------------------------------------------------------------
    # Orders (Invoices)
    # ------------------------------------------------------------------

    def get_orders(self, restaurant_unit_id, start_date=None, end_date=None,
                   status="approved"):
        """
        Get orders/invoices within a date range.

        Args:
            restaurant_unit_id: MarginEdge restaurant unit ID
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            status: Order status filter (e.g. 'approved')
        """
        params = {"restaurantUnitId": restaurant_unit_id}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if status:
            params["status"] = status

        orders = self._get("/orders", params=params, result_key="orders")
        logger.info(f"Retrieved {len(orders)} orders for unit {restaurant_unit_id}")
        return orders

    def get_order_detail(self, order_id, restaurant_unit_id):
        """Get detailed line-item information for a specific order/invoice."""
        resp = self.session.get(
            f"{BASE_URL}/orders/{order_id}",
            params={"restaurantUnitId": restaurant_unit_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


# ------------------------------------------------------------------
# CLI Test
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = MarginEdgeClient()

    if not API_KEY:
        print("ERROR: Set MARGINEDGE_API_KEY in your .env file first!")
        exit(1)

    print("\n" + "=" * 60)
    print("  MarginEdge API Connection Test")
    print("=" * 60)

    # 1. Restaurant units
    print("\n1. Restaurant Units:")
    units = client.get_restaurant_units()
    for u in units:
        print(f"   {u['name']} (ID: {u['id']})")

    if not units:
        print("   No units found. Check API key permissions.")
        exit(1)

    # 2. Categories for Dennis Port
    unit_id = UNIT_IDS["dennis"]
    print(f"\n2. COGS Categories (Dennis Port - {unit_id}):")
    categories = client.get_categories(unit_id)
    cat_map = {c["categoryId"]: c for c in categories}

    cogs_cats = [c for c in categories if c.get("categoryType") in COGS_CATEGORY_TYPES]
    for c in sorted(cogs_cats, key=lambda x: x["categoryType"]):
        print(f"   [{c['categoryType']:15}] {c['categoryName']} (ID: {c['categoryId']})")

    # 3. Products grouped by type
    print(f"\n3. Products by COGS Group (Dennis Port):")
    grouped = client.get_cogs_products(unit_id, cat_map)
    total_cogs = 0
    for group_name, products in grouped.items():
        if products and group_name != "other":
            total_cogs += len(products)
            sample = products[0]
            print(f"   {group_name.upper():8} - {len(products):3} products "
                  f"(e.g. {sample['productName']} @ "
                  f"${sample.get('latestPrice', 0):.2f}/{sample.get('reportByUnit', '?')})")

    print(f"\n   Total COGS products: {total_cogs}")
    print(f"   Non-COGS products:  {len(grouped.get('other', []))}")

    # 4. Vendors
    print(f"\n4. Vendors (Dennis Port):")
    vendors = client.get_vendors(unit_id)
    for v in vendors[:8]:
        print(f"   {v}")

    # 5. Recent invoices
    print(f"\n5. Recent Invoices (Dennis Port, last 30 days):")
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    orders = client.get_orders(unit_id, start_date=start, end_date=end)
    print(f"   Found {len(orders)} invoices")
    if orders:
        print(f"   Sample: {orders[0]}")

    print("\n" + "=" * 60)
    print("  MarginEdge API Test Complete!")
    print("=" * 60)
