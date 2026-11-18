# Deployment targets implementation plan

**Goal:** Keep Docker as the dependency-light default; provide explicit optional Compose and Kubernetes workflows, using native Compose where appropriate and preserving lifecycle/data protections.

**Spec:** `docs/superpowers/specs/2026-10-25-deployment-targets-design.md`.

1. **CLI target routing:** Restore the Go Docker runtime for default/explicit Docker. Restrict the new Compose adapter to explicit selection, reject cross-target ownership, invoke native Compose status/logs, and cover routing and persistence with meaningful regression tests.
2. **Compose guard and acceptance:** Port prerequisite, ownership, lock, initialization, persistence and stop checks into Go. Remove the host Python controller from sources and exports. Provide explicit external Compose file/environment selection using the same guards. Adapt both Compose real-service harnesses; leave the existing default Docker harness semantics intact.
3. **Documentation:** Restore three peer methods across product contracts and three site locales. Explain dependencies per target, native inspection versus guarded mutation, target-specific purge semantics, source/release capability and stable project selectors. Validate links, navigation and MCP sources.
4. **Integration:** Regenerate assets; run source suites, build the actual candidate CLI and snapshot code identities. Run fresh native Linux Docker and four explicit Compose combinations. Independently review the integrated patch, repair material findings, and record exact validation scope and cleanup.

Implementation ends with reviewable local changes and truthful evidence. Git delivery/publication remains a separate step under the owning repositories' authorization rules.
