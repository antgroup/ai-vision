# Layered Trace Capacity: OceanBase Integration Review, 2026-11-07

- Record: [trace-data-flow/layered-capacity-isolation@20261107-2203](../layered-capacity-isolation.md#trace-data-flow-layered-capacity-isolation20261107-2203).
- Exact reviewed ADR SHA-256: `133bc191335d75d530bc6ab763816530807cd133d7861f8886acd832e0c29022`.
- Previous complete revision: [20261107-2148](../layered-capacity-isolation-20261107-2148.md), preserved byte-for-byte with SHA-256 `e1f301bbf3886c39557a3290947284213f739076a3704973639b1369651fc343`; its [review](layered-capacity-isolation-research-20261107.md) remains historical.
- Decision status: **proposed**. Implementation status: **not_started**.
- Scope: the user's instruction to base the design on OceanBase capabilities, the [supporting execution profile](../research/oceanbase-execution-profile-20261107.md), native/journal snapshot alternatives and revised implementation-gap assessment. No database setting or product runtime was changed.

## Evidence and design review

Official OB/seekdb documentation was retrieved through Context7 and primary-source browsing. Separate read-only reviews covered transactional/index/snapshot semantics, LOB/HTAP/resource capabilities, and actual repository version/schema/fencing foundations. A fresh independent builder reviewed the complete revised ADR and its diff, then read back the same final hash above.

| Question | Resulting contract |
|---|---|
| Does the baseline actually exploit the current data foundation? | Reuse the owning MySQL-compatible telemetry module, existing relational keys/source locks/revision CAS and bounded transactions. Keep raw/current catalog/index/accounting in the same database/tenant fence; no mandatory second raw store. |
| Are atomicity and isolation overstated? | Explicit unique point locks/CAS protect shared invariants; empty-result/range `FOR UPDATE` and `LIMIT` are not lock/work budgets. No strict-serializability or automatic per-item transaction splitting is assumed. Current-value no-op and last-successful-arrival semantics remain. |
| Can native snapshots remove application history work? | Qualified native `AS OF SNAPSHOT` is preferred; only journal fallback needs the application history/epoch. Native retained-history cost, readable-point acquisition, schema/privileges/GC and operational retention must qualify. Current authorization and original token expiry remain mandatory. |
| Are native and journal pagination/count/token rules coherent? | Named backend per token, same snapshot/epoch for page and count, exact point strings, schema/profile binding, explicit expiry/unavailable outcomes and no silent backend/live switch. No long UI transaction. |
| Are engine features confused with deployed product behavior? | Standard/private images differ; protocol support, engine eligibility and actual schema/readiness are separate. seekdb does not inherit distributed tenant/replica claims. Native LOB range, HTAP, search/MV and resource groups remain qualified profiles. |
| Does the revision retain application failure isolation and bounded rendering? | R1 remains a live, bounded, single-pane closed flow; per-pane retained/reserved cap, exact-byte continuation, selected-content refusal and canonical/authorization rules remain. No engine capability is called an already implemented guarantee or measured performance advantage. |

No material builder blocker remained. The source/feature reviewers reported no substantive factual correction needed in the supporting profile. This is coding-agent technical review, not maintainer acceptance, SQL qualification or production rollout evidence.

## Verification boundary

Documentation checks cover relative links/anchors, current record/index/review consistency, both archived ADR hashes and unchanged product source/config fingerprints. `git diff --check` is part of this documentation-only verification. No SQL, schema migration, image upgrade, load test, VM/SSH operation, browser memory run or new screenshot was performed. Engine and package-specific qualification remains planned in the ADR and supporting profile.
