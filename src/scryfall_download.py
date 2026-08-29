"""Download/import Scryfall's official Oracle bulk dataset.

The no-argument function remains compatible with the original pipeline.  Tests
and offline users can pass ``local_file``; network responses are cached and
bounded before the JSONL is applied in one SQLite transaction.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from catalog import DATA_DIR, DB_NAME
from data_collection import (
    CollectionError,
    DEFAULT_USER_AGENT,
    fetch_cached,
    ingest_oracle_lines,
    sha256_bytes,
)

SCRYFALL_BULK_ALL_URL = "https://api.scryfall.com/bulk-data"


def _open_lines(path: str):
    if path.lower().endswith((".gz", ".gzip")):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _load_bulk_descriptor(
    *,
    cache_dir: str | None,
    timeout: float,
    user_agent: str,
    metadata_url: str,
    min_interval: float,
    retries: int,
) -> dict:
    raw, _ = fetch_cached(
        metadata_url,
        cache_dir=cache_dir,
        timeout=timeout,
        user_agent=user_agent,
        min_interval=min_interval,
        robots=False,
        max_bytes=5 * 1024 * 1024,
        retries=retries,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"Scryfall bulk metadata is not valid JSON: {exc}") from exc
    for item in payload.get("data", []):
        download_uri = item.get("download_uri") or item.get("jsonl_download_uri")
        if item.get("type") == "oracle_cards" and download_uri:
            item = dict(item)
            item["download_uri"] = download_uri
            return item
    raise CollectionError("Scryfall bulk metadata has no oracle_cards download URI.")


def download_and_process_scryfall(
    *,
    db_path: str = DB_NAME,
    local_file: str | None = None,
    metadata_url: str | None = None,
    cache_dir: str | None = None,
    timeout: float = 60,
    user_agent: str = DEFAULT_USER_AGENT,
    expected_sha256: str | None = None,
    dry_run: bool = False,
    min_interval: float = 0.1,
    retries: int = 3,
) -> dict:
    """Import Oracle data from a local file or Scryfall's public bulk URL."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    descriptor = {}
    cache_hit = False
    metadata_url = metadata_url or SCRYFALL_BULK_ALL_URL
    if local_file:
        path = Path(local_file)
        if not path.is_file():
            raise CollectionError(f"Scryfall local file not found: {local_file}")
        raw_bytes = path.read_bytes()
        source_path = str(path)
        digest = sha256_bytes(raw_bytes)
        if expected_sha256 and digest != expected_sha256.lower():
            raise CollectionError(f"Checksum mismatch for {local_file}.")
        handle = _open_lines(source_path)
    else:
        descriptor = _load_bulk_descriptor(
            cache_dir=cache_dir,
            timeout=timeout,
            user_agent=user_agent,
            metadata_url=metadata_url,
            min_interval=min_interval,
            retries=retries,
        )
        download_uri = descriptor["download_uri"]
        raw_bytes, cache_hit = fetch_cached(
            download_uri,
            cache_dir=cache_dir,
            timeout=timeout,
            user_agent=user_agent,
            min_interval=min_interval,
            robots=False,
            expected_sha256=expected_sha256,
            retries=retries,
        )
        # The response is cached as bytes so processing is repeatable and does
        # not hold an open network connection while SQLite is being written.
        cache_file = Path(cache_dir or os.path.join(DATA_DIR, "source_cache"))
        cache_file.mkdir(parents=True, exist_ok=True)
        compressed_path = cache_file / f"scryfall-{sha256_bytes(raw_bytes)}.json.gz"
        if not compressed_path.exists():
            compressed_path.write_bytes(raw_bytes)
        handle = _open_lines(str(compressed_path))
        source_path = download_uri
        digest = sha256_bytes(raw_bytes)
    try:
        result = ingest_oracle_lines(
            handle,
            db_path=db_path,
            raw_sha256=digest,
            raw_size=len(raw_bytes),
            dataset_id="scryfall:oracle_cards",
            source_url=None if local_file else source_path,
            local_path=source_path if local_file else None,
            metadata=descriptor,
            dry_run=dry_run,
        )
    except (OSError, gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"Invalid Scryfall JSONL payload: {exc}") from exc
    finally:
        handle.close()
    result.update(
        {
            "source_path": source_path,
            "cache_hit": cache_hit,
            "descriptor": {
                key: descriptor.get(key)
                for key in ("updated_at", "size", "content_type", "download_uri")
                if descriptor.get(key) is not None
            },
        }
    )
    print(f"Scryfall Oracle ready: {result.get('cards', 0)} records ({db_path}).")
    return result


if __name__ == "__main__":
    try:
        download_and_process_scryfall()
    except CollectionError as exc:
        print(f"Collection failed: {exc}")
