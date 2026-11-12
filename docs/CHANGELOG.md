# Changelog

All notable changes to LucidLog will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.6] - 2026-11-12

### Added
- Configurable team and file ownership mappings, CODEOWNERS resolution, escalation paths, and "who to ask" recommendations.
- Explicit GitHub and GitLab clients for commit, pull/merge request, and linked issue metadata.
- Commit diff previews, contribution timelines, and bounded heuristic change-risk and ownership-freshness scores.
- Deployment/environment correlation and privacy-conscious local SQLite incident history.
- Responsibility graph, ownership heatmap data, standalone HTML dashboard, and opt-in Slack/Teams webhooks.
- Structured `ResponsibilityRecord` compatibility alias and responsibility information in terminal diagnostics.

### Security
- VCS lookups and notifications only make network requests when explicitly invoked; credentials are passed in memory and never logged.
- Incident history stores diagnostic identifiers and ownership/runtime metadata, not exception messages or tracebacks.
- Provider responses, diff previews, identity fields, dashboard content, and notification text are bounded or escaped/redacted.

### Changed
- Added configurable ownership mapping and escalation options to `Config`.
- Package version advanced to 0.1.6.

## [0.1.5] - 2026-10-31

### Added
- Ownership-aware responsibility metadata with team and owner resolution.
- CODEOWNERS fallback and file-level blame fallback when line-level blame is unavailable.
- Responsibility confidence and source metadata for exact blame, file fallback, and CODEOWNERS-based ownership.
- Privacy controls for name and email exposure in responsibility details.

### Changed
- Responsibility data now includes richer context for escalation and incident triage.

## [0.1.4] - 2026-10-31

### Added
- Git-blame responsibility tracking for exception frames, including author attribution and commit metadata when a repository is available.

## [0.1.3] - 2026-10-28

### Added
- Professional release documentation, changelog, and local publishing helper.
- Production-ready repository structure with docs and scripts organization.
- CI and release automation workflow templates for GitHub.

### Changed
- Finalized version metadata and GitHub publication setup for a cleaner release pipeline.
- Improved README visibility with badges and project structure summary.

## [0.1.1] - 2026-10-28

### Added
- Full end-to-end example demonstrating chained exceptions, redaction, logging, and threading/asyncio integrations.
- GitHub Actions workflows for CI and release automation.
- Local publishing helper script and package metadata polish for release readiness.

### Changed
- Improved project metadata and documentation for a more production-ready presentation.

## [0.1.0] - 2026-10-28

### Added
- Initial public API for install/uninstall, diagnosis, rendering, and structured logging.
- Source-aware exception capture and redaction.
- Deterministic built-in diagnostic rules and modular integrations.
- Extensive enterprise-focused regression tests.
