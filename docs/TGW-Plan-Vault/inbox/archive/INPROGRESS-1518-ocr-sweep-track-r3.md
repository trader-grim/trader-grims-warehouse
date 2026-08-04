# In progress: #1518 PP-KNOWLEDGE-001 Track R3 (OCR sweep)

Working in worktree `/opt/TGW/var/worktrees/1518-ocr-sweep-track-r3` on branch
`todo/1518-ocr-sweep-track-r3`, off `catio-nix-0.0.1-alpha`.

Task: build the tesseract-via-recoll-filter OCR mechanism so serials/labels/barcodes
in ItemData photos become findable through `tgw search --full-text` / `tgw_search_full`
(follow-on to #1147/R2, already merged). Per the packet spec, this is a proof-of-mechanism
against a SMALL REAL SAMPLE of ItemData photos, not a full-fleet sweep (thermal-aware —
full sweep is a separate later operation, possibly on a1131's ro NFS mount).

Pre-flight: checking whether tesseract + a recoll image-OCR filter (rclocr / rclimage)
are installed on tgw-prod, and how `/opt/TGW/.recoll` config is laid out, before writing
any code.
