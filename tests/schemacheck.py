"""Stdlib-only validation of schema/report.schema.json and reports.

The project is stdlib-only (no `jsonschema` dependency — an audit tool
must run on the machine it audits). This module therefore implements
exactly the keyword subset the frozen schema uses, and nothing else:
`schema_meta_errors` rejects a schema that strays beyond the subset, so
"valid here" always means "checked here, with these rules".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "report.schema.json"

# Keywords the frozen subset understands (validation behavior).
SUPPORTED_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "minimum", "maximum", "pattern", "minItems", "minLength",
})
# JSON Schema metadata keywords: allowed, but ignored by this validator.
METADATA_KEYWORDS = frozenset({"$schema", "$id", "title", "description"})


def load_schema(path: Path = None) -> Any:
    p = path or SCHEMA_FILE
    return json.loads(p.read_text(encoding="utf-8"))


def schema_meta_errors(schema: Any) -> List[str]:
    """Structural meta-check: is this a schema within the frozen subset?"""
    errs: List[str] = []
    if not isinstance(schema, dict):
        return ["schema: not a JSON object"]
    if not isinstance(schema.get("$schema"), str) or \
            "json-schema" not in schema["$schema"]:
        errs.append("$schema: expected a JSON Schema URI")
    if schema.get("type") != "object":
        errs.append('root type must be "object"')
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        errs.append("properties: expected a non-empty object")
    required = schema.get("required")
    if not isinstance(required, list):
        errs.append("required: expected a list")
    elif isinstance(props, dict) and set(required) != set(props):
        errs.append(f"required must pin every property exactly: "
                    f"{sorted(set(required) ^ set(props))}")
    if schema.get("additionalProperties") is not False:
        errs.append("root additionalProperties must be false (the freeze)")
    _check_node(schema, "schema", errs)
    return errs


def _check_node(node: Any, path: str, errs: List[str]) -> None:
    if not isinstance(node, dict):
        errs.append(f"{path}: a schema node must be an object")
        return
    for key in node:
        if key not in SUPPORTED_KEYWORDS and key not in METADATA_KEYWORDS:
            errs.append(f"{path}.{key}: keyword outside the frozen subset")
    if isinstance(node.get("properties"), dict):
        for name, sub in node["properties"].items():
            _check_node(sub, f"{path}.properties.{name}", errs)
    if "items" in node:
        _check_node(node["items"], f"{path}.items", errs)
    if isinstance(node.get("additionalProperties"), dict):
        _check_node(node["additionalProperties"],
                    f"{path}.additionalProperties", errs)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate(instance: Any, schema: Any, path: str = "$") -> List[str]:
    """Validate `instance` against `schema` (the frozen keyword subset).

    Returns a list of human-readable violations; empty list = valid.
    """
    errs: List[str] = []
    if not isinstance(schema, dict):
        return errs

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, t) for t in types):
            errs.append(f"{path}: expected type {expected}, "
                        f"got {_type_name(instance)}")
            return errs  # deeper checks are meaningless on a type mismatch

    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: {instance} above maximum {schema['maximum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errs.append(f"{path}: {instance!r} does not match "
                        f"pattern {schema['pattern']!r}")

    if isinstance(instance, dict):
        for key in schema.get("required") or []:
            if key not in instance:
                errs.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in instance:
                errs.extend(validate(instance[key], sub, f"{path}.{key}"))
        additional = schema.get("additionalProperties")
        if additional is False:
            for key in instance:
                if key not in props:
                    errs.append(f"{path}: unknown key {key!r}")
        elif isinstance(additional, dict):
            for key, value in instance.items():
                if key not in props:
                    errs.extend(validate(value, additional, f"{path}.{key}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, value in enumerate(instance):
                errs.extend(validate(value, items, f"{path}[{i}]"))

    return errs
