# AI Vision Security Reporting

This guide prepares the bootstrap reporting process under [GOVERNANCE.md](GOVERNANCE.md). A named security recipient and tested private contact route have not yet been confirmed in this repository. See the [pending confirmations](GOVERNANCE.md#pending-owner-and-contact-confirmations). Adding this file does not enable GitHub private reporting or establish an organizational security service, response deadline, or reward program.

## Report privately

Do not put vulnerability details, exploit instructions, credentials, personal data, or private traces in public issues, pull requests, discussions, or release notes.

If GitHub displays **Report a vulnerability** for this repository and the maintainers have confirmed who receives those reports, use that private channel. Otherwise, ask an existing maintainer to confirm a private recipient and route before sending details. A public issue may request only a private security contact, without describing the vulnerability. If that request would itself disclose sensitive information, keep the report private until a verified route is available.

Do not assume that an Ant Group or OceanBase email address or reporting program covers this repository. Use only a route explicitly confirmed for AI Vision.

## What to include once a route is confirmed

- Affected component, actual version or commit, and deployment conditions. If unknown, state that and explain why.
- Expected security behavior, observed failure, likely impact, and the access or permissions needed to reproduce it.
- Minimal reproduction steps and sanitized evidence. Avoid real user data and secrets; provide only what is necessary.
- Any known workaround, related public reference, preferred private reply route, and intended disclosure timing.

Reports may concern the platform, AgentLens, collection and agent integrations, the CLI, or build and deployment artifacts. Identify third-party dependencies involved so maintainers can coordinate with the responsible project. A report does not require a proposed fix.

## Triage, fixes, and disclosure

Once responsibilities are confirmed, the receiving maintainers assess the report, involve relevant component owners, and coordinate validation, remediation, and disclosure with the reporter. Publish an advisory or release note when appropriate, describing affected and fixed versions, mitigation, and agreed attribution without exposing private information.

Supported release coverage, response expectations, and a backup recipient remain unconfirmed. Planned versions in [RFC 0002](docs/rfcs/0002-versioning-and-releases.md) are not a security-support matrix. Do not infer support or a published release from a version target, repository file, or build tag. This reporting guide does not define scanner exclusions or accepted vulnerabilities.
