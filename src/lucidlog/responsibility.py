from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Iterable, Mapping, TextIO
from urllib.parse import quote, urlsplit

from .config import Config, default_config
from .integrations.providers import CommitMetadata, IssueMetadata, PullRequestMetadata, VcsProvider
from .models import Diagnostic, GitResponsibility
from .ownership import OwnershipResolution, OwnershipResolver
from .redaction import redact_path, sanitize_text
from .source import _git_blame_for_line, _git_root_for_path, _normalize_personal_identity


@dataclass(frozen=True)
class ChangeRisk:
    score: int | None = None
    level: str = "unavailable"
    ownership_freshness: int | None = None
    commits_sampled: int = 0
    churn: int = 0
    contributors: int = 0
    bug_fix_commits: int = 0
    days_since_change: int | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return asdict(self)


@dataclass(frozen=True)
class ResponsibilityScore:
    score: int
    level: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


@dataclass(frozen=True)
class OwnershipTimelineEntry:
    commit_id: str
    author: str
    date: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DeploymentRecord:
    commit_id: str
    service: str
    environment: str
    deployed_at: str
    version: str | None = None
    repository: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class DeploymentRegistry:
    def __init__(self, deployments: Iterable[DeploymentRecord] = ()) -> None:
        self._deployments: list[DeploymentRecord] = []
        for deployment in deployments:
            self.add(deployment)

    def add(self, deployment: DeploymentRecord) -> None:
        if not isinstance(deployment, DeploymentRecord):
            raise TypeError("deployment must be a DeploymentRecord.")
        if not deployment.commit_id or not deployment.service or not deployment.environment:
            raise ValueError("Deployment commit_id, service, and environment are required.")
        try:
            timestamp = datetime.fromisoformat(deployment.deployed_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("deployed_at must be an ISO-8601 timestamp.") from error
        if timestamp.tzinfo is None:
            raise ValueError("deployed_at must include a timezone.")
        self._deployments.append(deployment)

    def match(
        self,
        commit_id: str | None,
        *,
        service: str | None = None,
        environment: str | None = None,
        repository: str | None = None,
    ) -> DeploymentRecord | None:
        if not commit_id:
            return None
        matching = [
            item
            for item in self._deployments
            if item.commit_id == commit_id
            and (service is None or item.service == service)
            and (environment is None or item.environment == environment)
            and (repository is None or item.repository in {None, repository})
        ]
        if not matching:
            return None
        return max(matching, key=lambda item: item.deployed_at)


@dataclass(frozen=True)
class IncidentRecord:
    diagnostic_id: str
    fingerprint: str
    exception_type: str
    file_path: str
    occurred_at: str
    service: str | None = None
    environment: str | None = None
    repository: str | None = None
    commit_id: str | None = None
    owner: str | None = None
    team: str | None = None
    author: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class IncidentStore:
    """Opt-in, local SQLite history; exception messages and tracebacks are never persisted."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    diagnostic_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    exception_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    service TEXT,
                    environment TEXT,
                    repository TEXT,
                    commit_id TEXT,
                    owner TEXT,
                    team TEXT,
                    author TEXT
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "author" not in columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN author TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS incidents_fingerprint_idx ON incidents(fingerprint)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS incidents_path_service_idx ON incidents(file_path, service)"
            )

    def record(
        self,
        diagnostic: Diagnostic,
        *,
        service: str | None = None,
        environment: str | None = None,
        repository: str | None = None,
        occurred_at: str | None = None,
        config: Config | None = None,
    ) -> IncidentRecord:
        config = config or default_config()
        frame = diagnostic.primary_frame
        responsibility = frame.responsibility if frame else None
        safe = lambda value: sanitize_text(value, config) if value is not None else None
        record = IncidentRecord(
            diagnostic_id=safe(diagnostic.diagnostic_id) or "",
            fingerprint=safe(diagnostic.diagnostic_id) or "",
            exception_type=safe(diagnostic.exception_type) or "",
            file_path=safe(frame.filename if frame else "") or "",
            occurred_at=occurred_at or datetime.now(timezone.utc).isoformat(),
            service=safe(service),
            environment=safe(environment),
            repository=safe(repository),
            commit_id=safe(responsibility.commit_id if responsibility else None),
            owner=safe(responsibility.owner if responsibility else None),
            team=safe(responsibility.team if responsibility else None),
            author=safe(responsibility.author if responsibility else None),
        )
        try:
            occurred = datetime.fromisoformat(record.occurred_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("occurred_at must be an ISO-8601 timestamp.") from error
        if occurred.tzinfo is None:
            raise ValueError("occurred_at must include a timezone.")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    diagnostic_id, fingerprint, exception_type, file_path, occurred_at,
                    service, environment, repository, commit_id, owner, team, author
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.diagnostic_id,
                    record.fingerprint,
                    record.exception_type,
                    record.file_path,
                    record.occurred_at,
                    record.service,
                    record.environment,
                    record.repository,
                    record.commit_id,
                    record.owner,
                    record.team,
                    record.author,
                ),
            )
        return record

    def find_related(
        self,
        *,
        fingerprint: str | None = None,
        file_path: str | None = None,
        service: str | None = None,
        author: str | None = None,
        limit: int = 20,
    ) -> tuple[IncidentRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer.")
        conditions: list[str] = []
        values: list[str | int] = []
        if fingerprint:
            conditions.append("fingerprint = ?")
            values.append(fingerprint)
        if file_path:
            conditions.append("file_path = ?")
            values.append(file_path)
        if service:
            conditions.append("service = ?")
            values.append(service)
        if author:
            conditions.append("author = ?")
            values.append(author)
        if not conditions:
            raise ValueError("At least one of fingerprint, file_path, service, or author is required.")
        query = (
            "SELECT diagnostic_id, fingerprint, exception_type, file_path, occurred_at, service, "
            "environment, repository, commit_id, owner, team, author FROM incidents WHERE "
            + " OR ".join(conditions)
            + " ORDER BY occurred_at DESC LIMIT ?"
        )
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(query, (*values, limit)).fetchall()
        return tuple(IncidentRecord(*row) for row in rows)


@dataclass(frozen=True)
class ResponsibilityReport:
    filename: str
    line_number: int
    responsibility: GitResponsibility | None
    ownership: OwnershipResolution
    change_risk: ChangeRisk
    timeline: tuple[OwnershipTimelineEntry, ...] = ()
    commit: CommitMetadata | None = None
    diff_preview: tuple[str, ...] = ()
    pull_requests: tuple[PullRequestMetadata, ...] = ()
    issues: tuple[IssueMetadata, ...] = ()
    deployment: DeploymentRecord | None = None
    related_incidents: tuple[IncidentRecord, ...] = ()
    repository: str | None = None
    service: str | None = None
    environment: str | None = None
    diagnostic_id: str | None = None
    exception_type: str | None = None
    failure_summary: str | None = None

    @property
    def recommendation(self) -> str | None:
        return self.ownership.recommendation or (
            self.responsibility.owner if self.responsibility else None
        )

    @property
    def escalation_target(self) -> str | None:
        return self.ownership.escalation[0] if self.ownership.escalation else None

    @property
    def recommended_reviewer(self) -> str | None:
        current_author = self.responsibility.author if self.responsibility else None
        return next(
            (entry.author for entry in self.timeline if entry.author != current_author),
            None,
        )

    @property
    def responsibility_score(self) -> ResponsibilityScore:
        confidence_score = {
            "exact_blame": 50,
            "file_fallback": 35,
            "codeowner_fallback": 25,
        }.get(self.responsibility.confidence, 0) if self.responsibility else 0
        score = min(
            100,
            confidence_score
            + (20 if self.ownership.owners or (self.responsibility and self.responsibility.owner) else 0)
            + (15 if self.ownership.team else 0)
            + (10 if self.ownership.escalation else 0)
            + (5 if self.recommended_reviewer else 0),
        )
        return ResponsibilityScore(
            score=score,
            level="high" if score >= 70 else "medium" if score >= 40 else "low",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "line_number": self.line_number,
            "responsibility": self.responsibility.to_dict() if self.responsibility else None,
            "ownership": self.ownership.to_dict(),
            "recommendation": self.recommendation,
            "recommended_reviewer": self.recommended_reviewer,
            "escalation_target": self.escalation_target,
            "responsibility_score": self.responsibility_score.to_dict(),
            "change_risk": self.change_risk.to_dict(),
            "timeline": [entry.to_dict() for entry in self.timeline],
            "commit": asdict(self.commit) if self.commit else None,
            "diff_preview": list(self.diff_preview),
            "pull_requests": [asdict(item) for item in self.pull_requests],
            "issues": [asdict(item) for item in self.issues],
            "deployment": self.deployment.to_dict() if self.deployment else None,
            "related_incidents": [item.to_dict() for item in self.related_incidents],
            "repository": self.repository,
            "service": self.service,
            "environment": self.environment,
            "diagnostic_id": self.diagnostic_id,
            "exception_type": self.exception_type,
            "failure_summary": self.failure_summary,
        }


def _git(
    cwd: str,
    *arguments: str,
    timeout: float = 3.0,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", cwd, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _repository_url(filename: str) -> tuple[str | None, str | None, str | None]:
    root = _git_root_for_path(filename)
    if root is None:
        return None, None, None
    result = _git(root, "config", "--get", "remote.origin.url")
    if result is None or result.returncode != 0:
        return root, None, None
    remote = result.stdout.strip()
    if remote.startswith("git@") and ":" in remote:
        host, path = remote[4:].split(":", 1)
    else:
        parsed = urlsplit(remote)
        host, path = parsed.hostname or "", parsed.path.lstrip("/")
    path = path.removesuffix(".git").strip("/")
    if not host or not path or any(part in {"", ".", ".."} for part in path.split("/")):
        return root, None, None
    return root, path, host.lower()


def _commit_web_url(host: str | None, repository: str | None, commit_id: str | None) -> str | None:
    if not host or not repository or not commit_id:
        return None
    path = quote(repository, safe="/")
    separator = "/-/commit/" if "gitlab" in host else "/commit/"
    return f"https://{host}/{path}{separator}{quote(commit_id, safe='')}"


def _commit_timeline(filename: str, root: str | None, config: Config, limit: int = 30) -> tuple[OwnershipTimelineEntry, ...]:
    if root is None:
        return ()
    result = _git(root, "log", f"-{limit}", "--format=%H%x09%an%x09%cI%x09%s", "--", filename)
    if result is None or result.returncode != 0:
        return ()
    entries: list[OwnershipTimelineEntry] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        entries.append(
            OwnershipTimelineEntry(
                commit_id=parts[0],
                author=sanitize_text(
                    _normalize_personal_identity(
                        parts[1],
                        include_full_name=config.include_full_author_name,
                        privacy_mode=config.redact_private_identity,
                    )
                    or "",
                    config,
                ),
                date=sanitize_text(parts[2], config),
                summary=sanitize_text(parts[3], config),
            )
        )
    return tuple(entries)


def assess_change_risk(
    filename: str,
    *,
    sample_size: int = 50,
    now: datetime | None = None,
) -> ChangeRisk:
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename must be a non-empty path.")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or not 1 <= sample_size <= 500:
        raise ValueError("sample_size must be an integer between 1 and 500.")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    root = _git_root_for_path(filename)
    if root is None:
        return ChangeRisk()
    try:
        relative = Path(filename).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return ChangeRisk()
    result = _git(root, "log", f"-{sample_size}", "--format=COMMIT%x09%ct%x09%an%x09%s", "--numstat", "--", relative)
    if result is None or result.returncode != 0:
        return ChangeRisk()
    commits = 0
    churn = 0
    contributors: set[str] = set()
    timestamps: list[int] = []
    bug_fix_commits = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[0] == "COMMIT":
            commits += 1
            try:
                timestamps.append(int(parts[1]))
            except ValueError:
                continue
            contributors.add(parts[2].casefold())
            if re.search(r"\b(?:fix|bug|hotfix|revert|regression)\b", parts[3], re.IGNORECASE):
                bug_fix_commits += 1
        elif len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            churn += int(parts[0]) + int(parts[1])
    if not commits or not timestamps:
        return ChangeRisk()
    days_since = max(0, (reference.timestamp() - max(timestamps)) // 86400)
    recent = sum(reference.timestamp() - stamp <= 90 * 86400 for stamp in timestamps)
    score = min(
        100,
        min(40, recent * 5)
        + min(30, churn // 20)
        + min(20, max(0, len(contributors) - 1) * 5)
        + min(20, bug_fix_commits * 4)
        + (10 if days_since > 365 else 0),
    )
    level = "high" if score >= 65 else "medium" if score >= 30 else "low"
    freshness = max(0, 100 - int(days_since // 3) - min(25, max(0, len(contributors) - 1) * 5))
    return ChangeRisk(
        score=int(score),
        level=level,
        ownership_freshness=freshness,
        commits_sampled=commits,
        churn=churn,
        contributors=len(contributors),
        bug_fix_commits=bug_fix_commits,
        days_since_change=int(days_since),
    )


def _diff_preview(filename: str, root: str | None, commit_id: str | None, config: Config) -> tuple[str, ...]:
    if root is None or commit_id is None:
        return ()
    try:
        relative = Path(filename).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return ()
    result = _git(root, "show", "--format=", "--no-ext-diff", "--unified=0", commit_id, "--", relative)
    if result is None or result.returncode != 0:
        return ()
    selected = [
        sanitize_text(line, config, limit=500)
        for line in result.stdout.splitlines()
        if line.startswith("@@")
        or (line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    ]
    return tuple(selected[:40])


def analyze_responsibility(
    filename: str,
    line_number: int,
    *,
    config: Config | None = None,
    provider: VcsProvider | None = None,
    deployments: DeploymentRegistry | None = None,
    incidents: IncidentStore | None = None,
    diagnostic: Diagnostic | None = None,
    service: str | None = None,
    environment: str | None = None,
) -> ResponsibilityReport:
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename must be a non-empty path.")
    if isinstance(line_number, bool) or not isinstance(line_number, int) or line_number < 1:
        raise ValueError("line_number must be a positive integer.")
    config = config or default_config()
    base = _git_blame_for_line(filename, line_number, config=config)
    resolver = OwnershipResolver(
        codeowners_path=config.codeowners_path,
        file_owners=config.ownership_mapping,
        author_teams=config.team_mapping,
        escalation_paths=config.escalation_paths,
    )
    ownership = resolver.resolve(filename, author=base.author if base else None)
    ownership = OwnershipResolution(
        owners=tuple(
            sanitize_text(
                _normalize_personal_identity(
                    owner,
                    include_full_name=config.include_full_author_name,
                    privacy_mode=config.redact_private_identity,
                )
                or "",
                config,
            )
            for owner in ownership.owners
        ),
        team=sanitize_text(ownership.team, config) if ownership.team else None,
        escalation=tuple(sanitize_text(item, config) for item in ownership.escalation),
        source=ownership.source,
    )
    effective_team = ownership.team or (base.team if base else None)
    if not ownership.escalation and effective_team:
        escalation_mapping = {team.casefold(): chain for team, chain in config.escalation_paths}
        ownership = OwnershipResolution(
            owners=ownership.owners,
            team=effective_team,
            escalation=tuple(
                sanitize_text(item, config)
                for item in escalation_mapping.get(effective_team.casefold(), ())
            ),
            source=ownership.source,
        )
    elif effective_team != ownership.team:
        ownership = OwnershipResolution(
            owners=ownership.owners,
            team=effective_team,
            escalation=ownership.escalation,
            source=ownership.source,
        )
    if base and (ownership.recommendation or ownership.team):
        base = GitResponsibility(
            **{
                **base.to_dict(),
                "issue_labels": tuple(base.issue_labels),
                "team": ownership.team or base.team,
                "owner": ownership.owners[0] if ownership.owners else base.owner,
            }
        )
    root, repository, remote_host = _repository_url(filename)
    relative_file = Path(filename).resolve()
    git_file = str(relative_file.relative_to(Path(root).resolve())) if root else str(relative_file)
    timeline = _commit_timeline(git_file, root, config)
    commit_url = _commit_web_url(remote_host, repository, base.commit_id if base else None)
    commit = (
        CommitMetadata(
            sha=base.commit_id,
            title=base.summary or "",
            url=commit_url,
            author=base.author,
            committed_at=base.date,
            message=base.summary,
        )
        if base and base.commit_id
        else None
    )
    if base and commit_url:
        base = GitResponsibility(
            **{
                **base.to_dict(),
                "issue_labels": tuple(base.issue_labels),
                "commit_url": commit_url,
            }
        )
    pull_requests: tuple[PullRequestMetadata, ...] = ()
    issues: tuple[IssueMetadata, ...] = ()
    if provider is not None and repository and base and base.commit_id:
        commit = provider.get_commit(repository, base.commit_id)
        pull_requests = provider.get_pull_requests_for_commit(repository, base.commit_id)
        issue_ids = tuple(dict.fromkeys(re.findall(r"(?i)(?:#|(?:fix(?:es)?|close(?:s)?|resolve(?:s)?)\s+#)(\d+)", commit.message or base.summary or "")))
        issues = tuple(provider.get_issue(repository, issue_id) for issue_id in issue_ids[:10])
        commit = CommitMetadata(
            sha=commit.sha,
            title=sanitize_text(commit.title, config),
            url=sanitize_text(commit.url, config) if commit.url else None,
            author=sanitize_text(
                _normalize_personal_identity(
                    commit.author,
                    include_full_name=config.include_full_author_name,
                    privacy_mode=config.redact_private_identity,
                )
                or "",
                config,
            )
            if commit.author
            else None,
            committed_at=sanitize_text(commit.committed_at, config) if commit.committed_at else None,
            added=commit.added,
            removed=commit.removed,
            message=sanitize_text(commit.message, config) if commit.message else None,
        )
        pull_requests = tuple(
            PullRequestMetadata(
                number=item.number,
                title=sanitize_text(item.title, config),
                url=sanitize_text(item.url, config),
                author=sanitize_text(
                    _normalize_personal_identity(
                        item.author,
                        include_full_name=config.include_full_author_name,
                        privacy_mode=config.redact_private_identity,
                    )
                    or "",
                    config,
                )
                if item.author
                else None,
                merged_at=sanitize_text(item.merged_at, config) if item.merged_at else None,
            )
            for item in pull_requests
        )
        issues = tuple(
            IssueMetadata(
                identifier=item.identifier,
                title=sanitize_text(item.title, config),
                url=sanitize_text(item.url, config),
                labels=tuple(sanitize_text(label, config) for label in item.labels),
                project=sanitize_text(item.project, config) if item.project else None,
            )
            for item in issues
        )
        pr = pull_requests[0] if pull_requests else None
        issue = issues[0] if issues else None
        base = GitResponsibility(
            **{
                **base.to_dict(),
                "issue_labels": tuple(base.issue_labels),
                "commit_url": commit.url,
                "added_lines": commit.added,
                "removed_lines": commit.removed,
                "pr_number": pr.number if pr else base.pr_number,
                "pr_url": pr.url if pr else base.pr_url,
                "pull_request_title": pr.title if pr else base.pull_request_title,
                "pull_request_author": pr.author if pr else base.pull_request_author,
                "pull_request_merged_at": pr.merged_at if pr else base.pull_request_merged_at,
                "issue_id": issue.identifier if issue else base.issue_id,
                "issue_url": issue.url if issue else base.issue_url,
                "issue_title": issue.title if issue else base.issue_title,
                "issue_labels": issue.labels if issue else base.issue_labels,
                "issue_project": issue.project if issue else base.issue_project,
            }
        )
    deployment = (
        deployments.match(
            base.commit_id if base else None,
            service=service,
            environment=environment,
            repository=repository,
        )
        if deployments
        else None
    )
    related = (
        incidents.find_related(
            fingerprint=diagnostic.diagnostic_id if diagnostic else None,
            file_path=redact_path(str(relative_file), config),
            service=service,
            author=base.author if base else None,
        )
        if incidents
        else ()
    )
    diff_preview = _diff_preview(
        str(relative_file),
        root,
        base.commit_id if base else None,
        config,
    )
    if base and diff_preview:
        added_lines = sum(line.startswith("+") for line in diff_preview)
        removed_lines = sum(line.startswith("-") for line in diff_preview)
        base = GitResponsibility(
            **{
                **base.to_dict(),
                "issue_labels": tuple(base.issue_labels),
                "added_lines": commit.added if commit and commit.added is not None else added_lines,
                "removed_lines": commit.removed if commit and commit.removed is not None else removed_lines,
            }
        )
    return ResponsibilityReport(
        filename=sanitize_text(redact_path(str(relative_file), config), config),
        line_number=line_number,
        responsibility=base,
        ownership=ownership,
        change_risk=assess_change_risk(str(relative_file)),
        timeline=timeline,
        commit=commit,
        diff_preview=diff_preview,
        pull_requests=pull_requests,
        issues=issues,
        deployment=deployment,
        related_incidents=related,
        repository=repository,
        service=sanitize_text(service, config) if service else None,
        environment=sanitize_text(environment, config) if environment else None,
        diagnostic_id=sanitize_text(diagnostic.diagnostic_id, config) if diagnostic else None,
        exception_type=sanitize_text(diagnostic.exception_type, config) if diagnostic else None,
        failure_summary=sanitize_text(diagnostic.summary, config) if diagnostic else None,
    )


def analyze_responsibilities(
    locations: Iterable[tuple[str, int]],
    *,
    config: Config | None = None,
    providers: Mapping[str, VcsProvider] | None = None,
    deployments: DeploymentRegistry | None = None,
    incidents: IncidentStore | None = None,
    services: Mapping[str, str] | None = None,
    environments: Mapping[str, str] | None = None,
) -> tuple[ResponsibilityReport, ...]:
    reports = []
    for filename, line_number in locations:
        _, repository, _ = _repository_url(filename)
        provider = providers.get(repository) if providers and repository else None
        reports.append(
            analyze_responsibility(
                filename,
                line_number,
                config=config,
                provider=provider,
                deployments=deployments,
                incidents=incidents,
                service=services.get(repository) if services and repository else None,
                environment=environments.get(repository) if environments and repository else None,
            )
        )
    return tuple(reports)


def record_incident(
    diagnostic: Diagnostic,
    store: IncidentStore,
    *,
    service: str | None = None,
    environment: str | None = None,
    repository: str | None = None,
    config: Config | None = None,
) -> IncidentRecord:
    return store.record(
        diagnostic,
        service=service,
        environment=environment,
        repository=repository,
        config=config,
    )


def render_responsibility_report(
    report: ResponsibilityReport,
    *,
    stream: TextIO | None = None,
) -> str:
    def safe(value: str | None) -> str:
        if value is None:
            return ""
        return "".join(
            character
            if character in "\n\t" or ord(character) >= 32 and not 127 <= ord(character) < 160
            else f"\\x{ord(character):02x}"
            for character in value
        )

    responsibility = report.responsibility
    lines = [
        f"Responsibility: {safe(report.filename)}:{report.line_number}",
        f"  Recommended owner: {safe(report.recommendation) or 'Unassigned'}",
        f"  Team: {safe(report.ownership.team) or 'Unassigned'}",
        f"  Escalation target: {safe(report.escalation_target) or 'None configured'}",
        f"  Attribution: {safe(responsibility.confidence if responsibility else 'unavailable')}",
        f"  Actionability: {report.responsibility_score.level} "
        f"({report.responsibility_score.score}/100)",
        f"  Change risk: {report.change_risk.level}"
        + (f" ({report.change_risk.score}/100)" if report.change_risk.score is not None else ""),
    ]
    if report.exception_type:
        lines.insert(1, f"  Failure: {safe(report.exception_type)}: {safe(report.failure_summary)}")
    if responsibility and responsibility.commit_id:
        lines.append(f"  Commit: {safe(responsibility.commit_id)}")
    if report.commit and report.commit.url:
        lines.append(f"  Commit URL: {safe(report.commit.url)}")
    if report.recommended_reviewer:
        lines.append(f"  Suggested reviewer: {safe(report.recommended_reviewer)}")
    for pull_request in report.pull_requests:
        lines.append(f"  Pull request: #{pull_request.number} {safe(pull_request.title)} ({safe(pull_request.url)})")
    for issue in report.issues:
        labels = f" [{', '.join(safe(label) for label in issue.labels)}]" if issue.labels else ""
        lines.append(f"  Issue: {safe(issue.identifier)} {safe(issue.title)}{labels} ({safe(issue.url)})")
    if report.deployment:
        lines.append(
            f"  Deployment: {safe(report.deployment.service)} / "
            f"{safe(report.deployment.environment)} ({safe(report.deployment.version)})"
        )
    if report.related_incidents:
        lines.append(f"  Related incidents: {len(report.related_incidents)}")
    rendered = "\n".join(lines)
    if stream is not None:
        stream.write(rendered + "\n")
    return rendered


def ownership_heatmap(reports: Iterable[ResponsibilityReport]) -> tuple[dict[str, object], ...]:
    aggregate: dict[str, dict[str, object]] = {}
    for report in reports:
        path = report.filename
        item = aggregate.setdefault(
            path,
            {
                "file": path,
                "team": report.ownership.team,
                "owners": list(report.ownership.owners),
                "risk_score": report.change_risk.score,
                "risk_level": report.change_risk.level,
                "incident_count": 0,
            },
        )
        item["incident_count"] = int(item["incident_count"]) + len(report.related_incidents)
        if report.change_risk.score is not None and (
            item["risk_score"] is None or report.change_risk.score > int(item["risk_score"])
        ):
            item["risk_score"] = report.change_risk.score
            item["risk_level"] = report.change_risk.level
    return tuple(aggregate[path] for path in sorted(aggregate))


def responsibility_graph(reports: Iterable[ResponsibilityReport]) -> dict[str, list[dict[str, str]]]:
    nodes: dict[str, dict[str, str]] = {}
    edges: set[tuple[str, str, str]] = set()

    def node(identifier: str, kind: str, label: str) -> None:
        nodes[identifier] = {"id": identifier, "type": kind, "label": label}

    for report in reports:
        file_id = f"file:{report.repository or ''}:{report.filename}"
        node(file_id, "file", report.filename)
        if report.repository:
            repository_id = f"repository:{report.repository}"
            node(repository_id, "repository", report.repository)
            edges.add((file_id, repository_id, "in_repository"))
        if report.responsibility and report.responsibility.commit_id:
            commit_id = f"commit:{report.repository or ''}:{report.responsibility.commit_id}"
            node(commit_id, "commit", report.responsibility.commit_id)
            edges.add((file_id, commit_id, "attributed_to"))
        if report.service:
            service_id = f"service:{report.service}"
            node(service_id, "service", report.service)
            edges.add((file_id, service_id, "serves"))
        if report.environment:
            environment_id = f"environment:{report.environment}"
            node(environment_id, "environment", report.environment)
            edges.add((file_id, environment_id, "runs_in"))
        if report.responsibility and report.responsibility.author:
            author_id = f"author:{report.responsibility.author}"
            node(author_id, "author", report.responsibility.author)
            edges.add((file_id, author_id, "last_touched_by"))
        if report.ownership.team:
            team_id = f"team:{report.ownership.team}"
            node(team_id, "team", report.ownership.team)
            edges.add((file_id, team_id, "owned_by"))
        for owner in report.ownership.owners:
            owner_id = f"owner:{owner}"
            node(owner_id, "owner", owner)
            edges.add((file_id, owner_id, "owned_by"))
        for item in report.pull_requests:
            pr_id = f"pr:{report.repository or ''}:{item.number}"
            node(pr_id, "pull_request", item.title)
            edges.add((file_id, pr_id, "introduced_by"))
        for item in report.issues:
            issue_id = f"issue:{report.repository or ''}:{item.identifier}"
            node(issue_id, "issue", item.title)
            edges.add((file_id, issue_id, "tracked_by"))
        if report.deployment:
            deployment_id = f"deployment:{report.deployment.environment}:{report.deployment.commit_id}"
            node(deployment_id, "deployment", report.deployment.version or report.deployment.environment)
            edges.add((file_id, deployment_id, "deployed_in"))
        for incident in report.related_incidents:
            incident_id = f"incident:{incident.diagnostic_id}"
            node(incident_id, "incident", incident.exception_type)
            edges.add((file_id, incident_id, "affected_by"))
    return {
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [
            {"source": source, "target": target, "relationship": relationship}
            for source, target, relationship in sorted(edges)
        ],
    }


def _dashboard_link(url: str, label: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return escape(label)
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def render_responsibility_dashboard(
    reports: Iterable[ResponsibilityReport],
    *,
    title: str = "LucidLog Responsibility Dashboard",
) -> str:
    rows = []
    for report in reports:
        risk = report.change_risk.level
        if risk not in {"low", "medium", "high", "unavailable"}:
            risk = "unavailable"
        owner = report.recommendation or "Unassigned"
        changes = report.commit.title if report.commit else (
            report.responsibility.summary if report.responsibility else "No Git attribution"
        )
        failure = (
            f"{report.exception_type}: {report.failure_summary}"
            if report.exception_type
            else "No runtime exception supplied"
        )
        pull_request = report.pull_requests[0] if report.pull_requests else None
        issue = report.issues[0] if report.issues else None
        row = [
            "<tr>",
            f"<td>{escape(report.filename)}</td>",
            f"<td>{escape(failure)}</td>",
            f"<td>{escape(owner)}</td>",
            f"<td>{report.responsibility_score.score} ({escape(report.responsibility_score.level)})</td>",
            f'<td class="risk-{escape(risk)}">{escape(risk)}</td>',
            f"<td>{escape(changes or '')}</td>",
        ]
        row.append(
            f"<td>{_dashboard_link(pull_request.url, pull_request.title)}</td>"
            if pull_request
            else "<td>None linked</td>"
        )
        row.append(
            f"<td>{_dashboard_link(issue.url, issue.title)}</td>"
            if issue
            else "<td>None linked</td>"
        )
        row.extend((f"<td>{len(report.related_incidents)}</td>", "</tr>"))
        rows.append("".join(row))
    safe_title = escape(title)
    body = "\n".join(rows) if rows else '<tr><td colspan="9">No responsibility reports available.</td></tr>'
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{safe_title}</title>"
        "<style>body{font:15px system-ui,sans-serif;margin:2rem;color:#172033}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #d5dbe5;"
        "padding:.65rem;text-align:left}th{background:#edf1f7}.risk-high{color:#a12020;font-weight:700}"
        ".risk-medium{color:#805600;font-weight:700}.risk-low{color:#176b3a}</style></head><body>"
        f"<h1>{safe_title}</h1><table><thead><tr><th>File</th><th>Failure</th><th>Recommended owner</th>"
        f"<th>Actionability</th><th>Risk</th><th>Change</th><th>Pull request</th><th>Issue</th>"
        f"<th>Incidents</th></tr></thead><tbody>{body}</tbody></table>"
        "</body></html>"
    )
