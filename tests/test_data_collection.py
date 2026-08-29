import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema, get_oracle_card
from data_collection import (
    CollectionError,
    EDHRECAdapter,
    MoxfieldAdapter,
    ParsedPayload,
    collect_edhrec_public,
    collect_moxfield_public,
    ingest_oracle_lines,
    ingest_parsed,
    normalize_card_name,
    persist_source_chroma,
)
from scryfall_download import download_and_process_scryfall


def _seed_cards(path):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    cards = [
        ("cmd", "Test Commander", "Legendary Creature", ["U"], "legal"),
        ("split", "Fire // Ice", "Instant", ["U", "R"], "legal"),
        ("draw", "Test Draw", "Enchantment", ["U"], "legal"),
    ]
    for card_id, name, type_line, colors, status in cards:
        conn.execute(
            """
            INSERT INTO cards
              (id, name, mana_cost, cmc, oracle_text, color_identity, type_line, legalities)
            VALUES (?, ?, '', 1, '', ?, ?, ?)
            """,
            (card_id, name, json.dumps(colors), type_line, json.dumps({"commander": status})),
        )
    conn.commit()
    conn.close()


class _FakeCollection:
    def __init__(self, name):
        self.name = name
        self.rows = {}

    def upsert(self, *, ids, documents, metadatas, embeddings):
        for values in zip(ids, documents, metadatas, embeddings):
            self.rows[values[0]] = values[1:]


class _FakeClient:
    def __init__(self, path):
        self.path = path
        self.collections = {}

    def get_or_create_collection(self, name, **kwargs):
        return self.collections.setdefault(name, _FakeCollection(name))

    def delete_collection(self, name):
        self.collections.pop(name, None)


class DataCollectionTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db = handle.name
        _seed_cards(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_schema_adds_normalized_tables_without_changing_cards(self):
        conn = sqlite3.connect(self.db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
        self.assertTrue({"sources", "datasets", "external_decks", "deck_cards",
                         "recommendations", "cooccurrence", "provenance"} <= tables)
        self.assertTrue({"id", "name", "oracle_text", "legalities"} <= columns)
        recommendation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(recommendations)")
        }
        self.assertTrue(
            {
                "category", "inclusion_count", "potential_decks",
                "inclusion_percent", "synergy", "salt_score", "metadata_json",
            } <= recommendation_columns
        )
        conn.close()

    def test_split_names_resolve_and_retain_raw_name(self):
        self.assertEqual(normalize_card_name("Fire/Ice"), "Fire // Ice")
        self.assertEqual(normalize_card_name("Fire / Ice"), "Fire // Ice")
        parsed = MoxfieldAdapter().parse(
            {
                "id": "fixture-deck",
                "name": "Offline deck",
                "commanders": ["Test Commander"],
                "mainboard": {"Fire/Ice": 1},
            },
            external_id="fixture",
        )
        result = ingest_parsed(
            parsed,
            MoxfieldAdapter(),
            b'{"fixture":true}',
            db_path=self.db,
            dataset_id="moxfield:fixture",
        )
        self.assertEqual(result["decks"], 1)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT card_name_raw, card_id FROM deck_cards WHERE section='mainboard'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("Fire/Ice", "split"))

    def test_ingest_is_idempotent_and_records_provenance(self):
        parsed = MoxfieldAdapter().parse(
            {
                "id": "same",
                "name": "Same deck",
                "commanders": ["Test Commander"],
                "mainboard": {"Test Draw": 1},
            }
        )
        for _ in range(2):
            ingest_parsed(
                parsed,
                MoxfieldAdapter(),
                b'{"same":true}',
                db_path=self.db,
                dataset_id="moxfield:stable",
            )
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM external_decks").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0], 1)
        conn.close()

    def test_ingest_sums_duplicate_card_entries_and_preserves_raw_entries(self):
        parsed = MoxfieldAdapter().parse(
            {
                "id": "duplicate-cards",
                "name": "Duplicate cards",
                "commanders": ["Test Commander"],
                "mainboard": [
                    {"name": "Test Draw", "quantity": 1, "printing": "first"},
                    {"name": " Test   Draw ", "quantity": 2, "printing": "second"},
                ],
            }
        )
        result = ingest_parsed(
            parsed,
            MoxfieldAdapter(),
            b'{"duplicate-cards":true}',
            db_path=self.db,
            dataset_id="moxfield:duplicate-cards",
        )
        self.assertEqual(result["deck_cards"], 2)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            """
            SELECT quantity, raw_json, provenance_id
            FROM deck_cards
            WHERE card_name_raw='Test Draw'
            """
        ).fetchone()
        self.assertEqual(row[0], 3)
        self.assertEqual(
            json.loads(row[1]),
            {
                "entries": [
                    {"name": "Test Draw", "quantity": 1, "printing": "first"},
                    {"name": " Test   Draw ", "quantity": 2, "printing": "second"},
                ]
            },
        )
        self.assertIsNotNone(row[2])
        self.assertEqual(
            conn.execute(
                """
                SELECT COUNT(*) FROM deck_cards
                WHERE card_name_raw='Test Draw' AND section='mainboard'
                """
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_ingest_upsert_keeps_valid_rows_and_largest_quantity(self):
        first = MoxfieldAdapter().parse(
            {
                "publicId": "upsert",
                "name": "Upsert deck",
                "commanders": ["Test Commander"],
                "mainboard": {"Test Draw": 3, "Fire/Ice": 1},
            }
        )
        second = MoxfieldAdapter().parse(
            {
                "publicId": "upsert",
                "name": "Upsert deck",
                "commanders": ["Test Commander"],
                "mainboard": {"Test Draw": 1},
            }
        )
        ingest_parsed(
            first,
            MoxfieldAdapter(),
            b'{"upsert":1}',
            db_path=self.db,
            dataset_id="moxfield:upsert-1",
        )
        ingest_parsed(
            second,
            MoxfieldAdapter(),
            b'{"upsert":2}',
            db_path=self.db,
            dataset_id="moxfield:upsert-2",
        )
        conn = sqlite3.connect(self.db)
        quantity = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id='moxfield:upsert' AND card_name_raw='Test Draw' "
            "AND section='mainboard'"
        ).fetchone()[0]
        row_count = conn.execute(
            "SELECT COUNT(*) FROM deck_cards WHERE deck_id='moxfield:upsert'"
        ).fetchone()[0]
        preserved_row = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id='moxfield:upsert' AND card_name_raw='Fire/Ice'"
        ).fetchone()
        conn.close()
        self.assertEqual(quantity, 3)
        self.assertEqual(row_count, 3)
        self.assertEqual(preserved_row, (1,))

    def test_edhrec_recommendations_resolve_canonical_card_id(self):
        parsed = EDHRECAdapter().parse(
            {
                "commander": "Test Commander",
                "recommendations": [
                    {"name": "Test Draw", "synergy": 0.9, "rank": 1}
                ],
                "cooccurrence": [
                    {"a": "Test Commander", "b": "Test Draw", "count": 3}
                ],
            }
        )
        ingest_parsed(
            parsed,
            EDHRECAdapter(),
            b'{"recommendations":true}',
            db_path=self.db,
            dataset_id="edhrec:fixture",
        )
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT commander_card_id, card_id, score FROM recommendations"
        ).fetchone()
        pair = conn.execute(
            "SELECT card_id_a, card_id_b, occurrence_count FROM cooccurrence"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("cmd", "draw", 0.9))
        self.assertEqual(pair, ("cmd", "draw", 3))

    def test_edhrec_cardlists_extract_metrics_categories_and_edges(self):
        parsed = EDHRECAdapter().parse(
            {
                "container": {
                    "json_dict": {
                        "card": {"name": "Test Commander"},
                        "num_decks": 100,
                        "cardlists": [
                            {
                                "header": "High Synergy Cards",
                                "tag": "highsynergy",
                                "cardviews": [
                                    {
                                        "name": "Test Draw",
                                        "inclusion": 80,
                                        "num_decks": 80,
                                        "potential_decks": 100,
                                        "synergy": 0.42,
                                        "salt": 0.1,
                                        "label": "In 80 decks (80%)",
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        )
        self.assertEqual(len(parsed.recommendations), 1)
        recommendation = parsed.recommendations[0]
        self.assertEqual(recommendation["category"], "highsynergy")
        self.assertEqual(recommendation["inclusion_count"], 80)
        self.assertEqual(recommendation["potential_decks"], 100)
        self.assertEqual(recommendation["inclusion_percent"], 80.0)
        self.assertEqual(recommendation["synergy"], 0.42)
        self.assertEqual(recommendation["salt_score"], 0.1)
        label_only = EDHRECAdapter().parse(
            {
                "commander": "Test Commander",
                "recommendations": [
                    {"name": "Test Draw", "label": "In 8 decks, 80% of 10 decks"}
                ],
            }
        ).recommendations[0]
        self.assertEqual(label_only["inclusion_count"], 8)
        self.assertEqual(label_only["potential_decks"], 10)
        self.assertEqual(label_only["inclusion_percent"], 80.0)
        self.assertEqual(
            parsed.cooccurrence[0]["relation_type"],
            "commander_recommendation",
        )
        ingest_parsed(
            parsed,
            EDHRECAdapter(),
            b'{"cardlists":true}',
            db_path=self.db,
            dataset_id="edhrec:cardlists",
        )
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            """
            SELECT category, inclusion_count, potential_decks, inclusion_percent,
                   synergy, salt_score
            FROM recommendations
            """
        ).fetchone()
        edge = conn.execute(
            """
            SELECT card_id_a, card_id_b, relation_type, occurrence_count,
                   inclusion_percent, synergy
            FROM cooccurrence
            """
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("highsynergy", 80, 100, 80.0, 0.42, 0.1))
        self.assertEqual(edge, ("cmd", "draw", "commander_recommendation", 80, 80.0, 0.42))

    def test_scryfall_fixture_updates_legacy_catalog_and_dataset(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "id": "new", "name": "New Oracle Card", "cmc": 2,
                    "oracle_text": "Draw a card.", "color_identity": ["U"],
                    "type_line": "Instant", "legalities": {"commander": "legal"},
                }) + "\n")
            result = download_and_process_scryfall(db_path=self.db, local_file=path)
            self.assertEqual(result["cards"], 1)
            self.assertEqual(get_oracle_card("New Oracle Card", self.db)["id"], "new")
            conn = sqlite3.connect(self.db)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets WHERE source_id='scryfall'").fetchone()[0],
                1,
            )
            conn.close()
        finally:
            os.unlink(path)

    def test_source_chroma_uses_additive_collections(self):
        fake_client = _FakeClient("fake")
        fake_module = types.SimpleNamespace(PersistentClient=lambda path: fake_client)
        parsed = EDHRECAdapter().parse(
            {"commander": "Test Commander", "recommendations": [{"name": "Test Draw"}]}
        )
        with patch.dict(sys.modules, {"chromadb": fake_module}):
            result = persist_source_chroma(
                parsed,
                EDHRECAdapter(),
                chroma_path="unused",
                dataset_id="edhrec:fixture",
            )
        self.assertEqual(result["documents"], 2)
        self.assertIn("recommendations", fake_client.collections)
        self.assertIn("cooccurrence", fake_client.collections)
        record = next(iter(fake_client.collections["recommendations"].rows.values()))
        self.assertEqual(record[1]["name"], "Test Draw")

    def test_moxfield_public_collection_discovers_and_enriches_decks(self):
        search = {
            "pageNumber": 1,
            "totalPages": 1,
            "data": [{
                "id": "internal-search-id",
                "publicId": "public-1",
                "name": "Public deck",
                "format": "commander",
                "publicUrl": "https://www.moxfield.com/decks/public-1",
            }],
        }
        detail = {
            "publicId": "public-1",
            "name": "Public deck",
            "commanders": {"Test Commander": {"quantity": 1}},
            "mainboard": {"Test Draw": {"quantity": 1}},
        }
        with patch(
            "data_collection._fetch_json",
            side_effect=[
                (search, b"search", False),
                (detail, b"detail", False),
            ],
        ) as fetch:
            result = collect_moxfield_public(
                db_path=self.db,
                search_url="https://example.test/search",
                deck_url_template="https://example.test/decks/{deck_id}",
                max_pages=1,
                max_decks=1,
                min_interval=0,
            )
        self.assertEqual(result["decks"], 1)
        self.assertEqual(
            fetch.call_args_list[1].args[0],
            "https://example.test/decks/public-1",
        )
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 2)
        conn.close()

    def test_moxfield_public_collection_falls_back_to_v2_after_v3_404(self):
        search = {
            "pageNumber": 1,
            "totalPages": 1,
            "data": [{
                "id": "internal-search-id",
                "name": "Public deck",
                "publicUrl": "https://www.moxfield.com/decks/public-1",
            }],
        }
        detail = {
            "publicId": "public-1",
            "name": "Public deck",
            "commanders": {"Test Commander": {"quantity": 1}},
            "mainboard": {"Test Draw": {"quantity": 1}},
        }
        with patch(
            "data_collection._fetch_json",
            side_effect=[
                (search, b"search", False),
                CollectionError("HTTP error fetching v3: 404 Not Found"),
                (detail, b"detail", False),
            ],
        ) as fetch:
            result = collect_moxfield_public(
                db_path=self.db,
                search_url="https://example.test/search",
                max_pages=1,
                max_decks=1,
                min_interval=0,
            )
        self.assertEqual(result["deck_cards"], 2)
        self.assertEqual(result["requests"], 3)
        self.assertEqual(result["summary_count"], 1)
        self.assertEqual(result["detail_successes"], 1)
        self.assertEqual(result["detail_fallback_attempts"], 1)
        self.assertEqual(result["detail_errors"], [])
        self.assertEqual(
            fetch.call_args_list[1].args[0],
            "https://api2.moxfield.com/v3/decks/all/public-1",
        )
        self.assertEqual(
            fetch.call_args_list[2].args[0],
            "https://api2.moxfield.com/v2/decks/all/public-1",
        )
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 2)
        conn.close()

    def test_moxfield_does_not_use_internal_search_id_for_details(self):
        search = {
            "pageNumber": 1,
            "totalPages": 1,
            "data": [{
                "id": "internal-search-id",
                "name": "Public summary without mapped ID",
                "publicUrl": None,
            }],
        }
        with patch(
            "data_collection._fetch_json",
            return_value=(search, b"search", False),
        ) as fetch:
            result = collect_moxfield_public(
                db_path=self.db,
                search_url="https://example.test/search",
                max_pages=1,
                max_decks=1,
                min_interval=0,
            )
        self.assertEqual(len(fetch.call_args_list), 1)
        self.assertEqual(result["requests"], 1)
        self.assertEqual(result["summary_count"], 1)
        self.assertEqual(result["summaries_without_public_id"], 1)
        self.assertEqual(result["detail_successes"], 0)
        self.assertEqual(result["detail_errors"], [])
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT external_id, name FROM external_decks"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("search:1", "Public summary without mapped ID"))

    def test_moxfield_does_not_fallback_after_access_denial(self):
        search = {
            "pageNumber": 1,
            "totalPages": 1,
            "data": [{"publicId": "public-1", "name": "Public deck"}],
        }
        with patch(
            "data_collection._fetch_json",
            side_effect=[
                (search, b"search", False),
                CollectionError("https://api2.moxfield.com: requires credentials or denied automated access (403)"),
            ],
        ) as fetch:
            result = collect_moxfield_public(
                db_path=self.db,
                search_url="https://example.test/search",
                max_pages=1,
                max_decks=1,
                min_interval=0,
            )
        self.assertEqual(len(fetch.call_args_list), 2)
        self.assertEqual(result["deck_cards"], 0)
        self.assertEqual(result["summary_count"], 1)
        self.assertEqual(len(result["detail_errors"]), 1)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT external_id, name FROM external_decks"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("public-1", "Public deck"))

    def test_edhrec_public_collection_works_without_commander(self):
        listing = {"recommendations": [{"name": "Test Commander"}]}
        top = {"recommendations": [{"name": "Test Commander"}]}
        commander_page = {
            "commander": "Test Commander",
            "recommendations": [{"name": "Test Draw", "synergy": 0.8}],
        }
        with patch(
            "data_collection._fetch_json",
            side_effect=[
                (listing, b"listing", False),
                (top, b"top", False),
                (commander_page, b"commander", False),
            ],
        ):
            result = collect_edhrec_public(
                db_path=self.db,
                listing_url="https://example.test/commanders.json",
                top_url_template="https://example.test/top/{page}.json",
                commander_url_template="https://example.test/commanders/{slug}.json",
                max_pages=1,
                max_commanders=1,
                min_interval=0,
            )
        self.assertEqual(result["commander_pages"], 1)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0], 2)
        conn.close()

    def test_edhrec_public_collection_reads_real_cardlists_wrapper(self):
        listing = {
            "container": {
                "json_dict": {
                    "cardlists": [
                        {"tag": "commanders", "cardviews": [{"name": "Test Commander"}]}
                    ]
                }
            }
        }
        top = {"cardlists": [{"header": "Top", "cardviews": [{"name": "Test Commander"}]}]}
        commander_page = {
            "container": {
                "json_dict": {
                    "card": {"name": "Test Commander"},
                    "cardlists": [
                        {
                            "tag": "highsynergy",
                            "cardviews": [
                                {
                                    "name": "Test Draw",
                                    "num_decks": 8,
                                    "potential_decks": 10,
                                    "synergy": 0.25,
                                }
                            ],
                        }
                    ],
                }
            }
        }
        with patch(
            "data_collection._fetch_json",
            side_effect=[
                (listing, b"listing-cardlists", False),
                (top, b"top-cardlists", False),
                (commander_page, b"commander-cardlists", False),
            ],
        ):
            result = collect_edhrec_public(
                db_path=self.db,
                listing_url="https://example.test/commanders.json",
                top_url_template="https://example.test/top/{page}.json",
                commander_url_template="https://example.test/commanders/{slug}.json",
                max_pages=1,
                max_commanders=1,
                min_interval=0,
            )
        self.assertEqual(result["commander_pages"], 1)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT category, inclusion_percent, synergy FROM recommendations "
            "WHERE card_name_raw='Test Draw'"
        ).fetchone()
        edge = conn.execute(
            "SELECT relation_type, occurrence_count FROM cooccurrence "
            "WHERE card_name_a_raw='Test Commander' AND card_name_b_raw='Test Draw'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("highsynergy", 80.0, 0.25))
        self.assertEqual(edge, ("commander_recommendation", 8))

    def test_edhrec_403_is_reported_without_an_auth_bypass(self):
        denied = CollectionError(
            "https://json.edhrec.com/pages/top/commanders--1.json requires "
            "credentials or denied automated access (403); provide an authorized export "
            "with --fixture/--file."
        )
        with patch("data_collection._fetch_json", side_effect=denied) as fetch:
            with self.assertRaisesRegex(CollectionError, "requires credentials"):
                collect_edhrec_public(
                    db_path=self.db,
                    listing_url="https://example.test/commanders.json",
                    top_url_template="https://example.test/top/{page}.json",
                    max_pages=1,
                    max_commanders=0,
                    min_interval=0,
                )
        self.assertEqual(len(fetch.call_args_list), 2)


if __name__ == "__main__":
    unittest.main()
