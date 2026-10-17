"""Verify the ZIP actually runs outside the checkout without hidden dependencies."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tools.build_validation_skill import build


class PackageTests(unittest.TestCase):
    def test_extracted_skill_runs_without_checkout_imports_and_manifest_matches(self):
        path = build()
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as archive:
            archive.extractall(tmp)
            root = Path(tmp)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            for name, digest in manifest["files"].items():
                self.assertEqual(sha256((root / name).read_bytes()).hexdigest(), digest)
            command = [sys.executable, "-I", str(root / "scripts" / "run.py")]
            result = subprocess.run(command + ["demo"], cwd=tmp, capture_output=True, text=True,
                                    encoding="utf-8", check=True, timeout=20)
            data = json.loads(result.stdout)
            self.assertEqual(len(data["results"]), 4)
            self.assertTrue(all(item["mode"] == "simulated" for item in data["results"]))
            result = subprocess.run(command + ["validate", "--recipe", str(root / "assets" / "lunch.recipe.json")],
                                    cwd=tmp, capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
            self.assertFalse(json.loads(result.stdout)["official_source_verified"])
            review = root / "review.json"
            result = subprocess.run(command + ["review", "--intent", str(root / "assets/flexible-cup.intent.json"),
                                               "--output", str(review)], cwd=tmp, capture_output=True,
                                    text=True, encoding="utf-8", check=True, timeout=20)
            conditions = json.loads(review.read_text(encoding="utf-8"))
            self.assertFalse(conditions["confirmed"])
            self.assertEqual(conditions["must_keep"]["slots"][1]["options"], {"糖": "无糖"})

    def test_invalid_import_returns_failure_without_echoing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = Path(tmp) / "invalid.json"
            recipe.write_text('{"token": "DO-NOT-ECHO"}', encoding="utf-8")
            runner = Path(__file__).resolve().parents[1] / "skills" / "mcfuke-validation" / "scripts" / "run.py"
            result = subprocess.run([sys.executable, "-I", str(runner), "validate", "--recipe", str(recipe)],
                                    cwd=tmp, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("DO-NOT-ECHO", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
