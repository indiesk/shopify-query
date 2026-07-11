"""Sample queries served to the UI dropdown, grouped by analysis area.

Adapted from the 123DOUGH "Online Retail — Analytical Questions & SQL
Framework" document to this app's schema. Column mapping, the derived views
(online_orders, catalog_items), and per-query notes are documented in
QUERIES.md. Output column headers are kept as written in the framework.
"""

SECTIONS = [
("A. Customer base composition", [

("A1 · New vs. returning customers, by week", """\
-- A1. What share of orders each week come from first-ever buyers
--     vs. customers who had bought before?
WITH first_order AS (
  SELECT email, MIN(created_at) AS first_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  DATE_TRUNC('week', o.created_at)::DATE                     AS "Week",
  COUNT(*) FILTER (WHERE o.created_at = f.first_order_at)    AS "First-Time Orders",
  COUNT(*) FILTER (WHERE o.created_at > f.first_order_at)    AS "Returning Orders",
  ROUND(100.0 * COUNT(*) FILTER (WHERE o.created_at > f.first_order_at)
        / NULLIF(COUNT(*), 0), 1)                            AS "% Returning"
FROM online_orders o
JOIN first_order f ON f.email = o.email
GROUP BY 1
ORDER BY 1;"""),

("A2 · Overall repeat-purchase rate (lifetime)", """\
-- A2. What percent of the customer base has placed 2+ orders?
SELECT
  COUNT(*)                                       AS "Customers Who Ever Bought",
  COUNT(*) FILTER (WHERE total_orders >= 2)      AS "Bought 2+ Times",
  ROUND(100.0 * COUNT(*) FILTER (WHERE total_orders >= 2)
        / NULLIF(COUNT(*), 0), 1)                AS "Repeat-Purchase Rate (%)"
FROM customers
WHERE total_orders >= 1;  -- exclude never-bought subscribers"""),

("A3 · First-time buyer acquisition curve", """\
-- A3. How many net-new customers do we acquire each month?
WITH first_order AS (
  SELECT email, MIN(DATE_TRUNC('month', created_at))::DATE AS acquisition_month
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  acquisition_month  AS "Acquisition Month",
  COUNT(*)           AS "New Customers"
FROM first_order
GROUP BY 1
ORDER BY 1;"""),

("A4 · Cohort retention matrix", """\
-- A4. Of customers acquired in month M, what share come back in
--     month M+1, M+2, M+3 ...? The key table for understanding CLV shape.
WITH first_order AS (
  SELECT email, DATE_TRUNC('month', MIN(created_at))::DATE AS cohort_month
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
),
activity AS (
  SELECT
    f.cohort_month,
    o.email,
    DATE_DIFF('month', f.cohort_month,
              DATE_TRUNC('month', o.created_at)::DATE) AS months_since_first
  FROM online_orders o
  JOIN first_order f ON f.email = o.email
),
cohort_sizes AS (
  SELECT cohort_month, COUNT(DISTINCT email) AS cohort_size
  FROM first_order
  GROUP BY 1
)
SELECT
  a.cohort_month                                            AS "Cohort Month",
  cs.cohort_size                                            AS "Cohort Size",
  a.months_since_first                                      AS "Months Since First Purchase",
  COUNT(DISTINCT a.email)                                   AS "Customers Active",
  ROUND(100.0 * COUNT(DISTINCT a.email) / cs.cohort_size, 1) AS "Retention (%)"
FROM activity a
JOIN cohort_sizes cs ON cs.cohort_month = a.cohort_month
GROUP BY a.cohort_month, cs.cohort_size, a.months_since_first
ORDER BY a.cohort_month, a.months_since_first;"""),

("A5 · Time to second purchase", """\
-- A5. Among repeat customers, how many days pass between order 1 and
--     order 2? Median matters more than mean.
WITH ranked AS (
  SELECT
    email, created_at,
    ROW_NUMBER() OVER (PARTITION BY email ORDER BY created_at) AS order_rank
  FROM online_orders
  WHERE email IS NOT NULL
),
pairs AS (
  SELECT a.email, DATE_DIFF('day', a.created_at, b.created_at) AS days_to_second
  FROM ranked a
  JOIN ranked b ON b.email = a.email AND a.order_rank = 1 AND b.order_rank = 2
)
SELECT
  COUNT(*)                                                   AS "Repeat Customers",
  ROUND(AVG(days_to_second), 1)                              AS "Average Days to 2nd Order",
  MEDIAN(days_to_second)                                     AS "Median Days to 2nd Order",
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY days_to_second) AS "25th Percentile (Days)",
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY days_to_second) AS "75th Percentile (Days)"
FROM pairs;"""),

("A6 · Reactivation rate", """\
-- A6. Of customers who were dormant (no order in 120+ days) as of 30
--     days ago, what share came back in the last 30 days?
WITH last_before_window AS (
  SELECT email, MAX(created_at) AS last_order_at
  FROM online_orders
  WHERE email IS NOT NULL
    AND created_at < CURRENT_DATE - INTERVAL '30 days'
  GROUP BY email
),
dormant_at_snapshot AS (
  SELECT email
  FROM last_before_window
  WHERE last_order_at <  CURRENT_DATE - INTERVAL '150 days'
    AND last_order_at >= CURRENT_DATE - INTERVAL '395 days'
),
reactivated AS (
  SELECT DISTINCT email
  FROM online_orders
  WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
)
SELECT
  COUNT(*)                                        AS "Dormant Customers (30 Days Ago)",
  COUNT(*) FILTER (WHERE email IN (SELECT email FROM reactivated))
                                                  AS "Reactivated in Last 30 Days",
  ROUND(100.0 * COUNT(*) FILTER (WHERE email IN (SELECT email FROM reactivated))
        / NULLIF(COUNT(*), 0), 1)                 AS "Reactivation Rate (%)"
FROM dormant_at_snapshot;"""),
]),

("B. Customer economics — AOV, LTV, frequency", [

("B1 · Average order value (AOV)", """\
-- B1. What is the average order value across all paid, uncancelled orders?
SELECT
  COUNT(*)                   AS "Number of Paid Orders",
  ROUND(AVG(subtotal), 2)    AS "Average Order Value Before Tax/Shipping ($)",
  ROUND(AVG(total), 2)       AS "Average Order Value Including Tax/Shipping ($)",
  ROUND(MEDIAN(subtotal), 2) AS "Typical Order Value (Median $)"
FROM online_orders
WHERE financial_status = 'paid'
  AND cancelled_at IS NULL;"""),

("B2 · AOV trend over time", """\
-- B2. Is average order value rising or falling month over month?
SELECT
  DATE_TRUNC('month', created_at)::DATE AS "Month",
  COUNT(*)                              AS "Paid Orders",
  ROUND(AVG(subtotal), 2)               AS "Average Order Value ($)",
  ROUND(MEDIAN(subtotal), 2)            AS "Typical Order Value ($)"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY 1
ORDER BY 1;"""),

("B3 · AOV: new vs. returning customers", """\
-- B3. Does a repeat customer spend more per order than a first-timer?
WITH first_order AS (
  SELECT email, MIN(created_at) AS first_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  CASE WHEN o.created_at = f.first_order_at
       THEN 'First-Time Buyer' ELSE 'Repeat Customer' END
                             AS "Customer Type",
  COUNT(*)                   AS "Number of Orders",
  ROUND(AVG(o.subtotal), 2)  AS "Average Order Value ($)",
  ROUND(MEDIAN(o.subtotal), 2) AS "Typical Order Value ($)"
FROM online_orders o
JOIN first_order f ON f.email = o.email
WHERE o.financial_status = 'paid'
GROUP BY 1;"""),

("B4 · Customer LTV distribution", """\
-- B4. How is lifetime spend distributed across the base?
SELECT
  COUNT(*)                                                  AS "Customers",
  ROUND(AVG(total_spent), 2)                                AS "Average Lifetime Spend ($)",
  ROUND(MEDIAN(total_spent), 2)                             AS "Median Lifetime Spend ($)",
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY total_spent) AS "75th Percentile ($)",
  PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY total_spent) AS "90th Percentile ($)",
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_spent) AS "95th Percentile ($)",
  MAX(total_spent)                                          AS "Highest Lifetime Spend ($)"
FROM customers
WHERE total_orders >= 1;"""),

("B5 · Purchase frequency among repeat customers", """\
-- B5. How many orders per year do returning customers place, and how
--     many days pass between orders?
WITH span AS (
  SELECT
    email,
    COUNT(*) AS orders,
    DATE_DIFF('day', MIN(created_at), MAX(created_at)) AS active_days
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
  HAVING COUNT(*) >= 2
)
SELECT
  COUNT(*)                                                AS "Repeat Customers",
  ROUND(AVG(orders), 2)                                   AS "Average Orders per Customer",
  ROUND(AVG(365.0 * orders / NULLIF(active_days, 0)), 2)  AS "Orders per Year (Average)",
  ROUND(AVG(active_days / NULLIF(orders - 1, 0)), 1)      AS "Average Days Between Orders"
FROM span;"""),

("B6 · RFM segmentation", """\
-- B6. Score every customer on Recency, Frequency, Monetary — the
--     classic segmentation for tailored marketing.
WITH last_order AS (
  SELECT email, MAX(created_at) AS last_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
),
rfm AS (
  SELECT
    c.email,
    c.total_orders AS frequency,
    c.total_spent  AS monetary,
    NTILE(5) OVER (ORDER BY DATE_DIFF('day', l.last_order_at::DATE, CURRENT_DATE) DESC)
                   AS r_score,
    NTILE(5) OVER (ORDER BY c.total_orders) AS f_score,
    NTILE(5) OVER (ORDER BY c.total_spent)  AS m_score
  FROM customers c
  JOIN last_order l ON l.email = c.email
  WHERE c.total_orders >= 1
)
SELECT
  r_score                 AS "Recency Score (5=Most Recent)",
  f_score                 AS "Frequency Score (5=Most Frequent)",
  m_score                 AS "Spending Score (5=Highest Spender)",
  COUNT(*)                AS "Customers in This Group",
  ROUND(AVG(monetary), 2) AS "Average Lifetime Spend ($)"
FROM rfm
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 2 DESC, 3 DESC;"""),
]),

("C. 80/20 concentration analysis", [

("C1 · Revenue concentration by customer (quintiles)", """\
-- C1. What share of revenue comes from the top 20% of customers?
WITH ranked AS (
  SELECT email, total_spent,
         NTILE(5) OVER (ORDER BY total_spent DESC) AS quintile
  FROM customers
  WHERE total_orders >= 1
)
SELECT
  CASE quintile
    WHEN 1 THEN 'Top 20% (Best Customers)'
    WHEN 2 THEN 'Next 20%'
    WHEN 3 THEN 'Middle 20%'
    WHEN 4 THEN 'Lower 20%'
    WHEN 5 THEN 'Bottom 20% (Smallest Spenders)'
  END                                          AS "Customer Group",
  COUNT(*)                                     AS "Customers",
  ROUND(SUM(total_spent), 2)                   AS "Lifetime Revenue ($)",
  ROUND(100.0 * SUM(total_spent) / SUM(SUM(total_spent)) OVER (), 1)
                                               AS "% of All Revenue"
FROM ranked
GROUP BY quintile
ORDER BY quintile;"""),

("C2 · Cumulative revenue curve (proper Pareto)", """\
-- C2. Sort customers highest to lowest and plot cumulative revenue share
--     against cumulative customer share. Where the curve hits 80% is the
--     real 80/20 answer.
WITH ranked AS (
  SELECT
    email, total_spent,
    ROW_NUMBER() OVER (ORDER BY total_spent DESC) AS rn,
    COUNT(*) OVER ()       AS total_customers,
    SUM(total_spent) OVER () AS grand_total
  FROM customers
  WHERE total_orders >= 1
)
SELECT
  ROUND(100.0 * rn / total_customers, 1)                          AS "% of Customers (Cumulative)",
  ROUND(100.0 * SUM(total_spent) OVER (ORDER BY rn) / grand_total, 1) AS "% of Revenue (Cumulative)"
FROM ranked
ORDER BY rn;"""),

("C3 · Revenue concentration by product", """\
-- C3. Which products carry the revenue? Filtered to current catalog only.
WITH product_rev AS (
  SELECT
    ol.lineitem_name,
    SUM(ol.quantity * ol.price) AS gross_revenue
  FROM catalog_items ol
  JOIN online_orders o ON o.order_name = ol.order_name
  WHERE o.financial_status = 'paid' AND o.cancelled_at IS NULL
  GROUP BY 1
),
ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (ORDER BY gross_revenue DESC) AS rn,
         SUM(gross_revenue) OVER () AS grand
  FROM product_rev
)
SELECT
  lineitem_name                                              AS "Product",
  ROUND(gross_revenue, 2)                                    AS "Product Revenue ($)",
  ROUND(100.0 * gross_revenue / grand, 1)                    AS "% of Revenue",
  ROUND(100.0 * SUM(gross_revenue) OVER (ORDER BY rn) / grand, 1)
                                                             AS "Cumulative % of Revenue"
FROM ranked
ORDER BY rn;"""),

("C4 · Revenue concentration by SKU/variant", """\
-- C4. Which pack size or variant drives revenue within a product family?
--     Filtered to current catalog only.
SELECT
  ol.sku                                    AS "SKU",
  ol.lineitem_name                          AS "Product / Variant",
  SUM(ol.quantity)                          AS "Units Sold",
  ROUND(SUM(ol.quantity * ol.price), 2)     AS "Revenue ($)"
FROM catalog_items ol
JOIN online_orders o ON o.order_name = ol.order_name
WHERE o.financial_status = 'paid'
GROUP BY 1, 2
ORDER BY 4 DESC;"""),

("C5 · Geographic concentration", """\
-- C5. Which states carry the revenue?
SELECT
  shipping_province                          AS "State",
  COUNT(*)                                   AS "Orders",
  ROUND(SUM(subtotal), 2)                    AS "Revenue ($)",
  ROUND(100.0 * SUM(subtotal) / SUM(SUM(subtotal)) OVER (), 1)
                                             AS "% of Revenue"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY shipping_province
ORDER BY 3 DESC;"""),
]),

("D. Product performance", [

("D1 · Top products by revenue, units, unique buyers", """\
-- D1. Top products by revenue, units, and unique buyers.
--     Filtered to current catalog only.
SELECT
  ol.lineitem_name                          AS "Product",
  SUM(ol.quantity)                          AS "Units Sold",
  ROUND(SUM(ol.quantity * ol.price), 2)     AS "Revenue ($)",
  COUNT(DISTINCT o.email)                   AS "Unique Buyers",
  ROUND(SUM(ol.quantity * ol.price)
        / NULLIF(COUNT(DISTINCT o.email), 0), 2)
                                            AS "Revenue per Buyer ($)"
FROM catalog_items ol
JOIN online_orders o ON o.order_name = ol.order_name
WHERE o.financial_status = 'paid'
GROUP BY ol.lineitem_name
ORDER BY 3 DESC;"""),

("D2 · Product attach rate (bought together)", """\
-- D2. When product X is in the basket, what else commonly appears?
--     Catalog items only, so tips and legacy items don't show as "attached".
WITH baskets AS (
  SELECT DISTINCT order_name, lineitem_name
  FROM catalog_items
)
SELECT
  a.lineitem_name  AS "Product in Basket",
  b.lineitem_name  AS "Also Bought",
  COUNT(*)         AS "Orders Together",
  ROUND(100.0 * COUNT(*)
        / (SELECT COUNT(DISTINCT order_name) FROM baskets
           WHERE lineitem_name = a.lineitem_name), 1)
                   AS "Attach Rate (%)"
FROM baskets a
JOIN baskets b ON a.order_name = b.order_name
              AND a.lineitem_name < b.lineitem_name
GROUP BY 1, 2
HAVING COUNT(*) >= 5
ORDER BY 3 DESC;"""),

("D3 · Entry products for new customers", """\
-- D3. What was in the very first order each customer placed? Reveals
--     which products drive acquisition.
WITH first_order AS (
  SELECT email, MIN(created_at) AS first_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
),
first_baskets AS (
  SELECT ol.lineitem_name, ol.order_name, o.email
  FROM catalog_items ol
  JOIN online_orders o ON o.order_name = ol.order_name
  JOIN first_order f ON f.email = o.email AND f.first_order_at = o.created_at
)
SELECT
  lineitem_name                 AS "Product",
  COUNT(DISTINCT order_name)    AS "First Orders Containing It",
  ROUND(100.0 * COUNT(DISTINCT order_name)
        / (SELECT COUNT(*) FROM first_order), 1)
                                AS "% of All First Orders"
FROM first_baskets
GROUP BY lineitem_name
ORDER BY 2 DESC;"""),

("D4 · Repeat rate by entry product", """\
-- D4. Given a customer's first order contained product X, what share came
--     back for a second order? Reveals the best acquisition product by
--     LTV, not just first-order revenue.
WITH first_order AS (
  SELECT email, MIN(created_at) AS first_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
),
first_products AS (
  SELECT DISTINCT o.email, ol.lineitem_name
  FROM catalog_items ol
  JOIN online_orders o ON o.order_name = ol.order_name
  JOIN first_order f ON f.email = o.email AND f.first_order_at = o.created_at
),
repeaters AS (
  SELECT email
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
  HAVING COUNT(*) >= 2
)
SELECT
  fp.lineitem_name           AS "Entry Product",
  COUNT(DISTINCT fp.email)   AS "First-Time Buyers",
  COUNT(DISTINCT r.email)    AS "Came Back Again",
  ROUND(100.0 * COUNT(DISTINCT r.email)
        / NULLIF(COUNT(DISTINCT fp.email), 0), 1)
                             AS "Repeat Rate (%)"
FROM first_products fp
LEFT JOIN repeaters r ON r.email = fp.email
GROUP BY 1
ORDER BY 4 DESC;"""),

("D5 · Product margin view", """\
-- D5. Revenue vs. COGS per product, using Cost per item from the catalog.
--     Each line item is matched to its single best variant:
--     exact SKU, then exact name, then longest title prefix.
WITH matched AS (
  SELECT ol.quantity, ol.price, v.product_title AS title, v.cost_per_item AS cost
  FROM catalog_items ol
  JOIN online_orders o ON o.order_name = ol.order_name
  LEFT JOIN LATERAL (
    SELECT product_title, cost_per_item
    FROM variants v
    WHERE v.sku = ol.sku
       OR v.lineitem_name = ol.lineitem_name
       OR ol.lineitem_name LIKE v.product_title || '%'
    ORDER BY (v.sku = ol.sku) DESC,
             (v.lineitem_name = ol.lineitem_name) DESC,
             length(v.product_title) DESC
    LIMIT 1
  ) v ON true
  WHERE o.financial_status = 'paid'
)
SELECT
  title                                             AS "Product",
  SUM(quantity)                                     AS "Units Sold",
  ROUND(SUM(quantity * price), 2)                   AS "Revenue ($)",
  ROUND(SUM(quantity * COALESCE(cost, 0)), 2)       AS "COGS ($)",
  ROUND(SUM(quantity * (price - COALESCE(cost, 0))), 2)
                                                    AS "Gross Margin ($)",
  ROUND(100.0 * SUM(quantity * (price - COALESCE(cost, 0)))
        / NULLIF(SUM(quantity * price), 0), 1)      AS "Gross Margin (%)"
FROM matched
GROUP BY title
ORDER BY 5 DESC;"""),

("D6 · Catalog products with zero online sales", """\
-- D6. Dead SKUs — candidates for delisting or repositioning.
SELECT
  v.product_title  AS "Product",
  v.sku            AS "SKU",
  v.price          AS "Listed Price ($)",
  v.status         AS "Status"
FROM variants v
WHERE v.status = 'active'
  AND NOT EXISTS (
    SELECT 1 FROM order_items ol
    WHERE ol.sku = v.sku
       OR ol.lineitem_name = v.lineitem_name
  )
ORDER BY v.product_title;"""),
]),

("E. Order-level and basket", [

("E1 · Basket size distribution", """\
-- E1. How many distinct products and units per order? Catalog items only,
--     so "Tip" and legacy items don't inflate basket size.
WITH basket AS (
  SELECT
    order_name,
    COUNT(DISTINCT lineitem_name) AS distinct_products,
    SUM(quantity)                 AS units
  FROM catalog_items
  GROUP BY order_name
)
SELECT
  ROUND(AVG(distinct_products), 2)                 AS "Avg Distinct Products per Order",
  ROUND(AVG(units), 2)                             AS "Avg Units per Order",
  ROUND(MEDIAN(units), 2)                          AS "Typical Units per Order (Median)",
  COUNT(*) FILTER (WHERE distinct_products = 1)    AS "Single-Product Orders",
  COUNT(*) FILTER (WHERE distinct_products >= 2)   AS "Multi-Product Orders"
FROM basket;"""),

("E2 · Discount code usage", """\
-- E2. What share of orders use a discount code? Which codes drive the
--     most revenue?
SELECT
  COALESCE(discount_code, '(none)')  AS "Discount Code",
  COUNT(*)                           AS "Orders Using Code",
  ROUND(SUM(subtotal), 2)            AS "Revenue from These Orders ($)",
  ROUND(SUM(discount_amount), 2)     AS "Total Discount Given ($)",
  ROUND(AVG(subtotal), 2)            AS "Average Order Value ($)"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY discount_code
ORDER BY 2 DESC;"""),

("E3 · Discount depth", """\
-- E3. Is a code driving orders that would have happened anyway?
SELECT
  CASE WHEN discount_code IS NULL
       THEN 'No Discount Code' ELSE 'Used a Discount Code' END
                                     AS "Discount Usage",
  COUNT(*)                           AS "Orders",
  ROUND(AVG(subtotal), 2)            AS "Average Order Value ($)",
  ROUND(AVG(discount_amount), 2)     AS "Average Discount ($)",
  ROUND(100.0 * AVG(discount_amount)
        / NULLIF(AVG(subtotal + discount_amount), 0), 1)
                                     AS "Discount Depth (%)"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY 1;"""),

("E4 · Shipping revenue vs. subsidy, monthly", """\
-- E4. Are we charging enough shipping to cover cost, or subsidizing?
SELECT
  DATE_TRUNC('month', created_at)::DATE            AS "Month",
  COUNT(*)                                         AS "Paid Orders",
  ROUND(SUM(shipping), 2)                          AS "Shipping Charged ($)",
  ROUND(AVG(shipping), 2)                          AS "Avg Shipping per Order ($)",
  COUNT(*) FILTER (WHERE shipping = 0)             AS "Free-Shipping Orders",
  ROUND(100.0 * COUNT(*) FILTER (WHERE shipping = 0) / COUNT(*), 1)
                                                   AS "% Orders Shipped Free"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY 1
ORDER BY 1;"""),

("E5 · Refund rate and concentration", """\
-- E5. How often do we refund, and how much revenue does it claw back?
SELECT
  COUNT(*)                                          AS "Orders",
  COUNT(*) FILTER (WHERE refunded_amount > 0)       AS "Orders with a Refund",
  ROUND(100.0 * COUNT(*) FILTER (WHERE refunded_amount > 0) / COUNT(*), 2)
                                                    AS "Refund Rate (%)",
  ROUND(SUM(refunded_amount), 2)                    AS "Total Refunded ($)",
  ROUND(100.0 * SUM(refunded_amount) / NULLIF(SUM(total), 0), 2)
                                                    AS "% of Revenue Refunded"
FROM online_orders;"""),
]),

("F. Channel and source", [

("F1 · Revenue mix: subscription vs. one-time", """\
-- F1. How does revenue split across subscription, one-time web, and
--     manual draft orders?
SELECT
  CASE
    WHEN source LIKE 'subscription%'      THEN 'Subscription'
    WHEN source = 'web'                   THEN 'One-Time Web'
    WHEN source = 'shopify_draft_order'   THEN 'Draft Order (Manual)'
    ELSE 'Other Channel'
  END                                     AS "Channel",
  COUNT(*)                                AS "Orders",
  ROUND(SUM(subtotal), 2)                 AS "Revenue ($)",
  ROUND(AVG(subtotal), 2)                 AS "Average Order Value ($)",
  ROUND(100.0 * SUM(subtotal) / SUM(SUM(subtotal)) OVER (), 1)
                                          AS "% of Revenue"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY 1
ORDER BY 3 DESC;"""),

("F2 · Subscriber LTV vs. one-time buyer LTV", """\
-- F2. Quantifies the LTV multiplier for subscribers — the number that
--     justifies pushing subscriptions harder.
WITH customer_source AS (
  SELECT
    email,
    MAX(CASE WHEN source LIKE 'subscription%' THEN 1 ELSE 0 END) AS ever_subscriber
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  CASE WHEN cs.ever_subscriber = 1
       THEN 'Has Subscribed' ELSE 'One-Time Only' END
                                   AS "Customer Type",
  COUNT(*)                         AS "Customers",
  ROUND(AVG(c.total_spent), 2)     AS "Average Lifetime Spend ($)",
  ROUND(MEDIAN(c.total_spent), 2)  AS "Median Lifetime Spend ($)",
  ROUND(AVG(c.total_orders), 2)    AS "Average Lifetime Orders"
FROM customer_source cs
JOIN customers c ON c.email = cs.email
GROUP BY 1;"""),

("F3 · Subscription ↔ one-time crossover", """\
-- F3. Do subscribers also place one-time orders? Do one-time buyers
--     convert to subscribers?
WITH customer_behavior AS (
  SELECT
    email,
    SUM(CASE WHEN source = 'web' THEN 1 ELSE 0 END)                 AS web_orders,
    SUM(CASE WHEN source LIKE 'subscription%' THEN 1 ELSE 0 END)    AS sub_orders
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  CASE
    WHEN web_orders > 0 AND sub_orders > 0 THEN 'Both Web & Subscription'
    WHEN sub_orders > 0                    THEN 'Subscription Only'
    WHEN web_orders > 0                    THEN 'One-Time Web Only'
    ELSE 'Other Channels Only'
  END        AS "Purchase Pattern",
  COUNT(*)   AS "Customers"
FROM customer_behavior
GROUP BY 1
ORDER BY 2 DESC;"""),
]),

("G. Geographic", [

("G1 · Revenue and repeat rate by state", """\
-- G1. Where are the customers, and where do they come back?
WITH state_stats AS (
  SELECT
    o.shipping_province      AS state,
    COUNT(DISTINCT o.email)  AS customers,
    COUNT(*)                 AS orders,
    SUM(o.subtotal)          AS revenue
  FROM online_orders o
  WHERE o.financial_status = 'paid'
  GROUP BY 1
),
state_repeat AS (
  SELECT
    o.shipping_province AS state,
    COUNT(DISTINCT o.email) FILTER (
      WHERE o.email IN (SELECT email FROM online_orders
                        WHERE email IS NOT NULL
                        GROUP BY email HAVING COUNT(*) >= 2)
    ) AS repeat_customers
  FROM online_orders o
  GROUP BY 1
)
SELECT
  s.state                                     AS "State",
  s.customers                                 AS "Customers",
  s.orders                                    AS "Orders",
  ROUND(s.revenue, 2)                         AS "Revenue ($)",
  ROUND(s.revenue / NULLIF(s.orders, 0), 2)   AS "Average Order Value ($)",
  r.repeat_customers                          AS "Repeat Customers",
  ROUND(100.0 * r.repeat_customers / NULLIF(s.customers, 0), 1)
                                              AS "% of Customers Who Repeat"
FROM state_stats s
JOIN state_repeat r ON r.state = s.state
ORDER BY 4 DESC;"""),

("G2 · Top 20 shipping ZIPs", """\
-- G2. The 20 ZIP codes that drive the most revenue.
SELECT
  shipping_zip             AS "ZIP Code",
  shipping_province        AS "State",
  COUNT(*)                 AS "Orders",
  COUNT(DISTINCT email)    AS "Unique Customers",
  ROUND(SUM(subtotal), 2)  AS "Revenue ($)"
FROM online_orders
WHERE financial_status = 'paid'
GROUP BY 1, 2
ORDER BY 5 DESC
LIMIT 20;"""),
]),

("H. Marketing consent", [

("H1 · Opt-in rates", """\
-- H1. How reachable is the base via email and SMS?
SELECT
  COUNT(*)                                              AS "Customers",
  COUNT(*) FILTER (WHERE accepts_email_marketing)       AS "Email Opt-Ins",
  COUNT(*) FILTER (WHERE accepts_sms_marketing)         AS "SMS Opt-Ins",
  COUNT(*) FILTER (WHERE accepts_email_marketing
                     AND accepts_sms_marketing)         AS "Both Email & SMS",
  ROUND(100.0 * COUNT(*) FILTER (WHERE accepts_email_marketing) / COUNT(*), 1)
                                                        AS "Email Opt-In Rate (%)",
  ROUND(100.0 * COUNT(*) FILTER (WHERE accepts_sms_marketing) / COUNT(*), 1)
                                                        AS "SMS Opt-In Rate (%)"
FROM customers;"""),

("H2 · Consent rate among high-LTV customers", """\
-- H2. Are our best customers reachable via email/SMS?
WITH ntiled AS (
  SELECT *, NTILE(5) OVER (ORDER BY total_spent DESC) AS ltv_quintile
  FROM customers
  WHERE total_orders >= 1
)
SELECT
  CASE ltv_quintile
    WHEN 1 THEN 'Top 20% (Best Customers)'
    WHEN 2 THEN 'Next 20%'
    WHEN 3 THEN 'Middle 20%'
    WHEN 4 THEN 'Lower 20%'
    WHEN 5 THEN 'Bottom 20%'
  END        AS "LTV Group",
  COUNT(*)   AS "Customers",
  ROUND(100.0 * COUNT(*) FILTER (WHERE accepts_email_marketing) / COUNT(*), 1)
             AS "Email Opt-In Rate (%)",
  ROUND(100.0 * COUNT(*) FILTER (WHERE accepts_sms_marketing) / COUNT(*), 1)
             AS "SMS Opt-In Rate (%)"
FROM ntiled
GROUP BY ltv_quintile
ORDER BY ltv_quintile;"""),

("H3 · Never-bought subscribers", """\
-- H3. How many people are on the email list but have never placed
--     an order?
SELECT
  COUNT(*) AS "Subscribers Who Never Bought"
FROM customers
WHERE total_orders = 0
  AND accepts_email_marketing;"""),
]),

("I. Segment health", [

("I1 · Recency segment sizes right now", """\
-- I1. Sizes the Active / At-Risk / Dormant / Never-Bought pools that
--     drive weekly send strategy.
WITH last_order AS (
  SELECT email, MAX(created_at) AS last_order_at
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  CASE
    WHEN l.last_order_at IS NULL                                          THEN '5 - Never Bought Online'
    WHEN DATE_DIFF('day', l.last_order_at::DATE, CURRENT_DATE) <= 30      THEN '1 - Active (0-30d)'
    WHEN DATE_DIFF('day', l.last_order_at::DATE, CURRENT_DATE) <= 75      THEN '2 - At Risk (31-75d)'
    WHEN DATE_DIFF('day', l.last_order_at::DATE, CURRENT_DATE) <= 125     THEN '3 - Dormant (76-125d)'
    ELSE                                                                       '4 - Lost (126d+)'
  END                                  AS "Segment",
  COUNT(*)                             AS "Customers",
  ROUND(SUM(c.total_spent), 2)         AS "Total Lifetime Spend ($)",
  ROUND(AVG(c.total_spent), 2)         AS "Average Lifetime Spend ($)",
  ROUND(100.0 * COUNT(*) FILTER (WHERE c.accepts_email_marketing) / COUNT(*), 1)
                                       AS "Email Opt-In Rate (%)"
FROM customers c
LEFT JOIN last_order l ON l.email = c.email
GROUP BY 1
ORDER BY 1;"""),

("I2 · Segment migration month-over-month", """\
-- I2. Between the start of this month and today, who slipped from Active
--     into At-Risk, and who recovered?
WITH last_order AS (
  SELECT
    email,
    MAX(created_at) AS last_order_at,
    MAX(CASE WHEN created_at < DATE_TRUNC('month', CURRENT_DATE)
             THEN created_at END) AS last_order_prev_month
  FROM online_orders
  WHERE email IS NOT NULL
  GROUP BY email
)
SELECT
  CASE
    WHEN last_order_prev_month IS NULL THEN 'Was New This Month'
    WHEN DATE_DIFF('day', last_order_prev_month::DATE,
                   DATE_TRUNC('month', CURRENT_DATE)::DATE) <= 30 THEN 'Was Active'
    WHEN DATE_DIFF('day', last_order_prev_month::DATE,
                   DATE_TRUNC('month', CURRENT_DATE)::DATE) <= 75 THEN 'Was At Risk'
    ELSE 'Was Dormant/Lost'
  END        AS "Segment at Start of Month",
  CASE
    WHEN DATE_DIFF('day', last_order_at::DATE, CURRENT_DATE) <= 30 THEN 'Now Active'
    WHEN DATE_DIFF('day', last_order_at::DATE, CURRENT_DATE) <= 75 THEN 'Now At Risk'
    ELSE 'Now Dormant/Lost'
  END        AS "Segment Today",
  COUNT(*)   AS "Customers"
FROM last_order
GROUP BY 1, 2
ORDER BY 1, 2;"""),
]),

("J. Unit economics", [

("J1 · Contribution margin per order", """\
-- J1. Revenue less COGS less shipping subsidy — the number that matters
--     more than gross revenue. Worst-margin orders first.
WITH line_cogs AS (
  SELECT
    ol.order_name,
    SUM(ol.quantity * COALESCE(v.cost_per_item, 0)) AS cogs
  FROM order_items ol
  LEFT JOIN LATERAL (
    SELECT cost_per_item
    FROM variants v
    WHERE v.sku = ol.sku
       OR v.lineitem_name = ol.lineitem_name
       OR ol.lineitem_name LIKE v.product_title || '%'
    ORDER BY (v.sku = ol.sku) DESC,
             (v.lineitem_name = ol.lineitem_name) DESC,
             length(v.product_title) DESC
    LIMIT 1
  ) v ON true
  GROUP BY ol.order_name
)
SELECT
  o.order_name                                       AS "Order",
  o.subtotal                                         AS "Subtotal ($)",
  o.shipping                                         AS "Shipping Charged ($)",
  ROUND(oc.cogs, 2)                                  AS "COGS ($)",
  ROUND(o.subtotal - oc.cogs, 2)                     AS "Product Margin ($)",
  ROUND(o.subtotal + o.shipping - oc.cogs, 2)        AS "Contribution incl. Shipping ($)",
  ROUND(100.0 * (o.subtotal - oc.cogs) / NULLIF(o.subtotal, 0), 1)
                                                     AS "Margin (%)"
FROM online_orders o
JOIN line_cogs oc ON oc.order_name = o.order_name
WHERE o.financial_status = 'paid'
ORDER BY 7;"""),
]),
]
