import sqlite3
import json
import os
import chromadb
from catalog import ensure_schema
from embeddings import MiniLMStrategy
from geometry import identity_where
from roles import SEARCH_ROLES, classify_roles

# Absolute paths so the script works regardless of cwd
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")


class RAGSearcher:
    def __init__(self):
        print("Loading the embedding model and connecting to the databases...")
        self.emb_fn = MiniLMStrategy().get_function()
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

        self.collection = self.chroma_client.get_collection(
            name="oracle_cards",
            embedding_function=self.emb_fn
        )
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

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

    def search_cards(
        self,
        query,
        allowed_colors,
        owned_only=False,
        limit=5,
        max_card_price=None,
        currency="usd",
        n_results=None,
        cmc_min=None,
        cmc_max=None,
        role=None,
    ):
        print(f"\nSearching for: '{query}'")
        print(
            f"Filters -> Colors: {allowed_colors} | Owned only: {owned_only} | "
            f"P_max: {max_card_price} | cmc: [{cmc_min}, {cmc_max}] | role: {role}"
        )
        role_key = (role or "").strip().lower() or None
        if role_key and role_key not in SEARCH_ROLES:
            return []

        fetch = n_results if n_results is not None else max(limit * 4, 50)
        if cmc_min is not None or cmc_max is not None or role_key:
            fetch = max(fetch, limit * 8)
        fetch = min(max(int(fetch), limit), 400)

        query_kwargs = {"query_texts": [query], "n_results": fetch}
        where = identity_where(allowed_colors)
        if where and self._has_identity_bits():
            query_kwargs["where"] = where

        results = self.collection.query(**query_kwargs)

        found_cards = []

        ids = results["ids"][0]
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        allowed_colors_set = set(allowed_colors)

        conn = sqlite3.connect(DB_NAME)
        ensure_schema(conn)
        cursor = conn.cursor()

        try:
            for i in range(len(ids)):
                name = metadatas[i]["name"]

                color_str = metadatas[i].get("color_identity", "[]")
                card_colors = set(json.loads(color_str))

                if not card_colors.issubset(allowed_colors_set):
                    continue

                cmc = metadatas[i].get("cmc")
                row = cursor.execute(
                    "SELECT price_usd, price_eur, type_line, oracle_text, keywords, cmc "
                    "FROM cards WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()

                if cmc is None and row is not None:
                    cmc = row[5]
                if cmc_min is not None and cmc is not None and float(cmc) < float(cmc_min):
                    continue
                if cmc_max is not None and cmc is not None and float(cmc) > float(cmc_max):
                    continue

                if role_key:
                    type_line = row[2] if row else ""
                    oracle_text = row[3] if row else ""
                    keywords = row[4] if row else []
                    if role_key not in classify_roles(type_line, oracle_text, keywords):
                        continue

                price = None
                if row:
                    price = row[1] if currency == "eur" else row[0]

                if max_card_price is not None and price is not None and price > max_card_price:
                    continue

                total_qty = 0
                allocation = {}

                if owned_only:
                    cursor.execute(
                        "SELECT total_quantity, allocations FROM inventory WHERE card_name = ?",
                        (name,),
                    )
                    row = cursor.fetchone()

                    if not row or row[0] <= 0:
                        continue

                    total_qty = row[0]
                    allocation = json.loads(row[1])

                found_cards.append({
                    "name": name,
                    "text": documents[i],
                    "distance": distances[i],
                    "quantity": total_qty,
                    "allocation": allocation,
                    "price": price,
                    "currency": currency,
                })

                if len(found_cards) == limit:
                    break
        finally:
            conn.close()

        return found_cards

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    searcher = RAGSearcher()

    question = "deal damage to all creatures"
    deck_identity = ["R", "U"]

    results = searcher.search_cards(
        query=question,
        allowed_colors=deck_identity,
        owned_only=True,
        limit=1000
    )

    print("\n--- SEARCH RESULTS ---")
    if not results:
        print("No cards matched all filters.")
    else:
        for i, card in enumerate(results):
            print(f"\n{i+1}. {card['name']} (distance: {card['distance']:.3f})")
            print(f"   Effect: {card['text']}")
            if card["quantity"] > 0:
                print(f"   Owned: {card['quantity']} copies -> {card['allocation']}")
            else:
                print("   Status: not in collection.")

    searcher.close()
