"""Exercise a relocated package through its public CLI, without Agent SDKs."""
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1]


class PortabilityTests(unittest.TestCase):
    def exercise_layout(self, host, omit_metadata=False):
        with tempfile.TemporaryDirectory(prefix="skill-portability-") as temporary:
            base = Path(temporary).resolve()
            skill = base / "用户 home" / host / "skills" / SOURCE.name
            shutil.copytree(SOURCE, skill, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".git", ".analysis-state"))
            if omit_metadata:
                (skill / "agents/openai.yaml").unlink()
            original = {p.relative_to(skill): p.read_bytes()
                        for p in skill.rglob("*") if p.is_file()}
            project = base / "科研 project"
            shutil.copytree(skill / "assets/minimal", project)
            caller = base / "unrelated cwd"
            caller.mkdir()
            manifest = project / "workflow.json"
            environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")

            def invoke(action):
                result = subprocess.run(
                    [sys.executable, "-B", str(skill / "scripts/analysis_workflow.py"),
                     action, str(manifest)], cwd=caller, env=environment,
                    capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            self.assertEqual([d["action"] for d in json.loads(invoke("plan"))],
                             ["run", "run"])
            invoke("run")
            with (project / "output/summary.tsv").open(encoding="utf-8", newline="") as table:
                rows = list(csv.DictReader(table, delimiter="\t"))
            self.assertEqual([(r["group"], int(r["n"]), float(r["mean"])) for r in rows],
                             [("A", 2, 3.0), ("B", 2, 7.0)])
            ET.parse(project / "output/means.svg")
            analysis_receipt = project / ".analysis-state/receipts/analysis.json"
            before = analysis_receipt.read_bytes()
            self.assertEqual(json.loads(before)["exit_code"], 0)
            self.assertEqual([d["action"] for d in json.loads(invoke("plan"))],
                             ["skip", "skip"])
            config = json.loads(manifest.read_text(encoding="utf-8"))
            config["stages"][1]["params"]["title"] = "Relocated plot"
            manifest.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual([d["action"] for d in json.loads(invoke("plan"))],
                             ["skip", "run"])
            invoke("run")
            self.assertEqual(analysis_receipt.read_bytes(), before)
            self.assertIn("Relocated plot", (project / "output/means.svg").read_text(encoding="utf-8"))
            self.assertEqual(original, {p.relative_to(skill): p.read_bytes()
                                        for p in skill.rglob("*") if p.is_file()})
            self.assertEqual(list(caller.iterdir()), [])

    def test_claude_layout(self):
        self.exercise_layout(".claude")

    def test_codex_layout(self):
        self.exercise_layout(".agents")

    def test_hermes_layout(self):
        self.exercise_layout(".hermes")

    def test_no_codex_metadata_dependency(self):
        self.exercise_layout(".hermes", omit_metadata=True)


if __name__ == "__main__":
    unittest.main()
