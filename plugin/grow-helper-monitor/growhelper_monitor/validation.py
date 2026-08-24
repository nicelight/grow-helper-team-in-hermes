"""Lightweight, non-blocking validation for specialist Kanban handoffs."""
from __future__ import annotations

import re
from typing import Any

CONFIDENCE = {"low", "medium", "high"}
URGENCY = {"now", "soon", "routine", "none"}
REVERSIBILITY = {"easy", "moderate", "hard", "unknown"}
VISION_DIAGNOSIS_RE = re.compile(
    r"\b(дефицит|недостаток|болезн(?:ь|и)|инфекц(?:ия|ии)|вредител(?:ь|и)|"
    r"deficien(?:cy|t)|disease|infection|pest|caused\s+by)\b",
    re.IGNORECASE,
)


def _warning(code: str, message: str, path: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "path": path, "severity": "warning"}


def _validate_items(items: Any, section: str) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not isinstance(items, list):
        return [_warning("section_not_array", f"{section} must be an array", section)]
    for index, item in enumerate(items):
        path = f"{section}[{index}]"
        if not isinstance(item, dict):
            warnings.append(_warning("item_not_object", f"{path} must be an object", path))
            continue
        if not str(item.get("id") or "").strip():
            warnings.append(_warning("missing_id", f"{path}.id is required", path + ".id"))
        if not str(item.get("text") or "").strip():
            warnings.append(_warning("missing_text", f"{path}.text is required", path + ".text"))
        confidence = item.get("confidence")
        if confidence not in CONFIDENCE:
            warnings.append(_warning("bad_confidence", f"{path}.confidence must be low|medium|high", path + ".confidence"))
        if section == "observation":
            if not str(item.get("source") or "").strip():
                warnings.append(_warning("missing_source", f"{path}.source is required", path + ".source"))
            if not str(item.get("timestamp") or "").strip():
                warnings.append(_warning("missing_timestamp", f"{path}.timestamp is required", path + ".timestamp"))
            if not isinstance(item.get("missing_data"), list):
                warnings.append(_warning("field_not_array", f"{path}.missing_data must be an array", path + ".missing_data"))
            if item.get("value") is not None and not str(item.get("unit") or "").strip():
                warnings.append(_warning("measurement_without_unit", f"{path}.unit is required when value is present", path + ".unit"))
        if section == "inference":
            for field in ("evidence_for", "evidence_against", "missing_data"):
                if not isinstance(item.get(field), list):
                    warnings.append(_warning("field_not_array", f"{path}.{field} must be an array", path + "." + field))
        if section == "recommendation":
            if not isinstance(item.get("based_on"), list):
                warnings.append(_warning("field_not_array", f"{path}.based_on must be an array", path + ".based_on"))
            if item.get("urgency") not in URGENCY:
                warnings.append(_warning("bad_urgency", f"{path}.urgency must be now|soon|routine|none", path + ".urgency"))
            if item.get("reversibility") not in REVERSIBILITY:
                warnings.append(_warning("bad_reversibility", f"{path}.reversibility must be easy|moderate|hard|unknown", path + ".reversibility"))
    return warnings


def validate_handoff(metadata: Any, *, role: str = "") -> list[dict[str, str]]:
    """Return warnings; never raise and never block a Cycle."""
    warnings: list[dict[str, str]] = []
    if not isinstance(metadata, dict):
        return [_warning("metadata_not_object", "Completion metadata is not an object", "metadata")]
    if metadata.get("schema_version") != "growhelper.v1":
        warnings.append(_warning("schema_version", "schema_version should be growhelper.v1", "schema_version"))
    for section in ("observation", "inference", "recommendation"):
        if section not in metadata:
            warnings.append(_warning("missing_section", f"Required top-level array {section} is missing", section))
        warnings.extend(_validate_items(metadata.get(section), section))
    if metadata.get("verdict") not in {"comment", "no_comments", "needs_data"}:
        warnings.append(_warning("bad_verdict", "verdict must be comment|no_comments|needs_data", "verdict"))
    if metadata.get("confidence") not in CONFIDENCE:
        warnings.append(_warning("bad_overall_confidence", "Top-level confidence must be low|medium|high", "confidence"))
    if not isinstance(metadata.get("missing_data", []), list):
        warnings.append(_warning("missing_data_not_array", "Top-level missing_data must be an array", "missing_data"))
    if metadata.get("verdict") == "no_comments" and any(metadata.get(section) for section in ("observation", "inference", "recommendation")):
        warnings.append(_warning("no_comments_has_content", "verdict=no_comments should use empty handoff arrays", "verdict"))

    normalized_role = role.lower()
    if normalized_role == "vision-observation":
        if metadata.get("inference"):
            warnings.append(_warning("vision_inference", "vision-observation must not emit diagnostic inference", "inference"))
        if metadata.get("recommendation"):
            warnings.append(_warning("vision_recommendation", "vision-observation must not emit recommendations", "recommendation"))
        for index, item in enumerate(metadata.get("observation") or []):
            if isinstance(item, dict) and VISION_DIAGNOSIS_RE.search(str(item.get("text") or "")):
                warnings.append(_warning(
                    "vision_diagnostic_language",
                    "Visual observation contains diagnostic language; describe only visible facts",
                    f"observation[{index}].text",
                ))
    return warnings
