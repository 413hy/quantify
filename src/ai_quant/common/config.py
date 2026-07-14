"""Strict configuration parsing and secret-value rejection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from yaml.nodes import MappingNode


class ConfigurationError(ValueError):
    """Configuration is invalid or unsafe."""


_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|passphrase|private_?key|access_?token|bot_?token|listen_?key)$",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^(?:\$\{[A-Z][A-Z0-9_]*\}|<[^>]+>|REQUIRED_AT_RUNTIME|)$")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate configuration key: {key}")
        result[key] = value
    return result


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConfigurationError("unhashable configuration key") from exc
        if duplicate:
            raise ConfigurationError(f"duplicate configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_strict_document(path: Path) -> Any:
    """Parse JSON/YAML without permitting duplicate keys or unsafe YAML tags."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        if path.suffix in {".yaml", ".yml"}:
            loader = _UniqueKeySafeLoader(text)
            try:
                return loader.get_single_data()
            finally:
                loader.dispose()  # type: ignore[no-untyped-call]
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("configuration cannot be parsed safely") from exc
    raise ConfigurationError(f"unsupported configuration format: {path.suffix}")


def reject_embedded_secret_values(value: Any, path: str = "$") -> None:
    """Reject scalar secret material while allowing runtime placeholders and file paths."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            key_text = str(key)
            is_location = key_text.lower().endswith(("_file", "_path", "_mount"))
            is_public = "public" in key_text.lower() or "verification" in key_text.lower()
            if (
                _SECRET_KEY.search(key_text)
                and not is_location
                and not is_public
                and isinstance(child, str)
                and not _PLACEHOLDER.fullmatch(child)
            ):
                raise ConfigurationError(f"embedded secret value rejected at {child_path}")
            reject_embedded_secret_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_embedded_secret_values(child, f"{path}[{index}]")


def validate_config(instance_path: Path, schema_path: Path) -> Any:
    instance = load_strict_document(instance_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    if errors:
        detail = "; ".join(
            f"/{'/'.join(map(str, error.path))}: {error.message}" for error in errors
        )
        raise ConfigurationError(detail)
    reject_embedded_secret_values(instance)
    return instance
