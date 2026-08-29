import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from ontology.schema import (  # noqa: E402
    Capability,
    EventName,
    ObjectName,
    PredicateName,
    SchemaValidationError,
    load_schema,
)


class OntologySchemaTests(unittest.TestCase):
    def test_v1_schema_loads_with_mechanical_enums(self):
        schema = load_schema()

        self.assertEqual(schema.schema_version, "1.0.0")
        self.assertIn(ObjectName.MANA.value, schema.objects)
        self.assertIn(EventName.ETB.value, schema.events)
        self.assertIn(PredicateName.PRODUCES.value, schema.predicates)
        self.assertIn(Capability.SAC_OUTLET, schema.capabilities)
        self.assertEqual(
            schema.predicate(PredicateName.EMITS).consumer,
            "solver._score_parts",
        )

    def test_schema_rejects_unknown_predicate(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                """
schema_version: "1.0.0"
labels_version: "test"
objects: [{name: mana, parameters: [color]}]
events: [{name: etb, parameters: []}]
capabilities: [sac_outlet]
threat_classes: [creature]
selectors: [any]
target_classes: [board]
predicates:
  - name: invented
    arguments: [object]
    consumer: diagnose_deck_json
"""
            )
            path = handle.name
        try:
            with self.assertRaises(SchemaValidationError):
                load_schema(path)
        finally:
            os.unlink(path)

    def test_forge_is_not_a_runtime_dependency(self):
        schema = load_schema()
        self.assertFalse(schema.provenance["forge"]["runtime_dependency"])


if __name__ == "__main__":
    unittest.main()
