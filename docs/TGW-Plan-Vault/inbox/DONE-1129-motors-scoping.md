# DONE — #1129 PP-EBAY-MOTORS-001 urgent planning pass

The urgent, unscoped Motors problem is now scoped and small. Used
today's #1131 census (parsed existing raw offer capture, zero live eBay
calls) to answer the two biggest open questions: **202 EBAY_MOTORS SKUs
out of 19,448 marketplace-tagged (~1% of the fleet)**, **zero
cross-marketplace duplicates found** in this snapshot. Checked
`trading.py` directly and found the SiteID hardcoding is one function
(`trading_call()`), not sprawling across every call site as originally
feared.

Wrote a decision-ready recommended order in `pp/PP-EBAY-MOTORS-001.md`:
backfill marketplace_id on the 202 known SKUs (free, data already
captured) → add schema field + ebay_stage population → thread site_id
through trading_call() → audit those 202 SKUs' config → re-run the census
periodically. No code changed — pure scoping pass per the todo's own
framing ("needs a dedicated planning session, not a packet").
