# Review Guidelines

Use the applicable `AGENTS.md`, [contribution guide](CONTRIBUTING.md), and affected contracts. Reviewers and coding agents follow the same evidence standards. Approval and merge authority follow [GOVERNANCE.md](GOVERNANCE.md).

## Establish the behavior

- Record the full reviewed SHA and the comparison base. Before publishing or approving, check for new commits and existing feedback; recheck findings affected by updates.
- Read the problem, relevant accepted RFC or ADR, diff, and affected callers and providers. Draft proposals do not establish current requirements. Implementation PRs should identify which part of a decision they deliver and what remains unfinished.
- Follow changed behavior through the relevant public interface, storage, deployment, or integration boundary. For a finding, state the inputs or conditions, expected behavior, actual behavior, and evidence. Distinguish executed reproductions from code-path analysis and unresolved hypotheses.
- Select checks by impact. Reuse the author's applicable evidence and reproduce material concerns. Fixtures, local tests, and screenshots establish only the behavior they actually exercise; see the contribution guide for evidence requirements.

## Separate required fixes from suggestions

Request changes for a demonstrated violation of an agreed contract, such as incorrect results, broken authorization, data loss, or an unusable supported upgrade path. State the required outcome without prescribing an unrelated implementation preference.

Label alternative abstractions, optional optimizations, and preferences as **Design note (non-blocking)** unless they violate an established requirement. Explain the tradeoff. Missing tests alone do not prove a defect, and passing tests do not disprove a demonstrated failure. A material validation gap may prevent approval; name the unsupported claim and the evidence needed.

For cross-component changes, inspect the callers and providers that promise the affected behavior. Check actual supported combinations rather than assuming every integration has identical capabilities. Tie any missing counterpart to a documented promise or record it as a non-blocking follow-up.

## Leave actionable feedback

- Write concise English and attach each finding to the smallest relevant changed range. Keep one thread per issue and link equivalent affected paths.
- Put required fixes before non-blocking suggestions. Avoid generic checklists, speculative failures, repeated resolved comments, and unrelated refactoring requests.
- Summarize material verification limits and unresolved decisions. Approval requires the relevant maintainer's judgment; a coding agent's review does not grant approval or merge authority.
- Record substantive design decisions in the linked RFC, ADR review, or PR. Use the [decision-comment example](docs/rfcs/decision-comment.md) when recording an RFC outcome. Do not mark a proposal accepted on someone else's behalf.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Handle vulnerability details through the [security reporting guide](SECURITY.md), not public review comments.
