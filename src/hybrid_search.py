"""Hybrid card search: ontology predicates first, Oracle harness second.

Natural language is compiled into ontology clauses; the Forge predicate index
is the mechanic search. Embedding + lexical Oracle search is a harness for
phrasing the index missed. Results are merged and filtered by identity /
price / role / legality.
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
from ontology.search import (
    OntologyClause,
    compile_search_intent,
    search_ontology_clauses,
    search_route,
)
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
        bootstrap = sqlite3.connect(DB_NAME)
        try:
            ensure_schema(bootstrap)
        finally:
            bootstrap.close()
        self._embed_cache: dict[tuple, list[dict]] = {}

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

    def _parse_chroma_hits(
        self,
        results: dict,
        index: int,
        allowed_colors: list,
    ) -> list[dict]:
        ids = (results.get("ids") or [[]])[index]
        documents = (results.get("documents") or [[]])[index]
        metadatas = (results.get("metadatas") or [[]])[index]
        distances = (results.get("distances") or [[]])[index]
        allowed = set(allowed_colors or [])
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

    def _embedding_hits_batch(
        self,
        queries: list[str],
        allowed_colors: list,
        fetch: int,
    ) -> list[dict]:
        unique: list[str] = []
        seen: set[str] = set()
        for query in queries:
            text = (query or "").strip()
            if not text:
                continue
            key = " ".join(text.lower().split())
            if key in seen:
                continue
            seen.add(key)
            unique.append(text)
        if not unique:
            return []

        allowed_t = tuple(allowed_colors or [])
        fetch_i = int(fetch)
        groups: list[list[dict]] = []
        uncached: list[tuple[str, tuple]] = []
        for query in unique:
            cache_key = (" ".join(query.lower().split()), allowed_t, fetch_i)
            cached = self._embed_cache.get(cache_key)
            if cached is not None:
                groups.append(cached)
            else:
                uncached.append((query, cache_key))

        if uncached:
            texts = [query for query, _key in uncached]
            query_kwargs = {"query_texts": texts, "n_results": fetch_i}
            where = identity_where(allowed_colors)
            if where and self._has_identity_bits():
                query_kwargs["where"] = where
            with _query_lock:
                results = self.collection.query(**query_kwargs)
            for i, (_query, cache_key) in enumerate(uncached):
                hits = self._parse_chroma_hits(results, i, allowed_colors)
                self._embed_cache[cache_key] = hits
                groups.append(hits)

        return self._union_hits(*groups)

    def _embedding_hits(
        self,
        query: str,
        allowed_colors: list,
        fetch: int,
    ) -> list[dict]:
        return self._embedding_hits_batch([query], allowed_colors, fetch)

    def _union_hits(self, *groups: list[dict]) -> list[dict]:
        """Keep the closest hit per card name. Ontology-only sources survive."""
        by_key: dict[str, dict] = {}
        for hits in groups:
            for hit in hits:
                name = (hit.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                prev = by_key.get(key)
                if prev is None or float(hit["distance"]) < float(prev["distance"]):
                    by_key[key] = hit
        return list(by_key.values())

    def _unique_texts(self, texts: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for text in texts:
            compact = " ".join((text or "").split())
            if not compact:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(compact)
        return unique

    def _materialize_hits(
        self,
        cursor: sqlite3.Cursor,
        merged: list[dict],
        *,
        card_pool,
        limit: int,
        max_card_price,
        currency: str,
        cmc_min,
        cmc_max,
        role_key: str | None,
        owned_only: bool,
    ) -> list[dict]:
        found_cards = []
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
        return found_cards

    def search_cards_batch(
        self,
        queries: list[str],
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
        lexical_only_queries: list[str] | None = None,
    ):
        """Compile unique phrases, one ontology SQL, one Chroma batch, one lexical pass."""
        phrases = self._unique_texts([str(q) for q in (queries or []) if q is not None])
        lex_only = self._unique_texts(list(lexical_only_queries or []))
        role_key = (role or "").strip().lower() or None
        if role_key and role_key not in SEARCH_ROLES:
            return []

        compile_texts = self._unique_texts([*phrases, *lex_only])
        intents = [compile_search_intent(text) for text in compile_texts]
        clauses: list[OntologyClause] = []
        seen_clause: set[tuple[str, str | None, str | None]] = set()
        for intent in intents:
            for clause in intent.clauses:
                key = (clause.predicate, clause.arg_key, clause.arg_value)
                if key in seen_clause:
                    continue
                seen_clause.add(key)
                clauses.append(clause)

        primary = phrases[0] if phrases else (lex_only[0] if lex_only else "")
        if len(phrases) == 1 and not lex_only:
            print(f"\nSearching for: '{primary}'")
        else:
            print(
                f"\nSearching batch: {len(phrases)} phrases"
                + (f" + {len(lex_only)} lexical-only" if lex_only else "")
            )
        print(
            f"Filters -> Colors: {allowed_colors} | Owned only: {owned_only} | "
            f"P_max: {max_card_price} | cmc: [{cmc_min}, {cmc_max}] | role: {role} | "
            f"hybrid: {hybrid}"
        )
        clause_fmt = ", ".join(
            f"{c.predicate}:{c.arg_key + ':' if c.arg_key else ''}{c.arg_value or '*'}"
            for c in clauses
        ) or "(none)"
        print(f"Compiled clauses: [{clause_fmt}]")
        if len(intents) == 1:
            print(f"Oracle harness: {intents[0].oracle_phrases}")

        fetch = n_results if n_results is not None else max(limit * 4, 50)
        if cmc_min is not None or cmc_max is not None or role_key:
            fetch = max(fetch, limit * 8)
        fetch = min(max(int(fetch), limit), 400)

        routes = {text: search_route(text) for text in phrases}
        all_ontology = bool(phrases) and all(
            routes.get(text) == "ontology" for text in phrases
        )
        need_embed = False
        need_lex = False
        need_ontology = False
        if not hybrid:
            need_embed = bool(phrases)
        elif all_ontology and not lex_only:
            need_ontology = bool(clauses)
        else:
            need_ontology = bool(clauses)
            need_embed = any(routes.get(text) != "ontology" for text in phrases)
            need_lex = True

        embed_queries: list[str] = []
        lex_extras: list[str] = []
        if need_embed or need_lex:
            for text in phrases:
                intent = compile_search_intent(text)
                route = routes.get(text, "hybrid")
                if need_embed and route != "ontology":
                    embed_queries.append(text)
                    if intent.clauses:
                        q_compact = " ".join(text.lower().split())
                        for phrase in intent.oracle_phrases:
                            if " ".join(phrase.lower().split()) != q_compact:
                                embed_queries.append(phrase)
                if need_lex and text != primary:
                    lex_extras.append(text)
                if need_lex and intent.clauses:
                    q_compact = " ".join(text.lower().split())
                    for phrase in intent.oracle_phrases:
                        if " ".join(phrase.lower().split()) != q_compact:
                            lex_extras.append(phrase)
            if need_lex:
                lex_extras.extend(lex_only)

        embedding_hits: list[dict] = []
        if need_embed:
            embedding_hits = self._embedding_hits_batch(
                embed_queries, allowed_colors, fetch
            )

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            lexical_hits: list[dict] = []
            ontology_hits: list[dict] = []
            if need_lex:
                n_lex = max(1, 1 + len(self._unique_texts(lex_extras)))
                lex_limit = max(limit * 15, 500) * n_lex
                lexical_hits = lexical_search_sqlite(
                    conn,
                    primary,
                    allowed_colors,
                    limit=lex_limit,
                    cmc_min=cmc_min,
                    cmc_max=cmc_max,
                    extra_phrases=self._unique_texts(lex_extras) or None,
                )
            if need_ontology:
                try:
                    ontology_hits = search_ontology_clauses(
                        conn,
                        clauses,
                        allowed_colors,
                        k=max(limit * 15, 200),
                    )
                except sqlite3.OperationalError:
                    ontology_hits = []
            if hybrid:
                print(
                    f"Hybrid: {len(embedding_hits)} embedding + {len(lexical_hits)} lexical "
                    f"+ {len(ontology_hits)} ontology (pre-merge)"
                )

            if hybrid:
                merged = merge_hit_maps(embedding_hits, lexical_hits)
                merged = merge_hit_maps(merged, ontology_hits)
            else:
                merged = sorted(embedding_hits, key=lambda h: h["distance"])
            q_compact = " ".join(str(primary).lower().split())
            if hybrid and len(q_compact) <= 80:
                merged.sort(key=hit_sort_key)
                if "counter" in q_compact:
                    merged = diversify_by_phrase(
                        merged, max(limit * 4, 80), prefer_phrase=q_compact
                    )

            return self._materialize_hits(
                cursor,
                merged,
                card_pool=card_pool,
                limit=limit,
                max_card_price=max_card_price,
                currency=currency,
                cmc_min=cmc_min,
                cmc_max=cmc_max,
                role_key=role_key,
                owned_only=owned_only,
            )
        finally:
            conn.close()

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
        return self.search_cards_batch(
            queries=[query],
            allowed_colors=allowed_colors,
            owned_only=owned_only,
            card_pool=card_pool,
            limit=limit,
            max_card_price=max_card_price,
            currency=currency,
            n_results=n_results,
            cmc_min=cmc_min,
            cmc_max=cmc_max,
            role=role,
            hybrid=hybrid,
        )

    def close(self):
        return


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
