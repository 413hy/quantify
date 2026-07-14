#!/usr/bin/env python3
"""Read-only preflight validator for the immutable documentation packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import rfc8785
import yaml
from defusedxml import ElementTree
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from referencing import Registry, Resource


class DuplicateKeyError(ValueError):
    pass


def reject_json_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise DuplicateKeyError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_json_duplicates)


def load_yaml(path: Path) -> Any:
    # UniqueKeyLoader derives from SafeLoader; this call preserves duplicate-key checks.
    return yaml.load(  # nosec B506
        path.read_text(encoding="utf-8"),
        Loader=UniqueKeyLoader,  # noqa: S506
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def jcs_hash(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def verify_manifest(root: Path, manifest_name: str = "MANIFEST.sha256") -> dict[str, Any]:
    manifest = root / manifest_name
    listed: dict[str, str] = {}
    failures: list[str] = []
    for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", raw)
        if not match:
            failures.append(f"line {line_number}: invalid format")
            continue
        expected, relative = match.groups()
        if relative in listed:
            failures.append(f"duplicate path: {relative}")
            continue
        listed[relative] = expected
        target = root / relative
        if not target.is_file():
            failures.append(f"missing: {relative}")
        elif sha256(target) != expected:
            failures.append(f"hash mismatch: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != manifest_name
    }
    missing_from_manifest = sorted(actual - set(listed))
    extra_in_manifest = sorted(set(listed) - actual)
    failures.extend(f"unlisted: {item}" for item in missing_from_manifest)
    failures.extend(f"listed but absent: {item}" for item in extra_in_manifest)
    return {"listed_files": len(listed), "actual_files": len(actual), "failures": failures}


def schema_for_example(name: str) -> str:
    domain_examples = {
        "feature-snapshot.json",
        "order-event-algo.json",
        "order-event.json",
        "signal-no-trade.json",
        "universe-snapshot.json",
    }
    rate_prefixes = (
        "rate-connection-",
        "rate-exchange-",
        "rate-header-",
        "rate-permit-",
        "rate-reserve-",
        "rate-send-",
        "rate-server-",
    )
    if name in domain_examples:
        return "domain-events.schema.json"
    if name.startswith(rate_prefixes):
        return "rate-budget-uds.schema.json"
    stem = name.removesuffix(".json")
    if stem.startswith("auto-iteration-report-"):
        stem = "auto-iteration-report"
    if stem.startswith("trade-plan-"):
        stem = "trade-plan"
    return f"{stem}.schema.json"


def validate_schemas_and_examples(root: Path) -> dict[str, Any]:
    schema_paths = sorted((root / "contracts").glob("*.schema.json")) + sorted(
        (root / "config").glob("*.schema.json")
    )
    failures: list[str] = []
    schemas: dict[Path, Any] = {}
    registry = Registry()
    for path in schema_paths:
        try:
            schema = load_json(path)
            Draft202012Validator.check_schema(schema)
            schemas[path] = schema
            resource = Resource.from_contents(schema)
            registry = registry.with_resource(path.as_uri(), resource)
            registry = registry.with_resource(path.name, resource)
        except Exception as exc:  # report every independent artifact
            failures.append(f"schema {path.relative_to(root)}: {exc}")

    format_checker = FormatChecker()
    contract_instances = 0
    for instance_path in sorted((root / "contracts/examples").glob("*.json")):
        contract_instances += 1
        schema_path = root / "contracts" / schema_for_example(instance_path.name)
        try:
            instance = load_json(instance_path)
            validator = Draft202012Validator(
                schemas[schema_path], format_checker=format_checker, registry=registry
            )
            errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
            for error in errors:
                pointer = "/" + "/".join(str(item) for item in error.path)
                failures.append(
                    f"instance {instance_path.relative_to(root)}{pointer}: {error.message}"
                )
        except Exception as exc:
            failures.append(f"instance {instance_path.relative_to(root)}: {exc}")

    config_instances = 0
    for instance_path in sorted((root / "config").glob("*.example.*")):
        if instance_path.suffix not in {".json", ".yaml"}:
            continue
        config_instances += 1
        base = instance_path.name.replace(".example.json", "").replace(".example.yaml", "")
        if base == "verification-keyring.host-control":
            base = "verification-keyring"
        schema_path = root / "config" / f"{base}.schema.json"
        try:
            instance = (
                load_json(instance_path)
                if instance_path.suffix == ".json"
                else load_yaml(instance_path)
            )
            validator = Draft202012Validator(
                schemas[schema_path], format_checker=format_checker, registry=registry
            )
            errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
            for error in errors:
                pointer = "/" + "/".join(str(item) for item in error.path)
                failures.append(
                    f"config {instance_path.relative_to(root)}{pointer}: {error.message}"
                )
        except Exception as exc:
            failures.append(f"config {instance_path.relative_to(root)}: {exc}")

    return {
        "schema_count": len(schema_paths),
        "contract_instance_count": contract_instances,
        "config_instance_count": config_instances,
        "failures": failures,
    }


HASH_RULES: dict[str, list[tuple[str, str]]] = {
    "auto-iteration-report-deferred-quota.json": [("content", "report_hash")],
    "auto-iteration-report-observe-only.json": [("content", "report_hash")],
    "calibration-dataset-manifest.json": [("signed_payload", "manifest_hash")],
    "calibration-dataset-plan.json": [
        ("content", "plan_hash"),
        ("registration.signed_payload", "registration.payload_hash"),
    ],
    "codex-review-report.json": [("content", "report_hash")],
    "cost-model.json": [("content", "model_hash")],
    "edge-decision.json": [("content", "edge_evaluation_hash")],
    "engineering-proposal.json": [("content", "proposal_hash")],
    "execution-model.json": [("content", "model_hash")],
    "host-rate-startup-evidence.json": [("content", "evidence_hash")],
    "market-decision-context.json": [("content", "context_hash")],
    "model-selection-decision.json": [("content", "decision_hash")],
    "of-calibration-search-plan.json": [("content", "plan_hash")],
    "of-parameter-candidate.json": [
        ("content", "candidate_hash"),
        ("content.parameters", "content.parameter_manifest_hash"),
    ],
    "operator-approval.json": [("signed_payload", "payload_hash")],
    "research-proposal.json": [("content", "proposal_hash")],
    "research-review.json": [("signed_payload", "payload_hash")],
    "strategy-approval.json": [("signed_payload", "payload_hash")],
    "strategy-health-report.json": [("content", "report_hash")],
    "strategy-package.json": [("content", "package_hash")],
    "testnet-protocol-probe-plan.json": [("content", "plan_hash")],
    "trade-plan-entry.json": [("content", "plan_hash")],
    "trade-plan-no-trade.json": [("content", "plan_hash")],
    "validation-equivalence-profile.json": [("signed_payload", "profile_hash")],
}


def get_path(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        current = current[part]
    return current


def validate_jcs_hashes(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    checks = 0
    for filename, rules in HASH_RULES.items():
        instance = load_json(root / "contracts/examples" / filename)
        for payload_path, hash_path in rules:
            checks += 1
            expected = get_path(instance, hash_path)
            actual = jcs_hash(get_path(instance, payload_path))
            if expected != actual:
                failures.append(f"{filename}:{hash_path}: expected {expected}, computed {actual}")
    return {"check_count": checks, "failures": failures}


def github_slug(heading: str) -> str:
    value = heading.strip().lower()
    value = re.sub(r"[^\w\-\u4e00-\u9fff ]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def markdown_anchors(path: Path) -> set[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        slug = github_slug(match.group(1))
        occurrence = counts[slug]
        counts[slug] += 1
        anchors.add(slug if occurrence == 0 else f"{slug}-{occurrence}")
    return anchors


LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def validate_markdown_links(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    count = 0
    anchor_cache: dict[Path, set[str]] = {}
    for source in sorted(root.rglob("*.md")):
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            count += 1
            path_text, separator, fragment = target.partition("#")
            target_path = (
                source if not path_text else (source.parent / unquote(path_text)).resolve()
            )
            try:
                target_path.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{source.relative_to(root)}: link escapes root: {target}")
                continue
            if not target_path.exists():
                failures.append(f"{source.relative_to(root)}: missing target: {target}")
                continue
            if separator and fragment and target_path.suffix.lower() == ".md":
                if target_path not in anchor_cache:
                    anchor_cache[target_path] = markdown_anchors(target_path)
                decoded = unquote(fragment).lower()
                if decoded not in anchor_cache[target_path]:
                    failures.append(f"{source.relative_to(root)}: missing anchor: {target}")
    return {"local_link_count": count, "failures": failures}


def validate_xml(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    drawio = root / "diagrams/AI_QUANT_SYSTEM_ARCHITECTURE.drawio"
    svg = root / "diagrams/AI_QUANT_SYSTEM_ARCHITECTURE.svg"
    try:
        tree = ElementTree.parse(drawio)
        diagrams = tree.getroot().findall("diagram")
        direct_models = sum(diagram.find("mxGraphModel") is not None for diagram in diagrams)
        names = [diagram.attrib.get("name") for diagram in diagrams]
        if len(diagrams) != 3 or direct_models != 3:
            failures.append(f"drawio pages/models: {len(diagrams)}/{direct_models}, expected 3/3")
        expected_names = ["01 生产架构", "02 数据·研究·发布", "03 阶段·信任边界"]
        if names != expected_names:
            failures.append(f"drawio page names: {names}")
    except Exception as exc:
        failures.append(f"drawio XML: {exc}")
    try:
        ElementTree.parse(svg)
    except Exception as exc:
        failures.append(f"SVG XML: {exc}")
    return {"files": 2, "failures": failures}


def validate_openapi(root: Path) -> dict[str, Any]:
    path = root / "contracts/openapi.yaml"
    try:
        validate_spec(load_yaml(path))
        return {"version": "3.1.0", "failures": []}
    except Exception as exc:
        return {"version": "unknown", "failures": [str(exc)]}


def parse_all_json_yaml(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    json_count = 0
    yaml_count = 0
    for path in sorted(root.rglob("*.json")):
        json_count += 1
        try:
            load_json(path)
        except Exception as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
    for suffix in ("*.yaml", "*.yml"):
        for path in sorted(root.rglob(suffix)):
            yaml_count += 1
            try:
                load_yaml(path)
            except Exception as exc:
                failures.append(f"{path.relative_to(root)}: {exc}")
    return {"json_count": json_count, "yaml_count": yaml_count, "failures": failures}


def verify_inventory(root: Path, inventory_name: str = "MANIFEST.txt") -> dict[str, Any]:
    listed = {
        line.strip()
        for line in (root / inventory_name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    failures = [f"unlisted: {item}" for item in sorted(actual - listed)]
    failures.extend(f"listed but absent: {item}" for item in sorted(listed - actual))
    return {"listed_files": len(listed), "actual_files": len(actual), "failures": failures}


def validate_reference_package(root: Path, kind: str) -> dict[str, Any]:
    failures: list[str] = []
    syntax = parse_all_json_yaml(root)
    failures.extend(syntax["failures"])
    schema_count = 0
    for path in sorted(root.rglob("*.schema.json")):
        schema_count += 1
        try:
            Draft202012Validator.check_schema(load_json(path))
        except Exception as exc:
            failures.append(f"schema {path.relative_to(root)}: {exc}")
    links = validate_markdown_links(root)
    failures.extend(links["failures"])
    if kind == "louie":
        inventory = verify_manifest(root)
    else:
        inventory = verify_inventory(root)
        docx_paths = sorted(root.glob("*.docx"))
        if len(docx_paths) != 1:
            failures.append(f"expected one DOCX, found {len(docx_paths)}")
        else:
            try:
                with zipfile.ZipFile(docx_paths[0]) as archive:
                    bad_member = archive.testzip()
                    if bad_member:
                        failures.append(f"DOCX corrupt member: {bad_member}")
                    ElementTree.fromstring(archive.read("word/document.xml"))
            except Exception as exc:
                failures.append(f"DOCX parse: {exc}")
    failures.extend(inventory["failures"])
    return {
        "root": str(root),
        "syntax": {key: value for key, value in syntax.items() if key != "failures"},
        "schema_count": schema_count,
        "local_link_count": links["local_link_count"],
        "inventory": {key: value for key, value in inventory.items() if key != "failures"},
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--louie-root", type=Path, required=True)
    parser.add_argument("--pyta-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.document_root.resolve()
    checks = {
        "manifest": verify_manifest(root),
        "syntax": parse_all_json_yaml(root),
        "schemas_and_examples": validate_schemas_and_examples(root),
        "jcs_hashes": validate_jcs_hashes(root),
        "openapi": validate_openapi(root),
        "markdown_links": validate_markdown_links(root),
        "xml": validate_xml(root),
        "louie_reference": validate_reference_package(args.louie_root.resolve(), "louie"),
        "pyta_reference": validate_reference_package(args.pyta_root.resolve(), "pyta"),
    }
    failure_count = sum(len(result["failures"]) for result in checks.values())
    report = {
        "document_root": str(root),
        "status": "PASS" if failure_count == 0 else "FAIL",
        "failure_count": failure_count,
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
