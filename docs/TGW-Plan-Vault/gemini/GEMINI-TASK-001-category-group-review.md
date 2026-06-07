# Gemini Task 001 — Category Group Quality Review

**Date prepared:** 2026-06-06
**Expected output:** Save your analysis as a Markdown file and drop it in `docs/TGW-Plan-Vault/inbox/`
**Output filename:** `GEMINI-001-result.md`

---

## Context

TGW (Trader Grim's Warehouse) is an eBay resale business with ~55,000 active listings.
Items are assigned to one of 24 "category groups" — a taxonomy that serves three purposes:

1. **Pricing fallback** — when Browse API comps are thin, `suggest_price()` uses the group's
   `typical_used`/`typical_new` prices (× a condition factor) as a fallback, with `floor` as
   a hard minimum applied to ALL prices including comps-based ones.
2. **Physical storage** — `size_class` encodes the physical bin class (flat/packet/small_box)
   since items are stored by size, not category.
3. **AI intake hints** — `ai_hint` is prepended to the ai_identify prompt to help the model
   identify items correctly at intake.

Pricing in the groups was seeded from velocity data (p25 of actual sold prices). The
`condition_factors` at the top of the file scale prices for condition grades.

The `velocity` data shows, per eBay category: how many items sold, how many are currently
active, what prices items actually sold for (median and p25). This is the ground truth for
whether the group pricing is calibrated correctly.

**Your task:** For each of the 24 groups, evaluate the quality of its pricing data and
group membership, then produce a structured report. Details below.

---

## Data: category-groups.json

```json
{
  "version": 1,
  "updated": "2026-06-06",
  "condition_factors": {
    "new": 1.5,
    "like new": 1.25,
    "very good": 1.0,
    "good": 0.75,
    "acceptable": 0.55,
    "for parts": 0.3
  },
  "global_floor": 0.99,
  "groups": {
    "books": {
      "name": "Books",
      "store_category": "",
      "ebay_categories": ["261186", "29223", "1105"],
      "size_class": "flat",
      "ai_hint": "book, novel, textbook, paperback, hardcover, nonfiction, reference",
      "pricing": { "floor": 4.47, "typical_used": 11.18, "typical_new": 16.77, "source": "velocity_p25" }
    },
    "manuals": {
      "name": "Manuals & Guides",
      "store_category": "",
      "ebay_categories": ["39996", "171208", "257888", "34210", "4684", "182152"],
      "size_class": "flat",
      "ai_hint": "manual, guide, instruction booklet, owner manual, service manual, technical manual",
      "pricing": { "floor": 5.78, "typical_used": 14.44, "typical_new": 21.66, "source": "velocity_p25" }
    },
    "magazines_periodicals": {
      "name": "Magazines & Periodicals",
      "store_category": "",
      "ebay_categories": ["280", "63819", "118254", "64488"],
      "size_class": "flat",
      "ai_hint": "magazine, periodical, catalog, catalogue, newsletter, trade publication",
      "pricing": { "floor": 5.6, "typical_used": 13.99, "typical_new": 20.98, "source": "velocity_p25" }
    },
    "sewing_crafts": {
      "name": "Sewing & Craft Patterns",
      "store_category": "",
      "ebay_categories": ["41228", "31730", "34032", "57740", "115"],
      "size_class": "flat",
      "ai_hint": "sewing pattern, cross stitch, needlework, weaving pattern, fabric transfer, craft pattern, button lot",
      "pricing": { "floor": 4.54, "typical_used": 11.36, "typical_new": 17.04, "source": "velocity_p25" }
    },
    "photos_ephemera": {
      "name": "Photos & Paper Ephemera",
      "store_category": "",
      "ebay_categories": ["262421", "13878"],
      "size_class": "flat",
      "ai_hint": "photograph, photo, postcard, print, ephemera, paper collectible",
      "pricing": { "floor": 5.69, "typical_used": 14.23, "typical_new": 21.34, "source": "velocity_p25" }
    },
    "stamps": {
      "name": "Stamps",
      "store_category": "",
      "ebay_categories": ["31740"],
      "size_class": "flat",
      "ai_hint": "postage stamp, stamp lot, stamp collection, philatelic",
      "pricing": { "floor": 3.63, "typical_used": 9.07, "typical_new": 13.61, "source": "velocity_p25" }
    },
    "collectibles_pins_buttons": {
      "name": "Collectible Pins & Buttons",
      "store_category": "",
      "ebay_categories": ["2036", "50677", "60115"],
      "size_class": "flat",
      "ai_hint": "pinback button, lapel pin, brooch, enamel pin, collectible button, badge",
      "pricing": { "floor": 3.89, "typical_used": 9.72, "typical_new": 14.58, "source": "velocity_p25" }
    },
    "refrigerator_magnets": {
      "name": "Refrigerator Magnets",
      "store_category": "",
      "ebay_categories": ["476"],
      "size_class": "flat",
      "ai_hint": "refrigerator magnet, fridge magnet, souvenir magnet",
      "pricing": { "floor": 2.0, "typical_used": 5.0, "typical_new": 7.5, "source": "velocity_p25" }
    },
    "playing_cards": {
      "name": "Playing Cards & Card Games",
      "store_category": "",
      "ebay_categories": ["1438"],
      "size_class": "flat",
      "ai_hint": "playing cards, card deck, poker deck, tarot deck, card game",
      "pricing": { "floor": 5.45, "typical_used": 13.62, "typical_new": 20.43, "source": "velocity_p25" }
    },
    "sports_cards": {
      "name": "Sports Cards",
      "store_category": "",
      "ebay_categories": ["24410"],
      "size_class": "flat",
      "ai_hint": "baseball card, sports card, trading card, player card, MLB card",
      "pricing": { "floor": 3.0, "typical_used": 7.49, "typical_new": 11.23, "source": "velocity_p25" }
    },
    "electronics_remotes": {
      "name": "Remote Controls",
      "store_category": "",
      "ebay_categories": ["61312"],
      "size_class": "packet",
      "ai_hint": "remote control, TV remote, universal remote, device remote, clicker",
      "pricing": { "floor": 4.5, "typical_used": 11.25, "typical_new": 16.88, "source": "velocity_p25" }
    },
    "electronics_adapters_chargers": {
      "name": "Power Adapters & Chargers",
      "store_category": "",
      "ebay_categories": ["88758", "31510", "162046", "260205"],
      "size_class": "packet",
      "ai_hint": "AC adapter, power adapter, wall charger, power supply, charging cable, laptop charger, DC adapter",
      "pricing": { "floor": 5.69, "typical_used": 14.23, "typical_new": 21.34, "source": "velocity_p25" }
    },
    "electronics_cables": {
      "name": "Cables & Interconnects",
      "store_category": "",
      "ebay_categories": ["14964", "32834", "44932"],
      "size_class": "packet",
      "ai_hint": "cable, interconnect, USB cable, HDMI cable, audio cable, video cable, adapter dongle",
      "pricing": { "floor": 3.3, "typical_used": 8.26, "typical_new": 12.39, "source": "velocity_p25" }
    },
    "electronics_input": {
      "name": "Computer Input Devices",
      "store_category": "",
      "ebay_categories": ["23160"],
      "size_class": "packet",
      "ai_hint": "mouse, trackball, touchpad, keyboard, pointing device, computer peripheral",
      "pricing": { "floor": 4.09, "typical_used": 10.22, "typical_new": 15.33, "source": "velocity_p25" }
    },
    "media_cassettes": {
      "name": "Cassette Tapes",
      "store_category": "",
      "ebay_categories": ["176983"],
      "size_class": "packet",
      "ai_hint": "cassette tape, audio cassette, music cassette, compact cassette, recorded tape",
      "pricing": { "floor": 4.4, "typical_used": 10.99, "typical_new": 16.48, "source": "velocity_p25" }
    },
    "media_records": {
      "name": "Vinyl Records & Albums",
      "store_category": "",
      "ebay_categories": ["4090", "985"],
      "size_class": "flat",
      "ai_hint": "vinyl record, LP album, 45 single, 33 RPM, record album, vinyl disc",
      "pricing": { "floor": 4.81, "typical_used": 12.03, "typical_new": 18.04, "source": "velocity_p25" }
    },
    "kitchen_utensils": {
      "name": "Kitchen Utensils & Cutlery",
      "store_category": "",
      "ebay_categories": ["20651", "20649", "261680", "261699", "177005", "122939", "261679", "261652", "20688", "11663", "137750", "260155", "43420"],
      "size_class": "packet",
      "ai_hint": "kitchen utensil, cooking utensil, spatula, ladle, knife, cutlery, flatware, garlic press, corkscrew, can opener, kitchen gadget",
      "pricing": { "floor": 5.33, "typical_used": 13.33, "typical_new": 20.0, "source": "velocity_p25" }
    },
    "kitchen_mugs": {
      "name": "Mugs & Drinkware",
      "store_category": "",
      "ebay_categories": ["261672"],
      "size_class": "packet",
      "ai_hint": "mug, coffee mug, tea cup, drinkware, novelty mug, ceramic mug",
      "pricing": { "floor": 6.0, "typical_used": 15.0, "typical_new": 22.5, "source": "velocity_p25" }
    },
    "holiday_ornaments": {
      "name": "Holiday Ornaments",
      "store_category": "",
      "ebay_categories": ["77988"],
      "size_class": "packet",
      "ai_hint": "Christmas ornament, holiday ornament, tree ornament, decorative ornament",
      "pricing": { "floor": 4.8, "typical_used": 11.99, "typical_new": 17.98, "source": "velocity_p25" }
    },
    "model_trains": {
      "name": "Model Trains & Accessories",
      "store_category": "",
      "ebay_categories": ["262308"],
      "size_class": "small_box",
      "ai_hint": "model train, train set, layout building, model railroad, train accessory, tunnel, bridge",
      "pricing": { "floor": 8.5, "typical_used": 21.24, "typical_new": 31.86, "source": "velocity_p25" }
    },
    "electrical_fixtures": {
      "name": "Electrical Fixtures & Hardware",
      "store_category": "",
      "ebay_categories": ["259281", "43412", "185134"],
      "size_class": "small_box",
      "ai_hint": "electrical switch, dimmer, outlet cover, wall plate, circuit breaker, electrical hardware",
      "pricing": { "floor": 6.17, "typical_used": 15.43, "typical_new": 23.14, "source": "velocity_p25" }
    },
    "tools_hand": {
      "name": "Hand Tools",
      "store_category": "",
      "ebay_categories": ["1461", "43994", "116005", "13863"],
      "size_class": "packet",
      "ai_hint": "hand tool, wrench, screwdriver, sharpener, flashlight, utility tool, multi-tool",
      "pricing": { "floor": 4.56, "typical_used": 11.39, "typical_new": 17.09, "source": "velocity_p25" }
    },
    "grooming_personal": {
      "name": "Grooming & Personal Care",
      "store_category": "",
      "ebay_categories": ["11844"],
      "size_class": "packet",
      "ai_hint": "electric shaver, razor, grooming device, personal care appliance",
      "pricing": { "floor": 4.4, "typical_used": 11.0, "typical_new": 16.5, "source": "velocity_p25" }
    },
    "figurines_collectibles": {
      "name": "Figurines & Collectibles",
      "store_category": "",
      "ebay_categories": ["11147"],
      "size_class": "packet",
      "ai_hint": "figurine, statue, ceramic figurine, animal figure, collectible figure",
      "pricing": { "floor": 3.6, "typical_used": 8.99, "typical_new": 13.48, "source": "velocity_p25" }
    }
  }
}
```

---

## Data: velocity stats for all 64 group category IDs

Each entry is keyed by eBay category ID and contains actual sold/active item counts and
realized sale prices from TGW's historical data. `median_sale_price` and `p25_sale_price`
are from actual TGW sales — if they're non-null, that's the best available ground truth for
pricing calibration. All `sell_at_*_pct` fields are currently 1.0 for `sell_at_unknown_pct`
because these are pre-new-pipeline sales; the stage breakdown will populate over time.

```json
{
  "261186": { "category_name": "Books", "sold_count": 154, "active_count": 3194, "stale_count": 0, "median_sale_price": 14.16, "p25_sale_price": 10.95 },
  "29223":  { "category_name": "Antiquarian & Collectible", "sold_count": 15, "active_count": 205, "stale_count": 0, "median_sale_price": 26.06, "p25_sale_price": 14.39 },
  "1105":   { "category_name": "Textbooks", "sold_count": 6, "active_count": 131, "stale_count": 0, "median_sale_price": 29.74, "p25_sale_price": 9.02 },
  "39996":  { "category_name": "Vintage Manuals", "sold_count": 8, "active_count": 35, "stale_count": 0, "median_sale_price": 21.99, "p25_sale_price": 18.73 },
  "171208": { "category_name": "Manuals & Guides", "sold_count": 3, "active_count": 20, "stale_count": 0, "median_sale_price": 10.0, "p25_sale_price": 10.0 },
  "257888": { "category_name": "Heavy Equipment Manuals & Books", "sold_count": 3, "active_count": 0, "stale_count": 0, "median_sale_price": 19.12, "p25_sale_price": 13.0 },
  "34210":  { "category_name": "Other Car Manuals", "sold_count": 3, "active_count": 9, "stale_count": 0, "median_sale_price": 20.94, "p25_sale_price": 16.99 },
  "4684":   { "category_name": "Camera Manuals & Guides", "sold_count": 3, "active_count": 31, "stale_count": 0, "median_sale_price": 11.98, "p25_sale_price": 9.59 },
  "182152": { "category_name": "Vintage & Antique", "sold_count": 5, "active_count": 44, "stale_count": 0, "median_sale_price": 15.99, "p25_sale_price": 12.5 },
  "280":    { "category_name": "Magazines", "sold_count": 55, "active_count": 1245, "stale_count": 0, "median_sale_price": 16.44, "p25_sale_price": 13.61 },
  "63819":  { "category_name": "Magazines", "sold_count": 41, "active_count": 489, "stale_count": 0, "median_sale_price": 17.0, "p25_sale_price": 14.99 },
  "118254": { "category_name": "Catalogs", "sold_count": 16, "active_count": 88, "stale_count": 0, "median_sale_price": 15.99, "p25_sale_price": 12.74 },
  "64488":  { "category_name": "Magazines", "sold_count": 3, "active_count": 17, "stale_count": 0, "median_sale_price": 15.0, "p25_sale_price": 14.0 },
  "41228":  { "category_name": "Patterns-Contemporary", "sold_count": 5, "active_count": 72, "stale_count": 0, "median_sale_price": 12.14, "p25_sale_price": 11.0 },
  "31730":  { "category_name": "Fabric Transfers", "sold_count": 3, "active_count": 44, "stale_count": 0, "median_sale_price": 9.0, "p25_sale_price": 9.0 },
  "34032":  { "category_name": "Cross Stitch Patterns", "sold_count": 3, "active_count": 86, "stale_count": 0, "median_sale_price": 11.0, "p25_sale_price": 10.19 },
  "57740":  { "category_name": "Weaving Books & Patterns", "sold_count": 3, "active_count": 1, "stale_count": 0, "median_sale_price": 16.28, "p25_sale_price": 12.83 },
  "115":    { "category_name": "Other Vintage Sewing Buttons", "sold_count": 5, "active_count": 0, "stale_count": 0, "median_sale_price": 15.74, "p25_sale_price": 12.95 },
  "262421": { "category_name": "Photographs", "sold_count": 6, "active_count": 252, "stale_count": 0, "median_sale_price": 16.99, "p25_sale_price": 14.99 },
  "13878":  { "category_name": "Amusement Parks", "sold_count": 3, "active_count": 1, "stale_count": 0, "median_sale_price": 15.19, "p25_sale_price": 12.72 },
  "31740":  { "category_name": "Stamps", "sold_count": 9, "active_count": 205, "stale_count": 0, "median_sale_price": 10.48, "p25_sale_price": 9.07 },
  "2036":   { "category_name": "Other Collectible Pinbacks", "sold_count": 10, "active_count": 58, "stale_count": 0, "median_sale_price": 11.99, "p25_sale_price": 8.51 },
  "50677":  { "category_name": "Brooches & Pins", "sold_count": 5, "active_count": 23, "stale_count": 0, "median_sale_price": 14.99, "p25_sale_price": 11.98 },
  "60115":  { "category_name": "Lapel Pins", "sold_count": 3, "active_count": 0, "stale_count": 0, "median_sale_price": 11.8, "p25_sale_price": 10.0 },
  "476":    { "category_name": "Refrigerator Magnets", "sold_count": 4, "active_count": 13, "stale_count": 0, "median_sale_price": 9.0, "p25_sale_price": 5.0 },
  "1438":   { "category_name": "Playing Cards", "sold_count": 9, "active_count": 36, "stale_count": 0, "median_sale_price": 15.99, "p25_sale_price": 13.62 },
  "24410":  { "category_name": "Baseball-MLB", "sold_count": 4, "active_count": 8, "stale_count": 0, "median_sale_price": 14.02, "p25_sale_price": 7.49 },
  "61312":  { "category_name": "Remote Controls", "sold_count": 15, "active_count": 242, "stale_count": 0, "median_sale_price": 13.5, "p25_sale_price": 11.25 },
  "88758":  { "category_name": "Multipurpose AC to DC Adapters", "sold_count": 35, "active_count": 770, "stale_count": 0, "median_sale_price": 16.39, "p25_sale_price": 14.39 },
  "31510":  { "category_name": "Laptop Power Adapters/Chargers", "sold_count": 4, "active_count": 14, "stale_count": 0, "median_sale_price": 22.56, "p25_sale_price": 14.11 },
  "162046": { "category_name": "Chargers & Cradles", "sold_count": 3, "active_count": 3, "stale_count": 0, "median_sale_price": 16.99, "p25_sale_price": 11.0 },
  "260205": { "category_name": "Power Tool Battery Chargers", "sold_count": 3, "active_count": 10, "stale_count": 0, "median_sale_price": 16.0, "p25_sale_price": 15.74 },
  "14964":  { "category_name": "Audio Cables & Interconnects", "sold_count": 3, "active_count": 42, "stale_count": 0, "median_sale_price": 10.95, "p25_sale_price": 10.39 },
  "32834":  { "category_name": "Video Cables & Interconnects", "sold_count": 3, "active_count": 29, "stale_count": 0, "median_sale_price": 10.0, "p25_sale_price": 7.99 },
  "44932":  { "category_name": "USB Cables, Hubs & Adapters", "sold_count": 3, "active_count": 17, "stale_count": 0, "median_sale_price": 11.91, "p25_sale_price": 6.41 },
  "23160":  { "category_name": "Mice, Trackballs & Touchpads", "sold_count": 6, "active_count": 38, "stale_count": 0, "median_sale_price": 13.71, "p25_sale_price": 10.22 },
  "176983": { "category_name": "Cassettes", "sold_count": 33, "active_count": 376, "stale_count": 0, "median_sale_price": 12.74, "p25_sale_price": 10.99 },
  "4090":   { "category_name": "1960-Now", "sold_count": 14, "active_count": 355, "stale_count": 0, "median_sale_price": 12.73, "p25_sale_price": 11.39 },
  "985":    { "category_name": "1970-Now", "sold_count": 3, "active_count": 6, "stale_count": 0, "median_sale_price": 16.08, "p25_sale_price": 15.0 },
  "20651":  { "category_name": "Other Kitchen Tools & Gadgets", "sold_count": 19, "active_count": 64, "stale_count": 0, "median_sale_price": 15.98, "p25_sale_price": 13.39 },
  "20649":  { "category_name": "Cooking Utensils", "sold_count": 18, "active_count": 116, "stale_count": 0, "median_sale_price": 15.21, "p25_sale_price": 12.58 },
  "261680": { "category_name": "Other Flatware & Cutlery", "sold_count": 12, "active_count": 163, "stale_count": 0, "median_sale_price": 16.83, "p25_sale_price": 13.99 },
  "261699": { "category_name": "Other Kitchen Tools & Gadgets", "sold_count": 11, "active_count": 92, "stale_count": 0, "median_sale_price": 19.27, "p25_sale_price": 15.0 },
  "177005": { "category_name": "Kitchen & Steak Knives", "sold_count": 12, "active_count": 107, "stale_count": 0, "median_sale_price": 16.66, "p25_sale_price": 15.98 },
  "122939": { "category_name": "Garlic Presses", "sold_count": 6, "active_count": 5, "stale_count": 0, "median_sale_price": 18.46, "p25_sale_price": 12.7 },
  "261679": { "category_name": "Single Flatware Pieces", "sold_count": 6, "active_count": 67, "stale_count": 0, "median_sale_price": 20.86, "p25_sale_price": 10.0 },
  "261652": { "category_name": "Corkscrews & Openers", "sold_count": 4, "active_count": 15, "stale_count": 0, "median_sale_price": 14.61, "p25_sale_price": 10.59 },
  "20688":  { "category_name": "Corkscrews & Openers", "sold_count": 3, "active_count": 44, "stale_count": 0, "median_sale_price": 15.03, "p25_sale_price": 13.0 },
  "11663":  { "category_name": "Scoops", "sold_count": 3, "active_count": 18, "stale_count": 0, "median_sale_price": 18.89, "p25_sale_price": 12.55 },
  "137750": { "category_name": "Serving Utensils & Sets", "sold_count": 3, "active_count": 31, "stale_count": 0, "median_sale_price": 17.0, "p25_sale_price": 14.0 },
  "260155": { "category_name": "Small Kitchen Appliance Parts", "sold_count": 4, "active_count": 17, "stale_count": 0, "median_sale_price": 17.0, "p25_sale_price": 11.99 },
  "43420":  { "category_name": "Can Openers (Manual)", "sold_count": 3, "active_count": 11, "stale_count": 0, "median_sale_price": 14.21, "p25_sale_price": 12.05 },
  "261672": { "category_name": "Mugs", "sold_count": 8, "active_count": 36, "stale_count": 0, "median_sale_price": 22.4, "p25_sale_price": 15.0 },
  "77988":  { "category_name": "Ornaments", "sold_count": 3, "active_count": 41, "stale_count": 0, "median_sale_price": 12.0, "p25_sale_price": 11.99 },
  "262308": { "category_name": "Buildings, Tunnels & Bridges", "sold_count": 9, "active_count": 13, "stale_count": 0, "median_sale_price": 29.69, "p25_sale_price": 21.24 },
  "259281": { "category_name": "Electrical Switches & Dimmers", "sold_count": 6, "active_count": 24, "stale_count": 0, "median_sale_price": 31.49, "p25_sale_price": 15.36 },
  "43412":  { "category_name": "Wall Plates & Outlet Covers", "sold_count": 4, "active_count": 7, "stale_count": 0, "median_sale_price": 12.16, "p25_sale_price": 8.0 },
  "185134": { "category_name": "Circuit Breakers", "sold_count": 3, "active_count": 9, "stale_count": 0, "median_sale_price": 33.99, "p25_sale_price": 25.47 },
  "1461":   { "category_name": "Other Collectible Tools", "sold_count": 3, "active_count": 8, "stale_count": 0, "median_sale_price": 17.99, "p25_sale_price": 13.62 },
  "43994":  { "category_name": "Wrenches", "sold_count": 3, "active_count": 3, "stale_count": 0, "median_sale_price": 10.0, "p25_sale_price": 6.0 },
  "116005": { "category_name": "Sharpeners", "sold_count": 3, "active_count": 8, "stale_count": 0, "median_sale_price": 16.24, "p25_sale_price": 10.49 },
  "13863":  { "category_name": "Flashlights", "sold_count": 3, "active_count": 2, "stale_count": 0, "median_sale_price": 26.62, "p25_sale_price": 15.44 },
  "11844":  { "category_name": "Men's Shavers", "sold_count": 3, "active_count": 1, "stale_count": 0, "median_sale_price": 11.5, "p25_sale_price": 11.0 },
  "11147":  { "category_name": "Lions", "sold_count": 3, "active_count": 23, "stale_count": 0, "median_sale_price": 10.5, "p25_sale_price": 8.99 }
}
```

---

## Your analysis tasks

For each group, compute the following and include in your report:

### 1. Pricing calibration check

For each group, compare `typical_used` against the **volume-weighted average p25_sale_price**
across all its categories (weight by sold_count). Flag groups where:
- `typical_used` deviates >30% from the weighted average p25 (pricing may be stale or wrong)
- `floor` is higher than the weighted average p25 (floor would override all comps — problematic)
- A group has very few total sales (<10 across all categories) — pricing is thin/unreliable

### 2. Category coherence check

Within each group, look at the spread of p25_sale_price values across its categories. Flag groups where:
- Price spread between highest and lowest p25_sale_price is >2× — the group may be mixing
  items that should be in separate groups
- A category's actual sale prices are wildly inconsistent with its siblings

Specific cases to call out:
- `books` group: `Textbooks` (1105) has p25=$9.02 but `Antiquarian` (29223) p25=$14.39 — does
  this spread warrant splitting?
- `electrical_fixtures` group: `Circuit Breakers` (185134) p25=$25.47 vs `Wall Plates` (43412)
  p25=$8.00 — very different price points, different size class too (circuit breakers are larger)
- `tools_hand` group: `Flashlights` (13863) p25=$15.44 vs `Wrenches` (43994) p25=$6.00

### 3. Category membership audit

Flag any category that looks misplaced in its group based on the eBay category name alone:
- `13878` "Amusement Parks" in `photos_ephemera` — is this right?
- `11147` "Lions" in `figurines_collectibles` — eBay put lion figurines under "Lions" subcategory
- `257888` "Heavy Equipment Manuals" has 0 active items — dead category?
- `115` "Other Vintage Sewing Buttons" has 0 active items — dead category or misrouted?

### 4. Split/merge recommendations

Based on the above, produce concrete recommendations:
- **Split**: group X should become X_a and X_b because [price range too wide / size_class differs]
- **Merge**: groups X and Y should merge because [similar prices, low volume each, coherent theme]
- **Add category**: group X is missing eBay category NNN (explain what it covers)
- **Remove category**: category NNN in group X is misplaced (suggest better home)

### 5. ai_hint quality notes

For any group where the ai_hint keywords seem incomplete or potentially misleading for the
categories it covers, note a suggested improvement. Keep hints short (fits in a prompt prefix).

---

## Output format

Produce a Markdown report with these sections:

```
# Category Group Quality Review — 2026-06-06

## Executive Summary
[3-5 bullet points: biggest issues found]

## Per-Group Analysis
[For each group: one table row or compact block with calibration status, any flags]

## Pricing Calibration Issues
[Groups with significant pricing drift — computed weighted p25 vs current typical_used]

## Split Recommendations
[Concrete proposals with rationale]

## Merge Recommendations
[Concrete proposals with rationale]

## Category Membership Issues
[Misplaced categories, dead categories]

## ai_hint Improvements
[Groups where hints need updating]

## Suggested JSON Patches
[For each actionable change: the exact JSON diff to apply to category-groups.json]
```

Keep the JSON patches minimal and surgical — only change what the analysis clearly supports.
Do not invent new groups speculatively. Focus on data-supported findings.
