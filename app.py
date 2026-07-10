#!/usr/bin/env python3
"""Shopify SQL — a local SQL workbench for Shopify admin CSV exports.

Loads the three Shopify exports (customers, orders, products) from ./data
into an in-memory DuckDB database, models them into clean relational tables,
and serves a browser UI where you can run SQL against them.

Usage:
    python app.py [--data DIR] [--port PORT] [--host HOST] [--no-browser]

Only dependency: duckdb  (pip install -r requirements.txt)
"""

import argparse
import csv
import json
import math
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_ROWS = 10_000  # hard cap on rows returned to the browser

# ---------------------------------------------------------------------------
# Locating the three exports. Files are identified by their header row, not
# their filename, so any export name from Shopify admin works.
# ---------------------------------------------------------------------------

SIGNATURES = {
    "customers": {"Customer ID", "Total Spent"},
    "orders": {"Lineitem name", "Financial Status"},
    "products": {"Handle", "Variant Price"},
}


def sniff_kind(path: Path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            header = set(next(csv.reader(f)))
    except (StopIteration, OSError, UnicodeDecodeError):
        return None
    for kind, signature in SIGNATURES.items():
        if signature <= header:
            return kind
    return None


def find_data_files(data_dir: Path):
    found = {}
    for path in sorted(data_dir.glob("*.csv")):
        kind = sniff_kind(path)
        if kind and kind not in found:
            found[kind] = path
    missing = [k for k in SIGNATURES if k not in found]
    if missing:
        sys.exit(
            f"error: could not find {', '.join(missing)} export(s) in {data_dir}/\n"
            "Drop your three Shopify admin CSV exports (customers, orders, products)\n"
            "into that folder — any filename works, files are identified by their columns."
        )
    return found


# ---------------------------------------------------------------------------
# Data modeling. Raw CSVs are loaded as all-VARCHAR tables with their original
# Shopify headers (raw_customers / raw_orders / raw_products), then cleaned
# into typed, snake_cased tables. Shopify export quirks handled here:
#   * IDs, zips and phones prefixed with a literal apostrophe ('11201)
#   * orders CSV has one row per line item; order-level fields only on the
#     first row of each order
#   * products CSV has one row per variant, plus image-only rows; product
#     fields only on the first row of each product
#   * yes/no and true/false strings instead of booleans
#   * timestamps like "2026-07-04 13:52:26 -0400"
# ---------------------------------------------------------------------------

# Shopify timestamps look like "2026-07-04 13:52:26 -0400" and are always in
# the store's local timezone; keep the wall-clock part as a naive TIMESTAMP so
# dates group the way they do in Shopify admin.
TRANSFORM_SQL = """
CREATE MACRO shopify_ts(s) AS TRY_STRPTIME(substr(s, 1, 19), '%Y-%m-%d %H:%M:%S');

CREATE TABLE customers AS
SELECT
    TRY_CAST(regexp_replace("Customer ID", '^''', '') AS BIGINT) AS customer_id,
    "First Name"                                            AS first_name,
    "Last Name"                                             AS last_name,
    lower("Email")                                          AS email,
    ("Accepts Email Marketing" = 'yes')                     AS accepts_email_marketing,
    ("Accepts SMS Marketing" = 'yes')                       AS accepts_sms_marketing,
    ("Accepts WhatsApp Marketing" = 'yes')                  AS accepts_whatsapp_marketing,
    "Default Address Company"                               AS company,
    "Default Address Address1"                              AS address1,
    "Default Address Address2"                              AS address2,
    "Default Address City"                                  AS city,
    "Default Address Province Code"                         AS province_code,
    "Default Address Country Code"                          AS country_code,
    regexp_replace("Default Address Zip", '^''', '')        AS zip,
    regexp_replace("Default Address Phone", '^''', '')      AS address_phone,
    regexp_replace("Phone", '^''', '')                      AS phone,
    TRY_CAST("Total Spent" AS DECIMAL(12,2))                AS total_spent,
    TRY_CAST("Total Orders" AS INTEGER)                     AS total_orders,
    ("Tax Exempt" = 'yes')                                  AS tax_exempt,
    "Note"                                                  AS note,
    "Tags"                                                  AS tags
FROM raw_customers;

CREATE TABLE order_items AS
SELECT
    "Name"                                                  AS order_name,
    MAX(TRY_CAST("Id" AS BIGINT)) OVER (PARTITION BY "Name") AS order_id,
    lower("Email")                                          AS email,
    shopify_ts("Created at")                        AS created_at,
    TRY_CAST("Lineitem quantity" AS INTEGER)                AS quantity,
    "Lineitem name"                                         AS lineitem_name,
    TRY_CAST("Lineitem price" AS DECIMAL(12,2))             AS price,
    TRY_CAST("Lineitem compare at price" AS DECIMAL(12,2))  AS compare_at_price,
    TRY_CAST("Lineitem quantity" AS INTEGER)
      * TRY_CAST("Lineitem price" AS DECIMAL(12,2))         AS line_total,
    TRY_CAST("Lineitem discount" AS DECIMAL(12,2))          AS discount,
    nullif("Lineitem sku", '')                              AS sku,
    ("Lineitem requires shipping" = 'true')                 AS requires_shipping,
    ("Lineitem taxable" = 'true')                           AS taxable,
    "Lineitem fulfillment status"                           AS fulfillment_status,
    "Vendor"                                                AS vendor
FROM raw_orders;

CREATE TABLE orders AS
WITH item_agg AS (
    SELECT
        "Name"                                              AS order_name,
        count(*)                                            AS line_count,
        sum(TRY_CAST("Lineitem quantity" AS INTEGER))       AS total_quantity
    FROM raw_orders
    GROUP BY 1
)
SELECT
    o."Name"                                                AS order_name,
    TRY_CAST(o."Id" AS BIGINT)                              AS order_id,
    lower(o."Email")                                        AS email,
    o."Financial Status"                                    AS financial_status,
    o."Fulfillment Status"                                  AS fulfillment_status,
    shopify_ts(o."Created at")                      AS created_at,
    shopify_ts(o."Paid at")                         AS paid_at,
    shopify_ts(o."Fulfilled at")                    AS fulfilled_at,
    shopify_ts(o."Cancelled at")                    AS cancelled_at,
    (o."Accepts Marketing" = 'yes')                         AS accepts_marketing,
    o."Currency"                                            AS currency,
    TRY_CAST(o."Subtotal" AS DECIMAL(12,2))                 AS subtotal,
    TRY_CAST(o."Shipping" AS DECIMAL(12,2))                 AS shipping,
    TRY_CAST(o."Taxes" AS DECIMAL(12,2))                    AS taxes,
    TRY_CAST(o."Total" AS DECIMAL(12,2))                    AS total,
    nullif(o."Discount Code", '')                           AS discount_code,
    TRY_CAST(o."Discount Amount" AS DECIMAL(12,2))          AS discount_amount,
    TRY_CAST(o."Refunded Amount" AS DECIMAL(12,2))          AS refunded_amount,
    TRY_CAST(o."Outstanding Balance" AS DECIMAL(12,2))      AS outstanding_balance,
    o."Shipping Method"                                     AS shipping_method,
    a.line_count,
    a.total_quantity,
    o."Billing Name"                                        AS billing_name,
    o."Billing Street"                                      AS billing_street,
    o."Billing Address1"                                    AS billing_address1,
    o."Billing Address2"                                    AS billing_address2,
    o."Billing Company"                                     AS billing_company,
    o."Billing City"                                        AS billing_city,
    regexp_replace(o."Billing Zip", '^''', '')              AS billing_zip,
    o."Billing Province"                                    AS billing_province,
    o."Billing Province Name"                               AS billing_province_name,
    o."Billing Country"                                     AS billing_country,
    o."Billing Phone"                                       AS billing_phone,
    o."Shipping Name"                                       AS shipping_name,
    o."Shipping Street"                                     AS shipping_street,
    o."Shipping Address1"                                   AS shipping_address1,
    o."Shipping Address2"                                   AS shipping_address2,
    o."Shipping Company"                                    AS shipping_company,
    o."Shipping City"                                       AS shipping_city,
    regexp_replace(o."Shipping Zip", '^''', '')             AS shipping_zip,
    o."Shipping Province"                                   AS shipping_province,
    o."Shipping Province Name"                              AS shipping_province_name,
    o."Shipping Country"                                    AS shipping_country,
    o."Shipping Phone"                                      AS shipping_phone,
    o."Notes"                                               AS notes,
    nullif(o."Note Attributes", '')                         AS note_attributes,
    o."Payment Method"                                      AS payment_method,
    o."Payment Reference"                                   AS payment_reference,
    o."Payment ID"                                          AS payment_id,
    o."Payment Terms Name"                                  AS payment_terms_name,
    shopify_ts(o."Next Payment Due At")             AS next_payment_due_at,
    o."Payment References"                                  AS payment_references,
    o."Employee"                                            AS employee,
    o."Location"                                            AS location,
    o."Device ID"                                           AS device_id,
    nullif(o."Tags", '')                                    AS tags,
    o."Risk Level"                                          AS risk_level,
    o."Source"                                              AS source,
    o."Tax 1 Name"                                          AS tax_1_name,
    TRY_CAST(o."Tax 1 Value" AS DECIMAL(12,2))              AS tax_1_value,
    o."Tax 2 Name"                                          AS tax_2_name,
    TRY_CAST(o."Tax 2 Value" AS DECIMAL(12,2))              AS tax_2_value,
    o."Tax 3 Name"                                          AS tax_3_name,
    TRY_CAST(o."Tax 3 Value" AS DECIMAL(12,2))              AS tax_3_value,
    o."Tax 4 Name"                                          AS tax_4_name,
    TRY_CAST(o."Tax 4 Value" AS DECIMAL(12,2))              AS tax_4_value,
    o."Tax 5 Name"                                          AS tax_5_name,
    TRY_CAST(o."Tax 5 Value" AS DECIMAL(12,2))              AS tax_5_value,
    o."Phone"                                               AS phone,
    o."Receipt Number"                                      AS receipt_number,
    TRY_CAST(o."Duties" AS DECIMAL(12,2))                   AS duties
FROM raw_orders o
JOIN item_agg a ON a.order_name = o."Name"
WHERE o."Total" IS NOT NULL;

CREATE TABLE products AS
WITH variant_agg AS (
    SELECT
        "Handle"                                            AS handle,
        count(*) FILTER ("Variant Price" IS NOT NULL)       AS variant_count,
        min(TRY_CAST("Variant Price" AS DECIMAL(12,2)))     AS min_price,
        max(TRY_CAST("Variant Price" AS DECIMAL(12,2)))     AS max_price,
        count(*) FILTER ("Image Src" IS NOT NULL)           AS image_count
    FROM raw_products
    GROUP BY 1
)
SELECT
    p."Handle"                                              AS handle,
    p."Title"                                               AS title,
    p."Vendor"                                              AS vendor,
    p."Product Category"                                    AS product_category,
    p."Type"                                                AS type,
    p."Tags"                                                AS tags,
    (p."Published" = 'true')                                AS published,
    coalesce(p."Status", 'active')                          AS status,
    a.variant_count,
    a.min_price,
    a.max_price,
    a.image_count,
    (p."Gift Card" = 'true')                                AS gift_card,
    p."SEO Title"                                           AS seo_title,
    p."SEO Description"                                     AS seo_description,
    p."Image Src"                                           AS image_src,
    TRY_CAST(p."Product rating count (product.metafields.reviews.rating_count)" AS INTEGER)
                                                            AS rating_count,
    p."Allergen information (product.metafields.shopify.allergen-information)"
                                                            AS allergen_information,
    p."Bakery items included (product.metafields.shopify.bakery-items-included)"
                                                            AS bakery_items_included,
    p."Dietary preferences (product.metafields.shopify.dietary-preferences)"
                                                            AS dietary_preferences,
    p."Flavor (product.metafields.shopify.flavor)"          AS flavor,
    p."Flour/Grain type (product.metafields.shopify.flour-grain-type)"
                                                            AS flour_grain_type,
    p."Body (HTML)"                                         AS body_html
FROM raw_products p
JOIN variant_agg a ON a.handle = p."Handle"
WHERE p."Title" IS NOT NULL;

CREATE TABLE variants AS
SELECT
    "Handle"                                                AS handle,
    max("Title") OVER (PARTITION BY "Handle")               AS product_title,
    max("Vendor") OVER (PARTITION BY "Handle")              AS vendor,
    coalesce(max("Status") OVER (PARTITION BY "Handle"), 'active') AS status,
    max("Option1 Name") OVER (PARTITION BY "Handle")        AS option1_name,
    nullif("Option1 Value", 'Default Title')                AS option1_value,
    max("Option2 Name") OVER (PARTITION BY "Handle")        AS option2_name,
    "Option2 Value"                                         AS option2_value,
    max("Option3 Name") OVER (PARTITION BY "Handle")        AS option3_name,
    "Option3 Value"                                         AS option3_value,
    CASE
        WHEN "Option1 Value" IS NULL OR "Option1 Value" = 'Default Title'
        THEN max("Title") OVER (PARTITION BY "Handle")
        ELSE max("Title") OVER (PARTITION BY "Handle") || ' - '
             || concat_ws(' / ', "Option1 Value", "Option2 Value", "Option3 Value")
    END                                                     AS lineitem_name,
    nullif("Variant SKU", '')                               AS sku,
    TRY_CAST("Variant Grams" AS DOUBLE)                     AS grams,
    "Variant Weight Unit"                                   AS weight_unit,
    TRY_CAST("Variant Price" AS DECIMAL(12,2))              AS price,
    TRY_CAST("Variant Compare At Price" AS DECIMAL(12,2))   AS compare_at_price,
    TRY_CAST("Cost per item" AS DECIMAL(12,2))              AS cost_per_item,
    "Variant Inventory Tracker"                             AS inventory_tracker,
    "Variant Inventory Policy"                              AS inventory_policy,
    "Variant Fulfillment Service"                           AS fulfillment_service,
    ("Variant Requires Shipping" = 'true')                  AS requires_shipping,
    ("Variant Taxable" = 'true')                            AS taxable,
    "Variant Barcode"                                       AS barcode
FROM raw_products
WHERE "Variant Price" IS NOT NULL;
"""

TABLE_INFO = {
    "customers": "One row per customer (cleaned from the customers export).",
    "orders": "One row per order — order-level fields from the orders export "
              "plus line_count / total_quantity aggregates.",
    "order_items": "One row per order line item, with order name/email/date on every row. "
                   "Join to variants on lineitem_name or sku.",
    "products": "One row per product, with variant_count / min_price / max_price aggregates.",
    "variants": "One row per product variant. lineitem_name matches "
                "order_items.lineitem_name for easy joins.",
    "raw_customers": "Customers CSV as-is (all VARCHAR, original Shopify headers).",
    "raw_orders": "Orders CSV as-is (all VARCHAR, original Shopify headers).",
    "raw_products": "Products CSV as-is (all VARCHAR, original Shopify headers).",
}

TABLE_ORDER = list(TABLE_INFO)


def build_database(files):
    con = duckdb.connect(":memory:")
    for kind, path in files.items():
        con.execute(
            f"CREATE TABLE raw_{kind} AS SELECT * FROM read_csv(?, header=true, all_varchar=true)",
            [str(path)],
        )
    con.execute(TRANSFORM_SQL)
    return con


# ---------------------------------------------------------------------------
# JSON serialization of DuckDB values
# ---------------------------------------------------------------------------

def jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S%z")
    if isinstance(v, (date, dtime, timedelta)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    con = None  # set at startup
    schema_cache = None

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = (STATIC_DIR / "index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/schema":
            self._send(200, self.get_schema())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/query":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            sql = (payload.get("sql") or "").strip()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        if not sql:
            self._send(400, {"error": "empty query"})
            return
        self._send(*self.run_query(sql))

    @classmethod
    def run_query(cls, sql):
        cur = cls.con.cursor()
        start = time.perf_counter()
        try:
            cur.execute(sql)
            columns = [{"name": d[0], "type": str(d[1])} for d in (cur.description or [])]
            raw_rows = cur.fetchmany(MAX_ROWS + 1) if columns else []
        except Exception as exc:  # surface DuckDB errors to the UI
            return 400, {"error": str(exc)}
        finally:
            cur.close()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        truncated = len(raw_rows) > MAX_ROWS
        rows = [[jsonable(v) for v in row] for row in raw_rows[:MAX_ROWS]]
        return 200, {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
        }

    @classmethod
    def get_schema(cls):
        if cls.schema_cache is None:
            cur = cls.con.cursor()
            cols = cur.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns ORDER BY table_name, ordinal_position"
            ).fetchall()
            by_table = {}
            for table, column, dtype in cols:
                by_table.setdefault(table, []).append({"name": column, "type": dtype})
            tables = []
            for name in TABLE_ORDER:
                if name not in by_table:
                    continue
                count = cur.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                tables.append({
                    "name": name,
                    "description": TABLE_INFO.get(name, ""),
                    "row_count": count,
                    "columns": by_table[name],
                })
            cur.close()
            cls.schema_cache = {"tables": tables}
        return cls.schema_cache


def main():
    parser = argparse.ArgumentParser(description="SQL workbench for Shopify CSV exports")
    parser.add_argument("--data", default=str(BASE_DIR / "data"), help="folder with the three exports")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        sys.exit(f"error: data folder {data_dir}/ does not exist — create it and add your three exports")

    files = find_data_files(data_dir)
    print("Loading Shopify exports:")
    for kind, path in files.items():
        print(f"  {kind:<10} {path.name}")
    Handler.con = build_database(files)

    for row in Handler.con.execute(
        "SELECT 'customers', count(*) FROM customers UNION ALL "
        "SELECT 'orders', count(*) FROM orders UNION ALL "
        "SELECT 'order_items', count(*) FROM order_items UNION ALL "
        "SELECT 'products', count(*) FROM products UNION ALL "
        "SELECT 'variants', count(*) FROM variants"
    ).fetchall():
        print(f"  table {row[0]:<12} {row[1]:>6} rows")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"\nShopify SQL running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
