"""Build an allowlisted public source tree and ZIP, never the whole workspace."""

from hashlib import sha256
import io
import json
from pathlib import Path
import re
import zipfile

from tools.build_validation_skill import build as build_skill
from mcfuke.recipe import load_recipe
from mcfuke.workflow import share_recipe
from mcfuke.intent import share_intent

ROOT = Path(__file__).resolve().parents[1]
DECLARATION_SHA256 = "cd28101bcb29cc8fb24b372fe91ce6b42c4a1487a54e01f7ade0c8c6a84241b7"
PUBLIC_FILES = (
    "README.md", "VERSION", "LICENSE", "NOTICE.md", "CHANGELOG.md", "ROADMAP.md", ".gitignore",
    "CONTEST_DECLARATION.md", "MCP_INTEGRATION.md", "mcp-config.example.json", "workbuddy.md",
    "docs/INSTALL.md", "docs/VALIDATION.md", "docs/CONTEST_SUBMISSION.md", "docs/DEMO.md",
    "docs/assets/result-overbudget.png", "docs/assets/result-overbudget.svg",
    "docs/assets/result-conditions-simulated.png", "docs/assets/result-conditions-simulated.svg",
    "docs/assets/result-conditions-workbuddy.png",
    "mcfuke/__init__.py", "mcfuke/__main__.py", "mcfuke/recipe.py", "mcfuke/workflow.py", "mcfuke/cards.py", "mcfuke/intent.py",
    "examples/lunch.recipe.json", "examples/grilled-chicken.recipe.json", "examples/replacement.recipe.json",
    "examples/sugar-free-flex-cup.recipe.json", "examples/flexible-cup.intent.json",
    "skills/mcfuke-validation/SKILL.md", "skills/mcfuke-validation/scripts/run.py",
    "skills/mcfuke-validation/references/live-validation.md", "skills/mcfuke-validation/references/workflow.md",
    "skills/mcfuke-validation/references/intent.md",
    "tools/build_validation_skill.py", "tools/build_release.py", "tools/audit_live_validation.py",
    "tests/test_recipe.py", "tests/test_workflow.py", "tests/test_cards.py",
    "tests/test_live_audit.py", "tests/test_skill_package.py", "tests/test_release_package.py",
    "tests/test_cup_choices.py", "tests/test_intent.py",
)
SECRET_PATTERNS = (
    re.compile(rb"Bearer\s+(?!\$\{[A-Z_]+\})[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def inspect_payload(name, content):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or any(
            part in ("research", ".workbuddy", ".tools", "__pycache__", ".git") for part in path.parts):
        raise ValueError("Private or unsafe path in release")
    if name.endswith(".local.json") or path.name.startswith(".env"):
        raise ValueError("Private file in release")
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for entry in archive.infolist():
                if entry.file_size > 10_000_000 or entry.filename.endswith(".zip"):
                    raise ValueError("Unexpected nested or oversized archive")
                inspect_payload(entry.filename, archive.read(entry))
    elif not name.endswith(".png"):
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ValueError("Potential credential in public artifact")


def build():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    if sha256((ROOT / "CONTEST_DECLARATION.md").read_bytes()).hexdigest() != DECLARATION_SHA256:
        raise ValueError("Official declaration differs from downloaded original")
    config = json.loads((ROOT / "mcp-config.example.json").read_text(encoding="utf-8"))
    if config["mcpServers"]["mcd-mcp"]["headers"]["Authorization"] != "Bearer ${MCD_MCP_TOKEN}":
        raise ValueError("Config must use only an environment placeholder")
    files = {name: (ROOT / name).read_bytes() for name in PUBLIC_FILES}
    files["skills/mcfuke-validation/assets/lunch.recipe.json"] = files["examples/lunch.recipe.json"]
    files["skills/mcfuke-validation/assets/flexible-cup.intent.json"] = files["examples/flexible-cup.intent.json"]
    files["downloads/mcfuke-validation.zip"] = build_skill().read_bytes()
    share = share_recipe(load_recipe(ROOT / "examples/replacement.recipe.json"), ROOT / f"dist/meal-share-{version}.zip")
    files["downloads/meal-share.zip"] = share.read_bytes()
    intent = json.loads((ROOT / "examples/flexible-cup.intent.json").read_text(encoding="utf-8"))
    conditions = share_intent(intent, ROOT / f"dist/conditions-share-{version}.zip")
    files["downloads/conditions-share.zip"] = conditions.read_bytes()
    for name, data in files.items():
        inspect_payload(name, data)
    manifest = {"format": "mcfuke.public-release.v1", "version": version,
                "scope": "read-only-store-pickup-no-coupon", "published": False,
                "declaration_sha256": DECLARATION_SHA256,
                "files": {name: sha256(data).hexdigest() for name, data in sorted(files.items())}}
    files["RELEASE_MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    destination = ROOT / "dist" / "public" / f"mcfuke-{version}"
    # Never remove an existing tree or silently include unknown files.
    if destination.exists():
        existing = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
        if existing - files.keys():
            raise ValueError("Public directory contains unexpected files; use a fresh version directory")
    for name, data in files.items():
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    archive_path = ROOT / "dist" / f"mcfuke-{version}-source.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(f"mcfuke-{version}/{name}", data)
    return destination, archive_path


if __name__ == "__main__":
    directory, archive = build()
    print(json.dumps({"public_directory": str(directory), "source_archive": str(archive), "published": False}))
