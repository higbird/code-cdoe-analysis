"""Exercise a relocated package through its module entry point, without Agent SDKs."""
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
    def exercise_layout(self, host, omit_metadata=False, skill_from_environment=False):
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
            (project / "output").mkdir(exist_ok=True)  # Version control may omit empty directories.
            caller = base / "unrelated cwd"
            caller.mkdir()
            manifest = project / "input/workflow.json"
            environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            environment.pop("CODE_CDOE_SKILL_ROOT", None)
            if skill_from_environment:
                environment["CODE_CDOE_SKILL_ROOT"] = str(skill)

            def assert_module_root():
                self.assertEqual({path.name for path in project.iterdir()},
                                 {"input", "output", "code.txt", "readme.md"})

            def invoke(action=None, *extra):
                command = [sys.executable, "-B", str(project / "code.txt")]
                if not skill_from_environment:
                    command.extend(("--skill-root", str(skill)))
                if action is not None:
                    command.append(action)
                command.extend(extra)
                result = subprocess.run(
                    command, cwd=caller, env=environment,
                    capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                assert_module_root()
                return result.stdout

            assert_module_root()
            self.assertEqual([d["action"] for d in json.loads(invoke("plan"))],
                             ["run", "run"])
            invoke()  # Omitted action uses the same entry point and defaults to run.
            with (project / "output/summary.tsv").open(encoding="utf-8", newline="") as table:
                rows = list(csv.DictReader(table, delimiter="\t"))
            self.assertEqual([(r["group"], int(r["n"]), float(r["mean"])) for r in rows],
                             [("A", 2, 3.0), ("B", 2, 7.0)])
            ET.parse(project / "output/means.svg")
            analysis_receipt = project / "output/.analysis-state/receipts/analysis.json"
            before = analysis_receipt.read_bytes()
            self.assertEqual(json.loads(before)["exit_code"], 0)
            self.assertEqual([d["action"] for d in json.loads(invoke("plan"))],
                             ["skip", "skip"])
            self.assertEqual([json.loads(line)["action"] for line in invoke("run").splitlines()],
                             ["skip", "skip"])
            self.assertEqual(analysis_receipt.read_bytes(), before)
            self.assertEqual(json.loads(invoke("recover", "--confirm-stopped"))["recovered"], [])
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

    def test_skill_root_environment(self):
        self.exercise_layout(".agents", skill_from_environment=True)


if __name__ == "__main__":
    unittest.main()
