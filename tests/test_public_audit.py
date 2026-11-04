"""Adversarial audit fixtures are assembled at runtime, never stored as secrets."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "audit_public.py"


class PublicAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-audit-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Audit Fixture")
        self.git("config", "user.email", "audit@users.noreply.github.com")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            target.write_bytes(value)
        else:
            target.write_text(value, encoding="utf-8")

    def audit(self, history=False):
        command = [sys.executable, str(AUDIT), "--root", str(self.root)]
        if history:
            command.append("--history")
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def assert_blocked(self, category, private_value=None, history=False):
        result = self.audit(history)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(":" + category, result.stdout)
        if private_value:
            self.assertNotIn(private_value, result.stdout + result.stderr)
        return result

    def test_current_high_confidence_token_is_hidden(self):
        tokens = ["gh" + "p_" + "a" * 36, "sk" + "-proj-" + "a" * 48]
        for token in tokens:
            with self.subTest(kind=token[:2]):
                self.write("leak.txt", "value=" + token)
                self.assert_blocked("secret_token", token)

    def test_complete_private_key_is_blocked(self):
        title = "PRIVATE" + " KEY"
        key = "-----BEGIN " + title + "-----\n" + "A" * 96 + "\n-----END " + title + "-----"
        self.write("key.pem", key)
        self.assert_blocked("private_key", key)

    def test_private_ip_is_blocked_without_echoing_it(self):
        address = ".".join(["10", "23", "45", "67"])
        self.write("config.txt", "endpoint=http://" + address + "/")
        self.assert_blocked("private_network_ip", address)

    def test_absolute_home_path_is_blocked_without_echoing_it(self):
        home = "/" + "Users" + "/" + "fixture-owner" + "/private/project"
        self.write("config.txt", home)
        self.assert_blocked("user_home_path", home)

    def test_binary_file_is_blocked(self):
        self.write("asset.dat", b"\x00\x01binary")
        self.assert_blocked("binary_file")

    def test_model_extension_and_raw_response_path_are_blocked(self):
        self.write("assets/model.glb", "not even a real model")
        self.write("platform_integrated_import/import-response.json", "{}")
        self.assert_blocked("restricted_artifact")
        self.assert_blocked("private_artifact_path")

    def test_long_platform_identifier_is_blocked(self):
        identifier = "84" + "6748" + "1234567890"
        self.write("record.json", '{"record": "' + identifier + '"}')
        self.assert_blocked("platform_identifier", identifier)

    def test_symlink_is_not_followed(self):
        (self.root / "link.txt").symlink_to(self.root.parent / "unreadable-outside-target")
        result = self.assert_blocked("symlink")
        self.assertNotIn("unreadable-outside-target", result.stdout)

    def test_explicitly_ignored_untracked_test_caches_are_allowed(self):
        self.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
        self.write("__pycache__/test.pyc", b"\x00\x01cache")
        self.write(".pytest_cache/state", b"\x00\x01cache")
        self.assertEqual(self.audit().returncode, 0)

    def test_ignore_rule_cannot_hide_tracked_sensitive_file(self):
        token = "gh" + "p_" + "b" * 36
        self.write(".gitignore", "__pycache__/\n")
        self.write("__pycache__/tracked.txt", token)
        self.git("add", "-f", "__pycache__/tracked.txt")
        self.assert_blocked("secret_token", token)

    def test_arbitrary_ignored_binary_is_still_blocked(self):
        self.write(".gitignore", "private.dat\n")
        self.write("private.dat", b"\x00not a test cache")
        self.assert_blocked("binary_file")

    def test_history_finds_deleted_secret(self):
        token = "gh" + "p_" + "c" * 36
        self.write("removed.txt", token)
        self.git("add", "removed.txt")
        self.git("commit", "-qm", "Add temporary fixture")
        self.git("rm", "-q", "removed.txt")
        self.git("commit", "-qm", "Remove temporary fixture")
        self.assertEqual(self.audit().returncode, 0)
        result = self.assert_blocked("secret_token", token, history=True)
        self.assertIn("removed.txt", result.stdout)

    def test_history_does_not_pass_an_empty_repository(self):
        self.assert_blocked("history_empty", history=True)

    def test_history_checks_commit_email_but_not_license_attribution(self):
        self.write("LICENSE", "MIT License\nCopyright A Contributor <contributor@example.com>\n")
        self.git("add", "LICENSE")
        self.git("commit", "-qm", "Add legal attribution")
        self.assertEqual(self.audit(history=True).returncode, 0)
        self.git("config", "user.email", "private" + "@" + "example.com")
        self.write("README.md", "fixture\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "Add fixture documentation")
        self.assert_blocked("commit_email", "private" + "@" + "example.com", history=True)

    def test_history_includes_non_default_branches(self):
        self.write("README.md", "fixture\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "Add clean fixture")
        original_branch = self.git("branch", "--show-current").stdout.decode().strip()
        self.git("checkout", "-qb", "fixture-secondary")
        token = "gh" + "p_" + "d" * 36
        self.write("secondary-only.txt", token)
        self.git("add", "secondary-only.txt")
        self.git("commit", "-qm", "Add branch-only fixture")
        self.git("checkout", "-q", original_branch)
        self.assertEqual(self.audit().returncode, 0)
        result = self.assert_blocked("secret_token", token, history=True)
        self.assertIn("secondary-only.txt", result.stdout)

    def test_ignored_but_tracked_binary_cache_is_blocked(self):
        self.write(".gitignore", "__pycache__/\n")
        self.write("__pycache__/tracked.pyc", b"\x00tracked binary fixture")
        self.git("add", "-f", "__pycache__/tracked.pyc")
        self.assert_blocked("binary_file")

    def test_legal_three_license_and_documentation_addresses_are_allowed(self):
        self.write("vendor/three/LICENSE", "The MIT License\nCopyright three.js authors and contributors\ncontact@example.com\n")
        self.write("README.md", "Local preview: http://127.0.0.1:8766/\nExamples: 192.0.2.1, 198.51.100.2, 203.0.113.3\n")
        self.assertEqual(self.audit().returncode, 0)

    def test_scanner_and_synthetic_test_sources_do_not_self_trigger(self):
        self.write("scripts/audit_public.py", AUDIT.read_text())
        self.write("tests/test_public_audit.py", Path(__file__).read_text())
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
