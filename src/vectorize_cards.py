"""Index oracle text into Chroma.

Default is incremental: cards whose document text already matches Chroma are
skipped. Full upsert of 38k into an existing HNSW index is the slow path.

  python src/vectorize_cards.py              # only changed cards
  python src/vectorize_cards.py --metadata-only
  python src/vectorize_cards.py --rebuild    # drop collection and encode all
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time

import chromadb
from embeddings import MiniLMStrategy, _device
from geometry import chroma_metadata

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
COLLECTION = "oracle_cards"
os.makedirs(CHROMA_DIR, exist_ok=True)


def _document(name, type_line, oracle_text) -> str:
    oracle_text = oracle_text if oracle_text else "Vanilla creature / No abilities."
    return f"{name} - {type_line}. Effect: {oracle_text}"


def generate_embeddings(rebuild: bool = False, batch_size: int = 256, encode_batch: int = 64):
    device = _device()
    print(f"Embedding device: {device}")
    print("Initializing the embedding provider...")
    strategy = MiniLMStrategy()
    embedding_provider = strategy.get_function()
    model = embedding_provider._model

    print(f"Connecting to ChromaDB at: {CHROMA_DIR}")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    if rebuild:
        try:
            chroma_client.delete_collection(COLLECTION)
            print("Dropped existing collection (rebuild).")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=embedding_provider,
        metadata={"description": "Vectorized Magic: The Gathering oracle text"},
    )

    print("Reading cards from the local SQLite database...")
    conn = sqlite3.connect(DB_NAME)
    cards = conn.execute(
        "SELECT id, name, type_line, oracle_text, color_identity, cmc FROM cards "
        "WHERE type_line NOT LIKE '%Basic Land%'"
    ).fetchall()
    conn.close()
    print(f"Found {len(cards)} cards.")

    ids = [row[0] for row in cards]
    documents = [_document(row[1], row[2], row[3]) for row in cards]
    metadatas = [chroma_metadata(row[1], row[4], row[5], row[2]) for row in cards]

    skip = set()
    if not rebuild:
        print("Comparing with existing Chroma documents...")
        t0 = time.perf_counter()
        for i in range(0, len(ids), 4000):
            chunk_ids = ids[i : i + 4000]
            got = collection.get(ids=chunk_ids, include=["documents"])
            have = dict(zip(got["ids"], got["documents"]))
            for j, card_id in enumerate(chunk_ids):
                if have.get(card_id) == documents[i + j]:
                    skip.add(card_id)
        print(
            f"Unchanged: {len(skip)} / {len(ids)} "
            f"({time.perf_counter() - t0:.1f}s). Encoding {len(ids) - len(skip)}."
        )

    pending_ids = []
    pending_docs = []
    pending_meta = []
    for card_id, doc, meta in zip(ids, documents, metadatas):
        if card_id in skip:
            continue
        pending_ids.append(card_id)
        pending_docs.append(doc)
        pending_meta.append(meta)

    if not pending_ids:
        print("Nothing to embed.")
        if not rebuild:
            stamp_chroma_metadata(collection, cards, metadatas, ids)
        return

    print(f"Encoding {len(pending_docs)} texts (batch_size={encode_batch})...")
    t0 = time.perf_counter()
    vectors = model.encode(
        pending_docs,
        batch_size=encode_batch,
        convert_to_numpy=True,
        normalize_embeddings=embedding_provider.normalize_embeddings,
        show_progress_bar=True,
    )
    print(f"Encode done in {time.perf_counter() - t0:.1f}s. Writing to Chroma...")

    writer = collection.add if rebuild or collection.count() == 0 else collection.upsert
    t1 = time.perf_counter()
    for i in range(0, len(pending_ids), batch_size):
        sl = slice(i, i + batch_size)
        writer(
            ids=pending_ids[sl],
            documents=pending_docs[sl],
            metadatas=pending_meta[sl],
            embeddings=vectors[sl].tolist(),
        )
        done = min(i + batch_size, len(pending_ids))
        print(f"Wrote {done} / {len(pending_ids)}")
    print(f"Chroma write done in {time.perf_counter() - t1:.1f}s.")
    stamp_chroma_metadata(collection, cards, metadatas, ids)
    print("Vectorization complete.")


def stamp_chroma_metadata(collection, cards, metadatas, ids, batch_size: int = 500):
    """Write cmc / color bits onto existing vectors without re-encoding."""
    print("Stamping Chroma metadata (no re-encode)...")
    t0 = time.perf_counter()
    written = 0
    for i in range(0, len(ids), batch_size):
        chunk_ids = ids[i : i + batch_size]
        chunk_meta = metadatas[i : i + batch_size]
        got = collection.get(ids=chunk_ids)
        have = set(got["ids"])
        keep_ids = []
        keep_meta = []
        for card_id, meta in zip(chunk_ids, chunk_meta):
            if card_id in have:
                keep_ids.append(card_id)
                keep_meta.append(meta)
        if keep_ids:
            collection.update(ids=keep_ids, metadatas=keep_meta)
            written += len(keep_ids)
        print(f"  metadata {min(i + batch_size, len(ids))} / {len(ids)}")
    print(f"Updated metadata on {written} cards ({time.perf_counter() - t0:.1f}s).")


def main():
    parser = argparse.ArgumentParser(description="Embed Scryfall oracle cards into Chroma.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop the collection and encode every card (use after changing the model).",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Update Chroma metadata (identity bits, cmc) without encoding.",
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Chroma upsert batch size.")
    parser.add_argument(
        "--encode-batch",
        type=int,
        default=64,
        help="SentenceTransformer encode batch size (CPU: 32-64, GPU: 128-256).",
    )
    args = parser.parse_args()
    if args.metadata_only:
        stamp_metadata_only()
        return
    generate_embeddings(
        rebuild=args.rebuild,
        batch_size=args.batch_size,
        encode_batch=args.encode_batch,
    )


def stamp_metadata_only():
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = chroma_client.get_collection(name=COLLECTION)
    conn = sqlite3.connect(DB_NAME)
    cards = conn.execute(
        "SELECT id, name, type_line, oracle_text, color_identity, cmc FROM cards "
        "WHERE type_line NOT LIKE '%Basic Land%'"
    ).fetchall()
    conn.close()
    ids = [row[0] for row in cards]
    metadatas = [chroma_metadata(row[1], row[4], row[5], row[2]) for row in cards]
    stamp_chroma_metadata(collection, cards, metadatas, ids)


if __name__ == "__main__":
    main()
