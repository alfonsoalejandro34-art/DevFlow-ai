"""Repository registration and inspection."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from devflow.config import Settings
from devflow.database.models import Repository, Revision
from devflow.database.session import Database
from devflow.errors import Conflict, NotFound, ValidationFailed
from devflow.security.commands import minimal_env, run_command
from devflow.security.paths import make_private_dir, validate_repository_path
from devflow.services import git
from devflow.services.detect import detect_structure
from devflow.services.settings import SettingsService

log = logging.getLogger(__name__)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,119}$")


class RepositoryService:
    def __init__(self, db: Database, settings: Settings, runtime: SettingsService) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime

    def register(
        self, raw_path: str, name: str | None = None, *, is_example: bool = False
    ) -> Repository:
        path = validate_repository_path(raw_path, self.runtime.allowed_roots())
        name = (name or path.name).strip()
        if not _NAME.match(name):
            raise ValidationFailed("Name may contain letters, digits, spaces, '.', '_' and '-'")
        head = git.head_commit(path)
        if head is None:
            raise ValidationFailed("Repository has no commits")
        languages, structure = detect_structure([e.path for e in git.list_tree(path, head)])
        repo = Repository(
            name=name,
            path=str(path),
            is_example=is_example,
            languages=languages,
            structure=structure,
        )
        try:
            with self.db.transaction() as session:
                session.add(repo)
        except IntegrityError as exc:
            raise Conflict("A repository with this name or path is already registered") from exc
        log.info("repository registered", extra={"repository_id": repo.id, "repo_name": name})
        return repo

    def list_all(self) -> list[Repository]:
        with self.db.session() as session:
            return list(session.scalars(select(Repository).order_by(Repository.created_at.desc())))

    def get(self, repo_id: int) -> Repository:
        with self.db.session() as session:
            repo = session.get(Repository, repo_id)
            if repo is None:
                raise NotFound(f"Repository {repo_id} not found")
            return repo

    def path_of(self, repo: Repository) -> Path:
        # Re-validate on every use: the directory could have been replaced by a symlink
        # or the approved roots could have changed since registration.
        return validate_repository_path(repo.path, self.runtime.allowed_roots())

    def inspect(self, repo_id: int) -> dict[str, Any]:
        repo = self.get(repo_id)
        path = self.path_of(repo)
        head = git.head_commit(path)
        return {
            "branch": git.current_branch(path),
            "head": head,
            "branches": git.list_branches(path),
            "recent_commits": [c.__dict__ for c in git.recent_commits(path, limit=10)]
            if head
            else [],
        }

    def refresh_structure(self, repo_id: int) -> Repository:
        repo = self.get(repo_id)
        path = self.path_of(repo)
        head = git.head_commit(path)
        if head is None:
            raise ValidationFailed("Repository has no commits")
        languages, structure = detect_structure([e.path for e in git.list_tree(path, head)])
        with self.db.transaction() as session:
            fresh = session.get(Repository, repo_id)
            assert fresh is not None
            fresh.languages, fresh.structure = languages, structure
        return self.get(repo_id)

    def unregister(self, repo_id: int) -> None:
        with self.db.transaction() as session:
            repo = session.get(Repository, repo_id)
            if repo is None:
                raise NotFound(f"Repository {repo_id} not found")
            session.delete(repo)

    def ensure_revision(self, repo: Repository, rev: str) -> Revision:
        path = self.path_of(repo)
        sha = git.resolve_commit(path, rev)
        commit = git.commit_info(path, sha)
        with self.db.transaction() as session:
            existing = session.scalar(
                select(Revision).where(Revision.repository_id == repo.id, Revision.sha == sha)
            )
            if existing is not None:
                return existing
            revision = Revision(
                repository_id=repo.id,
                sha=sha,
                ref=None if rev == sha else rev,
                message=commit.message,
                author=commit.author,
                committed_at=commit.committed_at,
            )
            session.add(revision)
        return revision

    def install_examples(self) -> list[Repository]:
        """Copy each bundled example into the managed repos directory as a fresh Git
        repository and register it. Existing registrations are left untouched."""
        make_private_dir(self.settings.managed_repos_dir)
        source_root = self.settings.examples_dir / "repos"
        registered = {r.name: r for r in self.list_all()}
        installed = []
        for source in sorted(p for p in source_root.iterdir() if p.is_dir()):
            if source.name in registered:
                installed.append(registered[source.name])
                continue
            target = self.settings.managed_repos_dir / source.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(
                source, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
            init_example_repo(target)
            installed.append(self.register(str(target), source.name, is_example=True))
        return installed


def init_example_repo(path: Path) -> None:
    env = minimal_env(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "DevFlow Examples",
            "GIT_AUTHOR_EMAIL": "examples@devflow.local",
            "GIT_COMMITTER_NAME": "DevFlow Examples",
            "GIT_COMMITTER_EMAIL": "examples@devflow.local",
        }
    )
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "Initial example import"],
    ):
        run_command("git", ["-C", str(path), *args], env=env)
