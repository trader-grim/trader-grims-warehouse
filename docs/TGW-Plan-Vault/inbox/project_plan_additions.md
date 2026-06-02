**PROJECT PLAN ADDITIONS**

Catalog, eBay Integration & Operations Modules

Prepared: June 2, 2026 \| Items: PP-ADD-001 through PP-ADD-010

**Summary**

  -------- --------------------------- -------------- ------------ --------------
  **ID**   **Title**                   **Priority**   **Effort**   **Phase**

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   

                                                                   
  -------- --------------------------- -------------- ------------ --------------

**PP-ADD-001 Satellite / Client Operation --- Disconnected Catalog
Support**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-001

  **Priority**       High

  **Estimated        Large (4--6 weeks)
  Effort**           

  **Phase / Track**  Infrastructure

  **Dependencies**   Master catalog schema, SKU normalization (PP-ADD-005),
                     History module (PP-ADD-003)
  ------------------ ------------------------------------------------------

  ------------------ ------------------------------------------------------
  **Overview**       

  Enable             
  satellite/client   
  nodes to operate   
  independently when 
  disconnected or    
  loosely connected  
  from the master    
  system. Includes   
  thumbnail          
  generation for     
  catalog browsing,  
  temporary catalog  
  update handling,   
  and a defined data 
  migration path to  
  promote local      
  changes back to    
  master.            
  ------------------ ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Thumbnail |                                                      |
|               |                                                      |
|    generation |                                                      |
|     service   |                                                      |
|     (catalog  |                                                      |
|     browse    |                                                      |
|     images,   |                                                      |
|               |                                                      |
|  configurable |                                                      |
|               |                                                      |
|   resolution) |                                                      |
|               |                                                      |
| -   Local     |                                                      |
|     temporary |                                                      |
|     catalog   |                                                      |
|     store     |                                                      |
|     (SQLite   |                                                      |
|     or        |                                                      |
|               |                                                      |
|   equivalent, |                                                      |
|     mirrored  |                                                      |
|     subset of |                                                      |
|     master)   |                                                      |
|               |                                                      |
| -             |                                                      |
|    Dirty-flag |                                                      |
|     /         |                                                      |
|               |                                                      |
|    change-log |                                                      |
|     mechanism |                                                      |
|     for all   |                                                      |
|     local     |                                                      |
|     edits     |                                                      |
|               |                                                      |
| -   S         |                                                      |
| ync/promotion |                                                      |
|     worker:   |                                                      |
|     conflict  |                                                      |
|               |                                                      |
|    detection, |                                                      |
|     merge     |                                                      |
|     strategy, |                                                      |
|     audit     |                                                      |
|     trail     |                                                      |
|               |                                                      |
| -   API       |                                                      |
|     surface   |                                                      |
|     for       |                                                      |
|     client    |                                                      |
|     nodes to  |                                                      |
|     pull      |                                                      |
|     catalog   |                                                      |
|     updates   |                                                      |
|     and push  |                                                      |
|     local     |                                                      |
|     changes   |                                                      |
|               |                                                      |
| -   Admin UI  |                                                      |
|     panel     |                                                      |
|     showing   |                                                      |
|     per-node  |                                                      |
|     sync      |                                                      |
|     status    |                                                      |
|     and       |                                                      |
|     pending   |                                                      |
|               |                                                      |
|    migrations |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Conflict          
  resolution policy  
  must be defined    
  before dev starts  
  (last-write-wins   
  vs. manual         
  review). Thumbnail 
  cache invalidation 
  should align with  
  the Backup/Sync    
  module             
  (PP-ADD-004).*     
  ------------------ ------------------------------------------------------

**PP-ADD-002 Linux / Android GUI Application**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-002

  **Priority**       High

  **Estimated        XL (8--12 weeks)
  Effort**           

  **Phase / Track**  Frontend / Client

  **Dependencies**   REST/gRPC API layer, SKU normalization (PP-ADD-005),
                     Satellite sync (PP-ADD-001)
  ------------------ ------------------------------------------------------

  ---------------- ------------------------------------------------------
  **Overview**     

  Cross-platform   
  GUI application  
  (Linux desktop + 
  Android)         
  providing full   
  system access:   
  catalog          
  browsing,        
  catalog editing, 
  inventory        
  management, and  
  related          
  interfaces.      
  Should expose    
  all major        
  backend          
  functions        
  currently        
  accessible only  
  through CLI or   
  web.             
  ---------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -             |                                                      |
|    Technology |                                                      |
|     selection |                                                      |
|     spike:    |                                                      |
|     Flutter,  |                                                      |
|     Tauri, or |                                                      |
|     Qt ---    |                                                      |
|     confirm   |                                                      |
|     once      |                                                      |
|     (target:  |                                                      |
|     Linux     |                                                      |
|     x86_64 +  |                                                      |
|     ARM,      |                                                      |
|     Android   |                                                      |
|     10+)      |                                                      |
|               |                                                      |
| -   Catalog   |                                                      |
|     browser:  |                                                      |
|     search,   |                                                      |
|     filter,   |                                                      |
|     sort,     |                                                      |
|     thumbnail |                                                      |
|     grid/list |                                                      |
|     toggle    |                                                      |
|               |                                                      |
| -   Catalog   |                                                      |
|     editor:   |                                                      |
|               |                                                      |
|   field-level |                                                      |
|     edit,     |                                                      |
|     image     |                                                      |
|               |                                                      |
|   management, |                                                      |
|     eBay      |                                                      |
|     field     |                                                      |
|     mapping   |                                                      |
|               |                                                      |
| -   Inventory |                                                      |
|               |                                                      |
|    interface: |                                                      |
|     stock     |                                                      |
|     levels,   |                                                      |
|     location, |                                                      |
|     picklist  |                                                      |
|               |                                                      |
|   integration |                                                      |
|               |                                                      |
|  (PP-ADD-009) |                                                      |
|               |                                                      |
| -   Settings  |                                                      |
|     /         |                                                      |
|               |                                                      |
|    connection |                                                      |
|     panel:    |                                                      |
|     master    |                                                      |
|     URL, sync |                                                      |
|     mode,     |                                                      |
|     auth      |                                                      |
|               |                                                      |
| -             |                                                      |
|    Packaging: |                                                      |
|     .deb /    |                                                      |
|     .AppImage |                                                      |
|     for       |                                                      |
|     Linux;    |                                                      |
|     signed    |                                                      |
|     .apk /    |                                                      |
|     Play      |                                                      |
|     Store for |                                                      |
|     Android   |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Reuse picklist    
  generator          
  (PP-ADD-009) as an 
  embedded screen.   
  QR code support    
  (from PP-ADD-009)  
  should be a        
  first-class        
  display element in 
  the inventory      
  view.*             
  ------------------ ------------------------------------------------------

**PP-ADD-003 History Integration --- SKU History Merge Worker**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-003

  **Priority**       Medium-High

  **Estimated        Medium (3--4 weeks)
  Effort**           

  **Phase / Track**  Data / Workers

  **Dependencies**   SKU normalization (PP-ADD-005), existing history
                     tables/logs
  ------------------ ------------------------------------------------------

  ---------------- ------------------------------------------------------
  **Overview**     

  Background       
  worker that      
  aggregates,      
  deduplicates,    
  and organizes    
  item history     
  records by SKU   
  across all data  
  sources.         
  Produces a       
  canonical        
  per-SKU history  
  timeline usable  
  by other modules 
  (eBay relisting, 
  duplicate        
  detection,       
  audit).          
  ---------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   History   |                                                      |
|     schema:   |                                                      |
|     per-SKU   |                                                      |
|     event log |                                                      |
|     (event    |                                                      |
|     type,     |                                                      |
|               |                                                      |
|    timestamp, |                                                      |
|     source,   |                                                      |
|     actor,    |                                                      |
|     payload)  |                                                      |
|               |                                                      |
| -   Ingest    |                                                      |
|     adapters  |                                                      |
|     for each  |                                                      |
|     existing  |                                                      |
|     history   |                                                      |
|     source    |                                                      |
|     (define   |                                                      |
|     list      |                                                      |
|     during    |                                                      |
|               |                                                      |
|    discovery) |                                                      |
|               |                                                      |
| -             |                                                      |
| Deduplication |                                                      |
|     logic:    |                                                      |
|     finge     |                                                      |
| rprint-based, |                                                      |
|               |                                                      |
|  configurable |                                                      |
|     merge     |                                                      |
|     window    |                                                      |
|               |                                                      |
| -   Scheduled |                                                      |
|     worker    |                                                      |
|               |                                                      |
| (cron/queue): |                                                      |
|               |                                                      |
|   incremental |                                                      |
|     merge on  |                                                      |
|     new       |                                                      |
|     events,   |                                                      |
|     full      |                                                      |
|     rebuild   |                                                      |
|     on demand |                                                      |
|               |                                                      |
| -   API       |                                                      |
|               |                                                      |
|    endpoints: |                                                      |
|     GET       |                                                      |
|     /h        |                                                      |
| istory/{sku}, |                                                      |
|     GET       |                                                      |
|               |                                                      |
|    /history/{ |                                                      |
| sku}/timeline |                                                      |
|               |                                                      |
| -   Admin     |                                                      |
|     trigger   |                                                      |
|     for full  |                                                      |
|     rebuild / |                                                      |
|     re-merge  |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *SKU normalization 
  (PP-ADD-005) must  
  be completed or    
  run in parallel    
  --- history        
  merging on         
  un-normalized SKUs 
  will produce       
  duplicates.*       
  ------------------ ------------------------------------------------------

**PP-ADD-004 Backup / Archive / Sync / History Merge Integration**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-004

  **Priority**       High

  **Estimated        Medium (3--5 weeks)
  Effort**           

  **Phase / Track**  Infrastructure

  **Dependencies**   Satellite sync (PP-ADD-001), History worker
                     (PP-ADD-003)
  ------------------ ------------------------------------------------------

  ----------------- ------------------------------------------------------
  **Overview**      

  Unified module    
  covering          
  automated backup, 
  long-term         
  archival,         
  multi-node sync,  
  and optional      
  integration with  
  the History merge 
  worker. Ensures   
  data durability,  
  recoverability,   
  and consistency   
  across all        
  environments.     
  ----------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Scheduled |                                                      |
|     full +    |                                                      |
|               |                                                      |
|   incremental |                                                      |
|     backup    |                                                      |
|               |                                                      |
| (configurable |                                                      |
|     retention |                                                      |
|     policy)   |                                                      |
|               |                                                      |
| -   Archive   |                                                      |
|     tier:     |                                                      |
|     compress  |                                                      |
|     and move  |                                                      |
|     aged      |                                                      |
|     records   |                                                      |
|     to cold   |                                                      |
|     storage   |                                                      |
|     path      |                                                      |
|               |                                                      |
| -   Sync      |                                                      |
|     engine:   |                                                      |
|     push/pull |                                                      |
|     between   |                                                      |
|     master    |                                                      |
|     and       |                                                      |
|     satellite |                                                      |
|     nodes     |                                                      |
|     (may      |                                                      |
|     reuse     |                                                      |
|               |                                                      |
|    PP-ADD-001 |                                                      |
|     worker)   |                                                      |
|               |                                                      |
| -   Optional: |                                                      |
|     post-sync |                                                      |
|     trigger   |                                                      |
|     to invoke |                                                      |
|     History   |                                                      |
|     merge     |                                                      |
|     worker    |                                                      |
|     for newly |                                                      |
|     synced    |                                                      |
|     records   |                                                      |
|               |                                                      |
| -   Restore   |                                                      |
|     procedure |                                                      |
|     and       |                                                      |
|     runbook   |                                                      |
|     (tested)  |                                                      |
|               |                                                      |
| -             |                                                      |
| Health/status |                                                      |
|               |                                                      |
|    dashboard: |                                                      |
|     last      |                                                      |
|     backup    |                                                      |
|     time,     |                                                      |
|     backup    |                                                      |
|     size,     |                                                      |
|     sync lag  |                                                      |
|     per node  |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Evaluate whether  
  sync engine is a   
  separate service   
  or a mode of the   
  satellite worker   
  from PP-ADD-001 to 
  avoid              
  duplication.*      
  ------------------ ------------------------------------------------------

**PP-ADD-005 SKU Normalization --- Epoch 0, 2005--2007, Length, and SKU
History**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-005

  **Priority**       Critical

  **Estimated        Medium (2--4 weeks)
  Effort**           

  **Phase / Track**  Data / Foundation

  **Dependencies**   None --- must run before or in parallel with
                     PP-ADD-003, PP-ADD-007, PP-ADD-008
  ------------------ ------------------------------------------------------

  ------------------ ------------------------------------------------------
  **Overview**       

  One-time (with     
  ongoing            
  enforcement)       
  normalization of   
  SKUs across three  
  defined groups:    
  Epoch 0 legacy     
  identifiers,       
  2005--2007 era     
  SKUs (stored as    
  202005--202007     
  prefix format),    
  and length         
  standardization.   
  Add a SKU          
  history/alias      
  table to track all 
  prior SKU values   
  for each item.     
  ------------------ ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   SKU audit |                                                      |
|     report:   |                                                      |
|     current   |                                                      |
|               |                                                      |
|  distribution |                                                      |
|     across    |                                                      |
|     all three |                                                      |
|     groups +  |                                                      |
|     length    |                                                      |
|     histogram |                                                      |
|               |                                                      |
| -             |                                                      |
| Normalization |                                                      |
|     rules     |                                                      |
|               |                                                      |
|    documented |                                                      |
|     per group |                                                      |
|     (Epoch 0  |                                                      |
|     mapping,  |                                                      |
|     2         |                                                      |
| 02005--202007 |                                                      |
|               |                                                      |
|  re-encoding, |                                                      |
|     length    |                                                      |
|     paddi     |                                                      |
| ng/truncation |                                                      |
|     rules)    |                                                      |
|               |                                                      |
| -   Migration |                                                      |
|     script:   |                                                      |
|     dry-run   |                                                      |
|     mode,     |                                                      |
|     then live |                                                      |
|     run with  |                                                      |
|     rollback  |                                                      |
|     support   |                                                      |
|               |                                                      |
| -             |                                                      |
|   sku_history |                                                      |
|     table:    |                                                      |
|               |                                                      |
| (sku_current, |                                                      |
|               |                                                      |
|    sku_prior, |                                                      |
|               |                                                      |
|   changed_at, |                                                      |
|     c         |                                                      |
| hange_reason, |                                                      |
|               |                                                      |
|   changed_by) |                                                      |
|               |                                                      |
| -             |                                                      |
|  Enforcement: |                                                      |
|               |                                                      |
|    validation |                                                      |
|     at        |                                                      |
|     ingestion |                                                      |
|     points to |                                                      |
|     reject or |                                                      |
|               |                                                      |
|  auto-correct |                                                      |
|     n         |                                                      |
| on-conforming |                                                      |
|     SKUs      |                                                      |
|               |                                                      |
| -   P         |                                                      |
| ost-migration |                                                      |
|               |                                                      |
|  verification |                                                      |
|     report    |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Define the        
  canonical SKU      
  format spec as a   
  shared document    
  before writing any 
  migration code.    
  All other modules  
  depend on this.*   
  ------------------ ------------------------------------------------------

**PP-ADD-006 Duplicate Item / Duplicate Listing Check Worker**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-006

  **Priority**       High

  **Estimated        Small--Medium (2--3 weeks)
  Effort**           

  **Phase / Track**  Workers / eBay Integration

  **Dependencies**   SKU normalization (PP-ADD-005), eBay API credentials
  ------------------ ------------------------------------------------------

  ---------------- ------------------------------------------------------
  **Overview**     

  Pre-upload       
  worker that      
  checks for       
  duplicate items  
  in the full      
  catalog and for  
  duplicate active 
  eBay listings    
  before any item  
  is submitted to  
  eBay. Prevents   
  redundant        
  listings, fee    
  waste, and       
  potential policy 
  violations.      
  ---------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Catalog   |                                                      |
|     duplicate |                                                      |
|     detector: |                                                      |
|     exact SKU |                                                      |
|     match +   |                                                      |
|     fuzzy     |                                                      |
|     titl      |                                                      |
| e/description |                                                      |
|     match     |                                                      |
|               |                                                      |
| (configurable |                                                      |
|               |                                                      |
|    threshold) |                                                      |
|               |                                                      |
| -   eBay      |                                                      |
|     active    |                                                      |
|     listing   |                                                      |
|     check:    |                                                      |
|     query     |                                                      |
|     eBay API  |                                                      |
|     for       |                                                      |
|     existing  |                                                      |
|     active    |                                                      |
|     listings  |                                                      |
|     by SKU /  |                                                      |
|     mock SKU  |                                                      |
|               |                                                      |
| -             |                                                      |
|    Pre-upload |                                                      |
|     gate:     |                                                      |
|     block or  |                                                      |
|     warn on   |                                                      |
|     duplicate |                                                      |
|     detection |                                                      |
|               |                                                      |
| (configurable |                                                      |
|     policy:   |                                                      |
|     block vs. |                                                      |
|     warn)     |                                                      |
|               |                                                      |
| -   Duplicate |                                                      |
|     report:   |                                                      |
|     surfaced  |                                                      |
|     in admin  |                                                      |
|     UI and/or |                                                      |
|     email     |                                                      |
|     digest    |                                                      |
|               |                                                      |
| -   CLI       |                                                      |
|               |                                                      |
|    invocation |                                                      |
|     for       |                                                      |
|     manual    |                                                      |
|               |                                                      |
|  full-catalog |                                                      |
|     scans     |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Fuzzy matching    
  algorithm (e.g.,   
  Jaccard, cosine    
  similarity on      
  title tokens)      
  should be tunable  
  to reduce false    
  positives without  
  missing real       
  duplicates.*       
  ------------------ ------------------------------------------------------

**PP-ADD-007 eBay Relisting Obfuscation Tool**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-007

  **Priority**       Medium

  **Estimated        Medium (3--4 weeks)
  Effort**           

  **Phase / Track**  eBay Integration / Tools

  **Dependencies**   SKU history (PP-ADD-005), History worker (PP-ADD-003),
                     eBay API credentials
  ------------------ ------------------------------------------------------

  -------------------- ------------------------------------------------------
  **Overview**         

  Tool to safely       
  relist aged or       
  removed eBay items   
  as new listings by   
  delisting, making    
  minor photo          
  alterations          
  (checksum change),   
  regenerating         
  title/description,   
  assigning a mock     
  SKU, and relisting.  
  All steps recorded   
  in eBay data and SKU 
  history.             
  -------------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Delist    |                                                      |
|     step:     |                                                      |
|     graceful  |                                                      |
|     eBay API  |                                                      |
|     delist    |                                                      |
|     with      |                                                      |
|               |                                                      |
|  confirmation |                                                      |
|               |                                                      |
| -   Photo     |                                                      |
|     mutation  |                                                      |
|     step:     |                                                      |
|               |                                                      |
|   pixel-level |                                                      |
|               |                                                      |
|    micro-edit |                                                      |
|     (e.g.,    |                                                      |
|               |                                                      |
|  single-pixel |                                                      |
|     hue shift |                                                      |
|     or        |                                                      |
|     metadata  |                                                      |
|     s         |                                                      |
| trip/rewrite) |                                                      |
|     to change |                                                      |
|     checksum  |                                                      |
|     without   |                                                      |
|     visible   |                                                      |
|               |                                                      |
|    difference |                                                      |
|               |                                                      |
| -   Titl      |                                                      |
| e/description |                                                      |
|               |                                                      |
| regeneration: |                                                      |
|     t         |                                                      |
| emplate-based |                                                      |
|     variation |                                                      |
|     or        |                                                      |
|               |                                                      |
|   AI-assisted |                                                      |
|     rewrite   |                                                      |
|               |                                                      |
| -   Mock SKU  |                                                      |
|               |                                                      |
|   generation: |                                                      |
|     new SKU   |                                                      |
|     linked to |                                                      |
|     original  |                                                      |
|     via       |                                                      |
|               |                                                      |
|   sku_history |                                                      |
|               |                                                      |
| -   Relist    |                                                      |
|     step:     |                                                      |
|     eBay API  |                                                      |
|     new       |                                                      |
|     listing   |                                                      |
|               |                                                      |
|    submission |                                                      |
|     with      |                                                      |
|               |                                                      |
|   regenerated |                                                      |
|     content   |                                                      |
|               |                                                      |
| -   Audit     |                                                      |
|     record:   |                                                      |
|     full      |                                                      |
|     event     |                                                      |
|     logged in |                                                      |
|     SKU       |                                                      |
|     history   |                                                      |
|     and eBay  |                                                      |
|     data      |                                                      |
|     store     |                                                      |
|               |                                                      |
| -   Batch     |                                                      |
|     mode:     |                                                      |
|     queue     |                                                      |
|     multiple  |                                                      |
|     items for |                                                      |
|     overnight |                                                      |
|               |                                                      |
|    processing |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *Legal/ToS review  
  recommended before 
  deployment. Photo  
  mutation must be   
  visually           
  imperceptible.     
  Mock SKU must be   
  resolvable back to 
  original SKU for   
  internal           
  tracking.*         
  ------------------ ------------------------------------------------------

**PP-ADD-008 Inventory API Migration Sweep Tool**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-008

  **Priority**       Medium

  **Estimated        Small--Medium (2--3 weeks)
  Effort**           

  **Phase / Track**  eBay Integration / Workers

  **Dependencies**   eBay API credentials, catalog data access
  ------------------ ------------------------------------------------------

  ---------------- ------------------------------------------------------
  **Overview**     

  Automated        
  periodic tool    
  that identifies  
  catalog items    
  not yet migrated 
  to the eBay      
  Inventory API    
  (seller hub item 
  creation flow)   
  and facilitates  
  their migration. 
  Enables use of   
  the modern       
  Inventory API    
  for item         
  creation and     
  management.      
  ---------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Sweep     |                                                      |
|     worker:   |                                                      |
|     periodic  |                                                      |
|     scan      |                                                      |
|               |                                                      |
| (configurable |                                                      |
|     interval) |                                                      |
|     comparing |                                                      |
|     catalog   |                                                      |
|     items     |                                                      |
|     against   |                                                      |
|     Inventory |                                                      |
|     API       |                                                      |
|     records   |                                                      |
|               |                                                      |
| -   Gap       |                                                      |
|     report:   |                                                      |
|     items     |                                                      |
|     present   |                                                      |
|     in        |                                                      |
|     catalog   |                                                      |
|     but       |                                                      |
|     absent    |                                                      |
|     from      |                                                      |
|     Inventory |                                                      |
|     API       |                                                      |
|               |                                                      |
| -   Migration |                                                      |
|     assist:   |                                                      |
|               |                                                      |
|   auto-create |                                                      |
|     Inventory |                                                      |
|     API       |                                                      |
|     records   |                                                      |
|     for       |                                                      |
|               |                                                      |
|    unmigrated |                                                      |
|     items     |                                                      |
|     (with     |                                                      |
|     dry-run   |                                                      |
|     mode)     |                                                      |
|               |                                                      |
| -   Seller    |                                                      |
|     Hub       |                                                      |
|     c         |                                                      |
| ompatibility: |                                                      |
|     ensure    |                                                      |
|     created   |                                                      |
|     records   |                                                      |
|     are       |                                                      |
|     visible   |                                                      |
|     and       |                                                      |
|     editable  |                                                      |
|     in Seller |                                                      |
|     Hub       |                                                      |
|               |                                                      |
| -   Dashboard |                                                      |
|     widget: % |                                                      |
|     migrated, |                                                      |
|     last      |                                                      |
|     sweep     |                                                      |
|     time,     |                                                      |
|     items     |                                                      |
|     pending   |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *eBay Inventory    
  API rate limits    
  must be respected; 
  implement backoff  
  and batch sizing.  
  Confirm which eBay 
  API version        
  (Trading vs.       
  Inventory vs. Sell 
  APIs) is the       
  target.*           
  ------------------ ------------------------------------------------------

**PP-ADD-009 Picklist Generator**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-009

  **Priority**       Medium

  **Estimated        Small (1--2 weeks)
  Effort**           

  **Phase / Track**  Operations / Tools

  **Dependencies**   Inventory data, order data, tgw.source picklist_line
                     schema
  ------------------ ------------------------------------------------------

  --------------------- ------------------------------------------------------
  **Overview**          

  Replacement/upgrade   
  for the current       
  phone-app-based       
  picklist generation.  
  Generates structured  
  pick lists from       
  orders against        
  inventory,            
  referencing the       
  picklist_line schema  
  from tgw.source.      
  Optionally replaces   
  plain-text            
  picklist_line in item 
  descriptions with a   
  scannable QR code.    
  --------------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -   Picklist  |                                                      |
|     generator |                                                      |
|     service:  |                                                      |
|     input     |                                                      |
|     order     |                                                      |
|     IDs,      |                                                      |
|     output    |                                                      |
|     ordered   |                                                      |
|     pick list |                                                      |
|     sorted by |                                                      |
|               |                                                      |
|  location/bin |                                                      |
|               |                                                      |
| -             |                                                      |
| picklist_line |                                                      |
|     schema    |                                                      |
|     c         |                                                      |
| ompatibility: |                                                      |
|     read from |                                                      |
|     and write |                                                      |
|     to        |                                                      |
|     existing  |                                                      |
|               |                                                      |
|    tgw.source |                                                      |
|     format    |                                                      |
|               |                                                      |
| -             |                                                      |
|   Print-ready |                                                      |
|     output:   |                                                      |
|     PDF pick  |                                                      |
|     sheet     |                                                      |
|     with      |                                                      |
|     item,     |                                                      |
|     SKU,      |                                                      |
|     location, |                                                      |
|     quantity  |                                                      |
|               |                                                      |
| -   QR code   |                                                      |
|     option:   |                                                      |
|     generate  |                                                      |
|     per-item  |                                                      |
|     QR        |                                                      |
|     encoding  |                                                      |
|               |                                                      |
| picklist_line |                                                      |
|     data;     |                                                      |
|     embed in  |                                                      |
|     item      |                                                      |
|               |                                                      |
|   description |                                                      |
|     and/or    |                                                      |
|     print     |                                                      |
|     sheet     |                                                      |
|               |                                                      |
| -   Web/app   |                                                      |
|               |                                                      |
|    interface: |                                                      |
|     trigger   |                                                      |
|     picklist  |                                                      |
|               |                                                      |
|    generation |                                                      |
|     from GUI  |                                                      |
|     app       |                                                      |
|               |                                                      |
|  (PP-ADD-002) |                                                      |
|     or        |                                                      |
|               |                                                      |
|    standalone |                                                      |
|     page      |                                                      |
+---------------+------------------------------------------------------+

  ------------------ ------------------------------------------------------
  **Notes &          
  Considerations**   

  *QR code approach  
  should be          
  evaluated against  
  existing phone app 
  scanning           
  capability before  
  finalizing format. 
  Keep plain-text    
  picklist_line as   
  fallback during    
  transition.*       
  ------------------ ------------------------------------------------------

**PP-ADD-010 Installer / Updater / Health Monitor --- Claude Code,
Ollama, Whisper.cpp**

  ------------------ ------------------------------------------------------
  **Project          
  Details**          

  **Project ID**     PP-ADD-010

  **Priority**       Medium

  **Estimated        Small--Medium (2--3 weeks)
  Effort**           

  **Phase / Track**  DevOps / Infrastructure

  **Dependencies**   Network access, target platform OS (Linux, Windows,
                     macOS --- confirm scope)
  ------------------ ------------------------------------------------------

  ---------------- ------------------------------------------------------
  **Overview**     

  Unified          
  installer,       
  auto-updater,    
  and health       
  monitoring tool  
  for the three    
  AI/ML runtime    
  dependencies:    
  Claude Code,     
  Ollama, and      
  Whisper.cpp.     
  Leverages the    
  common installer 
  pattern shared   
  across all three 
  to provide a     
  consistent       
  management       
  interface.       
  ---------------- ------------------------------------------------------

+---------------+------------------------------------------------------+
| **Key         |                                                      |
| D             |                                                      |
| eliverables** |                                                      |
+---------------+------------------------------------------------------+
| -             |                                                      |
|    Installer: |                                                      |
|     detect    |                                                      |
|     missing   |                                                      |
|               |                                                      |
|   components, |                                                      |
|     download  |                                                      |
|     correct   |                                                      |
|     release   |                                                      |
|     for       |                                                      |
|     p         |                                                      |
| latform/arch, |                                                      |
|     verify    |                                                      |
|     checksum, |                                                      |
|     install   |                                                      |
|               |                                                      |
| -   Updater:  |                                                      |
|     version   |                                                      |
|     check     |                                                      |
|     against   |                                                      |
|     upstream  |                                                      |
|     (GitHub   |                                                      |
|     releases  |                                                      |
|     / API),   |                                                      |
|     in-place  |                                                      |
|     upgrade   |                                                      |
|     with      |                                                      |
|     rollback  |                                                      |
|               |                                                      |
| -   Health    |                                                      |
|     monitor:  |                                                      |
|     process   |                                                      |
|     check,    |                                                      |
|     API       |                                                      |
|     r         |                                                      |
| esponsiveness |                                                      |
|     ping,     |                                                      |
|     GPU/CPU   |                                                      |
|     resource  |                                                      |
|     usage     |                                                      |
|     snapshot  |                                                      |
|               |                                                      |
| -   Unified   |                                                      |
|     CLI:      |                                                      |
|     \`mgr     |                                                      |
|     install   |                                                      |
|     \<c       |                                                      |
| omponent\>\`, |                                                      |
|     \`mgr     |                                                      |
|     update    |                                                      |
|               |                                                      |
|    \[all\]\`, |                                                      |
|     \`mgr     |                                                      |
|     status\`, |                                                      |
|     \`mgr     |                                                      |
|     restart   |                                                      |
|     \<        |                                                      |
| component\>\` |                                                      |
|               |                                                      |
| -   Scheduled |                                                      |
|     health    |                                                      |
|     check:    |                                                      |
|               |                                                      |
|  cron/service |                                                      |
|     wrapper,  |                                                      |
|     alert on  |                                                      |
|     unhealthy |                                                      |
|     state     |                                                      |
|     (log +    |                                                      |
|     optional  |                                                      |
|               |                                                      |
| notification) |                                                      |
|               |                                                      |
| -   Shared    |                                                      |
|     installer |                                                      |
|     base      |                                                      |
|               |                                                      |
|  class/module |                                                      |
|     reused    |                                                      |
|     across    |                                                      |
|     all three |                                                      |
|               |                                                      |
|    components |                                                      |
+---------------+------------------------------------------------------+

  ------------------- ------------------------------------------------------
  **Notes &           
  Considerations**    

  *Confirm target OS  
  matrix. Whisper.cpp 
  may require         
  build-from-source   
  on some platforms   
  --- factor in build 
  toolchain setup.    
  Claude Code         
  installer pattern   
  should be reviewed  
  first as the        
  reference           
  implementation.*    
  ------------------- ------------------------------------------------------
