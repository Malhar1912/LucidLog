from __future__ import annotations

from html import escape
import json
import math
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..config import Config, default_config
from ..redaction import sanitize_text
from ..responsibility import ResponsibilityReport


class NotificationError(RuntimeError):
    """Raised when an explicitly requested notification cannot be delivered."""


class _WebhookNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: float = 5.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        parsed_url = urlsplit(webhook_url) if isinstance(webhook_url, str) else None
        if (
            parsed_url is None
            or parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.fragment
        ):
            raise ValueError("Webhook URLs must be valid credential-free HTTPS URLs.")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive number.")
        self._url = webhook_url
        self._timeout = float(timeout)
        self._opener = opener or urlopen

    def _send(self, payload: dict[str, Any]) -> None:
        request = Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "LucidLog"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                status = getattr(response, "status", 200)
                response.read(4096)
        except HTTPError as error:
            raise NotificationError(f"Notification endpoint returned HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError) as error:
            raise NotificationError(
                f"Notification request failed: {type(error).__name__}."
            ) from None
        if not 200 <= status < 300:
            raise NotificationError(f"Notification endpoint returned HTTP {status}.")

    @staticmethod
    def _message(report: ResponsibilityReport, config: Config) -> str:
        responsibility = report.responsibility
        lines = [
            f"LucidLog: {responsibility.commit_id if responsibility else 'Unattributed failure'}",
            f"Location: {report.filename}:{report.line_number}",
            f"Recommended owner: {report.recommendation or 'Unassigned'}",
            f"Actionability: {report.responsibility_score.level} ({report.responsibility_score.score}/100)",
            f"Risk: {report.change_risk.level}",
        ]
        if report.exception_type:
            lines.insert(1, f"Failure: {report.exception_type}: {report.failure_summary}")
        if report.service or report.environment:
            lines.append(f"Service/environment: {report.service or 'unknown'} / {report.environment or 'unknown'}")
        if report.pull_requests:
            pr = report.pull_requests[0]
            lines.append(f"Pull request: {pr.title} ({pr.url})")
        if report.issues:
            issue = report.issues[0]
            lines.append(f"Issue: {issue.title} ({issue.url})")
        return sanitize_text("\n".join(lines), config, limit=3000)


class SlackWebhookNotifier(_WebhookNotifier):
    """Explicit Slack incoming-webhook notifier; messages are redacted before sending."""

    def send(self, report: ResponsibilityReport, *, config: Config | None = None) -> None:
        message = self._message(report, config or default_config())
        self._send({"text": message})


class TeamsWebhookNotifier(_WebhookNotifier):
    """Explicit Microsoft Teams MessageCard webhook notifier."""

    def send(self, report: ResponsibilityReport, *, config: Config | None = None) -> None:
        message = self._message(report, config or default_config())
        self._send(
            {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": "LucidLog responsibility report",
                "themeColor": "D83B01" if report.change_risk.level == "high" else "0078D4",
                "title": "LucidLog responsibility report",
                "text": escape(message).replace("\n", "<br>"),
            }
        )
