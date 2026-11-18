# Decision Comment Example

This is a copyable aid, not an approval or a grant of authority. An authorized maintainer records an actual decision in the proposal's PR or discussion under the [governance guidelines](../../GOVERNANCE.md) and [RFC process](0003-rfc-and-adr-process.md). Leave unknown facts `null` or `pending`; an incomplete draft is not an acceptance record.

```markdown
## RFC decision

- Proposal: pending — link the RFC and review PR or discussion.
- Proposed content: pending — full commit SHA and RFC path reviewed.
- Accepted content: null — full commit SHA and RFC path only if accepted.
- Decision maker: pending — actual name or GitHub account and applicable authority.
- Reviewers: pending — actual names or GitHub accounts with links to their reviews.
- Conclusion: pending — accepted, rejected, or deferred; summarize the decision.
- Rationale: pending — explain the choice and how substantive feedback was addressed.
- Effective scope and date: pending — identify covered behavior or rules and the actual date with timezone; state when no change takes effect.
- Open work: pending — remaining decisions, implementation, validation, or limitations.
- Implementation links: pending — existing PRs or issues, if any; no extra tracking issue is required.
```

The recorded SHA identifies the reviewed proposal content, not necessarily the final merge commit. Link the RFC at that full SHA. If the merge produces a different commit, verify that it preserves the decided content; substantive changes need renewed review rather than a replacement SHA implying prior approval.

Once the actual decision exists, update the RFC's `status`, set `decision_record` to the comment permalink, and synchronize the [index](README.md). Keep implementation status separate. Copying this example or merging a draft does not accept the proposal or grant implementation, merge, or release permissions.
