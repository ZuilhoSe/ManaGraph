"""Regression tests for the two sqlite concurrency bugs hit when the
Architect fires several search_cards tool calls in parallel:

  - catalog.ensure_schema() used to write (INSERT OR REPLACE + commit) on
    *every* call, from a fresh connection almost every time. Under real
    concurrency that collided with another connection's open write
    transaction -> sqlite3.OperationalError: database is locked.
  - hybrid_search.RAGSearcher shares one connection/cursor across every
    caller (it's a process-wide singleton) but nothing serialized concurrent
    access to it -> sqlite3.OperationalError: another row available.

No pytest in this venv -- run directly: python tests/test_db_concurrency.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import DB_NAME, ensure_schema  # noqa: E402


class TestEnsureSchemaConcurrency(unittest.TestCase):
    """A connection calling ensure_schema() must not attempt a write once the
    schema is already current -- even while another connection holds an open
    write transaction on the same file."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        shutil.copyfile(DB_NAME, self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_ensure_schema_skips_write_while_another_txn_is_open(self):
        conn_a = sqlite3.connect(self.db)
        ensure_schema(conn_a)  # real migration, marks self.db as ensured
        conn_a.close()

        # Holds an open write transaction, mirroring save_deck()/search_cards()
        # holding one on their own connection while calling into code that
        # (used to) call ensure_schema on a *different* connection.
        conn_b = sqlite3.connect(self.db)
        conn_b.execute("BEGIN IMMEDIATE")
        conn_b.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('probe', '1')")

        conn_c = sqlite3.connect(self.db, timeout=1)
        try:
            ensure_schema(conn_c)  # pre-fix: OperationalError: database is locked
        finally:
            conn_c.close()
            conn_b.rollback()
            conn_b.close()


class TestSearchCardsConcurrency(unittest.TestCase):
    """Several threads calling search_cards() at once (the Architect's
    parallel tool calls) must not race on the shared connection/cursor."""

    def test_concurrent_search_cards_no_cursor_race(self):
        from hybrid_search import RAGSearcher

        searcher = RAGSearcher.__new__(RAGSearcher)  # skip __init__: no Chroma/model needed
        searcher.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        searcher.cursor = searcher.conn.cursor()
        searcher._embedding_hits = lambda query, allowed_colors, fetch: []

        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(5):
                    searcher.search_cards("draw a card", ["U", "B", "G"], limit=5)
            except Exception as exc:  # noqa: BLE001 - capturing for the assertion below
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        searcher.conn.close()

        self.assertEqual(errors, [], f"concurrent search_cards raised: {errors!r}")


if __name__ == "__main__":
    unittest.main()
