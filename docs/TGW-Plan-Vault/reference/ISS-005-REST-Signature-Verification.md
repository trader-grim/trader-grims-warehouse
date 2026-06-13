# Implementation Spec: eBay REST Webhook Signature Verification (ISS-005)

This document provides the technical specification for implementing signature verification for eBay's REST-based event notifications, specifically the mandatory `MARKETPLACE_ACCOUNT_DELETION` topic.

## 1. Overview
eBay REST webhooks require a two-stage security handshake:
1. **Endpoint Validation (GET)**: A one-time or periodic challenge-response to prove ownership of the listener URL. Uses a shared secret (**Verification Token**).
2. **Payload Verification (POST)**: Cryptographic verification of every incoming notification using an asymmetric signature provided in the `X-EBAY-SIGNATURE` header.

## 2. Phase 1: Endpoint Validation (GET Request)
When you register the URL or click "Test" in the eBay Developer Portal, eBay sends a `GET` request.

**Request:**
`GET https://<your-endpoint>?challenge_code=<random_string>`

**Logic:**
1. Retrieve the `challenge_code` from the query parameters.
2. Retrieve the `verification_token` (set by the operator in the Developer Portal).
3. Retrieve the `endpoint_url` (exactly as registered, e.g., `https://api.tgw.com/webhooks/ebay`).
4. Compute the `challengeResponse` as a SHA-256 hash of the concatenated values:
   `challengeResponse = HEX(SHA256(challenge_code + verification_token + endpoint_url))`

**Response (HTTP 200 OK):**
```json
{
  "challengeResponse": "..."
}
```

## 3. Phase 2: Payload Verification (POST Request)
Every notification includes the `X-EBAY-SIGNATURE` header.

**Header Structure (Base64 Encoded JSON):**
```json
{
  "alg": "ecdsa",
  "kid": "...",
  "signature": "...",
  "digest": "SHA256"
}
```

**Verification Steps:**
1. **Extract Header**: Retrieve the `X-EBAY-SIGNATURE` value.
2. **Decode Metadata**: Base64-decode the header to extract the `kid` (Key ID), `alg` (Algorithm), and `signature`.
3. **Retrieve Public Key**:
   - Call eBay's Notification API: `GET https://api.ebay.com/commerce/notification/v1/public_key/{kid}`
   - **Authentication**: Requires an OAuth **Client Credentials** token.
   - **Caching**: The public key **MUST** be cached (e.g., in Redis or SQLite) for 24 hours to avoid rate limits.
4. **Cryptographic Verification**:
   - **Input Data**: The **RAW** bytes of the POST request body.
   - **Signature**: The Base64-decoded `signature` from the header.
   - **Algorithm**: ECDSA or Ed25519 (as specified in `alg`).
   - **Hash**: SHA-256 (as specified in `digest`).
5. **Acknowledge**:
   - If valid: Return **HTTP 204 No Content** (or 200).
   - If invalid: Return **HTTP 412 Precondition Failed**.

## 4. Required Secrets (`ebay-credentials.json`)
The following fields must be present in the credentials file:
- `verification_token`: The 32-80 character random string from the Developer Portal.
- `app_id`, `cert_id`, `dev_id`: Standard eBay App keys (needed for OAuth/API calls).

## 5. Python Implementation Guide
Use the `cryptography` library for signature verification.

```python
import hashlib
import json
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

def verify_rest_signature(raw_body: bytes, signature_header: str, public_key_pem: str):
    # 1. Decode header
    metadata = json.loads(base64.b64decode(signature_header))
    signature = base64.b64decode(metadata['signature'])
    
    # 2. Load public key
    # Note: Wrap in PEM headers if the API returns raw SPKI
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    
    # 3. Verify
    try:
        public_key.verify(
            signature,
            raw_body,
            ec.ECDSA(hashes.SHA256())
        )
        return True
    except Exception:
        return False
```

## 6. Constraints & Risks
- **Raw Body**: Any modification (e.g., JSON pretty-printing or character normalization) before verification will cause failure.
- **Key Fetching**: Fetching the public key requires an active OAuth token. The webhook handler must be able to refresh this token or use a cached one.
- **Clock Skew**: While not part of the signature, checking the `publishDate` in the payload for staleness is recommended.
