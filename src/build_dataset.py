"""Build the local ManaGraph dataset in one run.

Steps (same as the README catalog pipeline, including Stage 3.5 views):
  1. Download Scryfall oracle_cards bulk → SQLite (text, prices, keywords)
  2. Embed oracle documents into Chroma (`oracle_cards`)
  3. Stamp Chroma metadata (color-identity bits, cmc) without re-encoding
  4. Encode multi-view vectors (oracle, type, keywords, mana) → data/card_views.npz
  5. If inventory is empty, load the test pool/deck lists

Usage (from the repo root):

    python src/build_dataset.py
    python src/build_dataset.py --rebuild          # drop Chroma and re-encode every card
    python src/build_dataset.py --skip-download    # reuse existing SQLite
    python src/build_dataset.py --skip-inventory
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

from catalog import DATA_DIR, DB_NAME
from embeddings import default_encode_batch, describe_embedding_device, embedding_device
from import_inventory import import_list
from inventory import FREE_POOL
from scryfall_download import download_and_process_scryfall
from vectorize_cards import generate_card_views, generate_embeddings, stamp_metadata_only

BASE_DIR = os.path.dirname(DATA_DIR)


def _step(index: int, total: int, title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"[{index}/{total}] {title}")
    print("=" * 60)


def _card_count() -> int:
    try:
        conn = sqlite3.connect(DB_NAME)
        try:
            row = conn.execute("SELECT COUNT(*) FROM cards").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0


def _inventory_count() -> int:
    try:
        conn = sqlite3.connect(DB_NAME)
        try:
            row = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0


def _import_test_inventory() -> None:
    pool_path = os.path.join(DATA_DIR, "test_pool.txt")
    deck_path = os.path.join(DATA_DIR, "test_deck.txt")
    if not os.path.isfile(pool_path) and not os.path.isfile(deck_path):
        print("No test_pool.txt / test_deck.txt found; skipping inventory.")
        return
    if os.path.isfile(pool_path):
        import_list(pool_path, FREE_POOL)
    if os.path.isfile(deck_path):
        import_list(deck_path, "deck_krenko")


def build_dataset(
    skip_download: bool = False,
    rebuild: bool = False,
    skip_chroma: bool = False,
    skip_views: bool = False,
    skip_inventory: bool = False,
    force_inventory: bool = False,
    batch_size: int = 256,
    encode_batch: int | None = None,
) -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    started = time.perf_counter()
    total = 5
    if encode_batch is None:
        encode_batch = default_encode_batch()
    print("ManaGraph dataset build")
    print(f"Data directory: {DATA_DIR}")
    print(f"Compute device: {describe_embedding_device()}")
    print(f"Encode batch size: {encode_batch}")
    if embedding_device() != "cuda":
        print(
            "WARNING: CUDA is not available. MiniLM will run on CPU.\n"
            "Install a CUDA build of PyTorch if this machine has an NVIDIA GPU:\n"
            "  pip install torch --index-url https://download.pytorch.org/whl/cu128"
        )

    _step(1, total, "Download Scryfall oracle catalog → SQLite")
    if skip_download:
        print("Skipped (--skip-download).")
    else:
        download_and_process_scryfall()
    n_cards = _card_count()
    if n_cards == 0:
        print("Catalog is empty. Download failed or --skip-download was used too early.")
        return 1
    print(f"Catalog ready: {n_cards} cards in {DB_NAME}")

    _step(2, total, "Embed oracle text into Chroma")
    if skip_chroma:
        print("Skipped (--skip-chroma).")
    else:
        generate_embeddings(
            rebuild=rebuild,
            batch_size=batch_size,
            encode_batch=encode_batch,
        )

    _step(3, total, "Stamp Chroma metadata (color identity, cmc)")
    if skip_chroma:
        print("Skipped (no Chroma write in this run).")
    else:
        stamp_metadata_only()

    _step(4, total, "Encode multi-view vectors (oracle, type, keywords, mana)")
    if skip_views:
        print("Skipped (--skip-views).")
    else:
        generate_card_views(encode_batch=encode_batch)

    _step(5, total, "Load test inventory if the collection is empty")
    if skip_inventory:
        print("Skipped (--skip-inventory).")
    elif _inventory_count() > 0 and not force_inventory:
        print(f"Inventory already has {_inventory_count()} rows; not re-importing.")
        print("Pass --force-inventory to load test_pool.txt / test_deck.txt again (additive).")
    else:
        _import_test_inventory()

    elapsed = time.perf_counter() - started
    print(f"\nDataset ready in {elapsed:.1f}s.")
    print(f"  SQLite:     {DB_NAME} ({_card_count()} cards)")
    print(f"  Chroma:     {os.path.join(DATA_DIR, 'chroma_db')}")
    print(f"  Views:      {os.path.join(DATA_DIR, 'card_views.npz')}")
    print(f"  Inventory:  {_inventory_count()} unique names")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download, vectorize, stamp metadata, and build card views for ManaGraph."
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Reuse the existing SQLite catalog (no Scryfall fetch).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop the Chroma collection and encode every card.",
    )
    parser.add_argument(
        "--skip-chroma",
        action="store_true",
        help="Do not write the Chroma index (views-only / catalog-only refresh).",
    )
    parser.add_argument(
        "--skip-views",
        action="store_true",
        help="Do not rebuild data/card_views.npz.",
    )
    parser.add_argument(
        "--skip-inventory",
        action="store_true",
        help="Do not import test_pool.txt / test_deck.txt.",
    )
    parser.add_argument(
        "--force-inventory",
        action="store_true",
        help="Import test lists even if inventory already has rows (counts add up).",
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Chroma upsert batch size.")
    parser.add_argument(
        "--encode-batch",
        type=int,
        default=None,
        help="SentenceTransformer encode batch size (default: 256 on CUDA, 64 on CPU).",
    )
    args = parser.parse_args()
    return build_dataset(
        skip_download=args.skip_download,
        rebuild=args.rebuild,
        skip_chroma=args.skip_chroma,
        skip_views=args.skip_views,
        skip_inventory=args.skip_inventory,
        force_inventory=args.force_inventory,
        batch_size=args.batch_size,
        encode_batch=args.encode_batch,
    )


if __name__ == "__main__":
    sys.exit(main())
