# IN PROGRESS — #1134 self-powered comp engine + Amazon FBM exploration

Dave (2026-07-04): "Let's add something else to the pricing engine. Let's
use the pricing research web ui and our own dataset to build our own self
powered comp engine. Target our largest categories first. Let's also start
looking into branching out to Amazon fulfilled by merchant for books and
media."

Two tracks:
1. Self-powered comp engine — found `OwnSalesProvider`
   (`src/tgw/ebay/market_data.py`) + `velocity_stats` worker already exist
   and are running. Real coverage check: 1,316 categories tracked, but only
   ~12 clear the 3-sample minimum (best category has 18 sold items). Root
   cause: 39,224/55,419 items (71%) have no category (`attribute_set`)
   recorded at all. Real "largest categories" by current inventory:
   Collectibles (2,432), AC Adapter (2,059), Arts and Crafts (1,261), DVD
   (1,245), Magazines (954).
2. Amazon FBM for books/media — no existing PP item. Researching scope
   (Seller Central account, category ungating, API access, fee structure)
   before any build recommendation.

Writing findings + proposals to `pp/PP-PRICING-001.md` (comp engine
extension) and a new `pp/PP-AMAZON-001.md`. Design/scoping pass, not
implementation — matches the scale of this decision.

**DONE — research/scoping pass.** Key findings:
- Self-powered comp engine infra (`OwnSalesProvider` + `velocity_stats`
  worker) already exists and runs; real blocker is 71% of the catalog
  having no category recorded, not the comp math itself — ties directly
  into reprioritizing PP-CATPICK-001. Full plan in `pp/PP-PRICING-001.md`
  Phase -1.
- Amazon FBM: new `pp/PP-AMAZON-001.md`. Books ungating has tightened
  (10+-unit supplier invoices commonly required — a real blocker for
  TGW's sourcing model); DVD/CD/Video Games/Magazines are the better
  starting point. Fee math (Professional plan + ~15% referral + $1.35-1.80
  media closing fee + Media Mail shipping) needs per-category modeling
  before committing — could eat most margin on a typical $10-20 item.
  Recommended: read-only SP-API comp-data integration first (low risk),
  full listing pipeline only after margin confirmed.

Neither track has code changes — both are design/research passes sized
to the scale of the decision, per Dave's own standing rule (suggest before
building for exploratory/strategic asks).
