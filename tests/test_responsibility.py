import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import lucidlog
from lucidlog.integrations.notifications import SlackWebhookNotifier, TeamsWebhookNotifier
from lucidlog.integrations.providers import GitHubProvider, IssueMetadata, PullRequestMetadata
from lucidlog.models import GitResponsibility
from lucidlog.ownership import OwnershipResolver
from lucidlog.responsibility import ResponsibilityReport


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    repository = tmp_path / "repo"
    source = repository / "src" / "payments" / "service.py"
    source.parent.mkdir(parents=True)
    (repository / ".github").mkdir()
    (repository / ".github" / "CODEOWNERS").write_text(
        "* @org/default\nsrc/payments/*.py @org/payments @on-call\n",
        encoding="utf-8",
    )
    source.write_text("def fail():\n    raise RuntimeError('payment failure')\n", encoding="utf-8")
    _git(repository, "init")
    _git(repository, "config", "user.name", "Test Author")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Fix payment failure; closes #123")
    _git(repository, "remote", "add", "origin", "https://github.com/acme/payments.git")
    return repository, source, _git(repository, "rev-parse", "HEAD")


def test_codeowners_last_matching_rule_and_escalation(tmp_path):
    repository, source, _ = _git_repository(tmp_path)
    resolver = OwnershipResolver(
        author_teams=(("Test Author", "payments"),),
        escalation_paths=(("payments", ("payments-lead", "payments-on-call")),),
    )

    result = resolver.resolve(str(source), author="Test Author")

    assert result.owners == ("@org/payments", "@on-call")
    assert result.team == "payments"
    assert result.escalation == ("payments-lead", "payments-on-call")
    assert result.recommendation == "payments"
    assert result.source == "codeowners"
    assert repository.exists()


def test_configured_file_owner_mapping_overrides_codeowners(tmp_path):
    _, source, _ = _git_repository(tmp_path)
    resolver = OwnershipResolver(
        file_owners=(("src/payments/*.py", ("payments-primary", "payments-backup")),)
    )

    result = resolver.resolve(str(source))

    assert result.owners == ("payments-primary", "payments-backup")
    assert result.source == "configured"


def test_uncommitted_source_lines_are_not_reported_as_exact_blame(tmp_path):
    _, source, _ = _git_repository(tmp_path)
    source.write_text("def fail():\n    raise RuntimeError('uncommitted failure')\n", encoding="utf-8")

    report = lucidlog.analyze_responsibility(str(source), 2)

    assert report.responsibility is not None
    assert report.responsibility.confidence == "file_fallback"


def test_responsibility_report_correlates_git_history_risk_and_diff(tmp_path):
    repository, source, commit_id = _git_repository(tmp_path)
    config = lucidlog.Config(
        team_mapping=(("Test Author", "payments"),),
        escalation_paths=(("payments", ("payments-on-call",)),),
    )

    report = lucidlog.analyze_responsibility(str(source), 2, config=config, service="payments")

    assert report.responsibility is not None
    assert report.responsibility.commit_id == commit_id
    assert report.ownership.team == "payments"
    assert report.ownership.escalation == ("payments-on-call",)
    assert report.timeline[0].author == "Test Author"
    assert report.change_risk.score is not None
    assert report.change_risk.ownership_freshness is not None
    assert any("raise RuntimeError" in line for line in report.diff_preview)
    assert report.repository == "acme/payments"
    assert report.to_dict()["change_risk"]["level"] in {"low", "medium", "high"}
    assert repository.exists()


def test_identity_policy_keeps_team_routing_without_emails(tmp_path):
    _, source, _ = _git_repository(tmp_path)
    config = lucidlog.Config(
        include_email=False,
        redact_private_identity=True,
        team_mapping=(("Test Author", "payments"),),
        escalation_paths=(("payments", ("payments-on-call",)),),
    )

    report = lucidlog.analyze_responsibility(str(source), 2, config=config)

    assert report.responsibility is not None
    assert report.responsibility.email is None
    assert report.responsibility.author != "Test Author"
    assert report.ownership.team == "payments"
    assert report.escalation_target == "payments-on-call"


def test_external_provider_enrichment_attaches_commit_pr_and_issue_metadata(tmp_path):
    _, source, commit_id = _git_repository(tmp_path)

    class FakeProvider:
        def get_commit(self, repository: str, sha: str):
            assert repository == "acme/payments"
            assert sha == commit_id
            from lucidlog import CommitMetadata

            return CommitMetadata(
                sha=sha,
                title="Fix payment failure",
                url="https://github.com/acme/payments/commit/" + sha,
                author="Test Author",
                added=2,
                removed=0,
                message="Fix payment failure; closes #123",
            )

        def get_pull_requests_for_commit(self, repository: str, sha: str):
            return (PullRequestMetadata(8, "Payment fix", "https://github.com/acme/payments/pull/8", "reviewer", "2026-10-30T00:00:00Z"),)

        def get_issue(self, repository: str, identifier: str):
            assert identifier == "123"
            return IssueMetadata("123", "Payment incident", "https://github.com/acme/payments/issues/123", ("incident",))

    report = lucidlog.analyze_responsibility(str(source), 2, provider=FakeProvider())

    assert report.commit is not None and report.commit.added == 2
    assert report.pull_requests[0].number == 8
    assert report.issues[0].identifier == "123"
    assert report.responsibility is not None
    assert report.responsibility.pr_number == 8
    assert report.responsibility.issue_title == "Payment incident"
    assert report.responsibility.issue_labels == ("incident",)


def test_github_provider_uses_authenticated_bounded_explicit_request():
    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int):
            assert size == 1_000_001
            return json.dumps(
                {
                    "sha": "a" * 40,
                    "html_url": "https://github.com/acme/payments/commit/" + "a" * 40,
                    "commit": {
                        "message": "fix",
                        "author": {"name": "Author", "date": "2026-01-01T00:00:00Z"},
                    },
                    "stats": {"additions": 3, "deletions": 1},
                }
            ).encode()

    def opener(request, *, timeout):
        observed["url"] = request.full_url
        observed["authorization"] = request.get_header("Authorization")
        observed["timeout"] = timeout
        return Response()

    provider = GitHubProvider("test-token", opener=opener, timeout=2.5)
    commit = provider.get_commit("acme/payments", "a" * 40)

    assert observed["url"].endswith("/repos/acme/payments/commits/" + "a" * 40)
    assert observed["authorization"] == "Bearer test-token"
    assert observed["timeout"] == 2.5
    assert commit.added == 3
    assert commit.removed == 1


def test_github_provider_returns_pull_requests_and_issue_labels():
    responses = [
        [
            {
                "number": 17,
                "title": "fix issue",
                "html_url": "https://github.com/acme/payments/pull/17",
                "user": {"login": "reviewer"},
                "merged_at": "2026-10-30T00:00:00Z",
            }
        ],
        {
            "number": 123,
            "title": "payment regression",
            "html_url": "https://github.com/acme/payments/issues/123",
            "labels": [{"name": "incident"}, {"name": "payments"}],
        },
    ]

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int):
            return json.dumps(self.value).encode()

    def opener(_request, *, timeout):
        assert timeout == 5.0
        return Response(responses.pop(0))

    provider = GitHubProvider("test-token", opener=opener)
    pull_requests = provider.get_pull_requests_for_commit("acme/payments", "b" * 40)
    issue = provider.get_issue("acme/payments", "#123")

    assert pull_requests[0].number == 17
    assert pull_requests[0].author == "reviewer"
    assert pull_requests[0].merged_at.startswith("2026-10-30")
    assert issue.labels == ("incident", "payments")
    assert issue.project == "acme/payments"


def test_gitlab_provider_uses_encoded_project_paths():
    from lucidlog import GitLabProvider

    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int):
            return json.dumps(
                {
                    "id": "c" * 40,
                    "title": "fix failure",
                    "web_url": "https://gitlab.example/group/subgroup/project/-/commit/" + "c" * 40,
                    "author_name": "Developer",
                    "committed_date": "2026-01-01T00:00:00Z",
                    "stats": {"additions": 1, "deletions": 2},
                }
            ).encode()

    def opener(request, *, timeout):
        observed["url"] = request.full_url
        observed["token"] = request.get_header("Private-token")
        return Response()

    provider = GitLabProvider(
        "gitlab-token",
        api_url="https://gitlab.example/api/v4",
        opener=opener,
    )
    commit = provider.get_commit("group/subgroup/project", "c" * 40)

    assert "%2F" in observed["url"]
    assert observed["token"] == "gitlab-token"
    assert commit.added == 1
    assert commit.removed == 2


def test_provider_validation_and_errors_are_explicit():
    with pytest.raises(ValueError):
        GitHubProvider("")
    with pytest.raises(ValueError):
        GitHubProvider("token", api_url="http://example.test")
    with pytest.raises(ValueError):
        GitHubProvider("token").get_commit("acme/payments", "not-a-sha")


def test_incident_store_persists_only_sanitized_metadata(tmp_path):
    store = lucidlog.IncidentStore(tmp_path / "incidents.sqlite3")
    try:
        raise RuntimeError("customer-secret-8675309")
    except RuntimeError as error:
        diagnostic = lucidlog.diagnose(error)

    record = lucidlog.record_incident(
        diagnostic,
        store,
        service="payments",
        environment="production",
    )
    matches = store.find_related(file_path=record.file_path, service="payments")

    assert len(matches) == 1
    assert matches[0].exception_type == "RuntimeError"
    with sqlite3.connect(store.path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(incidents)")}
        stored_text = " ".join(
            str(value)
            for row in connection.execute("SELECT * FROM incidents")
            for value in row
        )
    assert "message" not in columns
    assert "traceback" not in columns
    assert "customer-secret-8675309" not in stored_text


def test_runtime_diagnostic_enriches_report_and_correlates_same_incident(tmp_path):
    _, source, _ = _git_repository(tmp_path)
    store = lucidlog.IncidentStore(tmp_path / "runtime-incidents.sqlite3")
    try:
        exec(compile("raise RuntimeError('service failure')", str(source), "exec"), {})
    except RuntimeError as error:
        diagnostic = lucidlog.diagnose(error)
    lucidlog.record_incident(diagnostic, store, service="payments")

    report = lucidlog.analyze_responsibility(
        str(source),
        1,
        diagnostic=diagnostic,
        incidents=store,
        service="payments",
    )

    assert report.exception_type == "RuntimeError"
    assert report.failure_summary
    assert report.diagnostic_id == diagnostic.diagnostic_id
    assert len(report.related_incidents) == 1
    assert "Failure: RuntimeError" in lucidlog.render_responsibility_report(report)


def test_deployment_correlation_risk_and_ownership_visualizations(tmp_path):
    _, source, commit_id = _git_repository(tmp_path)
    deployment = lucidlog.DeploymentRecord(
        commit_id=commit_id,
        service="payments",
        environment="production",
        deployed_at="2026-10-31T08:00:00+00:00",
        version="2026.10.31.1",
        repository="acme/payments",
    )
    deployments = lucidlog.DeploymentRegistry([deployment])
    report = lucidlog.analyze_responsibility(
        str(source),
        2,
        deployments=deployments,
        service="payments",
        environment="production",
    )

    assert report.deployment == deployment
    heatmap = lucidlog.ownership_heatmap([report])
    graph = lucidlog.responsibility_graph([report])
    dashboard = lucidlog.render_responsibility_dashboard([report], title="<safe dashboard>")

    assert heatmap[0]["risk_level"] in {"low", "medium", "high"}
    assert any(node["type"] == "deployment" for node in graph["nodes"])
    assert "&lt;safe dashboard&gt;" in dashboard
    assert "<safe dashboard>" not in dashboard
    assert "<th>Failure</th>" in dashboard


def test_multi_repository_analysis_and_terminal_report_rendering(tmp_path):
    _, source, _ = _git_repository(tmp_path)

    reports = lucidlog.analyze_responsibilities(
        [(str(source), 2)],
        services={"acme/payments": "payments"},
        environments={"acme/payments": "staging"},
    )
    text = lucidlog.render_responsibility_report(reports[0])

    assert reports[0].service == "payments"
    assert reports[0].environment == "staging"
    assert "Recommended owner:" in text
    assert "Change risk:" in text


def test_notifications_are_explicit_redacted_and_html_escaped():
    report = ResponsibilityReport(
        filename="service.py",
        line_number=12,
        responsibility=GitResponsibility(commit_id="a" * 40),
        ownership=lucidlog.OwnershipResolution(owners=("@payments",), team="payments"),
        change_risk=lucidlog.ChangeRisk(score=70, level="high"),
        pull_requests=(PullRequestMetadata(5, "<img src=x> token=super-secret", "https://example.test/pr/5"),),
        issues=(),
        service="payments",
        environment="production",
    )
    payloads = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int):
            return b""

    def opener(request, *, timeout):
        payloads.append(json.loads(request.data.decode()))
        return Response()

    SlackWebhookNotifier("https://hooks.slack.test/services/demo", opener=opener).send(report)
    TeamsWebhookNotifier("https://hooks.teams.test/webhook/demo", opener=opener).send(report)

    assert "[REDACTED]" in payloads[0]["text"]
    assert "super-secret" not in repr(payloads)
    assert "&lt;img" in payloads[1]["text"]


def test_risk_sample_bounds_and_timezone_validation(tmp_path):
    _, source, _ = _git_repository(tmp_path)

    with pytest.raises(ValueError):
        lucidlog.assess_change_risk(str(source), sample_size=501)
    with pytest.raises(ValueError):
        lucidlog.assess_change_risk(str(source), now=datetime.now())
