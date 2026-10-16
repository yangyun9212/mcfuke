from hashlib import sha256
import json
import unittest
import zipfile

from tools.build_release import build, inspect_payload


class ReleasePackageTests(unittest.TestCase):
    def test_public_archive_has_required_files_and_no_local_account_records(self):
        directory, archive_path = build()
        with zipfile.ZipFile(archive_path) as archive:
            names = {name.split("/", 1)[1] for name in archive.namelist()}
            required = {"README.md", "CONTEST_DECLARATION.md", "MCP_INTEGRATION.md",
                        "mcp-config.example.json", "workbuddy.md", "downloads/mcfuke-validation.zip"}
            self.assertTrue(required <= names)
            self.assertFalse(any(name.startswith(("research/", ".workbuddy/", ".tools/"))
                                 or name.endswith(".local.json") for name in names))
        manifest = json.loads((directory / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            self.assertEqual(sha256((directory / name).read_bytes()).hexdigest(), digest)

    def test_credential_and_private_record_refused_including_zip_contents(self):
        for name, data in ((".env", b"x"), ("research/session.local.json", b"{}"),
                           ("config.json", b"Bearer " + b"test-secret-" * 3)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_payload(name, data)
        import io
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("secret.local.json", "{}")
        with self.assertRaises(ValueError):
            inspect_payload("skill.zip", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
