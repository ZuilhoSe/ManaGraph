import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from fastapi.testclient import TestClient

from contracts import AllocationCommand, RunEvent
from service.api import app
from tools import move_inventory_card


class RuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_deck_run_rejects_unknown_and_negative_payloads(self):
        response = self.client.post(
            "/api/deck/run",
            json={
                "query": "test",
                "deck": {"cards": {"Island": -1}},
            },
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/deck/run",
            json={"query": "test", "deck": {"not_a_deck_field": True}},
        )
        self.assertEqual(response.status_code, 422)

    def test_schema_version_is_enforced(self):
        response = self.client.post(
            "/api/deck/run",
            json={"schema_version": "999", "query": "test"},
        )
        self.assertEqual(response.status_code, 422)

    def test_run_stream_uses_versioned_ndjson_events(self):
        line = (
            '{"type":"run_id","schema_version":"1","event_type":"run_started",'
            '"run_id":"run-1","sequence":0,"node":"manager",'
            '"state_revision":0,"payload":{}}\n'
        )
        with patch("service.api.stream_deck_run", return_value=iter([line])):
            response = self.client.post("/api/deck/run", json={"query": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"schema_version":"1"', response.text)
        self.assertIn('"event_type":"run_started"', response.text)

    def test_allocation_requires_confirmation_and_is_typed(self):
        command = AllocationCommand(
            card="Island",
            source="free_pool",
            destination="deck_test",
            quantity=1,
            confirmation_id="manager-run-1",
        )
        with patch("service.api.execute_allocation", return_value={"ok": True}) as move:
            response = self.client.post(
                "/api/inventory/allocate",
                json=command.model_dump(),
            )
        self.assertEqual(response.status_code, 200)
        move.assert_called_once()
        with self.assertRaises(ValueError):
            AllocationCommand(
                card="Island",
                source="free_pool",
                destination="deck_test",
                quantity=0,
                confirmation_id="manager-run-1",
            )

    def test_llm_inventory_move_tool_cannot_mutate(self):
        result = move_inventory_card.invoke(
            {
                "card_name": "Island",
                "source": "free_pool",
                "destination": "deck_test",
                "quantity": 1,
            }
        )
        self.assertIn("disabled", result)
        self.assertIn("AllocationCommand", result)

    def test_event_contract_rejects_negative_sequence(self):
        with self.assertRaises(ValueError):
            RunEvent(
                run_id="run-1",
                sequence=-1,
                node="manager",
                event_type="run_started",
            )


if __name__ == "__main__":
    unittest.main()
