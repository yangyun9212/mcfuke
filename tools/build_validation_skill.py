"""Build an allowlisted, self-contained skill ZIP without local account data."""

from hashlib import sha256
import json
import re
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "mcfuke-validation"


def build():
    files = {
        "SKILL.md": SKILL / "SKILL.md",
        "references/live-validation.md": SKILL / "references" / "live-validation.md",
        "references/workflow.md": SKILL / "references" / "workflow.md",
        "references/intent.md": SKILL / "references" / "intent.md",
        "scripts/run.py": SKILL / "scripts" / "run.py",
        "assets/lunch.recipe.json": ROOT / "examples" / "lunch.recipe.json",
        "assets/flexible-cup.intent.json": ROOT / "examples" / "flexible-cup.intent.json",
    }
    for name in ("__init__.py", "__main__.py", "recipe.py", "workflow.py", "cards.py", "intent.py"):
        files[f"scripts/mcfuke/{name}"] = ROOT / "mcfuke" / name
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid version")
    manifest = {"format": "mcfuke.skill-manifest.v1", "version": version, "status": "read-only-release-candidate",
                "files": {path: sha256(source.read_bytes()).hexdigest() for path, source in files.items()}}
    destination = ROOT / "dist" / f"mcfuke-validation-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, source in files.items():
            archive.writestr(path, source.read_bytes())
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return destination


if __name__ == "__main__":
    print(build())
