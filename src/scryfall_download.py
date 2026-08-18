import json
import sqlite3
import urllib.request
import urllib.error
import gzip
import os

from catalog import (
    DB_NAME,
    DATA_DIR,
    ensure_schema,
    mana_cost_from_scryfall,
    oracle_text_from_scryfall,
    parse_price,
    stamp_price_snapshot,
)

os.makedirs(DATA_DIR, exist_ok=True)

SCRYFALL_BULK_ALL_URL = "https://api.scryfall.com/bulk-data"


def download_and_process_scryfall():
    print("Querying the Scryfall API for the bulk catalog...")

    headers = {
        "User-Agent": "ManaGraph-Agent/1.0",
        "Accept": "application/json",
    }

    req_info = urllib.request.Request(SCRYFALL_BULK_ALL_URL, headers=headers)

    try:
        with urllib.request.urlopen(req_info) as response:
            bulk_list = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Error accessing the API: {e.code} - {e.reason}")
        return

    download_uri = None
    for item in bulk_list.get("data", []):
        if item.get("type") == "oracle_cards":
            download_uri = item.get("jsonl_download_uri")
            break

    if not download_uri:
        print("Error: could not find a JSONL download URI.")
        return

    print("Download link found. Fetching compressed JSONL...")
    print(f"URL: {download_uri}")
    print("(About ~24MB download; cards are processed as a stream)")

    req_data = urllib.request.Request(download_uri, headers=headers)

    try:
        with urllib.request.urlopen(req_data) as response:
            with gzip.open(response, "rt", encoding="utf-8") as f:
                print("Connecting to the local database...")
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                ensure_schema(conn)

                batch_data = []
                count = 0

                for line in f:
                    if not line.strip():
                        continue

                    card = json.loads(line)
                    prices = card.get("prices") or {}
                    batch_data.append(
                        (
                            card.get("id"),
                            card.get("name"),
                            mana_cost_from_scryfall(card),
                            card.get("cmc", 0.0),
                            oracle_text_from_scryfall(card),
                            json.dumps(card.get("color_identity", [])),
                            card.get("type_line", ""),
                            json.dumps(card.get("legalities", {})),
                            parse_price(prices.get("usd")),
                            parse_price(prices.get("eur")),
                        )
                    )

                    count += 1

                    if len(batch_data) >= 2000:
                        cursor.executemany(
                            """
                            INSERT OR REPLACE INTO cards
                            (id, name, mana_cost, cmc, oracle_text, color_identity,
                             type_line, legalities, price_usd, price_eur)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            batch_data,
                        )
                        batch_data = []
                        print(f"{count} cards processed...")

                if batch_data:
                    cursor.executemany(
                        """
                        INSERT OR REPLACE INTO cards
                        (id, name, mana_cost, cmc, oracle_text, color_identity,
                         type_line, legalities, price_usd, price_eur)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        batch_data,
                    )

                stamp_price_snapshot(conn, count)
                conn.commit()
                conn.close()
                print(f"\nSuccess! {count} cards inserted/updated.")
                print(f"Database '{DB_NAME}' populated successfully.")
                print("Price snapshot stored in catalog_meta (re-run this script to refresh).")

    except urllib.error.HTTPError as e:
        print(f"Error downloading the file: {e.code} - {e.reason}")
    except Exception as e:
        print(f"Unexpected error while processing: {e}")


if __name__ == "__main__":
    download_and_process_scryfall()
