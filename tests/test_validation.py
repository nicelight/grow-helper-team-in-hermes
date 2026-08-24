from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor.validation import validate_handoff


def valid_metadata():
    return {
        "schema_version": "growhelper.v1",
        "round_id": "R1",
        "verdict": "comment",
        "observation": [{
            "id": "obs-1", "text": "Светлые участки между жилками",
            "source": "photo:leaf.jpg", "timestamp": "unknown",
            "confidence": "medium", "missing_data": [],
        }],
        "inference": [],
        "recommendation": [],
        "confidence": "medium",
        "missing_data": [],
    }


class ValidationTests(unittest.TestCase):
    def test_valid_vision_handoff(self) -> None:
        self.assertEqual(validate_handoff(valid_metadata(), role="vision-observation"), [])

    def test_vision_diagnosis_and_inference_warn(self) -> None:
        value = valid_metadata()
        value["observation"][0]["text"] = "Это дефицит магния"
        value["inference"] = [{
            "id": "inf-1", "text": "Mg", "confidence": "low",
            "evidence_for": ["obs-1"], "evidence_against": [], "missing_data": [],
        }]
        codes = {warning["code"] for warning in validate_handoff(value, role="vision-observation")}
        self.assertIn("vision_diagnostic_language", codes)
        self.assertIn("vision_inference", codes)

    def test_missing_required_sections_warn(self) -> None:
        codes = {warning["code"] for warning in validate_handoff({}, role="plant-state")}
        self.assertIn("schema_version", codes)
        self.assertIn("missing_section", codes)


if __name__ == "__main__":
    unittest.main()
