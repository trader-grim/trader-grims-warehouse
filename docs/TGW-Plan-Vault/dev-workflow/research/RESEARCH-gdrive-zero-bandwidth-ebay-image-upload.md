# RESEARCH: Zero-Bandwidth GDrive → eBay Image Upload

**Recovered:** 2026-06-19 (originally researched 2026-06-11, lost in vault consolidation)
**Related PP:** PP-PHOTO-001 (bulk photo re-upload / Trading API migration)
**Also applies to:** AI vision tasks — any worker that currently reads photos from local disk
**Status:** Research recovered partially. The original session included follow-up questions
about integrating this pattern into TGW's specific design for both eBay upload and AI vision
tasks, with sample code for multiple scenarios. That content was lost and needs to be
re-researched or reconstructed.

---

## Core Concept

ItemData photos are rclone-synced to Google Drive. Rather than reading photos from local
disk and uploading them to eBay via `UploadSiteHostedPictures` (Trading API, deprecated),
we can:

1. Temporarily grant "anyone with the link" read access to the GDrive file
2. Construct a direct download URL (`uc?export=download&id=<file_id>`)
3. Pass that URL to eBay's Inventory API `imageUrls[]` field
4. eBay's servers fetch the image directly from Google's CDN → creates EPS copy
5. Revoke the public permission immediately after eBay has fetched

**Google's servers handle all image hosting and bandwidth. Our server sends zero image bytes.**

This eliminates dependency on `UploadSiteHostedPictures` (Trading API, deprecated) and
removes local upload bandwidth from the photo pipeline entirely.

---

## Direct Download URL Format

The URL that bypasses Google Drive's preview page and serves the raw file:

```
https://drive.google.com/uc?export=download&id=<FILE_ID>
```

The `file_id` is the alphanumeric string in a standard GDrive share link:
`https://drive.google.com/file/d/<FILE_ID>/view`

---

## Python: Make File Temporarily Public + Get Direct Link

Uses `google-api-python-client`. Install: `pip install google-api-python-client google-auth-oauthlib`

```python
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive']

def get_drive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def make_public_and_get_direct_link(file_id: str) -> str:
    """Grant anyone-with-link read access and return the direct download URL."""
    service = get_drive_service()
    permission = {'role': 'reader', 'type': 'anyone'}
    service.permissions().create(
        fileId=file_id,
        body=permission,
        fields='id',
    ).execute()
    # Direct download URL — bypasses GDrive preview page
    # NOTE: original research had this truncated as "https://google.com{file_id}" — corrected
    direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"
    return direct_link


def revoke_public_access(file_id: str) -> None:
    """Remove the anyone-with-link permission after eBay has fetched the image."""
    service = get_drive_service()
    # List permissions to find the 'anyone' entry
    perms = service.permissions().list(fileId=file_id, fields='permissions(id,type)').execute()
    for perm in perms.get('permissions', []):
        if perm.get('type') == 'anyone':
            service.permissions().delete(fileId=file_id, permissionId=perm['id']).execute()
            break
```

---

## Integration Pattern for TGW

### How it fits into the existing pipeline

Current flow (deprecated):
```
local photo → ebay_upload worker → UploadSiteHostedPictures (Trading API) → EPS URL
EPS URL → ebay_stage (Inventory API imageUrls[])
```

Replacement flow:
```
local photo → rclone sync → GDrive
GDrive file_id → make_public → direct URL
direct URL → Inventory API imageUrls[] → eBay fetches → EPS URL stored in ebay_live
revoke_public (after eBay confirms fetch)
```

### Mapping local paths to GDrive file IDs

rclone syncs `ItemData/<SKU>/` to `dbukove:/TGW/data/ItemData/<SKU>/`. To get the
GDrive file_id for a photo:

```python
def get_gdrive_file_id(service, sku: str, filename: str) -> str | None:
    """Find the GDrive file_id for a given SKU photo."""
    # rclone sync preserves directory structure
    query = (
        f"name = '{filename}' and "
        f"'{sku}_folder_id' in parents and "
        f"trashed = false"
    )
    # Better: search by full path using parent folder traversal
    results = service.files().list(
        q=f"name = '{filename}' and trashed = false",
        spaces='drive',
        fields='files(id, name, parents)',
    ).execute()
    files = results.get('files', [])
    return files[0]['id'] if files else None
```

**Note:** Caching the file_id alongside the photo path in ItemData JSON would be more
efficient than searching on every upload. Could store as `gdrive_file_ids: {filename: id}`
in item JSON, populated by a one-time scan or maintained by the rclone sync event hook.

### Temporary exposure window

eBay typically fetches `imageUrls[]` at offer-create time (Inventory API PUT/POST).
The public window can be narrow:
1. Grant public access
2. POST to Inventory API
3. Wait for eBay to confirm (check `ebay_live.product.imageUrls[]` is populated)
4. Revoke public access

Or simpler: grant before the batch, revoke after the full SKU is staged. The exposure
window is seconds to minutes, not permanent.

### Authentication for TGW workers

Workers run as `tgw` user. OAuth token should be stored in `secrets_root/gdrive-token.json`
(same pattern as `ebay-token.json`). Service account key is an alternative that avoids
browser OAuth but requires a separate Google Cloud service account with Drive access.

---

## Application to AI Vision Tasks

The same pattern applies to `ai_identify` and `alt_text` workers. Currently they read
photos from local disk and pass them to Ollama (local) or base64-encode them for external
APIs. With GDrive URLs:

- External vision APIs (Gemini, OpenRouter vision models) can fetch images directly from
  GDrive URLs instead of receiving base64-encoded bytes in the request body
- Eliminates the base64 encoding overhead (photos are typically 500KB–3MB each)
- Same temporary-public pattern: grant → call API with URL → revoke

```python
# Instead of base64 encoding:
# base64_image = encode_image(photo_path)
# "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}

# Use GDrive direct URL:
gdrive_url = make_public_and_get_direct_link(file_id)
# "image_url": {"url": gdrive_url}
# ... call vision API ...
# revoke_public_access(file_id)
```

Gemini natively accepts image URLs. OpenRouter vision models accept the OpenAI image_url
format with HTTPS URLs. Neither requires base64 for URL-accessible images.

---

## Setup Requirements

1. **Google Cloud project** with Drive API enabled
2. **OAuth 2.0 credentials** (`credentials.json`) → store in `secrets_root/`
3. **Token file** (`token.json` / `gdrive-token.json`) → `secrets_root/`
4. **`google-api-python-client`** in TGW Python dependencies
5. **Drive scope** in OAuth client: `https://www.googleapis.com/auth/drive`
   (or `drive.file` scope if only managing files we created — more restrictive, preferred)

**Important:** The Drive scope must be added to the Google Cloud OAuth client, not to
eBay OAuth. These are separate credential systems.

---

## What Was Lost in the Original Research

The original session (2026-06-11) included follow-up questions and answers covering:
- Complete integration design for TGW's specific worker architecture
- Sample code for the full `ebay_upload` worker replacement
- Sample code for `ai_identify` and `alt_text` with GDrive URL pattern
- Batch processing logic (multiple photos per SKU in order)
- Error handling (GDrive rate limits, eBay fetch failures, revoke-on-error)
- File ID caching strategy in ItemData JSON
- Service account vs OAuth token tradeoffs for unattended worker operation

This document recovers the core concept and setup code. The integration-specific code
needs to be re-researched or reconstructed during PP-PHOTO-001 implementation.

---

## Sources

- Google Drive API Python quickstart: developers.google.com/drive/api/quickstart/python
- Permissions.create: developers.google.com/drive/api/reference/rest/v3/permissions/create
- Direct download URL format: `https://drive.google.com/uc?export=download&id=<id>`
- eBay Inventory API imageUrls: developer.ebay.com/api-docs/sell/inventory/types/slr:Product
