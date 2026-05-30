def migrate_batch(listing_ids, max_retries=4):
    payload = {"requests": [{"listingId": x} for x in listing_ids]}

    for attempt in range(1, max_retries + 1):
        token = os.environ.get("EBAY_OAUTH_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Missing EBAY_OAUTH_TOKEN")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            r = requests.post(
                BULK_MIGRATE_URL,
                headers=headers,
                json=payload,
                timeout=(10, 90),
            )

            if r.status_code == 401:
                raise RuntimeError("401 Unauthorized: access token invalid or expired")

            if r.status_code in (429, 500, 502, 503, 504):
                if attempt == max_retries:
                    r.raise_for_status()
                sleep_s = min(30, 2 ** attempt)
                print(f"Retryable HTTP {r.status_code} on {listing_ids}, sleeping {sleep_s}s...")
                time.sleep(sleep_s)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.Timeout:
            if attempt == max_retries:
                raise
            sleep_s = min(30, 2 ** attempt)
            print(f"Timeout on {listing_ids}, retry {attempt}/{max_retries}, sleeping {sleep_s}s...")
            time.sleep(sleep_s)

        except requests.exceptions.ConnectionError:
            if attempt == max_retries:
                raise
            sleep_s = min(30, 2 ** attempt)
            print(f"Connection error on {listing_ids}, retry {attempt}/{max_retries}, sleeping {sleep_s}s...")
            time.sleep(sleep_s)
