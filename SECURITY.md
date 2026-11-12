# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue.

- Use GitHub's **Report a vulnerability** button on the repository's Security tab (private advisory), or
- email the maintainers at the security contact your CQRE agreement names.

Include the version (`/api/version`), what you did, what you expected and what happened. We acknowledge a report
within 3 working days and aim to ship a fix or mitigation for confirmed issues within 30 days. We credit reporters
who want credit.

## Supported versions

Only the latest release receives security fixes. Upgrade notes for breaking changes are in
[CHANGELOG.md](CHANGELOG.md) and [DEPLOY.md](DEPLOY.md).

## What to read first

| Document | Contents |
|---|---|
| [docs/security/threat-model.md](docs/security/threat-model.md) | Architecture, trust boundaries, STRIDE analysis, controls with evidence, accepted risks |
| [docs/security/operations.md](docs/security/operations.md) | Access matrix, data inventory and retention, secret rotation, monitoring |
| [docs/security/outbound-http-review.md](docs/security/outbound-http-review.md) | Review of every outbound HTTP path |
| [DEPLOY.md](DEPLOY.md) | Hardened deployment, hardening checklist, upgrades |

## Automated checks

Every push and pull request runs: ruff, the test suite (including route-authorisation tests), `pip-audit` on the
hash-locked dependencies, a Trivy scan of the built image and of the Dockerfile, a gitleaks scan of the full git
history, and CodeQL for Python and JavaScript. The release workflow repeats the image scan and blocks on fixable
HIGH/CRITICAL findings.
