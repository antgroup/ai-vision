# Selected-Trace diagnosis service contract

`GET runs/options` lists safe saved Diagnoser choices. `POST runs` accepts exactly `request_id`,
`trace_ids` and one `diagnoser_uuid`; its source is the current authorized Agent. The saved definition
owns its model/rule configuration, including configured Specialist policy. No inline model, Prompt,
spec or policy override is accepted. The existing diagnoser page owns configuration.

The schema-2 plan stores only the saved UUID/digest, public model identity and trusted datasource
scope/selection in the provisioning receipt. Complete saved policy is resolved and frozen only inside
AgentLens. Unaccepted definition drift returns a conflict. Accepted replay verifies the durable owned
receipt and returns its original Task without requiring the current definition or model to stay usable.
The new `bounded_diagnosis_v1` policy runs one saved check on 1–10 Traces with a 30/3 Judge request
budget and AVAILABLE same-Space models. Its Task context has `can_retest=false` with no denial message;
running again uses a new request and freezes the current saved configuration.

The internal receipt table and managed-resource marker retain their existing names. Old `/quick-runs`
routes and their input/model/retest behavior remain compatibility contracts as described below.

`diagnosis_quick_input` accepts only a fixed Trace selection, request UUID and catalog keys/scene/model reference.
Retest accepts only `request_id`, `trace_ids`, `before_task_uuid`; current UI defaults cannot override frozen checks.
Unknown browser fields are rejected. All current user/Space/member/Agent/binding, datasource, Trace/model and before
Task checks happen before receipt or remote resource writes. Normal and Pending can check existing authorized data;
Disabled/deleted/foreign bindings cannot. No onboarding ensure, provider probe or status promotion occurs.

`DiagnosisQuickRunRequest` uniquely retains `(space,user,request_id)`, normalized input hash, frozen plan, owned UUIDs
and a short provisioning lease/epoch. It is not a Task status table. Retry reuses owned UUIDs and revalidates current
access; changed input returns 409. The upstream Job keeps a durable Task receipt even after Task retention.

All quick result routes require receipt owner plus current `diagnosis.execute` before cache. Feedback also requires
`diagnosis.manage` and forces `reviewed_by` to the caller. General personal Job/Dataset CRUD remains disabled.
Record labels/category use the Record's Task snapshot; no current catalog fallback is presented as historical fact.

Public endpoints are `GET quick-runs/options` and `POST quick-runs`, under the existing Space diagnosis prefix.
Accepted has `state=accepted` and all three UUIDs. Unknown/in-progress provisioning has `state=provisioning`, request_id
and retry_after_seconds=2; retry the same body/key. Task `quick_run` adds current capabilities and fixed selections.
Comparison distinguishes rejudgment/new_traces/mixed, overlap counts, checking versus sampling conditions, and makes
no repair claim. Budget summary is forwarded from AgentLens unchanged; unknown usage stays null.

Database tests require `AIVISION_QUICK_TEST_DB_BACKEND=oceanbase|seekdb` and a controller-owned isolated Django
connection. Never run them against default settings or substitute SQLite/MySQL. Pure tests use a dummy backend and
cannot connect to a database. Source code does not start services or provision test databases.
