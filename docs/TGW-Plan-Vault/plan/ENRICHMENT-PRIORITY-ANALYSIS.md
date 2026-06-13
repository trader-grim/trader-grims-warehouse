# PP-REF-002: Enrichment Priority Analysis (IGDB/Discogs)

This report identifies the category groups that benefit most from external data enrichment and specifies the fields to retrieve from IGDB (Video Games) and Discogs (Music).

## 1. Category Ranking by Enrichment Value
Ranked by combined Velocity (sales volume) and Data Complexity (benefit of structured fields).

| Rank | Category Group | Priority | Source | Rationale |
|------|----------------|----------|--------|-----------|
| 1 | **Media: Music** | **High** | Discogs | High volume (Cassettes: 33, Vinyl: 14+ sold). Manual description is tedious; Discogs provides precise tracklists and variants. |
| 2 | **Video Games** | **Medium** | IGDB | Moderate volume (Manuals/Games/Consoles: ~5-10 sold). High data depth (developer, genre, release date) improves SEO and buyer confidence. |
| 3 | **Books** | **Medium** | Open Library | Highest volume (Books: 154 sold). While not IGDB/Discogs, it shares the same "Barcode -> Enrichment" pattern. |

## 2. Top 10 Fields to Pull per Source

### Discogs (Music)
1.  **Title**: Official release title.
2.  **Artist**: Primary artist(s).
3.  **Format**: Essential for eBay (e.g., "LP, Album, Repress").
4.  **Released**: Release year/date.
5.  **Label**: Publishing record label.
6.  **Genre**: High-level categorization (e.g., Rock, Electronic).
7.  **Style**: Granular sub-genres (e.g., Synth-pop, Industrial).
8.  **Tracklist**: Full list of titles and durations.
9.  **Country**: Country of release (important for collectors).
10. **Catno**: Label catalog number (high-confidence identifier).

### IGDB (Video Games)
1.  **Name**: Canonical game title.
2.  **Platforms**: Target system (e.g., NES, Genesis, PS2).
3.  **First Release Date**: Useful for vintage/modern classification.
4.  **Involved Companies**: Developer and Publisher details.
5.  **Genres**: (e.g., RPG, Platformer, Racing).
6.  **Game Modes**: (e.g., Single player, Multiplayer, Co-op).
7.  **Themes**: (e.g., Sci-fi, Horror, Fantasy).
8.  **Summary**: Professional game description.
9.  **Age Ratings**: ESRB/PEGI ratings.
10. **Cover Image**: URL for visual confirmation during identification.

## 3. Implementation Brief for Claude
**Objective**: Update `apis/lookup/` adapters to retrieve the above fields and integrate them into the `ebay_draft` workflow.

**Steps**:
1.  **Refactor `apis/lookup/discogs.py`**: Migrate from `discogs_client` to direct `httpx` (Issue #98). Ensure it returns the 10 fields above in a `LookupResult` object.
2.  **Enhance `apis/lookup/igdb.py`**: Update the Apicalypse query to include the 10 fields above.
3.  **Update `tgw suggest` / `ebay_draft`**:
    - When an enrichment result is found, automatically append a "Product Specifications" section to the description.
    - Map enrichment fields to eBay `aspects` (e.g., Discogs `Released` -> eBay `Release Year`).
4.  **Caching**: Ensure `product_lookup` block in item JSON is updated with the enriched data to prevent redundant API calls.

**Acceptance**:
- `tgw lookup <SKU>` displays the new enriched fields.
- `tgw draft <SKU>` includes the enriched description and aspects.
