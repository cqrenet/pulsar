# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue.

Use GitHub's private reporting: open the **Security** tab of this repository and choose
**Report a vulnerability**. Include the affected version or commit, what an attacker can do, and steps to
reproduce. Do not include real tenant data; redact tenant IDs, domains and secrets.

Include the version (`/api/version`), what you did, what you expected and what happened. Target response times (best effort, not a contract): acknowledgement within 5 business days, an assessment
within 15 business days, and a fix or mitigation plan for confirmed issues after that. Reporters are credited in the
release notes unless they ask not to be.

## Supported versions

Only the latest release receives security fixes. Upgrade notes for breaking changes are in
[CHANGELOG.md](CHANGELOG.md) and [DEPLOY.md](DEPLOY.md).

## What to read first

| Document | Contents |
|---|---|
| [docs/security/README.md](docs/security/README.md) | **Reviewer guide**: scope, code map, build and test, a local sandbox without a Microsoft tenant |
| [docs/security/known-issues.md](docs/security/known-issues.md) | Every weakness and limit we know about, with our severity rating and status |
| [docs/security/threat-model.md](docs/security/threat-model.md) | Architecture, trust boundaries, STRIDE analysis, controls with evidence, residual risk |
| [docs/security/operations.md](docs/security/operations.md) | Access matrix, data inventory and retention, secret rotation, monitoring, hardening checklist |
| [docs/security/outbound-http-review.md](docs/security/outbound-http-review.md) | Every outbound HTTP call and its controls |
| [DEPLOY.md](DEPLOY.md) | Hardened deployment and upgrades |

Before reporting, check [known-issues.md](docs/security/known-issues.md): if the issue is listed, a report is still
welcome when you think our severity rating is wrong or you found a way to make it worse.

## Automated checks

Every push and pull request runs: ruff, the test suite (including route-authorisation tests), `pip-audit` on the
hash-locked dependencies, a Trivy scan of the built image and of the Dockerfile, a gitleaks scan of the full git
history, and CodeQL for Python and JavaScript. The release workflow repeats the image scan and blocks on fixable
HIGH/CRITICAL findings.

## Project process

PULSAR has a single maintainer. Changes reach `main` only through pull requests that pass all of the checks above,
but there is no second-person review, and commits and tags are not signed. Release tags are immutable once pushed.
Release images are not signed and ship without an SBOM
([KI-22](docs/security/known-issues.md#ki-22-supply-chain-gaps---low-open),
[KI-23](docs/security/known-issues.md#ki-23-process---info-accepted)).
