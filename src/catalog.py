import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from inventory import _split_face_query_name, get_card as get_inventory_card

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
COLLECTION_SCHEMA_VERSION = "5"

os.makedirs(DATA_DIR, exist_ok=True)

# ensure_schema() used to run its CREATE TABLE/ALTER/INSERT+commit on *every*
# call, from a fresh connection almost every time (get_oracle_card, every
# search_cards call, ...). Harmless alone, but under real concurrency -- the
# Architect firing several search_cards tool calls in parallel, or a deck
# save whose caller already holds an open write transaction -- many
# connections ended up writing to the same file at once: "database is
# locked" / "another row available". The schema is only ever migrated once
# per file per process, so verify it once per db file and skip the rest
# (including the unconditional commit) after that.
_schema_ensured: set[str] = set()
_schema_ensured_lock = threading.Lock()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def parse_price(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def oracle_text_from_scryfall(card: dict) -> str:
    """Oracle cards store DFC/split text on card_faces, not top-level oracle_text."""
    text = (card.get("oracle_text") or "").strip()
    if text:
        return text
    faces = card.get("card_faces") or []
    parts = [(face.get("oracle_text") or "").strip() for face in faces]
    return "\n\n".join(part for part in parts if part)


def keywords_from_scryfall(card: dict) -> str:
    return json.dumps(card.get("keywords") or [])


def mana_cost_from_scryfall(card: dict) -> str:
    cost = card.get("mana_cost")
    if cost:
        return cost
    faces = card.get("card_faces") or []
    if not faces:
        return ""
    return faces[0].get("mana_cost") or ""


def ensure_schema(conn: sqlite3.Connection):
    db_file = conn.execute("PRAGMA database_list").fetchone()[2] or ""
    with _schema_ensured_lock:
        if db_file in _schema_ensured:
            return
        _migrate_schema(conn)
        _schema_ensured.add(db_file)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """CREATE/ALTER/INSERT+commit. Only runs once per db file per process,
    with `_schema_ensured_lock` held (see ensure_schema).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mana_cost TEXT,
            cmc REAL,
            oracle_text TEXT,
            color_identity TEXT,
            type_line TEXT,
            legalities TEXT,
            price_usd REAL,
            price_eur REAL,
            scryfall_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    columns = _table_columns(conn, "cards")
    if "price_usd" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN price_usd REAL")
    if "price_eur" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN price_eur REAL")
    if "keywords" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN keywords TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    # The original cards/catalog_meta tables are intentionally left intact.
    # Collection data lives in separate normalized tables so old catalog,
    # validator, inventory, and solver callers remain source-compatible.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            adapter TEXT NOT NULL,
            base_url TEXT,
            terms_url TEXT,
            robots_url TEXT,
            license TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            dataset_type TEXT NOT NULL,
            version TEXT,
            url TEXT,
            local_path TEXT,
            sha256 TEXT,
            byte_size INTEGER,
            fetched_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (source_id) REFERENCES sources(source_id)
        );

        CREATE TABLE IF NOT EXISTS external_decks (
            deck_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            name TEXT,
            commander_name_raw TEXT,
            commander_card_id TEXT,
            format TEXT,
            url TEXT,
            dataset_id TEXT,
            raw_hash TEXT,
            license TEXT,
            collected_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (source_id, external_id),
            FOREIGN KEY (source_id) REFERENCES sources(source_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );

        CREATE TABLE IF NOT EXISTS deck_cards (
            deck_id TEXT NOT NULL,
            card_name_raw TEXT NOT NULL,
            card_id TEXT,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            section TEXT NOT NULL DEFAULT 'mainboard',
            raw_json TEXT NOT NULL DEFAULT '{}',
            provenance_id INTEGER,
            PRIMARY KEY (deck_id, card_name_raw, section),
            FOREIGN KEY (deck_id) REFERENCES external_decks(deck_id)
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            source_id TEXT NOT NULL,
            dataset_id TEXT,
            commander_card_id TEXT,
            card_id TEXT,
            card_name_raw TEXT NOT NULL,
            score REAL,
            rank INTEGER,
            recommendation_type TEXT NOT NULL DEFAULT 'recommendation',
            category TEXT,
            inclusion_count INTEGER,
            potential_decks INTEGER,
            inclusion_percent REAL,
            synergy REAL,
            salt_score REAL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            collected_at TEXT NOT NULL,
            PRIMARY KEY (
                source_id, dataset_id, commander_card_id,
                card_name_raw, recommendation_type
            ),
            FOREIGN KEY (source_id) REFERENCES sources(source_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );

        CREATE TABLE IF NOT EXISTS cooccurrence (
            source_id TEXT NOT NULL,
            dataset_id TEXT,
            card_id_a TEXT,
            card_id_b TEXT,
            card_name_a_raw TEXT NOT NULL,
            card_name_b_raw TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count >= 0),
            score REAL,
            relation_type TEXT NOT NULL DEFAULT 'cooccurrence',
            category TEXT,
            inclusion_percent REAL,
            synergy REAL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            collected_at TEXT NOT NULL,
            PRIMARY KEY (
                source_id, dataset_id, card_name_a_raw, card_name_b_raw
            ),
            FOREIGN KEY (source_id) REFERENCES sources(source_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );

        CREATE TABLE IF NOT EXISTS provenance (
            provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            source_id TEXT NOT NULL,
            dataset_id TEXT,
            raw_name TEXT,
            raw_hash TEXT NOT NULL,
            license TEXT,
            collected_at TEXT NOT NULL,
            raw_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (entity_type, entity_key, source_id, dataset_id, raw_hash),
            FOREIGN KEY (source_id) REFERENCES sources(source_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );

        CREATE TABLE IF NOT EXISTS ontology_cards (
            card_id TEXT PRIMARY KEY,
            scryfall_name TEXT NOT NULL,
            forge_record_key TEXT,
            forge_name TEXT,
            forge_match_status TEXT NOT NULL DEFAULT 'unmatched',
            forge_json TEXT,
            canonical_facts_json TEXT NOT NULL DEFAULT '{}',
            resolved_facts_json TEXT NOT NULL DEFAULT '{}',
            model_facts_json TEXT NOT NULL DEFAULT '{}',
            forge_facts_json TEXT NOT NULL DEFAULT '{}',
            forge_candidates_json TEXT NOT NULL DEFAULT '[]',
            forge_warnings_json TEXT NOT NULL DEFAULT '[]',
            enriched_at TEXT NOT NULL,
            FOREIGN KEY (card_id) REFERENCES cards(id)
        );

        CREATE TABLE IF NOT EXISTS forge_records (
            forge_record_key TEXT PRIMARY KEY,
            forge_name TEXT NOT NULL,
            matched_card_id TEXT,
            match_status TEXT NOT NULL,
            record_json TEXT NOT NULL,
            facts_json TEXT NOT NULL DEFAULT '{}',
            candidates_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            enriched_at TEXT NOT NULL,
            FOREIGN KEY (matched_card_id) REFERENCES cards(id)
        );

        CREATE TABLE IF NOT EXISTS ontology_reviews (
            card_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'unreviewed',
            selected_source TEXT NOT NULL DEFAULT 'resolved',
            field_checks_json TEXT NOT NULL DEFAULT '{}',
            labels_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            FOREIGN KEY (card_id) REFERENCES cards(id)
        );

        CREATE TABLE IF NOT EXISTS ontology_predicates (
            card_id TEXT,
            card_name TEXT,
            predicate TEXT,
            arg_key TEXT,
            arg_value TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_datasets_source
            ON datasets(source_id, fetched_at);
        CREATE INDEX IF NOT EXISTS idx_external_decks_source
            ON external_decks(source_id, external_id);
        CREATE INDEX IF NOT EXISTS idx_deck_cards_card
            ON deck_cards(card_id, card_name_raw);
        CREATE INDEX IF NOT EXISTS idx_recommendations_commander
            ON recommendations(commander_card_id, rank);
        CREATE INDEX IF NOT EXISTS idx_cooccurrence_cards
            ON cooccurrence(card_id_a, card_id_b);
        CREATE INDEX IF NOT EXISTS idx_provenance_entity
            ON provenance(entity_type, entity_key, collected_at);
        CREATE INDEX IF NOT EXISTS idx_ontology_cards_match
            ON ontology_cards(forge_match_status, scryfall_name);
        CREATE INDEX IF NOT EXISTS idx_forge_records_match
            ON forge_records(match_status, forge_name);
        CREATE INDEX IF NOT EXISTS idx_ontology_reviews_status
            ON ontology_reviews(status, reviewed_at);
        CREATE INDEX IF NOT EXISTS idx_ontology_predicates_predicate
            ON ontology_predicates(predicate);
        CREATE INDEX IF NOT EXISTS idx_ontology_predicates_predicate_value
            ON ontology_predicates(predicate, arg_value);
        CREATE INDEX IF NOT EXISTS idx_ontology_predicates_card
            ON ontology_predicates(card_id);
        """
    )
    # The collector was added after the original catalog.  Keep upgrades
    # additive for databases created by an earlier checkout or by a partial
    # migration; never rebuild/drop a table that existing callers may use.
    migrations = {
        "cards": {
            "scryfall_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "ontology_cards": {
            "canonical_facts_json": "TEXT NOT NULL DEFAULT '{}'",
            "resolved_facts_json": "TEXT NOT NULL DEFAULT '{}'",
            "model_facts_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "ontology_reviews": {
            "selected_source": "TEXT NOT NULL DEFAULT 'resolved'",
            "field_checks_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "sources": {
            "terms_url": "TEXT",
            "robots_url": "TEXT",
        },
        "datasets": {
            "version": "TEXT",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "external_decks": {
            "dataset_id": "TEXT",
            "raw_hash": "TEXT",
            "license": "TEXT",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "deck_cards": {
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "provenance_id": "INTEGER",
        },
        "recommendations": {
            "dataset_id": "TEXT",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "category": "TEXT",
            "inclusion_count": "INTEGER",
            "potential_decks": "INTEGER",
            "inclusion_percent": "REAL",
            "synergy": "REAL",
            "salt_score": "REAL",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "cooccurrence": {
            "dataset_id": "TEXT",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "relation_type": "TEXT NOT NULL DEFAULT 'cooccurrence'",
            "category": "TEXT",
            "inclusion_percent": "REAL",
            "synergy": "REAL",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "provenance": {
            "dataset_id": "TEXT",
            "raw_json": "TEXT",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    }
    for table, columns_to_add in migrations.items():
        existing = _table_columns(conn, table)
        for column, definition in columns_to_add.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recommendations_card_metrics
            ON recommendations(card_id, inclusion_percent, synergy)
        """
    )
    current = conn.execute(
        "SELECT value FROM catalog_meta WHERE key = ?",
        ("collection_schema_version",),
    ).fetchone()
    if not current or current[0] != COLLECTION_SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("collection_schema_version", COLLECTION_SCHEMA_VERSION),
        )
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
        (key, value),
    )


def stamp_price_snapshot(conn: sqlite3.Connection, card_count: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_meta(conn, "price_snapshot_at", now)
    set_meta(conn, "scryfall_bulk_type", "oracle_cards")
    set_meta(conn, "card_count", str(card_count))
    conn.commit()


def get_meta(db_path: str = DB_NAME) -> dict:
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        rows = conn.execute("SELECT key, value FROM catalog_meta").fetchall()
        return {key: value for key, value in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def _row_to_card(row: sqlite3.Row) -> dict:
    keys = set(row.keys())
    legalities_raw = row["legalities"] if "legalities" in keys else "{}"
    identity_raw = row["color_identity"] if "color_identity" in keys else "[]"
    return {
        "id": row["id"] if "id" in keys else "",
        "name": row["name"],
        "mana_cost": row["mana_cost"] if "mana_cost" in keys else "",
        "cmc": row["cmc"] if "cmc" in keys else 0.0,
        "oracle_text": row["oracle_text"] or "" if "oracle_text" in keys else "",
        "color_identity": json.loads(identity_raw or "[]"),
        "type_line": row["type_line"] or "" if "type_line" in keys else "",
        "legalities": json.loads(legalities_raw or "{}"),
        "price_usd": row["price_usd"] if "price_usd" in keys else None,
        "price_eur": row["price_eur"] if "price_eur" in keys else None,
        "keywords": json.loads(row["keywords"] or "[]") if "keywords" in keys and row["keywords"] else [],
    }


def find_card_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Name-match cascade shared by get_oracle_card and any caller that needs
    to resolve a card name using its *own* connection (see
    service/handlers/decks.py's canonicalization: it must not open a second
    connection to the same file while already holding an open write
    transaction on this one). Exact match -> split-card face -> normalized
    DFC/split name."""
    row = conn.execute(
        "SELECT * FROM cards WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM cards WHERE name LIKE ? COLLATE NOCASE",
            (f"{name} //%",),
        ).fetchone()
    if row is None:
        normalized = _split_face_query_name(name)
        if normalized:
            row = conn.execute(
                "SELECT * FROM cards WHERE name = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
    return row


def get_oracle_card(name: str, db_path: str = DB_NAME) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        row = find_card_row(conn, name)
        if row is None:
            return None
        return _row_to_card(row)
    finally:
        conn.close()


def get_oracle_cards(names, db_path: str = DB_NAME) -> dict[str, dict]:
    """Batched get_oracle_card: one query for every name, instead of one
    connection+query per card -- diagnose_deck used to do exactly that (a
    real measured cost elsewhere in this codebase, ~150ms/card, see
    list_inventory_cards/legal_commanders_in_pool). Falls back to
    get_oracle_card's slower split-card/DFC-face matching only for names the
    batch query misses, so behavior stays identical to calling get_oracle_card
    once per name -- just fast for the common case (an exact name match).

    Returns {input name lowercased: card info}; a name that resolves to
    nothing (like get_oracle_card returning None) is simply absent.
    """
    result: dict[str, dict] = {}
    unique = [n for n in dict.fromkeys(names) if n]
    if not unique:
        return result
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        placeholders = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT * FROM cards WHERE name COLLATE NOCASE IN ({placeholders})",
            unique,
        ).fetchall()
    finally:
        conn.close()
    by_lower = {row["name"].lower(): _row_to_card(row) for row in rows}
    for name in unique:
        info = by_lower.get(name.lower())
        if info is None:
            info = get_oracle_card(name, db_path)
        if info is not None:
            result[name.lower()] = info
    return result


def card_unit_price(card: dict | None, currency: str = "usd") -> float | None:
    if not card:
        return None
    if currency == "eur":
        return parse_price(card.get("price_eur"))
    return parse_price(card.get("price_usd"))


def acquisition_cost(quantity: int, owned_qty: int, unit_price: float | None, owned_cost_zero: bool):
    """Return (cost_or_none, owned_used, buy_qty, price_unknown)."""
    owned_used = min(max(owned_qty, 0), quantity)
    buy_qty = quantity - owned_used
    if not owned_cost_zero:
        if unit_price is None:
            return None, 0, quantity, True
        return unit_price * quantity, 0, quantity, False
    if buy_qty == 0:
        return 0.0, owned_used, 0, False
    if unit_price is None:
        return None, owned_used, buy_qty, True
    return unit_price * buy_qty, owned_used, buy_qty, False


def enrich_deck(deck, db_path: str = DB_NAME) -> dict:
    """Attach catalog, inventory, curve, and acquisition cost to a DeckState."""
    from deck_state import DeckState

    if isinstance(deck, dict):
        deck = DeckState.from_dict(deck)

    details = []
    budget_used = 0.0
    price_unknown = []
    curve = {str(i): 0 for i in range(0, 7)}
    curve["7+"] = 0
    land_count = 0

    names = list(deck.cards.keys())
    if deck.commander:
        names = [deck.commander] + names

    seen_commander = False
    for name in names:
        is_commander = bool(deck.commander) and name.lower() == deck.commander.lower() and not seen_commander
        if is_commander:
            seen_commander = True
            qty = 1
        else:
            qty = deck.cards.get(name, 0)
            if qty <= 0:
                continue

        info = get_oracle_card(name, db_path)
        inv = get_inventory_card(name, db_path)
        owned_qty = inv["total_quantity"] if inv else 0
        unit_price = card_unit_price(info, deck.currency)
        cost, owned_used, buy_qty, unknown = acquisition_cost(
            qty, owned_qty, unit_price, deck.owned_cost_zero
        )
        if unknown:
            price_unknown.append(name)
        elif cost is not None:
            budget_used += cost

        cmc = float(info["cmc"]) if info and info.get("cmc") is not None else 0.0
        type_line = info["type_line"] if info else ""
        is_land = "Land" in type_line
        if is_land and not is_commander:
            land_count += qty
        elif not is_commander:
            bucket = "7+" if cmc >= 7 else str(int(cmc))
            curve[bucket] = curve.get(bucket, 0) + qty

        details.append(
            {
                "name": info["name"] if info else name,
                "quantity": qty,
                "is_commander": is_commander,
                "owned_qty": owned_qty,
                "owned_used": owned_used,
                "buy_qty": buy_qty,
                "unit_price": unit_price,
                "acquisition_cost": cost,
                "price_unknown": unknown,
                "cmc": cmc,
                "type_line": type_line,
                "color_identity": info["color_identity"] if info else [],
                "in_catalog": info is not None,
            }
        )

    return {
        "details": details,
        "budget_used": round(budget_used, 2),
        "price_unknown": price_unknown,
        "curve": curve,
        "land_count": land_count,
        "slot_count": deck.slot_count(),
        "catalog_meta": get_meta(db_path),
    }
