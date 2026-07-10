## Shelved
### eBay relisting obfuscation (PP-ADD-007) — shelved; ToS review required
- Concept: delist → mutate photo checksum → regenerate title/description → assign mock SKU → relist as new
- Shelved because: the photo mutation step is designed to defeat eBay's duplicate image detection — this is the mechanism that makes it work, and it is almost certainly a policy violation
- Simple relist (end listing → relist unchanged) is permitted and does not need this tool
- eBay policies to read before reconsidering:
  - **Duplicate listings**: same single-quantity item cannot appear as multiple active listings; technical manipulation to defeat detection is not permitted
  - **Search and browse manipulation**: relisting to artificially reset listing age or boost placement is prohibited
  - **Image manipulation**: pixel-level edits to change image hashes specifically to evade duplicate detection
  - **Item identity via custom label**: using a new mock SKU to cause eBay to treat a relisted item as unrelated
  - **Account risk**: violations can trigger listing removal, seller limits, or account suspension
- Do not implement until an explicit eBay ToS review confirms the specific techniques are compliant

