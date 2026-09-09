#!/usr/bin/env python3
"""Optional scientific workflow helpers; Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone

VERSION = 1
RECEIPT_VERSION = 2
LEGACY_STATE = ".analysis-state"
STATE = "output/.analysis-state"


class WorkflowError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def read_json(path):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path, value):
    """Replace one JSON file; this does not promise power-loss durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def relative(value):
    require(isinstance(value, str) and value, "Expected a nonempty relative path")
    require("\\" not in value and ":" not in value, f"Use portable '/' relative paths: {value}")
    parts = PurePosixPath(value).parts
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in value.split("/")),
            f"Unsafe relative path: {value}")
    require(all(not p.endswith((" ", ".")) for p in parts), f"Ambiguous path: {value}")
    require(all(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", p) for p in parts),
            f"Reserved filename: {value}")
    return value


def guarded(root, value):
    """Managed writes reject symlinks and Windows junction/reparse components."""
    relative(value)
    path = root
    for part in value.split("/"):
        path = path / part
        if os.path.lexists(path):
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not
                    (getattr(info, "st_file_attributes", 0) & 0x400),
                    f"Managed path must not be a link/reparse point: {path}")
    require(path.resolve().is_relative_to(root.resolve()), f"Path escapes root: {value}")
    return path


def snapshot(path):
    if not path.is_file():
        return None
    identity = file_identity(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    require(identity == file_identity(path), f"File changed while hashing: {path}")
    return {"size": identity[2], "sha256": digest.hexdigest()}


def table_rule(rule):
    return Path(rule["path"]).suffix.lower() in (".csv", ".tsv") or any(
        key in rule for key in ("columns", "unique", "numeric", "min_rows", "max_rows", "delimiter"))


def validation_level(rule):
    return rule.get("validation", "full" if table_rule(rule) else "exists")


def file_identity(path):
    """Cache invalidation hint, never persisted as a cryptographic integrity claim."""
    if not path.is_file():
        return None
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def phase_snapshot(path, cache):
    """Reuse a hash only within one read phase, with fresh filesystem metadata."""
    identity = file_identity(path)
    if identity is None:
        return None
    key = ("sha256", str(path.resolve()), identity)
    if key not in cache:
        cache[key] = snapshot(path)
        require(identity == file_identity(path), f"File changed while hashing: {path}")
    return cache[key]


def external_claim(root, rule):
    path = root / rule["path"]
    checksum = root / rule["checksum"]
    require(checksum.is_file(), f"Checksum file missing: {rule['checksum']}")
    require(checksum.stat().st_size <= 65536, "Checksum sidecar must be a small single-file checksum")
    raw = checksum.read_bytes()
    text = raw.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    require(len(lines) == 1, "Checksum sidecar must contain exactly one digest/filename record")
    match = re.fullmatch(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{64}) [ *](.+)", lines[0])
    require(match is not None, "Expected GNU md5sum/sha256sum format: digest, space, space-or-*, filename")
    value, name = match.groups()
    relative(name)
    require((checksum.parent / name).resolve() == path.resolve(),
            "Checksum filename does not refer to the declared data file")
    require(checksum.resolve() != path.resolve(), "Data file cannot be its own checksum")
    return {"algorithm": "md5" if len(value) == 32 else "sha256", "digest": value.lower(),
            "sidecar_sha256": hashlib.sha256(raw).hexdigest()}


def file_fingerprint(root, rule, cache):
    path = root / rule["path"]
    identity = file_identity(path)
    if identity is None:
        return None
    mode = rule.get("fingerprint", "sha256")
    if mode == "sha256":
        return {"mode": mode, "content_verified": True, **phase_snapshot(path, cache)}
    metadata = {"size": identity[2], "mtime_ns": identity[3]}
    if mode == "stat":
        return {"mode": mode, "content_verified": False, **metadata}
    claim = external_claim(root, rule)
    result = {"mode": mode, "content_verified": False, **metadata,
              "checksum": rule["checksum"], **claim}
    if rule.get("verify_checksum", False):
        if claim["algorithm"] == "sha256":
            actual = phase_snapshot(path, cache)["sha256"]
        else:
            key = ("md5", str(path.resolve()), identity)
            if key not in cache:
                hasher = hashlib.md5()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                require(identity == file_identity(path), f"File changed while verifying checksum: {path}")
                cache[key] = hasher.hexdigest()
            actual = cache[key]
        require(actual == claim["digest"], f"Checksum mismatch: {rule['path']}")
        result["content_verified"] = True
    require(identity == file_identity(path), f"File changed while reading fingerprint: {path}")
    return result


def output_fingerprints(root, stage, cache):
    for rule in stage["outputs"]:
        guarded(root, rule["path"])
        if "checksum" in rule:
            guarded(root, rule["checksum"])
    return {rule["path"]: file_fingerprint(root, rule, cache) for rule in stage["outputs"]}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def keys(value, allowed, context):
    require(isinstance(value, dict), f"{context} must be an object")
    require(not set(value) - set(allowed), f"Unknown {context} fields: {sorted(set(value) - set(allowed))}")


def validate_rule(rule):
    keys(rule, ("path", "allow_empty", "columns", "unique", "numeric", "min_rows",
                "max_rows", "delimiter", "encoding", "fingerprint", "checksum",
                "verify_checksum", "validation"), "file rule")
    relative(rule.get("path"))
    require(isinstance(rule.get("allow_empty", False), bool), "allow_empty must be boolean")
    require(rule.get("fingerprint", "sha256") in ("sha256", "stat", "external"), "Unknown fingerprint mode")
    if rule.get("fingerprint") == "external":
        relative(rule.get("checksum"))
        require(rule["checksum"] != rule["path"], "Checksum cannot be the data file")
        require(isinstance(rule.get("verify_checksum", False), bool), "verify_checksum must be boolean")
    else:
        require(not ({"checksum", "verify_checksum"} & set(rule)), "Checksum fields require external mode")
    require(validation_level(rule) in ("exists", "header", "full"), "Unknown validation level")
    forbidden = {"unique", "numeric", "min_rows", "max_rows"} if validation_level(rule) == "header" else (
        {"columns", "unique", "numeric", "min_rows", "max_rows", "delimiter"} if validation_level(rule) == "exists" else set())
    require(not forbidden.intersection(rule), f"Validation level conflicts with fields: {sorted(forbidden.intersection(rule))}")

    for field in ("columns", "unique"):
        values = rule.get(field, [])
        require(isinstance(values, list) and all(isinstance(x, str) and x for x in values)
                and len(values) == len(set(values)), f"{field} must list distinct column names")
    for field in ("min_rows", "max_rows"):
        if field in rule:
            require(type(rule[field]) is int and rule[field] >= 0, f"{field} must be a nonnegative integer")
    require(rule.get("max_rows", math.inf) >= rule.get("min_rows", 0), "max_rows is below min_rows")
    require(isinstance(rule.get("delimiter", "\t"), str) and len(rule.get("delimiter", "\t")) == 1,
            "delimiter must be one character")
    require(isinstance(rule.get("encoding", "utf-8-sig"), str), "encoding must be a string")
    require(isinstance(rule.get("numeric", {}), dict), "numeric must be an object")
    for column, limits in rule.get("numeric", {}).items():
        require(isinstance(column, str) and column, "numeric column must have a name")
        keys(limits, ("min", "max", "allow_missing"), "numeric constraint")
        require(isinstance(limits.get("allow_missing", False), bool), "allow_missing must be boolean")
        for bound in ("min", "max"):
            if bound in limits:
                require(type(limits[bound]) in (int, float) and math.isfinite(limits[bound]),
                        f"{bound} must be finite")
        require(limits.get("min", -math.inf) <= limits.get("max", math.inf), "numeric min exceeds max")


def check_files(root, rules):
    results = []
    for rule in rules:
        validate_rule(rule)
        path = root / rule["path"]
        level = validation_level(rule)
        errors, completed = [], []
        rows, scanned = None, 0
        def error(message):
            if len(errors) < 20:
                errors.append(message)
        completed.append("exists")
        if not path.is_file():
            error("missing or not a regular file")
        elif path.stat().st_size == 0:
            completed.append("size")
            if not rule.get("allow_empty", False):
                error("zero-byte file")
            rows = 0
            if rule.get("min_rows", 0) > 0:
                error("zero rows below min_rows")
        else:
            completed.append("size")
            if level != "exists":
                try:
                    delimiter = rule.get("delimiter", "," if path.suffix.lower() == ".csv" else "\t")
                    with path.open(encoding=rule.get("encoding", "utf-8-sig"), newline="") as handle:
                        reader = csv.reader(handle, delimiter=delimiter, strict=True)
                        header = next(reader, [])
                        completed.append("header")
                        require(header and all(x.strip() for x in header) and len(header) == len(set(header)),
                                "invalid/duplicate header")
                        needed = set(rule.get("columns", [])) | set(rule.get("unique", [])) | set(rule.get("numeric", {}))
                        completed.append("columns")
                        require(needed <= set(header), f"missing columns: {sorted(needed - set(header))}")
                        if level == "full":
                            seen = set()
                            for row in reader:
                                scanned += 1
                                if len(row) != len(header):
                                    error(f"row {scanned}: wrong field count")
                                    continue
                                values = dict(zip(header, row))
                                if rule.get("unique"):
                                    identity = tuple(values[x].strip() for x in rule["unique"])
                                    if not all(identity) or identity in seen:
                                        error(f"row {scanned}: missing or duplicate key")
                                    seen.add(identity)
                                for column, limits in rule.get("numeric", {}).items():
                                    raw = values[column].strip()
                                    if raw.lower() in ("", "na", "n/a", "null") and limits.get("allow_missing", False):
                                        continue
                                    try:
                                        number = float(raw)
                                        if not math.isfinite(number):
                                            raise ValueError()
                                        if number < limits.get("min", -math.inf) or number > limits.get("max", math.inf):
                                            error(f"row {scanned}: {column} outside range")
                                    except ValueError:
                                        error(f"row {scanned}: {column} is not a finite number")
                            rows = scanned
                            completed.extend(["field_count", "row_count"])
                            if rule.get("unique"):
                                completed.append("unique")
                            if rule.get("numeric"):
                                completed.append("numeric")
                            if rows < rule.get("min_rows", 0) or rows > rule.get("max_rows", math.inf):
                                error(f"row count {rows} outside declared bounds")
                except (OSError, UnicodeError, csv.Error, LookupError, WorkflowError) as exc:
                    error(str(exc))
        checks = ("header", "columns", "field_count", "row_count", "unique", "numeric")
        results.append({"path": rule["path"], "validation": level, "ok": not errors,
                        "rows": rows, "rows_scanned": scanned, "completed_checks": completed,
                        "not_performed": [x for x in checks if x not in completed], "errors": errors})
    return results


def assert_checks(root, rules):
    results = check_files(root, rules)
    require(all(x["ok"] for x in results), "File validation failed: " + json.dumps(results, ensure_ascii=False))
    return results


def project_root(path, root=None):
    root = (root if root is not None else path.resolve().parent).resolve()
    require(root.is_dir(), f"Project root is not a directory: {root}")
    return root


def require_current_state(root):
    require(not os.path.lexists(root / LEGACY_STATE),
            "Legacy .analysis-state exists; no state was migrated or ignored. "
            "Inspect it and, only after confirming all workflow/child processes stopped, use "
            "recover --legacy-state --confirm-stopped with the same --root. "
            "Resolve the legacy layout explicitly before using plan/run.")


def load_manifest(path, root=None):
    root = project_root(path, root)
    cfg = read_json(path)
    keys(cfg, ("version", "stages"), "manifest")
    require(cfg.get("version") == VERSION and type(cfg["version"]) is int, "Unsupported manifest version")
    require(isinstance(cfg.get("stages"), list) and cfg["stages"], "stages must be a nonempty list")
    stages = {}
    owned = {}
    for stage in cfg["stages"]:
        keys(stage, ("id", "depends", "inputs", "code", "params", "versions", "command",
                     "outputs", "timeout"), "stage")
        sid = stage.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", sid), "Invalid stage id")
        require(sid not in stages, f"Duplicate stage: {sid}")
        require(isinstance(stage.get("command"), list) and stage["command"] and
                all(isinstance(x, str) and x for x in stage["command"]), f"{sid}: command must be an argv list")
        for field in ("depends", "code"):
            values = stage.get(field, [])
            require(isinstance(values, list) and all(isinstance(x, str) for x in values)
                    and len(values) == len(set(values)), f"{sid}: invalid {field}")
        require(stage.get("code"), f"{sid}: explicitly list code files")
        for code in stage["code"]:
            relative(code)
        require(isinstance(stage.get("params", {}), dict), f"{sid}: params must be an object")
        require(isinstance(stage.get("versions", {}), dict), f"{sid}: versions must be an object")
        for label, argv in stage.get("versions", {}).items():
            require(isinstance(label, str) and isinstance(argv, list) and argv
                    and all(isinstance(x, str) and x for x in argv), f"{sid}: invalid version probe")
        if "timeout" in stage:
            require(type(stage["timeout"]) in (int, float) and math.isfinite(stage["timeout"])
                    and stage["timeout"] > 0, f"{sid}: invalid timeout")
        for field in ("inputs", "outputs"):
            require(isinstance(stage.get(field, []), list), f"{sid}: {field} must be a list")
            for rule in stage.get(field, []):
                validate_rule(rule)
        input_names = [rule["path"].casefold() for rule in stage.get("inputs", [])]
        require(len(input_names) == len(set(input_names)), f"{sid}: duplicate input declarations")
        for rule in stage.get("inputs", []):
            if rule["path"].casefold() in {name.casefold() for name in stage["code"]}:
                require(rule.get("fingerprint", "sha256") == "sha256", "Code files must use SHA-256")
        require(stage.get("outputs"), f"{sid}: outputs are required")
        output_names = {rule["path"].casefold() for rule in stage["outputs"]}
        for rule in stage["outputs"]:
            if "checksum" in rule:
                require(rule["checksum"].casefold() in output_names, "Output checksum must also be a declared output of this stage")
        for rule in stage["outputs"]:
            target = rule["path"].casefold()
            require(target.split("/", 1)[0] != LEGACY_STATE,
                    f"Output overlaps protected runner state: {rule['path']}")
            require(all(target != prior and not target.startswith(prior + "/")
                        and not prior.startswith(target + "/") for prior in owned),
                    f"Overlapping output ownership: {target}")
            owned[target] = sid
            guarded(root, "output/" + rule["path"])
        stages[sid] = stage
    order, visiting = [], set()
    def visit(sid):
        require(sid in stages, f"Unknown dependency: {sid}")
        require(sid not in visiting, f"Dependency cycle at {sid}")
        if sid in order:
            return
        visiting.add(sid)
        for dep in stages[sid].get("depends", []):
            visit(dep)
        visiting.remove(sid)
        order.append(sid)
    for sid in stages:
        visit(sid)
    ancestors = {}
    for sid in order:
        ancestors[sid] = set(stages[sid].get("depends", []))
        for dep in stages[sid].get("depends", []):
            ancestors[sid].update(ancestors[dep])
        for rule in stages[sid].get("inputs", []):
            for source in [rule["path"]] + ([rule["checksum"]] if "checksum" in rule else []):
                name = source.casefold()
                if name.startswith("output/"):
                    producer = owned.get(name[7:])
                    require(producer in ancestors[sid], f"{sid}: output input needs a declared upstream producer: {name}")
                require(not any(name == state or name.startswith(state + "/")
                                for state in (STATE, LEGACY_STATE)), "State cannot be an analysis input")
        for code in stages[sid]["code"]:
            require(not code.casefold().startswith(("output/", LEGACY_STATE + "/"))
                    and code.casefold() not in ("output", LEGACY_STATE), "Code cannot be managed output/state")
    return root, stages, order, ancestors


def expand(argv, root, output=None):
    expanded = []
    for arg in argv:
        arg = arg.replace("{python}", sys.executable).replace("{root}", str(root))
        if output is not None:
            arg = arg.replace("{output}", str(output))
        expanded.append(arg)
    if not Path(expanded[0]).is_absolute() and ("/" in expanded[0] or "\\" in expanded[0]):
        expanded[0] = str(root / expanded[0])
    executable = shutil.which(expanded[0])
    require(executable is not None, f"Executable unavailable: {expanded[0]}")
    expanded[0] = str(Path(executable).resolve())
    return expanded


def fingerprint(root, stage, cache):
    versions = {}
    for label, argv in stage.get("versions", {}).items():
        command = expand(argv, root)
        key = tuple(command)
        if key not in cache:
            completed = subprocess.run(command, cwd=root, capture_output=True, timeout=15,
                                       encoding="utf-8", errors="replace", shell=False)
            require(completed.returncode == 0, f"Version probe failed: {label}")
            cache[key] = {"command": command, "version": (completed.stdout + completed.stderr).strip()}
        versions[label] = cache[key]
    executable = expand(stage["command"], root, root / "output")[0]
    rules = {name: {"path": name} for name in stage["code"]}
    rules.update({x["path"]: x for x in stage.get("inputs", [])})
    files = {name: file_fingerprint(root, rule, cache) for name, rule in sorted(rules.items())}
    basis = {"stage": stage, "files": files, "versions": versions,
             "runner": phase_snapshot(Path(__file__), cache), "python": sys.version,
             "platform": sys.platform, "executable": {"path": executable, **(phase_snapshot(Path(executable), cache) or {})}}
    return {"signature": digest(basis), "basis": basis}


def receipt_path(root, sid):
    return guarded(root, f"{STATE}/receipts/{sid}.json")


def receipt(root, sid):
    path = receipt_path(root, sid)
    if not path.exists():
        return None
    try:
        value = read_json(path)
        require(isinstance(value, dict) and value.get("version") == RECEIPT_VERSION and
                value.get("stage") == sid and isinstance(value.get("outputs"), dict)
                and value.get("run_id"), f"Invalid receipt: {sid}")
        return value
    except (ValueError, WorkflowError):
        return None


def targets(order, ancestors, selected):
    if not selected:
        return order
    require(set(selected) <= set(order), "Unknown target stage")
    needed = set(selected)
    for sid in selected:
        needed.update(ancestors[sid])
    return [sid for sid in order if sid in needed]


def plan(root, stages, order, cache, force=False):
    require_current_state(root)
    result, pending = [], set()
    for sid in order:
        stage = stages[sid]
        weaker = [rule["path"] for rule in stage.get("inputs", []) + stage["outputs"]
                  if rule.get("fingerprint", "sha256") == "stat" or
                  (rule.get("fingerprint") == "external" and not rule.get("verify_checksum", False))]
        # Stale upstream data cannot validate a future downstream run.
        if any(dep in pending for dep in stage.get("depends", [])):
            pending.add(sid)
            result.append({"stage": sid, "action": "run",
                           "reasons": ["upstream scheduled; recheck after upstream"],
                           "weaker_fingerprints": weaker})
            continue
        fp = fingerprint(root, stage, cache)
        prior = receipt(root, sid)
        reasons = []
        if force:
            reasons.append("forced")
        if prior is None:
            reasons.append("no valid receipt")
        else:
            if prior.get("signature") != fp["signature"]:
                reasons.append("input/code/parameter/environment/contract changed")
            guarded(root, "output")
            try:
                expected = output_fingerprints(root / "output", stage, cache)
                if expected != prior["outputs"] or any(x is None for x in expected.values()):
                    reasons.append("output missing or changed")
            except WorkflowError as exc:
                reasons.append(f"output fingerprint invalid: {exc}")
            upstream = {dep: (receipt(root, dep) or {}).get("run_id") for dep in stage.get("depends", [])}
            if upstream != prior.get("upstream", {}):
                reasons.append("upstream receipt changed")
        if any(value is None for value in fp["basis"]["files"].values()):
            reasons.append("input or code missing")
        if reasons:
            pending.add(sid)
        result.append({"stage": sid, "action": "run" if reasons else "skip", "reasons": reasons,
                       "weaker_fingerprints": weaker})
    return result


def lock(root):
    require_current_state(root)
    path = guarded(root, STATE + "/lock.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created": datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError:
        raise WorkflowError("Workflow locked. Confirm the previous process has stopped before recover --confirm-stopped.")
    return path


def rollback(root, transaction, journal, state=STATE):
    """Restore every original, even if termination occurred between rename and journal update."""
    require(journal.get("status") in ("prepared", "committed"), "Invalid transaction status")
    entries = journal.get("entries")
    require(isinstance(entries, list), "Invalid transaction entries")
    for index, item in reversed(list(enumerate(entries))):
        keys(item, ("target", "existed", "original"), "transaction entry")
        name = relative(item["target"])
        target = name.casefold()
        is_receipt = name.startswith(state + "/receipts/")
        is_output = name.startswith("output/") and not (
            target == STATE or target.startswith(STATE + "/"))
        require(is_receipt or is_output, "Invalid recovery target")
        destination = guarded(root, name)
        saved = guarded(root, transaction.relative_to(root).as_posix() + f"/backup/{index}")
        if item["existed"]:
            if saved.is_file():
                require(snapshot(saved) == item.get("original"), "Recovery backup changed; inspect manually")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(saved, destination)
            else:
                require(snapshot(destination) == item.get("original") and item.get("original") is not None,
                        "Original and usable backup unavailable; inspect manually")
        elif destination.exists():
            require(destination.is_file(), "Recovery target is not a file")
            destination.unlink()


def remove_owned(root, path):
    """Only remove an exact helper-owned transaction after checking its resolved location."""
    base = guarded(root, STATE + "/transactions")
    require(path.parent == base and re.fullmatch(r"[0-9a-f]{32}", path.name), "Invalid transaction directory")
    guarded(root, path.relative_to(root).as_posix())
    require(path.resolve().is_relative_to(base.resolve()), "Transaction escapes state")
    # Refuse links anywhere before recursive deletion, including Windows junctions.
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            guarded(root, (Path(folder) / name).relative_to(root).as_posix())
    shutil.rmtree(path)


def publish(root, transaction, staged, stage, record):
    replacements = [("output/" + x["path"], staged / x["path"]) for x in stage["outputs"]]
    staged_receipt = transaction / "receipt.json"
    write_json(staged_receipt, record)
    replacements.append((f"{STATE}/receipts/{stage['id']}.json", staged_receipt))
    journal = {"status": "prepared", "entries": []}
    for name, source in replacements:
        destination = guarded(root, name)
        require(not destination.exists() or destination.is_file(), f"Output target is not a file: {name}")
        require(source.is_file(), f"Missing staged file: {source}")
        journal["entries"].append({"target": name, "existed": destination.exists(),
                                   "original": snapshot(destination)})
    (transaction / "backup").mkdir()
    journal_path = transaction / "journal.json"
    write_json(journal_path, journal)
    try:
        for index, (name, source) in enumerate(replacements):
            destination = guarded(root, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                os.replace(destination, transaction / "backup" / str(index))
            os.replace(source, destination)
        journal["status"] = "committed"
        write_json(journal_path, journal)
    except BaseException:
        rollback(root, transaction, journal)
        journal["status"] = "committed"  # Rollback completed; no pending transaction remains.
        write_json(journal_path, journal)
        raise


def execute_stage(root, stage, cache):
    sid = stage["id"]
    input_checks = assert_checks(root, stage.get("inputs", []))
    before = fingerprint(root, stage, cache)
    require(all(x is not None for x in before["basis"]["files"].values()), f"{sid}: missing input/code")
    transaction = guarded(root, f"{STATE}/transactions/{uuid.uuid4().hex}")
    staged = transaction / "output"
    staged.mkdir(parents=True)
    command = expand(stage["command"], root, staged)
    environment = os.environ.copy()
    environment.update(ANALYSIS_OUTPUT_DIR=str(staged), ANALYSIS_ROOT=str(root),
                       ANALYSIS_PARAMS=json.dumps(stage.get("params", {}), ensure_ascii=False, allow_nan=False))
    started = datetime.now(timezone.utc).isoformat()
    try:
        # Commands must run synchronously and propagate child failures. This is not a sandbox.
        completed = subprocess.run(command, cwd=root, env=environment, timeout=stage.get("timeout"), shell=False)
        require(completed.returncode == 0, f"{sid}: process exited {completed.returncode}")
        for rule in stage["outputs"]:
            guarded(root, (staged / rule["path"]).relative_to(root).as_posix())
        checks = assert_checks(staged, stage["outputs"])
        after = fingerprint(root, stage, {})  # Fresh probes detect environment changes during execution.
        require(before["signature"] == after["signature"], f"{sid}: inputs/code/environment changed during run")
        record = {"version": RECEIPT_VERSION, "stage": sid, "run_id": transaction.name,
                  "started": started, "completed": datetime.now(timezone.utc).isoformat(),
                  "exit_code": completed.returncode, "signature": before["signature"],
                  "basis": before["basis"], "command": command, "checks": checks, "input_checks": input_checks,
                  "upstream": {dep: (receipt(root, dep) or {}).get("run_id") for dep in stage.get("depends", [])},
                  "outputs": output_fingerprints(staged, stage, {})}
        publish(root, transaction, staged, stage, record)
    except BaseException as exc:
        write_json(transaction / "failure.json", {"stage": sid, "error": str(exc), "started": started})
        raise
    else:
        remove_owned(root, transaction)


def unresolved(root):
    base = guarded(root, STATE + "/transactions")
    if not base.exists():
        return []
    pending = []
    for folder in base.iterdir():
        guarded(root, folder.relative_to(root).as_posix())
        journal = folder / "journal.json"
        if journal.exists() and read_json(journal).get("status") != "committed":
            pending.append(folder.name)
    return pending


def recover(root, confirmed, legacy_state=False):
    require(confirmed, "Recovery requires --confirm-stopped after confirming no workflow/child process is active")
    if legacy_state:
        require(not os.path.lexists(root / STATE),
                "Both state layouts exist; inspect manually before legacy recovery to avoid overwriting newer outputs")
        require(os.path.lexists(root / LEGACY_STATE), "No legacy .analysis-state exists")
    else:
        require_current_state(root)
    state = LEGACY_STATE if legacy_state else STATE
    guarded(root, state)
    base = guarded(root, state + "/transactions")
    recovered = []
    if base.exists():
        for folder in sorted(base.iterdir()):
            require(re.fullmatch(r"[0-9a-f]{32}", folder.name), "Unexpected state entry; inspect manually")
            guarded(root, folder.relative_to(root).as_posix())
            journal_path = folder / "journal.json"
            if journal_path.exists():
                journal = read_json(journal_path)
                if journal.get("status") != "committed":
                    rollback(root, folder, journal, state)
                    journal["status"] = "committed"
                    write_json(journal_path, journal)
                    recovered.append(folder.name)
    marker = guarded(root, state + "/lock.json")
    if marker.exists():
        marker.unlink()
    note = "Failure/staging evidence retained; previous outputs restored where needed."
    if legacy_state:
        note += " Legacy state retained; plan/run remain blocked until the legacy layout is explicitly resolved."
    return {"recovered": recovered, "state": state, "note": note}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check", help="Check files from a JSON contract; no project writes")
    check.add_argument("contract", type=Path)
    check.add_argument("--root", type=Path)
    for name in ("plan", "run", "recover"):
        item = sub.add_parser(name)
        item.add_argument("manifest", type=Path)
        item.add_argument("--root", type=Path, help="Project root; defaults to the manifest's parent directory")
        if name in ("plan", "run"):
            item.add_argument("--target", action="append", help="Include this stage and its ancestors; repeatable")
            item.add_argument("--force", action="store_true")
        else:
            item.add_argument("--confirm-stopped", action="store_true")
            item.add_argument("--legacy-state", action="store_true",
                              help="Explicitly recover root .analysis-state without migrating or deleting it")
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            contract = read_json(args.contract)
            keys(contract, ("files",), "contract")
            require(isinstance(contract.get("files"), list) and contract["files"], "files must be a nonempty list")
            results = check_files((args.root or args.contract.resolve().parent).resolve(), contract["files"])
            phase = {}
            for rule, result in zip(contract["files"], results):
                if "fingerprint" in rule:
                    result["fingerprint"] = file_fingerprint(
                        (args.root or args.contract.resolve().parent).resolve(), rule, phase)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(x["ok"] for x in results) else 1
        if args.action == "recover":
            print(json.dumps(recover(project_root(args.manifest, args.root), args.confirm_stopped,
                                     args.legacy_state), ensure_ascii=False, indent=2))
            return 0
        root, stages, order, ancestors = load_manifest(args.manifest, args.root)
        require_current_state(root)
        require(not guarded(root, STATE + "/lock.json").exists() and not unresolved(root),
                "Unfinished/active workflow; inspect it before recovery")
        order = targets(order, ancestors, args.target)
        cache = {}
        if args.action == "plan":
            print(json.dumps(plan(root, stages, order, cache, args.force), ensure_ascii=False, indent=2))
            return 0
        marker = lock(root)
        keep_lock = False
        try:
            for sid in order:
                # Share only this stage's pre-execution phase; prior stages may have changed files.
                phase = {}
                decision = plan(root, stages, [sid], phase, args.force)[0]
                print(json.dumps(decision, ensure_ascii=False), flush=True)
                if decision["action"] == "run":
                    execute_stage(root, stages[sid], phase)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            keep_lock = True
            raise
        finally:
            if not keep_lock and not unresolved(root):
                marker.unlink()
        return 0
    except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; inspect child processes before retry/recovery.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
