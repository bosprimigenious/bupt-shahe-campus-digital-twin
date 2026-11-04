#!/usr/bin/env python3
"""Fail closed on private material in a public candidate and its Git history.

Diagnostics contain only relative paths, line numbers and categories. No matched
content, commit identity, symlink target or exception text is printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Iterable


MAX_TEXT_BYTES = 32 * 1024 * 1024
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
PRIVATE_SEGMENTS = {
    "platform_integrated_import", "platform_backups", "platform-backups",
    "platform_backup", "platform-backup", "private-backups", "private-backup",
    "private_archive", "private-archive", "private-evidence", "backups", "private",
}
PRIVATE_NAMES = {
    "platform_session.py", "scene-link.json", "scenes-before.json",
    "scenes-after.json", "import-state.json", "campus-full-manifest.json.gz",
    ".env", ".netrc", "id_rsa", "id_ed25519",
}
ARTIFACT_SUFFIXES = {
    ".ppt", ".pptx", ".glb", ".gltf", ".tjs", ".blend", ".blend1",
    ".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".headers",
}
TOKEN_PATTERNS = {
    "secret_token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
        r"xox[baprs]-[A-Za-z0-9-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
        r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{35})\b"
    ),
    "bearer_token": re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/-]{20,}", re.I),
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*"
        r"[A-Za-z0-9+/=\r\n \t]{64,}"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "session_cookie": re.compile(
        r"\b(?:JSESSIONID|sessionid|connect\.sid)[ \t]*[:=][ \t]*"
        r"[\"']?[A-Za-z0-9%._~-]{16,}", re.I
    ),
    "user_home_path": re.compile(r"/(?:Users|home)/[^\s/\"'<>]+(?:/[^\s\"'<>]*)?"),
    "platform_identifier": re.compile(r"(?<![\w.])[0-9]{15,20}(?![\w.])"),
    "student_identifier": re.compile(r"(?<![\w.])20[0-9]{8}(?![\w.])"),
    "scene_identifier": re.compile(
        r"\bsceneCode[ \t]*[:=][ \t]*[\"']?[A-Za-z0-9]{12,}", re.I
    ),
}
IP_PATTERN = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
NOREPLY_EMAIL = re.compile(r"(?:[0-9]+\+)?[A-Za-z0-9_.-]+@users\.noreply\.github\.com", re.I)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    category: str


def run_git(root: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def is_git_root(root: Path) -> bool:
    result = run_git(root, "rev-parse", "--show-toplevel")
    return result.returncode == 0 and Path(os.fsdecode(result.stdout.strip())).resolve() == root.resolve()


def tracked_paths(root: Path) -> set[str]:
    result = run_git(root, "ls-files", "-z")
    if result.returncode:
        raise RuntimeError("git inventory failed")
    return {os.fsdecode(value) for value in result.stdout.split(b"\0") if value}


def fallback_ignored(path: str, rules: Iterable[str]) -> bool:
    """Conservative cache-only fallback before the candidate has a Git repo."""
    ignored = False
    for raw in rules:
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negate = rule.startswith("!")
        if negate:
            rule = rule[1:]
        directory_only = rule.endswith("/")
        rule = rule.strip("/")
        if not rule:
            continue
        parts = path.split("/")
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        candidates = prefixes if "/" in rule else parts
        match = any(fnmatch.fnmatchcase(value, rule) for value in candidates)
        if directory_only and rule not in parts[:-1] and not any(
            fnmatch.fnmatchcase(prefix, rule) for prefix in prefixes[:-1]
        ):
            match = False
        if match:
            ignored = not negate
    return ignored


def ignored_cache(root: Path, path: str, tracked: set[str], git: bool) -> bool:
    if path in tracked:
        return False
    parts = Path(path).parts
    if not (set(parts) & CACHE_NAMES or Path(path).suffix in {".pyc", ".pyo"}):
        return False
    if git:
        result = run_git(root, "check-ignore", "--no-index", "--quiet", "--", path)
        return result.returncode == 0
    ignore_file = root / ".gitignore"
    if ignore_file.is_symlink() or not ignore_file.is_file():
        return False
    try:
        return fallback_ignored(path, ignore_file.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError):
        return False


def private_path(path: str) -> bool:
    value = Path(path)
    return bool({part.lower() for part in value.parts} & PRIVATE_SEGMENTS) or value.name.lower() in PRIVATE_NAMES or bool(
        re.search(r"(?:^|[-_])(?:request|response)\.json$", value.name, re.I)
    ) or value.name.startswith(("campus-full.part-", "platform-login", "platform_login"))


def scan_text(path: str, text: str) -> list[Finding]:
    findings = []
    for category, pattern in TOKEN_PATTERNS.items():
        for match in pattern.finditer(text):
            if category == "platform_identifier" and len(set(match.group())) == 1:
                continue  # Repeated-digit placeholders carry no platform identity.
            findings.append(Finding(path, text.count("\n", 0, match.start()) + 1, category))
    for match in IP_PATTERN.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group())
        except ipaddress.AddressValueError:
            continue
        first, second, *_ = address.packed
        if first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168):
            findings.append(Finding(path, text.count("\n", 0, match.start()) + 1, "private_network_ip"))
    return findings


def scan_blob(path: str, content: bytes) -> list[Finding]:
    findings = []
    if private_path(path):
        findings.append(Finding(path, 0, "private_artifact_path"))
    if Path(path).suffix.lower() in ARTIFACT_SUFFIXES:
        findings.append(Finding(path, 0, "restricted_artifact"))
    if len(content) > MAX_TEXT_BYTES:
        return findings + [Finding(path, 0, "oversized_unreviewed_file")]
    if b"\0" in content:
        return findings + [Finding(path, 0, "binary_file")]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        return findings + [Finding(path, 0, "binary_file")]
    return findings + scan_text(path, text)


def scan_tree(root: Path, git: bool) -> list[Finding]:
    findings: list[Finding] = []
    tracked = tracked_paths(root) if git else set()

    def inaccessible(error: OSError) -> None:
        try:
            path = Path(error.filename or root).relative_to(root).as_posix()
        except ValueError:
            path = "."
        findings.append(Finding(path, 0, "unreadable_directory"))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        parent = Path(directory)
        if parent == root and ".git" in dirs:
            dirs.remove(".git")
        symlink_dirs = [name for name in dirs if (parent / name).is_symlink()]
        for name in symlink_dirs:
            dirs.remove(name)
        for name in files + symlink_dirs:
            item = parent / name
            relative = item.relative_to(root).as_posix()
            if relative == ".git":
                continue
            if ignored_cache(root, relative, tracked, git):
                continue
            if item.is_symlink():
                findings.append(Finding(relative, 0, "symlink"))
                continue
            try:
                if not stat.S_ISREG(item.stat(follow_symlinks=False).st_mode):
                    findings.append(Finding(relative, 0, "special_file"))
                    continue
                findings.extend(scan_blob(relative, item.read_bytes()))
            except OSError:
                findings.append(Finding(relative, 0, "unreadable_file"))
    return findings


def scan_history(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    shallow = run_git(root, "rev-parse", "--is-shallow-repository")
    if shallow.returncode or shallow.stdout.strip() == b"true":
        findings.append(Finding(".git", 0, "incomplete_history"))
    result = run_git(root, "rev-list", "--all")
    if result.returncode:
        return findings + [Finding(".git", 0, "history_unavailable")]
    commits = result.stdout.decode("ascii").splitlines()
    if not commits:
        return findings + [Finding(".git", 0, "history_empty")]
    seen = set()
    blobs: dict[str, bytes] = {}
    for commit in commits:
        identities = run_git(root, "show", "-s", "--format=%ae%x00%ce", commit)
        if identities.returncode:
            findings.append(Finding(f"history/{commit}", 0, "unreadable_commit"))
        else:
            emails = identities.stdout.strip().decode("utf-8", errors="replace").split("\0")
            if any(not NOREPLY_EMAIL.fullmatch(email) for email in emails):
                findings.append(Finding(f"history/{commit}", 0, "commit_email"))
        message = run_git(root, "show", "-s", "--format=%B", commit)
        if message.returncode == 0:
            findings.extend(scan_text(f"history/{commit}/commit-message", message.stdout.decode("utf-8", errors="replace")))
        tree = run_git(root, "ls-tree", "-r", "-z", commit)
        if tree.returncode:
            findings.append(Finding(f"history/{commit}", 0, "unreadable_tree"))
            continue
        for entry in tree.stdout.split(b"\0"):
            if not entry:
                continue
            info, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = info.decode("ascii").split()
            path = os.fsdecode(raw_path)
            if (mode, object_id, path) in seen:
                continue
            seen.add((mode, object_id, path))
            diagnostic_path = f"history/{commit}/{path}"
            if mode == "120000":
                findings.append(Finding(diagnostic_path, 0, "symlink"))
                continue
            if mode == "160000" or kind != "blob":
                findings.append(Finding(diagnostic_path, 0, "gitlink"))
                continue
            if object_id not in blobs:
                value = run_git(root, "cat-file", "blob", object_id)
                if value.returncode:
                    findings.append(Finding(diagnostic_path, 0, "unreadable_blob"))
                    continue
                blobs[object_id] = value.stdout
            findings.extend(scan_blob(diagnostic_path, blobs[object_id]))
    return findings


def safe_path(path: str) -> str:
    value = TOKEN_PATTERNS["secret_token"].sub("<redacted-token>", path)
    value = TOKEN_PATTERNS["platform_identifier"].sub("<platform-id>", value)
    value = TOKEN_PATTERNS["student_identifier"].sub("<student-id>", value)
    value = IP_PATTERN.sub("<ip>", value)
    return value.replace("\n", "\\n").replace("\r", "\\r")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true", help="Inspect every locally available Git ref, historical blob and commit identity.")
    args = parser.parse_args(argv)
    root = args.root.absolute()
    if root.is_symlink():
        findings = [Finding(".", 0, "symlink_root")]
    elif not root.is_dir():
        findings = [Finding(".", 0, "missing_root")]
    else:
        try:
            git = is_git_root(root)
            findings = scan_tree(root, git)
            if args.history:
                findings.extend(scan_history(root) if git else [Finding(".git", 0, "history_unavailable")])
        except (OSError, RuntimeError, ValueError):
            findings = [Finding(".", 0, "audit_error")]
    for finding in sorted(set(findings)):
        print(f"{safe_path(finding.path)}:{finding.line}:{finding.category}")
    print(f"{'FAIL' if findings else 'PASS'}: {len(set(findings))} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
