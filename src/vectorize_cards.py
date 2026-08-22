"""Index oracle text into Chroma.

Documents are type_line + oracle only (see retrieval_text.DOCUMENT_FORMAT).
The card name is metadata, not embedded — name collisions were drowning
functional queries like \"draw a card\".

Default is incremental: cards whose document text already matches Chroma are
skipped. Full upsert of 38k into an existing HNSW index is the slow path.

Prefer the full pipeline: python src/build_dataset.py

  python src/vectorize_cards.py              # only changed cards
  python src/vectorize_cards.py --metadata-only
  python src/vectorize_cards.py --views-only   # oracle+type+keywords+mana, for scoring
  python src/vectorize_cards.py --rebuild
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time

import chromadb
from embeddings import (
    MiniLMStrategy,
    default_encode_batch,
    describe_embedding_device,
    encode_texts,
    place_model_on_device,
)
from geometry import VIEWS_PATH, chroma_metadata, save_card_views, view_texts
from retrieval_text import DOCUMENT_FORMAT, card_document, is_searchable_card

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
COLLECTION = "oracle_cards"
os.makedirs(CHROMA_DIR, exist_ok=True)


def _document(type_line, oracle_text) -> str:
    return card_document(type_line, oracle_text)


def _collection_format(collection) -> str | None:
    meta = getattr(collection, "metadata", None) or {}
    if isinstance(meta, dict):
        return meta.get("document_format")
    return None


def generate_embeddings(
    rebuild: bool = False, batch_size: int = 256, encode_batch: int | None = None
):
    if encode_batch is None:
        encode_batch = default_encode_batch()
    print(f"Embedding device: {describe_embedding_device()}")
    print(f"Encode batch size: {encode_batch}")
    print(f"Document format: {DOCUMENT_FORMAT} (type + oracle; name is metadata only)")
    print("Initializing the embedding provider...")
    strategy = MiniLMStrategy()
    embedding_provider = strategy.get_function()
    model = embedding_provider._model
    place_model_on_device(model)

    print(f"Connecting to ChromaDB at: {CHROMA_DIR}")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    if not rebuild:
        try:
            existing = chroma_client.get_collection(name=COLLECTION)
            fmt = _collection_format(existing)
            if fmt != DOCUMENT_FORMAT:
                print(
                    f"Chroma document_format={fmt!r} != {DOCUMENT_FORMAT!r}; "
                    "forcing rebuild so embeddings match the new text."
                )
                rebuild = True
        except Exception:
            pass

    if rebuild:
        try:
            chroma_client.delete_collection(COLLECTION)
            print("Dropped existing collection (rebuild).")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=embedding_provider,
        metadata={
            "description": "MTG type_line + oracle_text (no card name)",
            "document_format": DOCUMENT_FORMAT,
        },
    )

    print("Reading cards from the local SQLite database...")
    conn = sqlite3.connect(DB_NAME)
    cards = conn.execute(
        "SELECT id, name, type_line, oracle_text, color_identity, cmc, legalities FROM cards "
        "WHERE type_line NOT LIKE '%Basic Land%'"
    ).fetchall()
    conn.close()

    kept = []
    skipped_junk = 0
    for row in cards:
        _card_id, name, type_line, _oracle_text, _color_identity, _cmc, legalities = row
        if not is_searchable_card(name, type_line, legalities):
            skipped_junk += 1
            continue
        kept.append(row)
    print(f"Found {len(cards)} cards; indexing {len(kept)} (skipped {skipped_junk} non-searchable).")

    ids = [row[0] for row in kept]
    documents = [_document(row[2], row[3]) for row in kept]
    metadatas = [chroma_metadata(row[1], row[4], row[5], row[2]) for row in kept]

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
            stamp_chroma_metadata(collection, kept, metadatas, ids)
        return

    print(f"Encoding {len(pending_docs)} texts on {describe_embedding_device()} (batch_size={encode_batch})...")
    t0 = time.perf_counter()
    vectors = encode_texts(
        model,
        pending_docs,
        batch_size=encode_batch,
        normalize=embedding_provider.normalize_embeddings,
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
    stamp_chroma_metadata(collection, kept, metadatas, ids)
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
        help="Drop the collection and encode every card (use after changing the model or document format).",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Update Chroma metadata (identity bits, cmc) without encoding.",
    )
    parser.add_argument(
        "--views-only",
        action="store_true",
        help="Encode oracle, type, keywords, and mana-cost views once into data/card_views.npz (no Chroma write).",
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Chroma upsert batch size.")
    parser.add_argument(
        "--encode-batch",
        type=int,
        default=None,
        help="SentenceTransformer encode batch size (default: 256 on CUDA, 64 on CPU).",
    )
    args = parser.parse_args()
    if args.metadata_only:
        stamp_metadata_only()
        return
    if args.views_only:
        generate_card_views(encode_batch=args.encode_batch)
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
        "SELECT id, name, type_line, oracle_text, color_identity, cmc, legalities FROM cards "
        "WHERE type_line NOT LIKE '%Basic Land%'"
    ).fetchall()
    conn.close()
    kept = [
        row for row in cards
        if is_searchable_card(row[1], row[2], row[6])
    ]
    ids = [row[0] for row in kept]
    metadatas = [chroma_metadata(row[1], row[4], row[5], row[2]) for row in kept]
    stamp_chroma_metadata(collection, kept, metadatas, ids)


def generate_card_views(encode_batch: int | None = None):
    """One-shot MiniLM encode of oracle, type, keywords, mana cost; fill only looks up ids."""
    if encode_batch is None:
        encode_batch = default_encode_batch()
    print(f"Embedding device: {describe_embedding_device()}")
    print(f"Encode batch size: {encode_batch}")
    print("Encoding oracle + type + keywords + mana views (once)...")
    model = MiniLMStrategy().get_function()._model
    place_model_on_device(model)
    conn = sqlite3.connect(DB_NAME)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    kw_sql = "keywords" if "keywords" in cols else "NULL"
    cards = conn.execute(
        f"SELECT id, name, type_line, oracle_text, mana_cost, {kw_sql}, legalities FROM cards "
        "WHERE type_line NOT LIKE '%Basic Land%'"
    ).fetchall()
    conn.close()
    kept = [row for row in cards if is_searchable_card(row[1], row[2], row[6])]
    ids = [row[0] for row in kept]
    texts = {key: [] for key in ("oracle", "type", "keywords", "mana")}
    for row in kept:
        parts = view_texts(
            {
                "type_line": row[2],
                "oracle_text": row[3],
                "mana_cost": row[4] or "",
                "keywords": row[5],
            }
        )
        for key in texts:
            texts[key].append(parts[key])
    t0 = time.perf_counter()
    n = len(ids)
    blob = texts["oracle"] + texts["type"] + texts["keywords"] + texts["mana"]
    encoded = encode_texts(model, blob, batch_size=encode_batch, normalize=False)
    save_card_views(
        ids,
        encoded[0:n],
        encoded[n : 2 * n],
        keywords=encoded[2 * n : 3 * n],
        mana=encoded[3 * n : 4 * n],
    )
    print(f"Wrote {n} x 4 views to {VIEWS_PATH} ({time.perf_counter() - t0:.1f}s).")


if __name__ == "__main__":
    main()
