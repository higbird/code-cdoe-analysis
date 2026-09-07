"""Behavior tests use synthetic data in isolated directories; no external tools required."""
import contextlib
import copy
import csv
import shutil
import xml.etree.ElementTree as ET
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis_workflow.py"
spec = importlib.util.spec_from_file_location("workflow", SCRIPT)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow-test-")
        self.root = Path(self.temporary.name).resolve()
        (self.root / "input").mkdir()
        (self.root / "input/data.tsv").write_text("id\tvalue\na\t1\nb\t2\n", encoding="utf-8")
        (self.root / "analysis.py").write_text(
            "import os,json,csv\n"
            "from pathlib import Path\n"
            "p=json.loads(os.environ['ANALYSIS_PARAMS'])\n"
            "rows=list(csv.DictReader(open('input/data.tsv'),delimiter='\\t'))\n"
            "value=sum(float(r['value']) for r in rows)*p.get('scale',1)\n"
            "Path(os.environ['ANALYSIS_OUTPUT_DIR'],'stats.tsv').write_text('value\\n'+str(value)+'\\n')\n",
            encoding="utf-8")
        (self.root / "plot.py").write_text(
            "import os,json\nfrom pathlib import Path\n"
            "p=json.loads(os.environ['ANALYSIS_PARAMS'])\n"
            "Path(os.environ['ANALYSIS_OUTPUT_DIR'],'plot.svg').write_text("
            "'<svg xmlns=\"http://www.w3.org/2000/svg\"><text>'+p['label']+"
            "Path('output/stats.tsv').read_text()+'</text></svg>')\n", encoding="utf-8")
        self.cfg = {"version": 1, "stages": [
            {"id": "analysis", "code": ["analysis.py"], "inputs": [
                {"path": "input/data.tsv", "columns": ["id", "value"], "unique": ["id"],
                 "numeric": {"value": {"min": 0}}, "min_rows": 1}],
             "params": {"scale": 1}, "versions": {"python": ["{python}", "--version"]},
             "command": ["{python}", "{root}/analysis.py"],
             "outputs": [{"path": "stats.tsv", "columns": ["value"],
                          "numeric": {"value": {"min": 0}}, "min_rows": 1}]},
            {"id": "plot", "depends": ["analysis"], "code": ["plot.py"],
             "inputs": [{"path": "output/stats.tsv", "columns": ["value"]}],
             "params": {"label": "Total"}, "command": ["{python}", "{root}/plot.py"],
             "outputs": [{"path": "plot.svg"}]}]}
        self.manifest = self.root / "workflow.json"
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        self.manifest.write_text(json.dumps(self.cfg), encoding="utf-8")

    def call(self, action="run", *flags):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return workflow.main([action, str(self.manifest), *flags])

    def decisions(self):
        root, stages, order, ancestors = workflow.load_manifest(self.manifest)
        return workflow.plan(root, stages, order, {})

    def current(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in
                [self.root / "output/stats.tsv", self.root / "output/plot.svg",
                 self.root / ".analysis-state/receipts/analysis.json",
                 self.root / ".analysis-state/receipts/plot.json"] if p.exists()}

    def test_run_skip_and_receipt(self):
        self.assertEqual(self.call(), 0)
        self.assertEqual([x["action"] for x in self.decisions()], ["skip", "skip"])
        record = workflow.receipt(self.root, "analysis")
        self.assertEqual(record["exit_code"], 0)
        self.assertTrue(record["basis"]["files"]["input/data.tsv"]["sha256"])
        self.assertIn("Python", record["basis"]["versions"]["python"]["version"])
        self.assertIn("3.0", (self.root / "output/stats.tsv").read_text())
        before = self.current()
        self.assertEqual(self.call(), 0)
        self.assertEqual(before, self.current())
        self.assertFalse((self.root / ".analysis-state/lock.json").exists())

    def test_plot_parameter_does_not_rerun_analysis(self):
        self.assertEqual(self.call(), 0)
        analysis = workflow.receipt(self.root, "analysis")["run_id"]
        self.cfg["stages"][1]["params"]["label"] = "Updated"
        self.save()
        self.assertEqual([x["action"] for x in self.decisions()], ["skip", "run"])
        self.assertEqual(self.call(), 0)
        self.assertEqual(workflow.receipt(self.root, "analysis")["run_id"], analysis)
        self.assertIn("Updated", (self.root / "output/plot.svg").read_text())

    def test_code_parameter_and_input_changes_propagate(self):
        self.assertEqual(self.call(), 0)
        for mutate in [
            lambda: (self.root / "input/data.tsv").write_text("id\tvalue\na\t4\n"),
            lambda: self.cfg["stages"][0]["params"].update(scale=2),
            lambda: (self.root / "analysis.py").write_text((self.root / "analysis.py").read_text() + "\n# changed\n")]:
            mutate()
            self.save()
            self.assertEqual([x["action"] for x in self.decisions()], ["run", "run"])
            self.assertEqual(self.call(), 0)

    def test_output_tampering_and_target_ancestors(self):
        self.assertEqual(self.call(), 0)
        (self.root / "output/plot.svg").write_text("tampered")
        self.assertEqual([x["action"] for x in self.decisions()], ["skip", "run"])
        self.assertEqual(self.call("run", "--target", "plot"), 0)
        (self.root / "output/stats.tsv").unlink()
        self.assertEqual([x["action"] for x in self.decisions()], ["run", "run"])
        self.assertEqual(self.call("run", "--target", "analysis"), 0)
        self.assertEqual([x["action"] for x in self.decisions()], ["skip", "run"])

    def test_process_failure_preserves_old_outputs_and_receipts(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        (self.root / "analysis.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['ANALYSIS_OUTPUT_DIR'],'stats.tsv').write_text('value\\n99\\n')\n"
            "raise SystemExit(7)\n")
        self.assertEqual(self.call(), 1)
        self.assertEqual(before, self.current())
        self.assertTrue(list((self.root / ".analysis-state/transactions").glob("*/failure.json")))

    def test_missing_output_not_satisfied_by_old_file(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        (self.root / "analysis.py").write_text("pass\n")
        self.assertEqual(self.call(), 1)
        self.assertEqual(before, self.current())

    def test_bad_input_prevents_command(self):
        (self.root / "input/data.tsv").write_text("id\tvalue\na\t1\na\t2\n")
        self.assertEqual(self.call(), 1)
        self.assertFalse((self.root / "output/stats.tsv").exists())

    def test_contract_failure_preserves_old_outputs(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        self.cfg["stages"][0]["outputs"][0]["numeric"]["value"]["max"] = 1
        self.save()
        self.assertEqual(self.call(), 1)
        self.assertEqual(before, self.current())

    def test_input_mutated_during_run_rejected(self):
        (self.root / "analysis.py").write_text((self.root / "analysis.py").read_text() +
            "\nPath('input/data.tsv').write_text('id\\tvalue\\na\\t9\\n')\n")
        self.assertEqual(self.call(), 1)
        self.assertFalse((self.root / "output/stats.tsv").exists())

    def test_publication_error_rolls_back_all_files_and_receipt(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        self.cfg["stages"][0]["params"]["scale"] = 2
        self.save()
        original = os.replace
        fired = False
        def fail_once(source, destination):
            nonlocal fired
            if not fired and str(source).endswith("receipt.json"):
                fired = True
                raise OSError("simulated receipt replacement failure")
            return original(source, destination)
        with mock.patch.object(workflow.os, "replace", side_effect=fail_once):
            self.assertEqual(self.call(), 1)
        self.assertTrue(fired)
        self.assertEqual(before, self.current())

    def test_recover_interrupted_partial_publication_is_idempotent(self):
        output = self.root / "output"
        output.mkdir()
        (output / "stats.tsv").write_text("old")
        transaction = self.root / ".analysis-state/transactions" / ("a" * 32)
        (transaction / "backup").mkdir(parents=True)
        journal = {"status": "prepared", "entries": [
            {"target": "output/stats.tsv", "existed": True, "original": workflow.snapshot(output / "stats.tsv")},
            {"target": "output/new.tsv", "existed": False}]}
        workflow.write_json(transaction / "journal.json", journal)
        os.replace(output / "stats.tsv", transaction / "backup/0")
        (output / "stats.tsv").write_text("new")
        (output / "new.tsv").write_text("partly published")
        workflow.lock(self.root)
        self.assertEqual(self.call("run"), 1)
        self.assertEqual(self.call("recover"), 1)
        self.assertEqual(self.call("recover", "--confirm-stopped"), 0)
        self.assertEqual((output / "stats.tsv").read_text(), "old")
        self.assertFalse((output / "new.tsv").exists())
        self.assertEqual(self.call("recover", "--confirm-stopped"), 0)
        self.assertEqual((output / "stats.tsv").read_text(), "old")

    def test_read_only_plan_and_no_automatic_lock_breaking(self):
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        self.assertEqual(self.call("plan"), 0)
        self.assertEqual(before, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")))
        workflow.lock(self.root)
        self.assertEqual(self.call(), 1)
        self.assertTrue((self.root / ".analysis-state/lock.json").exists())

    def test_invalid_graph_and_ownership_rejected(self):
        original = copy.deepcopy(self.cfg)
        for mutation in [
            lambda: self.cfg["stages"][0].update(depends=["plot"]),
            lambda: self.cfg["stages"][1].update(depends=[]),
            lambda: self.cfg["stages"][1].update(outputs=[{"path": "stats.tsv"}]),
            lambda: self.cfg["stages"][1].update(outputs=[{"path": "../input/data.tsv"}]),
            lambda: self.cfg["stages"][1].update(outputs=[{"path": "STATS.tsv"}]),
            lambda: self.cfg["stages"][1].update(unrecognized=True)]:
            self.cfg = copy.deepcopy(original)
            mutation()
            self.save()
            self.assertEqual(self.call("plan"), 1)

    def test_symlink_output_refused(self):
        external = self.root / "external"
        external.mkdir()
        try:
            (self.root / "output").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit symlink creation")
        self.assertEqual(self.call(), 1)
        self.assertEqual(list(external.iterdir()), [])

    def test_header_only_numeric_nonfinite_and_composite_id(self):
        path = self.root / "input/empty.tsv"
        path.write_text("id\tgroup\tp\n")
        rule = {"path": "input/empty.tsv", "columns": ["id", "group", "p"],
                "unique": ["id", "group"], "numeric": {"p": {"min": 0, "max": 1}}}
        self.assertTrue(workflow.check_files(self.root, [rule])[0]["ok"])
        for data in ["a\tx\tNaN\n", "a\tx\tinf\n", "a\tx\t1.5\n", "a\tx\t0.2\na\tx\t0.4\n"]:
            path.write_text("id\tgroup\tp\n" + data)
            self.assertFalse(workflow.check_files(self.root, [rule])[0]["ok"])
        path.write_text("id\tgroup\tp\na\tx\t0.2\na\ty\t0.4\n")
        self.assertTrue(workflow.check_files(self.root, [rule])[0]["ok"])

    def test_missing_zero_byte_and_malformed_table(self):
        rule = {"path": "input/check.tsv"}
        self.assertFalse(workflow.check_files(self.root, [rule])[0]["ok"])
        (self.root / rule["path"]).touch()
        self.assertFalse(workflow.check_files(self.root, [rule])[0]["ok"])
        self.assertTrue(workflow.check_files(self.root, [{**rule, "allow_empty": True}])[0]["ok"])
        for data in ["id\tid\na\tb\n", "id\tvalue\na\t2\textra\n", "  \tvalue\na\t2\n"]:
            (self.root / rule["path"]).write_text(data)
            self.assertFalse(workflow.check_files(self.root, [rule])[0]["ok"])

    def test_standalone_check_cli(self):
        contract = self.root / "checks.json"
        contract.write_text(json.dumps({"files": self.cfg["stages"][0]["inputs"]}))
        result = subprocess.run([sys.executable, str(SCRIPT), "check", str(contract)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["rows"], 2)

    def test_success_preserves_unrelated_output(self):
        (self.root / "output").mkdir()
        (self.root / "output/user.txt").write_text("keep")
        self.assertEqual(self.call(), 0)
        self.assertEqual((self.root / "output/user.txt").read_text(), "keep")
        self.assertEqual(list((self.root / ".analysis-state/transactions").iterdir()), [])

    def test_force_propagates_even_if_output_content_unchanged(self):
        self.assertEqual(self.call(), 0)
        self.assertEqual(self.call("run", "--force", "--target", "analysis"), 0)
        self.assertEqual([x["action"] for x in self.decisions()], ["skip", "run"])



    def test_multi_file_rollback_removes_new_and_restores_existing(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        self.cfg["stages"][0]["outputs"].append({"path": "extra.txt"})
        self.save()
        (self.root / "analysis.py").write_text((self.root / "analysis.py").read_text() +
            "\nPath(os.environ['ANALYSIS_OUTPUT_DIR'],'extra.txt').write_text('new')\n")
        original = os.replace
        fired = False
        def fail_once(source, destination):
            nonlocal fired
            if not fired and str(source).endswith("receipt.json"):
                fired = True
                raise OSError("interrupted after multiple replacements")
            return original(source, destination)
        with mock.patch.object(workflow.os, "replace", side_effect=fail_once):
            self.assertEqual(self.call(), 1)
        self.assertEqual(before, self.current())
        self.assertFalse((self.root / "output/extra.txt").exists())

    def test_failed_rollback_keeps_lock_and_recovers_later(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        self.cfg["stages"][0]["params"]["scale"] = 5
        self.save()
        original = os.replace
        def fail_publication_and_restore(source, destination):
            if str(source).endswith("receipt.json") or "backup" in Path(source).parts:
                raise OSError("temporary filesystem failure")
            return original(source, destination)
        with mock.patch.object(workflow.os, "replace", side_effect=fail_publication_and_restore):
            self.assertEqual(self.call(), 1)
        self.assertTrue((self.root / ".analysis-state/lock.json").exists())
        self.manifest.write_text("broken manifest")
        self.assertEqual(self.call("recover", "--confirm-stopped"), 0)
        self.assertEqual(before, self.current())

    def test_timeout_keeps_lock_and_formal_output(self):
        self.assertEqual(self.call(), 0)
        before = self.current()
        (self.root / "analysis.py").write_text("import time\ntime.sleep(10)\n")
        self.cfg["stages"][0]["timeout"] = 0.1
        self.save()
        self.assertEqual(self.call(), 1)
        self.assertEqual(before, self.current())
        self.assertTrue((self.root / ".analysis-state/lock.json").exists())
        self.assertEqual(self.call("recover", "--confirm-stopped"), 0)

    def test_environment_probe_change_triggers_rerun(self):
        (self.root / "tool-version.txt").write_text("tool v1")
        self.cfg["stages"][0]["versions"]["tool"] = [
            "{python}", "-c", "from pathlib import Path;print(Path('tool-version.txt').read_text())"]
        self.save()
        self.assertEqual(self.call(), 0)
        (self.root / "tool-version.txt").write_text("tool v2")
        self.assertEqual([x["action"] for x in self.decisions()], ["run", "run"])

    def test_windows_junction_output_refused(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        external = self.root / "junction-target"
        external.mkdir()
        link = self.root / "output"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            self.assertEqual(self.call(), 1)
            self.assertEqual(list(external.iterdir()), [])
        finally:
            os.rmdir(link)  # Removes only this tested junction, never its target directory.

    def test_recovery_refuses_corrupt_backup(self):
        self.assertEqual(self.call(), 0)
        target = self.root / "output/stats.tsv"
        transaction = self.root / ".analysis-state/transactions" / ("b" * 32)
        (transaction / "backup").mkdir(parents=True)
        workflow.write_json(transaction / "journal.json", {"status": "prepared", "entries": [
            {"target": "output/stats.tsv", "existed": True, "original": workflow.snapshot(target)}]})
        os.replace(target, transaction / "backup/0")
        target.write_text("new")
        (transaction / "backup/0").write_text("corrupted")
        workflow.lock(self.root)
        self.assertEqual(self.call("recover", "--confirm-stopped"), 1)
        self.assertTrue((self.root / ".analysis-state/lock.json").exists())

    def test_repeat_recovery_after_interrupted_recovery(self):
        output = self.root / "output"
        output.mkdir()
        (output / "a.txt").write_text("old a")
        (output / "b.txt").write_text("old b")
        transaction = self.root / ".analysis-state/transactions" / ("c" * 32)
        (transaction / "backup").mkdir(parents=True)
        entries = [{"target": "output/" + name, "existed": True,
                    "original": workflow.snapshot(output / name)} for name in ("a.txt", "b.txt")]
        for index, name in enumerate(("a.txt", "b.txt")):
            os.replace(output / name, transaction / "backup" / str(index))
            (output / name).write_text("new")
        workflow.write_json(transaction / "journal.json", {"status": "prepared", "entries": entries})
        workflow.lock(self.root)
        original = os.replace
        def interrupt_second_restore(source, destination):
            if str(source).endswith(str(Path("backup") / "0")):
                raise OSError("interrupted recovery")
            return original(source, destination)
        with mock.patch.object(workflow.os, "replace", side_effect=interrupt_second_restore):
            self.assertEqual(self.call("recover", "--confirm-stopped"), 1)
        self.assertEqual(self.call("recover", "--confirm-stopped"), 0)
        self.assertEqual((output / "a.txt").read_text(), "old a")
        self.assertEqual((output / "b.txt").read_text(), "old b")



    def test_bundled_example_cli_from_other_directory_and_unicode_path(self):
        project = self.root / "中文 project"
        shutil.copytree(SCRIPT.parents[1] / "assets/minimal", project)
        manifest = project / "workflow.json"
        def cli(action):
            return subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), action, str(manifest)],
                                  cwd=self.root, capture_output=True, encoding="utf-8")
        first = cli("run")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        with (project / "output/summary.tsv").open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual([float(row["mean"]) for row in rows], [3, 7])
        self.assertEqual([int(row["n"]) for row in rows], [2, 2])
        tree = ET.parse(project / "output/means.svg")
        labels = [node.text for node in tree.findall(".//{http://www.w3.org/2000/svg}text")]
        self.assertEqual(labels, ["Synthetic group means", "A", "3", "B", "7"])
        self.assertEqual([x["action"] for x in json.loads(cli("plan").stdout)], ["skip", "skip"])
        original = workflow.receipt(project, "analysis")["run_id"]
        cfg = workflow.read_json(manifest)
        cfg["stages"][1]["params"]["title"] = "中文标题"
        workflow.write_json(manifest, cfg)
        self.assertEqual([x["action"] for x in json.loads(cli("plan").stdout)], ["skip", "run"])
        self.assertEqual(cli("run").returncode, 0)
        self.assertEqual(workflow.receipt(project, "analysis")["run_id"], original)
        self.assertIn("中文标题", (project / "output/means.svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
