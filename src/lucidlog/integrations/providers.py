from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Raised when an explicitly requested VCS provider operation fails."""


@dataclass(frozen=True)
class CommitMetadata:
    sha: str
    title: str
    url: str | None = None
    author: str | None = None
    committed_at: str | None = None
    added: int | None = None
    removed: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class PullRequestMetadata:
    number: int
    title: str
    url: str
    author: str | None = None
    merged_at: str | None = None


@dataclass(frozen=True)
class IssueMetadata:
    identifier: str
    title: str
    url: str
    labels: tuple[str, ...] = ()
    project: str | None = None


class VcsProvider(Protocol):
    def get_commit(self, repository: str, sha: str) -> CommitMetadata: ...

    def get_pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequestMetadata, ...]: ...

    def get_issue(self, repository: str, identifier: str) -> IssueMetadata: ...


Opener = Callable[..., Any]


class _JsonApi:
    def __init__(
        self,
        *,
        token: str,
        api_url: str,
        timeout: float,
        opener: Opener | None,
        user_agent: str,
    ) -> None:
        if not isinstance(token, str) or not token.strip() or "\r" in token or "\n" in token:
            raise ValueError("A non-empty provider token is required.")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive number.")
        parsed_url = urlsplit(api_url) if isinstance(api_url, str) else None
        if (
            parsed_url is None
            or parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Provider API URLs must use HTTPS.")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = float(timeout)
        self._opener = opener or urlopen
        self._user_agent = user_agent

    def _get(self, path: str, *, accept: str = "application/json") -> Any:
        request = Request(
            f"{self._api_url}{path}",
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
                "User-Agent": self._user_agent,
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                body = response.read(1_000_001)
        except HTTPError as error:
            raise ProviderError(f"VCS provider returned HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError) as error:
            raise ProviderError(f"VCS provider request failed: {type(error).__name__}.") from None
        if len(body) > 1_000_000:
            raise ProviderError("VCS provider response exceeded the 1 MB limit.")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("VCS provider returned invalid JSON.") from None


def _validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository must be a non-empty provider project path.")
    parts = repository.strip("/").split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("repository must be in owner/repository form.")
    return "/".join(quote(part, safe="") for part in parts)


def _validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise ValueError("sha must be a full hexadecimal commit identifier.")


def _validate_gitlab_project(repository: str) -> str:
    if not isinstance(repository, str) or not repository.strip("/"):
        raise ValueError("repository must be a non-empty GitLab project path.")
    parts = repository.strip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("repository must be a valid GitLab namespace/project path.")
    return quote(repository.strip("/"), safe="")


def _response_object(value: Any, provider: str, resource: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError(f"{provider} returned an unexpected {resource} response.")
    return value


class GitHubProvider(_JsonApi):
    """Explicit, token-authenticated GitHub metadata client; no calls happen at import."""

    def __init__(self, token: str, *, api_url: str = "https://api.github.com", timeout: float = 5.0, opener: Opener | None = None) -> None:
        super().__init__(token=token, api_url=api_url, timeout=timeout, opener=opener, user_agent="LucidLog")

    def get_commit(self, repository: str, sha: str) -> CommitMetadata:
        repo = _validate_repository(repository)
        _validate_sha(sha)
        result = self._get(f"/repos/{repo}/commits/{quote(sha, safe='')}")
        result = _response_object(result, "GitHub", "commit")
        details = _response_object(result.get("commit"), "GitHub", "commit details")
        author = details.get("author") or {}
        stats = result.get("stats") or {}
        if not isinstance(author, dict) or not isinstance(stats, dict):
            raise ProviderError("GitHub returned malformed commit metadata.")
        message = details.get("message", "")
        return CommitMetadata(
            sha=str(result.get("sha", sha)),
            title=message.splitlines()[0] if message else "",
            url=result.get("html_url"),
            author=author.get("name"),
            committed_at=author.get("date"),
            added=stats.get("additions"),
            removed=stats.get("deletions"),
            message=message,
        )

    def get_pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequestMetadata, ...]:
        repo = _validate_repository(repository)
        _validate_sha(sha)
        results = self._get(f"/repos/{repo}/commits/{quote(sha, safe='')}/pulls")
        if not isinstance(results, list):
            raise ProviderError("GitHub returned an unexpected pull request response.")
        pull_requests = []
        for item in results:
            if not isinstance(item, dict):
                raise ProviderError("GitHub returned a malformed pull request entry.")
            user = item.get("user") or {}
            try:
                number = int(item["number"])
                title = str(item["title"])
                url = str(item["html_url"])
            except (KeyError, TypeError, ValueError):
                raise ProviderError("GitHub returned incomplete pull request metadata.") from None
            if number < 1 or not title or not url or not isinstance(user, dict):
                raise ProviderError("GitHub returned incomplete pull request metadata.")
            pull_requests.append(
                PullRequestMetadata(
                    number=number,
                    title=title,
                    url=url,
                    author=user.get("login"),
                    merged_at=item.get("merged_at"),
                )
            )
        return tuple(pull_requests)

    def get_issue(self, repository: str, identifier: str) -> IssueMetadata:
        repo = _validate_repository(repository)
        number = str(identifier).removeprefix("#")
        if not number.isdecimal() or int(number) < 1:
            raise ValueError("GitHub issue identifier must be a positive number.")
        result = _response_object(self._get(f"/repos/{repo}/issues/{number}"), "GitHub", "issue")
        labels = result.get("labels", ())
        if not isinstance(labels, list):
            raise ProviderError("GitHub returned malformed issue labels.")
        if not isinstance(result.get("title"), str) or not isinstance(result.get("html_url"), str):
            raise ProviderError("GitHub returned incomplete issue metadata.")
        return IssueMetadata(
            identifier=number,
            title=str(result["title"]),
            url=str(result["html_url"]),
            labels=tuple(str(label["name"]) for label in labels if isinstance(label, dict) and label.get("name") is not None),
            project=repository,
        )


class GitLabProvider(_JsonApi):
    """Explicit, token-authenticated GitLab metadata client."""

    def __init__(self, token: str, *, api_url: str = "https://gitlab.com/api/v4", timeout: float = 5.0, opener: Opener | None = None) -> None:
        super().__init__(token=token, api_url=api_url, timeout=timeout, opener=opener, user_agent="LucidLog")

    def _get(self, path: str, *, accept: str = "application/json") -> Any:
        request = Request(
            f"{self._api_url}{path}",
            headers={
                "Accept": accept,
                "PRIVATE-TOKEN": self._token,
                "User-Agent": self._user_agent,
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                body = response.read(1_000_001)
        except HTTPError as error:
            raise ProviderError(f"VCS provider returned HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError) as error:
            raise ProviderError(f"VCS provider request failed: {type(error).__name__}.") from None
        if len(body) > 1_000_000:
            raise ProviderError("VCS provider response exceeded the 1 MB limit.")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("VCS provider returned invalid JSON.") from None

    def get_commit(self, repository: str, sha: str) -> CommitMetadata:
        repo = _validate_gitlab_project(repository)
        _validate_sha(sha)
        result = _response_object(
            self._get(f"/projects/{repo}/repository/commits/{quote(sha, safe='')}"),
            "GitLab",
            "commit",
        )
        stats = result.get("stats") or {}
        if not isinstance(stats, dict):
            raise ProviderError("GitLab returned malformed commit statistics.")
        return CommitMetadata(
            sha=str(result.get("id", sha)),
            title=str(result.get("title", "")),
            url=result.get("web_url"),
            author=result.get("author_name"),
            committed_at=result.get("committed_date"),
            added=stats.get("additions"),
            removed=stats.get("deletions"),
            message=result.get("message"),
        )

    def get_pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequestMetadata, ...]:
        repo = _validate_gitlab_project(repository)
        _validate_sha(sha)
        results = self._get(f"/projects/{repo}/repository/commits/{quote(sha, safe='')}/merge_requests")
        if not isinstance(results, list):
            raise ProviderError("GitLab returned an unexpected merge request response.")
        pull_requests = []
        for item in results:
            if not isinstance(item, dict):
                raise ProviderError("GitLab returned a malformed merge request entry.")
            author = item.get("author") or {}
            try:
                number = int(item["iid"])
                title = str(item["title"])
                url = str(item["web_url"])
            except (KeyError, TypeError, ValueError):
                raise ProviderError("GitLab returned incomplete merge request metadata.") from None
            if number < 1 or not title or not url or not isinstance(author, dict):
                raise ProviderError("GitLab returned incomplete merge request metadata.")
            pull_requests.append(
                PullRequestMetadata(
                    number=number,
                    title=title,
                    url=url,
                    author=author.get("username"),
                    merged_at=item.get("merged_at"),
                )
            )
        return tuple(pull_requests)

    def get_issue(self, repository: str, identifier: str) -> IssueMetadata:
        repo = _validate_gitlab_project(repository)
        issue_id = str(identifier).removeprefix("#")
        if not issue_id.isdecimal() or int(issue_id) < 1:
            raise ValueError("repository and a positive issue identifier are required.")
        result = _response_object(self._get(f"/projects/{repo}/issues/{issue_id}"), "GitLab", "issue")
        labels = result.get("labels", ())
        if not isinstance(labels, list):
            raise ProviderError("GitLab returned malformed issue labels.")
        if not isinstance(result.get("title"), str) or not isinstance(result.get("web_url"), str):
            raise ProviderError("GitLab returned incomplete issue metadata.")
        return IssueMetadata(
            identifier=issue_id,
            title=str(result["title"]),
            url=str(result["web_url"]),
            labels=tuple(str(label) for label in labels),
            project=repository,
        )
