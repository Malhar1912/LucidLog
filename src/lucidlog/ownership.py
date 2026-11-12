from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path


@dataclass(frozen=True)
class OwnershipResolution:
    owners: tuple[str, ...] = ()
    team: str | None = None
    escalation: tuple[str, ...] = ()
    source: str = "unavailable"

    @property
    def recommendation(self) -> str | None:
        return self.team or (self.owners[0] if self.owners else None)

    def to_dict(self) -> dict[str, object]:
        return {
            "owners": list(self.owners),
            "team": self.team,
            "escalation": list(self.escalation),
            "recommendation": self.recommendation,
            "source": self.source,
        }


def _repository_root(path: Path) -> Path | None:
    for candidate in (path.parent, *path.parent.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _matches(pattern: str, relative_path: str) -> bool:
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):
        normalized += "*"
    if "/" not in normalized:
        return any(fnmatch.fnmatchcase(part, normalized) for part in relative_path.split("/"))
    return fnmatch.fnmatchcase(relative_path, normalized)


def _read_codeowners(path: Path, relative_path: str) -> tuple[str, ...]:
    owners: tuple[str, ...] = ()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.split("#", 1)[0].strip()
                parts = line.split()
                if len(parts) < 2:
                    continue
                if _matches(parts[0], relative_path):
                    owners = tuple(parts[1:])
    except OSError:
        return ()
    return owners


class OwnershipResolver:
    def __init__(
        self,
        *,
        codeowners_path: str | None = None,
        file_owners: tuple[tuple[str, tuple[str, ...]], ...] = (),
        author_teams: tuple[tuple[str, str], ...] = (),
        escalation_paths: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        if codeowners_path is not None and not isinstance(codeowners_path, str):
            raise TypeError("codeowners_path must be a string or None.")
        if not isinstance(file_owners, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], tuple)
            and all(isinstance(owner, str) for owner in item[1])
            for item in file_owners
        ):
            raise TypeError("file_owners must contain (pattern, owners) tuple pairs.")
        if not isinstance(author_teams, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and all(isinstance(value, str) for value in item)
            for item in author_teams
        ):
            raise TypeError("author_teams must contain (author, team) string pairs.")
        if not isinstance(escalation_paths, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], tuple)
            and all(isinstance(value, str) for value in item[1])
            for item in escalation_paths
        ):
            raise TypeError("escalation_paths must contain (team, chain) tuple pairs.")
        self.codeowners_path = codeowners_path
        self.file_owners = file_owners
        self.author_teams = {author.casefold(): team for author, team in author_teams}
        self.escalation_paths = {team.casefold(): chain for team, chain in escalation_paths}

    def resolve(self, filename: str, *, author: str | None = None) -> OwnershipResolution:
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty path.")
        if author is not None and not isinstance(author, str):
            raise TypeError("author must be a string or None.")
        file_path = Path(filename).resolve()
        root = _repository_root(file_path)
        relative = file_path.relative_to(root).as_posix() if root else file_path.name

        configured_owners: tuple[str, ...] = ()
        for pattern, owners in self.file_owners:
            if _matches(pattern, relative):
                configured_owners = owners

        codeowners = Path(self.codeowners_path) if self.codeowners_path else None
        candidates: list[Path] = []
        if codeowners is not None and codeowners.is_dir():
            candidates.append(codeowners / "CODEOWNERS")
        elif codeowners is not None:
            candidates.append(codeowners)
        elif root is not None:
            candidates.extend(
                (
                    root / ".github" / "CODEOWNERS",
                    root / "CODEOWNERS",
                    root / ".gitlab" / "CODEOWNERS",
                )
            )

        codeowners_owners: tuple[str, ...] = ()
        for candidate in candidates:
            if candidate.is_file():
                codeowners_owners = _read_codeowners(candidate, relative)
                break

        owners = configured_owners or codeowners_owners
        source = "configured" if configured_owners else "codeowners" if codeowners_owners else "unavailable"
        team = None
        if author:
            team = self.author_teams.get(author.casefold())
        if team is None:
            team_owner = next(
                (owner.lstrip("@") for owner in owners if owner.startswith("@") and "/" in owner),
                None,
            )
            team = team_owner.rsplit("/", 1)[-1] if team_owner else None
        escalation = self.escalation_paths.get(team.casefold(), ()) if team else ()
        return OwnershipResolution(owners=owners, team=team, escalation=escalation, source=source)


def codeowners_for_file(
    filename: str,
    *,
    repo_root: str | None = None,
    codeowners_path: str | None = None,
) -> tuple[str | None, str | None]:
    del repo_root  # OwnershipResolver discovers the root from the source path.
    result = OwnershipResolver(codeowners_path=codeowners_path).resolve(filename)
    owner = result.owners[0] if result.owners else None
    return owner, result.team
