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
STATE = ".analysis-state"


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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def keys(value, allowed, context):
    require(isinstance(value, dict), f"{context} must be an object")
    require(not set(value) - set(allowed), f"Unknown {context} fields: {sorted(set(value) - set(allowed))}")


def validate_rule(rule):
    keys(rule, ("path", "allow_empty", "columns", "unique", "numeric", "min_rows",
                "max_rows", "delimiter", "encoding"), "file rule")
    relative(rule.get("path"))
    require(isinstance(rule.get("allow_empty", False), bool), "allow_empty must be boolean")
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
        errors = []
        rows = 0
        def error(message):
            if len(errors) < 20:
                errors.append(message)
        if not path.is_file():
            error("missing or not a regular file")
        elif path.stat().st_size == 0:
            if not rule.get("allow_empty", False):
                error("zero-byte file")
            if rule.get("min_rows", 0) > 0:
                error("zero rows below min_rows")
        elif path.suffix.lower() in (".csv", ".tsv") or any(
                x in rule for x in ("columns", "unique", "numeric", "min_rows", "max_rows", "delimiter")):
            try:
                delimiter = rule.get("delimiter", "," if path.suffix.lower() == ".csv" else "\t")
                with path.open(encoding=rule.get("encoding", "utf-8-sig"), newline="") as handle:
                    reader = csv.reader(handle, delimiter=delimiter, strict=True)
                    header = next(reader, [])
                    require(header and all(x.strip() for x in header) and len(header) == len(set(header)), "invalid/duplicate header")
                    needed = set(rule.get("columns", [])) | set(rule.get("unique", [])) | set(rule.get("numeric", {}))
                    require(needed <= set(header), f"missing columns: {sorted(needed - set(header))}")
                    seen = set()
                    for row in reader:
                        rows += 1
                        if len(row) != len(header):
                            error(f"row {rows}: wrong field count")
                            continue
                        values = dict(zip(header, row))
                        if rule.get("unique"):
                            identity = tuple(values[x].strip() for x in rule["unique"])
                            if not all(identity) or identity in seen:
                                error(f"row {rows}: missing or duplicate key")
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
                                    error(f"row {rows}: {column} outside range")
                            except ValueError:
                                error(f"row {rows}: {column} is not a finite number")
                    if rows < rule.get("min_rows", 0) or rows > rule.get("max_rows", math.inf):
                        error(f"row count {rows} outside declared bounds")
            except (OSError, UnicodeError, csv.Error, LookupError, WorkflowError) as exc:
                error(str(exc))
        results.append({"path": rule["path"], "ok": not errors, "rows": rows, "errors": errors})
    return results


def assert_checks(root, rules):
    results = check_files(root, rules)
    require(all(x["ok"] for x in results), "File validation failed: " + json.dumps(results, ensure_ascii=False))
    return results


def load_manifest(path):
    root = path.resolve().parent
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
        require(stage.get("outputs"), f"{sid}: outputs are required")
        for rule in stage["outputs"]:
            target = rule["path"].casefold()
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
            name = rule["path"].casefold()
            if name.startswith("output/"):
                producer = owned.get(name[7:])
                require(producer in ancestors[sid], f"{sid}: output input needs a declared upstream producer: {name}")
            require(not name.startswith(STATE + "/"), "State cannot be an analysis input")
        for code in stages[sid]["code"]:
            require(not code.casefold().startswith(("output/", STATE + "/")), "Code cannot be managed output/state")
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
    files = {name: snapshot(root / name) for name in
             sorted(set(stage["code"] + [x["path"] for x in stage.get("inputs", [])]))}
    basis = {"stage": stage, "files": files, "versions": versions,
             "runner": snapshot(Path(__file__)), "python": sys.version,
             "platform": sys.platform, "executable": {"path": executable, **(snapshot(Path(executable)) or {})}}
    return {"signature": digest(basis), "basis": basis}


def receipt_path(root, sid):
    return guarded(root, f"{STATE}/receipts/{sid}.json")


def receipt(root, sid):
    path = receipt_path(root, sid)
    if not path.exists():
        return None
    try:
        value = read_json(path)
        require(isinstance(value, dict) and value.get("version") == VERSION and
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
    result, pending = [], set()
    for sid in order:
        stage = stages[sid]
        fp = fingerprint(root, stage, cache)
        prior = receipt(root, sid)
        reasons = []
        if force:
            reasons.append("forced")
        if any(dep in pending for dep in stage.get("depends", [])):
            reasons.append("upstream scheduled")
        if prior is None:
            reasons.append("no valid receipt")
        else:
            if prior.get("signature") != fp["signature"]:
                reasons.append("input/code/parameter/environment/contract changed")
            expected = {x["path"]: snapshot(guarded(root, "output/" + x["path"])) for x in stage["outputs"]}
            if expected != prior["outputs"] or any(x is None for x in expected.values()):
                reasons.append("output missing or changed")
            upstream = {dep: (receipt(root, dep) or {}).get("run_id") for dep in stage.get("depends", [])}
            if upstream != prior.get("upstream", {}):
                reasons.append("upstream receipt changed")
        if any(value is None for value in fp["basis"]["files"].values()):
            reasons.append("input or code missing")
        if reasons:
            pending.add(sid)
        result.append({"stage": sid, "action": "run" if reasons else "skip", "reasons": reasons})
    return result


def lock(root):
    path = guarded(root, STATE + "/lock.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created": datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError:
        raise WorkflowError("Workflow locked. Confirm the previous process has stopped before recover --confirm-stopped.")
    return path


def rollback(root, transaction, journal):
    """Restore every original, even if termination occurred between rename and journal update."""
    require(journal.get("status") in ("prepared", "committed"), "Invalid transaction status")
    entries = journal.get("entries")
    require(isinstance(entries, list), "Invalid transaction entries")
    for index, item in reversed(list(enumerate(entries))):
        keys(item, ("target", "existed", "original"), "transaction entry")
        name = relative(item["target"])
        require(name.startswith(("output/", STATE + "/receipts/")), "Invalid recovery target")
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
    assert_checks(root, stage.get("inputs", []))
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
        record = {"version": VERSION, "stage": sid, "run_id": transaction.name,
                  "started": started, "completed": datetime.now(timezone.utc).isoformat(),
                  "exit_code": completed.returncode, "signature": before["signature"],
                  "basis": before["basis"], "command": command, "checks": checks,
                  "upstream": {dep: (receipt(root, dep) or {}).get("run_id") for dep in stage.get("depends", [])},
                  "outputs": {x["path"]: snapshot(staged / x["path"]) for x in stage["outputs"]}}
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


def recover(root, confirmed):
    require(confirmed, "Recovery requires --confirm-stopped after confirming no workflow/child process is active")
    guarded(root, STATE)
    base = guarded(root, STATE + "/transactions")
    recovered = []
    if base.exists():
        for folder in sorted(base.iterdir()):
            require(re.fullmatch(r"[0-9a-f]{32}", folder.name), "Unexpected state entry; inspect manually")
            guarded(root, folder.relative_to(root).as_posix())
            journal_path = folder / "journal.json"
            if journal_path.exists():
                journal = read_json(journal_path)
                if journal.get("status") != "committed":
                    rollback(root, folder, journal)
                    journal["status"] = "committed"
                    write_json(journal_path, journal)
                    recovered.append(folder.name)
    marker = guarded(root, STATE + "/lock.json")
    if marker.exists():
        marker.unlink()
    return {"recovered": recovered, "note": "Failure/staging evidence retained; previous outputs restored where needed."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check", help="Check files from a JSON contract; no project writes")
    check.add_argument("contract", type=Path)
    check.add_argument("--root", type=Path)
    for name in ("plan", "run", "recover"):
        item = sub.add_parser(name)
        item.add_argument("manifest", type=Path)
        if name in ("plan", "run"):
            item.add_argument("--target", action="append", help="Include this stage and its ancestors; repeatable")
            item.add_argument("--force", action="store_true")
        else:
            item.add_argument("--confirm-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            contract = read_json(args.contract)
            keys(contract, ("files",), "contract")
            require(isinstance(contract.get("files"), list) and contract["files"], "files must be a nonempty list")
            results = check_files((args.root or args.contract.resolve().parent).resolve(), contract["files"])
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(x["ok"] for x in results) else 1
        if args.action == "recover":
            print(json.dumps(recover(args.manifest.resolve().parent, args.confirm_stopped), ensure_ascii=False, indent=2))
            return 0
        root, stages, order, ancestors = load_manifest(args.manifest)
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
            decisions = plan(root, stages, order, cache, args.force)
            for decision in decisions:
                print(json.dumps(decision, ensure_ascii=False), flush=True)
                if decision["action"] == "run":
                    execute_stage(root, stages[decision["stage"]], {})
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
