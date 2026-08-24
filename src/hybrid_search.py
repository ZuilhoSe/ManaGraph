"""Hybrid card search: Chroma embeddings + SQLite lexical oracle match.

Embedding documents are type + oracle only (no card name). Lexical search
recovers literal staples (Phyrexian Arena for \"draw a card\") that ANN alone
buries. Results are merged and filtered by identity / price / role / legality.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

import chromadb
from catalog import ensure_schema
from deck_state import _normalize_key
from embeddings import MiniLMStrategy, describe_embedding_device, place_model_on_device
from geometry import identity_where
from retrieval_text import (
    DOCUMENT_FORMAT,
    diversify_by_phrase,
    hit_sort_key,
    is_searchable_card,
    lexical_search_sqlite,
    merge_hit_maps,
)
from roles import SEARCH_ROLES, classify_roles

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")

_client_lock = threading.Lock()
_searcher_lock = threading.Lock()
_query_lock = threading.Lock()
_chroma_client = None
_shared_searcher = None


def get_chroma_client():
    """One PersistentClient per process. Opening chroma_db from two threads crashes."""
    global _chroma_client
    with _client_lock:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        return _chroma_client


class RAGSearcher:
    def __init__(self, chroma_client=None):
        print(f"Loading the embedding model on {describe_embedding_device()}...")
        self.emb_fn = MiniLMStrategy().get_function()
        place_model_on_device(self.emb_fn._model)
        self.chroma_client = chroma_client or get_chroma_client()
        self.collection = self.chroma_client.get_collection(
            name="oracle_cards",
            embedding_function=self.emb_fn,
        )
        fmt = (getattr(self.collection, "metadata", None) or {}).get("document_format")
        if fmt and fmt != DOCUMENT_FORMAT:
            print(
                f"Warning: Chroma document_format={fmt!r}, code expects {DOCUMENT_FORMAT!r}. "
                "Run: python src/vectorize_cards.py --rebuild"
            )
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.cursor = self.conn.cursor()

    @classmethod
    def shared(cls):
        global _shared_searcher
        with _searcher_lock:
            if _shared_searcher is None:
                _shared_searcher = cls(chroma_client=get_chroma_client())
            return _shared_searcher

    def _has_identity_bits(self) -> bool:
        if getattr(self, "_identity_bits", None) is not None:
            return self._identity_bits
        try:
            peek = self.collection.peek(1)
        except TypeError:
            peek = self.collection.peek()
        metas = peek.get("metadatas") or []
        self._identity_bits = bool(metas and isinstance(metas[0], dict) and "ci_r" in metas[0])
        return self._identity_bits

    def _embedding_hits(
        self,
        query: str,
        allowed_colors: list,
        fetch: int,
    ) -> list[dict]:
        query_kwargs = {"query_texts": [query], "n_results": fetch}
        where = identity_where(allowed_colors)
        if where and self._has_identity_bits():
            query_kwargs["where"] = where

        with _query_lock:
            results = self.collection.query(**query_kwargs)

        ids = results["ids"][0]
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]
        allowed = set(allowed_colors)
        hits = []
        for i in range(len(ids)):
            name = metadatas[i]["name"]
            color_str = metadatas[i].get("color_identity", "[]")
            try:
                card_colors = set(json.loads(color_str))
            except json.JSONDecodeError:
                continue
            if not card_colors.issubset(allowed):
                continue
            hits.append(
                {
                    "name": name,
                    "text": documents[i],
                    "distance": float(distances[i]),
                    "cmc": metadatas[i].get("cmc"),
                    "source": "embedding",
                }
            )
        return hits

    def search_cards(
        self,
        query,
        allowed_colors,
        owned_only=False,
        card_pool=None,
        limit=5,
        max_card_price=None,
        currency="usd",
        n_results=None,
        cmc_min=None,
        cmc_max=None,
        role=None,
        hybrid=True,
    ):
        print(f"\nSearching for: '{query}'")
        print(
            f"Filters -> Colors: {allowed_colors} | Owned only: {owned_only} | "
            f"P_max: {max_card_price} | cmc: [{cmc_min}, {cmc_max}] | role: {role} | "
            f"hybrid: {hybrid}"
        )
        role_key = (role or "").strip().lower() or None
        if role_key and role_key not in SEARCH_ROLES:
            return []

        fetch = n_results if n_results is not None else max(limit * 4, 50)
        if cmc_min is not None or cmc_max is not None or role_key:
            fetch = max(fetch, limit * 8)
        fetch = min(max(int(fetch), limit), 400)

        embedding_hits = self._embedding_hits(query, allowed_colors, fetch)

        lexical_hits: list[dict] = []
        if hybrid:
            ensure_schema(self.conn)
            lexical_hits = lexical_search_sqlite(
                self.conn,
                query,
                allowed_colors,
                limit=max(limit * 15, 500),
                cmc_min=cmc_min,
                cmc_max=cmc_max,
            )
            print(
                f"Hybrid: {len(embedding_hits)} embedding + {len(lexical_hits)} lexical "
                f"(pre-merge)"
            )

        merged = (
            merge_hit_maps(embedding_hits, lexical_hits)
            if hybrid
            else sorted(embedding_hits, key=lambda h: h["distance"])
        )
        # Short oracle-shaped queries: prefer lexical/hybrid, then engine-aware
        # distance (merge_hit_maps already sorts; re-apply after any future edits).
        q_compact = " ".join(str(query).lower().split())
        if hybrid and len(q_compact) <= 80:
            merged.sort(key=hit_sort_key)
            if "counter" in q_compact:
                # Keep Negate / Veto / Muddle visible inside Architect/Solver limits.
                merged = diversify_by_phrase(
                    merged, max(limit * 4, 80), prefer_phrase=q_compact
                )

        found_cards = []
        conn = sqlite3.connect(DB_NAME)
        ensure_schema(conn)
        cursor = conn.cursor()
        try:
            for hit in merged:
                name = hit["name"]
                if card_pool is not None and _normalize_key(name) not in card_pool:
                    continue
                row = cursor.execute(
                    "SELECT price_usd, price_eur, type_line, oracle_text, keywords, cmc, legalities "
                    "FROM cards WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if not row:
                    continue
                price_usd, price_eur, type_line, oracle_text, keywords, cmc_db, legalities = row
                if not is_searchable_card(name, type_line, legalities):
                    continue

                cmc = hit.get("cmc")
                if cmc is None:
                    cmc = cmc_db
                if cmc_min is not None and cmc is not None and float(cmc) < float(cmc_min):
                    continue
                if cmc_max is not None and cmc is not None and float(cmc) > float(cmc_max):
                    continue

                if role_key:
                    if role_key not in classify_roles(type_line, oracle_text, keywords):
                        continue

                price = price_eur if currency == "eur" else price_usd
                if max_card_price is not None and price is not None and price > max_card_price:
                    continue

                total_qty = 0
                allocation = {}
                if owned_only:
                    inv = cursor.execute(
                        "SELECT total_quantity, allocations FROM inventory WHERE card_name = ?",
                        (name,),
                    ).fetchone()
                    if not inv or inv[0] <= 0:
                        continue
                    total_qty = inv[0]
                    allocation = json.loads(inv[1])

                found_cards.append(
                    {
                        "name": name,
                        "text": hit.get("text") or "",
                        "distance": hit["distance"],
                        "quantity": total_qty,
                        "allocation": allocation,
                        "price": price,
                        "currency": currency,
                        "source": hit.get("source") or "embedding",
                        "matched_phrase": hit.get("matched_phrase"),
                    }
                )
                if len(found_cards) == limit:
                    break
        finally:
            conn.close()

        return found_cards

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    searcher = RAGSearcher()

    question = "draw a card"
    deck_identity = ["B"]

    results = searcher.search_cards(
        query=question,
        allowed_colors=deck_identity,
        owned_only=False,
        limit=20,
    )

    print("\n--- SEARCH RESULTS ---")
    if not results:
        print("No cards matched all filters.")
    else:
        for i, card in enumerate(results):
            print(
                f"\n{i+1}. {card['name']} "
                f"(distance: {card['distance']:.3f}, source: {card.get('source')})"
            )
            print(f"   Effect: {card['text'][:120]}")

    searcher.close()
