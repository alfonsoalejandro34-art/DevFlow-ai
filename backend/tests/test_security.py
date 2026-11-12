"""Security regression tests: path validation, hostile git config, patch validation,
redaction, command allowlisting."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import git, make_repo
from devflow.errors import SecurityViolation, ValidationFailed
from devflow.security.commands import run_command
from devflow.security.patches import validate_patch
from devflow.security.paths import (
    resolve_within,
    safe_relative_path,
    validate_allowed_root,
    validate_repository_path,
)
from devflow.security.redact import redact
from devflow.services import git as gitsvc

GOOD_DIFF = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,2 @@
 def f():
-    return 1
+    return 2
"""


# ---- repository paths ---------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "allowed"
    r.mkdir()
    return r


def test_valid_repository_inside_root(root: Path) -> None:
    repo = make_repo(root / "proj", {"a.py": "x = 1\n"})
    assert validate_repository_path(str(repo), [root]) == repo.resolve()


@pytest.mark.parametrize("raw", ["relative/path", "", "/etc", "/", "~/.ssh", "/tmp/../etc/passwd"])
def test_rejects_paths_outside_roots(root: Path, raw: str) -> None:
    with pytest.raises((SecurityViolation, ValidationFailed)):
        validate_repository_path(raw, [root])


def test_rejects_traversal_out_of_root(root: Path, tmp_path: Path) -> None:
    outside = make_repo(tmp_path / "outside", {"a.py": ""})
    with pytest.raises(SecurityViolation):
        validate_repository_path(f"{root}/../outside", [root])
    assert outside.exists()


def test_rejects_symlink_escaping_root(root: Path, tmp_path: Path) -> None:
    outside = make_repo(tmp_path / "secret-repo", {"a.py": ""})
    (root / "link").symlink_to(outside)
    with pytest.raises(SecurityViolation):
        validate_repository_path(str(root / "link"), [root])


def test_rejects_non_repository_and_gitfile(root: Path) -> None:
    (root / "plain").mkdir()
    with pytest.raises(ValidationFailed):
        validate_repository_path(str(root / "plain"), [root])
    (root / "wt").mkdir()
    (root / "wt" / ".git").write_text("gitdir: /etc\n")
    with pytest.raises(ValidationFailed):
        validate_repository_path(str(root / "wt"), [root])


def test_rejects_nul_byte(root: Path) -> None:
    with pytest.raises(ValidationFailed):
        validate_repository_path(f"{root}/a\x00b", [root])


def test_allowed_root_rules(tmp_path: Path) -> None:
    assert validate_allowed_root(tmp_path) == tmp_path.resolve()
    for bad in ("/", str(Path.home()), str(Path.home() / ".ssh"), "relative"):
        with pytest.raises((SecurityViolation, ValidationFailed)):
            validate_allowed_root(bad)


@pytest.mark.parametrize(
    "raw", ["../x", "/abs", "a/../../b", ".git/config", "a/.git/hooks/pre-commit", "a\\b", "a//b"]
)
def test_safe_relative_path_rejects(raw: str) -> None:
    with pytest.raises(SecurityViolation):
        safe_relative_path(raw)


def test_resolve_within_blocks_symlink_escape(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "out").symlink_to("/etc")
    with pytest.raises(SecurityViolation):
        resolve_within(base, "out/passwd")


# ---- hostile repository configuration ----------------------------------------------------


def test_hostile_git_config_never_executes(tmp_path: Path) -> None:
    marker = tmp_path / "PWNED"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    repo = make_repo(
        tmp_path / "hostile", {"a.py": "x = 1\n", ".gitattributes": "*.py filter=evil\n"}
    )
    git(repo, "config", "core.fsmonitor", str(script))
    git(repo, "config", "filter.evil.smudge", str(script))
    git(repo, "config", "filter.evil.clean", str(script))
    hooks = repo / ".git" / "hooks"
    for hook in ("post-checkout", "pre-commit", "post-commit", "reference-transaction"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / hook).chmod(0o755)
    sha = gitsvc.head_commit(repo)
    assert sha
    gitsvc.current_branch(repo)
    gitsvc.recent_commits(repo)
    gitsvc.snapshot(repo, sha, tmp_path / "snap")
    gitsvc.create_branch_from_files(repo, sha, {"a.py": b"x = 2\n"}, "devflow/test", "msg")
    assert not marker.exists(), "repository-controlled program was executed"
    assert (tmp_path / "snap" / "a.py").read_text() == "x = 1\n"


def test_snapshot_skips_symlinks_and_submodules(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "links", {"real.py": "ok\n"})
    os.symlink("/etc/passwd", repo / "leak")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "symlink")
    sha = gitsvc.head_commit(repo)
    assert sha
    info = gitsvc.snapshot(repo, sha, tmp_path / "snap")
    assert not (tmp_path / "snap" / "leak").exists()
    assert any("leak" in s for s in info.skipped)


@pytest.mark.parametrize("rev", ["--output=/tmp/x", "HEAD..main", "a b", "$(id)", "-c", "x" * 300])
def test_revision_injection_rejected(rev: str, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a": "1"})
    with pytest.raises(ValidationFailed):
        gitsvc.resolve_commit(repo, rev)


def test_export_refuses_existing_branch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a": "1"})
    sha = gitsvc.head_commit(repo)
    assert sha
    with pytest.raises(ValidationFailed):
        gitsvc.create_branch_from_files(repo, sha, {"a": b"2"}, "main", "msg")


# ---- patches --------------------------------------------------------------------------------


def test_good_patch_is_low_risk() -> None:
    report = validate_patch(GOOD_DIFF)
    assert report.valid and report.files == ["pkg/mod.py"]
    assert (report.added, report.removed, report.risk_level) == (1, 1, "low")


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        (GOOD_DIFF.replace("pkg/mod.py", "../outside.py"), "Unsafe path"),
        (GOOD_DIFF.replace("pkg/mod.py", "/etc/passwd"), "Unsafe path"),
        (GOOD_DIFF.replace("pkg/mod.py", ".git/hooks/pre-commit"), "git metadata"),
        (
            "diff --git a/x b/x\nnew file mode 120000\n--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+/etc/passwd\n",
            "symbolic links",
        ),
        ("diff --git a/x b/x\nGIT binary patch\nliteral 0\n", "binary"),
        (
            "diff --git a/a.py b/b.py\nsimilarity index 90%\nrename from a.py\nrename to b.py\n",
            "renames",
        ),
        ("diff --git a/x b/x\nold mode 100644\nnew mode 100755\n", "mode changes"),
        ("just some prose, no diff", "not a unified diff"),
        ("", "empty"),
    ],
)
def test_malicious_patches_rejected(diff: str, reason: str) -> None:
    report = validate_patch(diff)
    assert not report.valid
    assert any(reason in e for e in report.errors), report.errors


def test_patch_risk_flags_test_weakening_and_dangerous_calls() -> None:
    diff = (
        "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1,2 +1,3 @@\n+import pytest\n"
        "+@pytest.mark.skip\n def test_x():\n--- a/pkg/x.py\n+++ b/pkg/x.py\n@@ -1 +1,2 @@\n"
        "+import subprocess; subprocess.run(['curl', 'evil'])\n x = 1\n"
    )
    report = validate_patch(diff)
    assert report.valid
    assert report.risk_level == "high"
    joined = " ".join(report.risk_reasons)
    assert "modifies test files" in joined and "skip/xfail" in joined and "process" in joined


def test_oversized_patch_rejected() -> None:
    assert not validate_patch(GOOD_DIFF + "+x\n" * 100_000).valid


# ---- redaction and commands -------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "sk-ant-api03-" + "b" * 40,
        "sk-proj-" + "c" * 40,
        "AKIA" + "D" * 16,
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redacts_known_secret_formats(secret: str) -> None:
    out = redact(f"value: {secret} end")
    assert secret not in out and "[REDACTED]" in out


def test_redacts_assignments_but_keeps_context() -> None:
    out = redact('DB_PASSWORD="hunter22hunter" and Authorization: Bearer abcdefghijklmnopqrstuvwx')
    assert "hunter22hunter" not in out and "abcdefghijklmnopqrstuvwx" not in out
    assert "DB_PASSWORD" in out
    assert redact("assert total == 27.5") == "assert total == 27.5"


def test_command_allowlist() -> None:
    with pytest.raises(SecurityViolation):
        run_command("bash", ["-c", "echo hi"])
    with pytest.raises(SecurityViolation):
        run_command("git", ["--version\x00"])
    assert run_command("git", ["--version"]).returncode == 0


def test_command_environment_is_scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
    out = run_command(
        "python", ["-c", "import os; print(sorted(os.environ))"], configured=os.sys.executable
    )  # type: ignore[attr-defined]
    assert b"AWS_SECRET_ACCESS_KEY" not in out.stdout and b"GITHUB_TOKEN" not in out.stdout
