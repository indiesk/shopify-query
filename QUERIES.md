# The analytics query library

The "Sample queries…" dropdown ships the full 123DOUGH *Online Retail —
Analytical Questions & SQL Framework*, adapted to this app's schema and
verified against the real exports. Every query below can be picked from the
dropdown, edited in place, and downloaded as CSV.

**Scope:** online sales only. All order-level queries run against the
`online_orders` view (POS storefront and iPhone POS excluded). Product-level
queries additionally use `catalog_items` so legacy items and tips stay out.

## The two derived views

| View | Definition | Why |
|---|---|---|
| `online_orders` | `orders` where `source` is not `pos` / `iphone` | Keeps walk-in register sales out of e-commerce analytics |
| `catalog_items` | `order_items` whose line matches a product in the current catalog — by SKU when populated, else by title-prefix | Excludes renamed/discontinued products and non-product lines ("Tip"); on this dataset it keeps ~73% of line-item rows. Swap it for `order_items` in any query to include the full historical tail |

## How the framework's names map to this app

The framework document was written against ad-hoc CSV aliases. This app's
loader already cleans the CSVs into typed tables, so the queries were
translated:

| Framework | This app |
|---|---|
| `order_lines` | `order_items` (line-level) / `online_orders` (order-level fields) |
| `orders` (derived view) | `online_orders` |
| `lineitem_qty`, `lineitem_price`, `lineitem_sku` | `quantity`, `price`, `sku` |
| `products` (one row per variant) | `variants` (`product_title`, `sku`, `price`, `cost_per_item`) |
| `products.variant_sku` | `variants.sku` |
| `cost` | `cost_per_item` |
| `accepts_email = 'yes'`, `accepts_sms = 'yes'` | `accepts_email_marketing`, `accepts_sms_marketing` (real booleans) |
| `shipping_state` | `shipping_province` |
| `state` (customers) | `province_code` |

## Global adjustments

- **Guest orders:** 60 online orders have no email (drafts/POS-style entry).
  Queries that build customer identity from email add `WHERE email IS NOT
  NULL` so those orders don't collapse into one fake "NULL customer."
- **Revenue formula:** product queries use `quantity * price` (gross), as the
  framework wrote them. `order_items.line_total` (net of line discounts) is
  available if you prefer net — the difference is ~$2.6k over 26 months.
- **Lifetime metrics:** `customers.total_spent` / `total_orders` come from
  Shopify and include *all* channels, while order-derived metrics are
  online-only. A2/B4/C1/C2/F2/H2/I1 use the lifetime numbers by design.
- **COGS coverage:** 109 of 192 variants have `Cost per item` filled in.
  D5 and J1 treat missing cost as $0, so margins are *overstated* for
  products without cost data — fill in costs in Shopify to tighten these.

## A. Customer base composition

- **A1 · New vs. returning, by week** — share of each week's orders from
  first-ever buyers vs. repeats. A falling "% Returning" means acquisition is
  outpacing retention (or retention is slipping).
- **A2 · Repeat-purchase rate (lifetime)** — % of everyone who ever bought
  that bought again. The single-number health check.
- **A3 · Acquisition curve** — net-new customers per month; shows growth
  peaks/troughs (holiday spikes, ad pushes).
- **A4 · Cohort retention matrix** — for each acquisition month, the % still
  ordering 1, 2, 3… months later. *The* CLV-shape table; read across a row to
  see decay, down a column to compare cohorts. (Flagged in the framework as
  the #1 query to run.)
- **A5 · Time to second purchase** — distribution of days between order 1 and
  2. The median sets your winback-email timing.
- **A6 · Reactivation rate** — *rewritten:* the original compared "dormant
  120+ days ago" with "ordered in the last 30 days," which is impossible by
  construction (always 0%). This version snapshots dormancy as of 30 days ago
  (no order in the 120–365 days before that) and then checks who returned in
  the last 30 days.

## B. Customer economics

- **B1 · AOV** — average paid, uncancelled order value, before and after
  tax/shipping, plus median.
- **B2 · AOV trend** — monthly AOV; is basket value growing?
- **B3 · AOV new vs. returning** — do repeat customers spend more per order?
- **B4 · LTV distribution** — lifetime spend percentiles; the p90/p95 vs.
  median gap shows how top-heavy the base is.
- **B5 · Purchase frequency (repeaters)** — orders/year and days between
  orders among 2+ time buyers; sets subscription cadence expectations.
- **B6 · RFM segmentation** — every buyer scored 1–5 on Recency, Frequency,
  Monetary (NTILE quintiles). 5/5/5 is your VIP list. *Adjusted:* customers
  with no online order are excluded (the original's LEFT JOIN gave them NULL
  recency, which NTILE silently ranked as most-recent).

## C. 80/20 concentration

- **C1 · Revenue by customer quintile** — how much of lifetime revenue the
  top 20% of customers carry.
- **C2 · Cumulative revenue curve (Pareto)** — one row per customer,
  cumulative % of customers vs. % of revenue. Find where the second column
  crosses 80 — that's your true "80/20". Chart-ready output.
- **C3 · Revenue by product** — cumulative revenue share by product; how few
  products make 80% of sales. Catalog items only.
- **C4 · Revenue by SKU/variant** — same, at pack-size/variant grain.
- **C5 · Geographic concentration** — revenue share by state.

## D. Product performance

- **D1 · Top products** — revenue, units, unique buyers, revenue per buyer.
- **D2 · Attach rate** — for baskets containing product X, what else appears
  (pairs seen 5+ times). Feeds bundle and cross-sell ideas.
- **D3 · Entry products** — what's in customers' first-ever orders; which
  products acquire customers.
- **D4 · Repeat rate by entry product** — of customers whose first order
  contained X, % who came back. The framework's pick for the most important
  product-level marketing metric: the best *acquisition* product by LTV.
- **D5 · Product margin** — revenue, COGS, gross margin by product.
  *Rewritten:* the original's `OR`-join could match one line to several
  variants and double-count; this version picks each line's single best
  variant match (exact SKU → exact name → longest title prefix) via a
  `LATERAL` join. See COGS coverage note above.
- **D6 · Dead SKUs** — active catalog variants with zero online sales.
  *Adjusted:* also matches by product name, not just SKU, so name-sold
  items aren't falsely reported dead.

## E. Order-level and basket

- **E1 · Basket size** — distinct products and units per order,
  single- vs. multi-product split.
- **E2 · Discount code usage** — orders, revenue, and total discount per code
  (`(none)` row = full-price orders).
- **E3 · Discount depth** — AOV and average % off, discounted vs. not; big
  volume at shallow depth hints codes are subsidizing orders that would have
  happened anyway.
- **E4 · Shipping charged vs. free, monthly** — how much shipping revenue you
  collect and what share of orders ship free.
- **E5 · Refund rate** — orders refunded, dollars refunded, % of revenue.

## F. Channel and source

- **F1 · Revenue mix** — Subscription vs. One-Time Web vs. Draft vs. Other,
  with AOV and revenue share.
- **F2 · Subscriber vs. one-time LTV** — the LTV multiplier for customers
  who ever subscribed; the framework's justification number for pushing
  subscriptions. (Second most important query per the framework.)
- **F3 · Crossover** — customers who did both web and subscription vs. only
  one. *Adjusted:* added an `ELSE 'Other Channels Only'` bucket so draft-only
  customers aren't dropped as NULL.

## G. Geographic

- **G1 · State scorecard** — customers, orders, revenue, AOV, and repeat rate
  per state.
- **G2 · Top 20 ZIPs** — the ZIP codes driving revenue; useful for local
  marketing and wholesale prospecting.

## H. Marketing consent

- **H1 · Opt-in rates** — email/SMS/both across the whole list.
- **H2 · Consent among high-LTV** — are the best customers actually
  reachable? Low opt-in in the top quintile is the most expensive gap.
- **H3 · Never-bought subscribers** — list members with zero orders; your
  nurture pool.

## I. Segment health

- **I1 · Recency segments** — Active (≤30d) / At Risk (≤75d) / Dormant
  (≤125d) / Lost / Never Bought Online, with spend and email reachability
  per segment. Sizes the pools for weekly send strategy.
- **I2 · Segment migration** — segment at the start of this month vs. today;
  shows slippage (Active → At Risk) and recovery in one table. (Fourth of the
  framework's must-run four.) *Reconstructed:* the PDF's version was
  truncated; labels and thresholds follow I1.

## J. Unit economics

- **J1 · Contribution margin per order** — subtotal − COGS (+ shipping
  charged), worst orders first. Uses the same duplicate-safe variant matching
  as D5. Orders at the top of this list are the ones you lose money shipping.
- **J2 · LTV : CAC by channel** — *not included:* requires ad-spend data
  (Google/Meta/Klaviyo) that isn't in these three exports.

## The strategic short-list

The framework's own priority order, for a first pass: **A4** (is retention
improving or decaying?), **D4** (which product acquires the best customers?),
**F2** (what's the subscriber LTV multiplier?), **I2** (is the email program
holding customers in Active?).
