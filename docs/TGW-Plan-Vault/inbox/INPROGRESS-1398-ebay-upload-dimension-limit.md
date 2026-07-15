# In progress: todo #1398 — ebay_upload dimension-limit pre-flight resize

Working in worktree `/opt/TGW/var/worktrees/1398-ebay-upload-dimension-limit`
on branch `todo/1398-ebay-upload-dimension-limit`.

Adding a pre-flight dimension check + downscale (Pillow) in
`src/tgw/ebay/upload.py`'s `upload_photo()` for photos exceeding eBay's
15000px UploadSiteHostedPictures limit. Resize happens on a temp copy only
— never mutates the stored ItemData original. Logs original->resized
dimensions via tgw_logging.log_event for invariant C11 durability. Unit
tests added per packet acceptance criteria (oversized resized, normal
untouched byte-identical, original file on disk untouched).

Packet: docs/TGW-Plan-Vault/plan/packets/1398-ebay-upload-dimension-limit.md
