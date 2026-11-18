use std::collections::HashMap;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::common::otel::TurnReplayOpts;
use crate::common::turn_seed::current_seed_for_turn;
use crate::hooks::langfuse::realtime_state::{contexts, RealtimeContext};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct PropagationMetadata {
    parent_span_id: Option<String>,
    sofa_traceid: Option<String>,
    sofa_rpcid: Option<String>,
}

pub(crate) fn build_turn_trace_opts(
    state: &Value,
    session_id: &str,
    turn_id: &str,
) -> Option<TurnReplayOpts> {
    let session_id = non_empty(session_id)?;
    let turn_id = non_empty(turn_id)?;
    let with_billing_mode = |mut opts: TurnReplayOpts| {
        opts.billing_mode =
            super::state::turn_billing_mode(state, session_id, turn_id).map(str::to_string);
        opts
    };

    if let Some(context) = exact_realtime_context_for_turn(state, session_id, turn_id) {
        let mut propagation = propagation_from_realtime_context(&context);
        if let Some(seed) = current_seed_for_turn(session_id, turn_id)
            .filter(|seed| seed.trace_id == context.trace_id)
        {
            fill_missing_sofa_from_seed(&mut propagation, &seed);
        }
        return Some(with_billing_mode(opts_from_identity(
            context.trace_id.clone(),
            context.root_span_id.clone(),
            propagation,
        )));
    }

    if let Some(seed) = current_seed_for_turn(session_id, turn_id) {
        let propagation = propagation_from_seed(&seed);
        return Some(with_billing_mode(opts_from_identity(
            seed.trace_id,
            stable_root_span_id(session_id, turn_id),
            propagation,
        )));
    }

    Some(with_billing_mode(opts_from_identity(
        stable_trace_id(session_id, turn_id),
        stable_root_span_id(session_id, turn_id),
        PropagationMetadata::default(),
    )))
}

pub(crate) fn apply_exact_trace_root_metadata(
    state: &Value,
    session_id: &str,
    trace_opts: &mut Option<TurnReplayOpts>,
    trace_id: String,
    root_span_id: String,
) {
    let mut opts = trace_opts.take().unwrap_or_default();
    let existing_identity_matches = opts.trace_id.as_deref() == Some(trace_id.as_str())
        && opts.root_span_id.as_deref() == Some(root_span_id.as_str());
    let propagation = propagation_for_trace_root(state, session_id, &trace_id, &root_span_id)
        .or_else(|| {
            existing_identity_matches.then(|| PropagationMetadata {
                parent_span_id: opts.parent_span_id.clone(),
                sofa_traceid: opts.sofa_traceid.clone(),
                sofa_rpcid: opts.sofa_rpcid.clone(),
            })
        })
        .unwrap_or_default();
    opts.trace_id = Some(trace_id);
    opts.root_span_id = Some(root_span_id);
    opts.parent_span_id = propagation.parent_span_id;
    opts.sofa_traceid = propagation.sofa_traceid;
    opts.sofa_rpcid = propagation.sofa_rpcid;
    *trace_opts = Some(opts);
}

fn opts_from_identity(
    trace_id: String,
    root_span_id: String,
    propagation: PropagationMetadata,
) -> TurnReplayOpts {
    TurnReplayOpts {
        trace_id: Some(trace_id),
        root_span_id: Some(root_span_id),
        parent_span_id: propagation.parent_span_id,
        sofa_traceid: propagation.sofa_traceid,
        sofa_rpcid: propagation.sofa_rpcid,
        billing_mode: None,
        generation_span_ids: HashMap::new(),
        subagent_span_ids: HashMap::new(),
    }
}

fn exact_realtime_context_for_turn(
    state: &Value,
    session_id: &str,
    turn_id: &str,
) -> Option<RealtimeContext> {
    let matches: Vec<_> = contexts(state, session_id)
        .into_iter()
        .filter(|context| context.turn_id.as_deref() == Some(turn_id))
        .filter(|context| is_valid_trace_id(&context.trace_id))
        .filter(|context| is_valid_span_id(&context.root_span_id))
        .collect();
    (matches.len() == 1).then(|| matches[0].clone())
}

fn propagation_for_trace_root(
    state: &Value,
    session_id: &str,
    trace_id: &str,
    root_span_id: &str,
) -> Option<PropagationMetadata> {
    let matches: Vec<_> = contexts(state, session_id)
        .into_iter()
        .filter(|context| context.trace_id == trace_id && context.root_span_id == root_span_id)
        .collect();
    if matches.is_empty() {
        return None;
    }
    Some(merge_matching_context_metadata(&matches, trace_id))
}

fn merge_matching_context_metadata(
    contexts: &[RealtimeContext],
    trace_id: &str,
) -> PropagationMetadata {
    let mut merged = PropagationMetadata::default();
    let mut parent_conflict = false;
    let mut sofa_trace_conflict = false;
    let mut sofa_rpc_conflict = false;

    for context in contexts {
        let current = propagation_from_realtime_context(context);
        merge_field(
            &mut merged.parent_span_id,
            current.parent_span_id,
            &mut parent_conflict,
        );
        merge_field(
            &mut merged.sofa_traceid,
            current.sofa_traceid,
            &mut sofa_trace_conflict,
        );
        merge_field(
            &mut merged.sofa_rpcid,
            current.sofa_rpcid,
            &mut sofa_rpc_conflict,
        );
    }

    if parent_conflict {
        merged.parent_span_id = None;
    } else if let Some(parent_span_id) = merged.parent_span_id.as_deref() {
        if !is_valid_span_id(parent_span_id) {
            merged.parent_span_id = None;
        }
    }
    if sofa_trace_conflict {
        merged.sofa_traceid = None;
    }
    if sofa_rpc_conflict {
        merged.sofa_rpcid = None;
    }
    if trace_id.trim().is_empty() {
        merged.parent_span_id = None;
    }
    merged
}

fn merge_field(target: &mut Option<String>, value: Option<String>, conflict: &mut bool) {
    let Some(value) = value.and_then(|value| non_empty_owned(&value)) else {
        return;
    };
    match target {
        Some(existing) if existing != &value => *conflict = true,
        Some(_) => {}
        None => *target = Some(value),
    }
}

fn propagation_from_realtime_context(context: &RealtimeContext) -> PropagationMetadata {
    let parent_from_traceparent = context
        .trace
        .traceparent
        .as_deref()
        .and_then(|traceparent| propagation_from_traceparent(traceparent, &context.trace_id))
        .and_then(|metadata| metadata.parent_span_id);
    PropagationMetadata {
        parent_span_id: context
            .trace
            .upstream_parent_span_id
            .as_deref()
            .and_then(non_empty_owned)
            .or(parent_from_traceparent),
        sofa_traceid: context
            .trace
            .sofa_traceid
            .as_deref()
            .and_then(non_empty_owned),
        sofa_rpcid: context
            .trace
            .sofa_rpcid
            .as_deref()
            .and_then(non_empty_owned),
    }
}

fn propagation_from_seed(seed: &crate::common::turn_seed::TurnSeed) -> PropagationMetadata {
    let mut propagation = seed
        .upstream_traceparent
        .as_deref()
        .and_then(|traceparent| propagation_from_traceparent(traceparent, &seed.trace_id))
        .unwrap_or_default();
    fill_missing_sofa_from_seed(&mut propagation, seed);
    propagation
}

fn fill_missing_sofa_from_seed(
    propagation: &mut PropagationMetadata,
    seed: &crate::common::turn_seed::TurnSeed,
) {
    if propagation.sofa_traceid.is_none() {
        propagation.sofa_traceid = seed.sofa_traceid.as_deref().and_then(non_empty_owned);
    }
    if propagation.sofa_rpcid.is_none() {
        propagation.sofa_rpcid = seed.sofa_rpcid.as_deref().and_then(non_empty_owned);
    }
}

fn propagation_from_traceparent(
    traceparent: &str,
    expected_trace_id: &str,
) -> Option<PropagationMetadata> {
    let parsed = agentic_otel_common::parse_traceparent(traceparent)?;
    (parsed.trace_id == expected_trace_id && is_valid_span_id(&parsed.parent_span_id)).then_some(
        PropagationMetadata {
            parent_span_id: Some(parsed.parent_span_id),
            sofa_traceid: None,
            sofa_rpcid: None,
        },
    )
}

fn stable_trace_id(session_id: &str, turn_id: &str) -> String {
    stable_hex("cdxhooks.turn-trace.trace.v1", session_id, turn_id, 16)
}

fn stable_root_span_id(session_id: &str, turn_id: &str) -> String {
    stable_hex("cdxhooks.turn-trace.root.v1", session_id, turn_id, 8)
}

fn stable_hex(domain: &str, session_id: &str, turn_id: &str, bytes: usize) -> String {
    let mut hasher = Sha256::new();
    update_length_delimited(&mut hasher, domain);
    update_length_delimited(&mut hasher, session_id);
    update_length_delimited(&mut hasher, turn_id);
    hasher
        .finalize()
        .iter()
        .take(bytes)
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn update_length_delimited(hasher: &mut Sha256, value: &str) {
    hasher.update(value.len().to_string().as_bytes());
    hasher.update(b":");
    hasher.update(value.as_bytes());
    hasher.update(b"|");
}

fn non_empty(value: &str) -> Option<&str> {
    let value = value.trim();
    (!value.is_empty()).then_some(value)
}

fn non_empty_owned(value: &str) -> Option<String> {
    non_empty(value).map(str::to_string)
}

fn is_valid_trace_id(value: &str) -> bool {
    value.len() == 32
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && !value.bytes().all(|byte| byte == b'0')
}

fn is_valid_span_id(value: &str) -> bool {
    value.len() == 16
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && !value.bytes().all(|byte| byte == b'0')
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn ignores_stale_trace_context_history_and_uses_stable_turn_identity() {
        let state = json!({
            "sess": {
                "trace_context_history": [{
                    "trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                    "upstream_parent_span_id": "bbbbbbbbbbbbbbbb"
                }],
                "current_trace_context": {
                    "trace_id": "cccccccccccccccccccccccccccccccc",
                    "traceparent": "00-cccccccccccccccccccccccccccccccc-dddddddddddddddd-01",
                    "upstream_parent_span_id": "dddddddddddddddd"
                }
            }
        });

        let first = build_turn_trace_opts(&state, "sess", "turn-4").expect("first opts");
        let second = build_turn_trace_opts(&state, "sess", "turn-5").expect("second opts");

        assert_ne!(first.trace_id, second.trace_id);
        assert_ne!(first.root_span_id, second.root_span_id);
        assert_eq!(first.parent_span_id, None);
        assert_eq!(second.parent_span_id, None);
        assert_ne!(
            first.trace_id.as_deref(),
            Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        );
        let repeated = build_turn_trace_opts(&state, "sess", "turn-4").expect("stable opts");
        assert_eq!(first.trace_id, repeated.trace_id);
        assert_eq!(first.root_span_id, repeated.root_span_id);
        assert_eq!(first.trace_id.as_deref().map(str::len), Some(32));
        assert_eq!(first.root_span_id.as_deref().map(str::len), Some(16));
    }

    #[test]
    fn exact_realtime_context_supplies_root_and_matching_propagation() {
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "context_sequence": 1,
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "2222222222222222",
                        "turn_id": "turn-1",
                        "created_at": "2026-09-05T00:00:00Z",
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "trace": {
                            "traceparent": "00-11111111111111111111111111111111-3333333333333333-01",
                            "upstream_parent_span_id": "4444444444444444",
                            "sofa_traceid": "sofa-trace",
                            "sofa_rpcid": "0.1"
                        },
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {}
                    }
                }
            }
        });

        let opts = build_turn_trace_opts(&state, "sess", "turn-1").expect("realtime opts");

        assert_eq!(
            opts.trace_id.as_deref(),
            Some("11111111111111111111111111111111")
        );
        assert_eq!(opts.root_span_id.as_deref(), Some("2222222222222222"));
        assert_eq!(opts.parent_span_id.as_deref(), Some("4444444444444444"));
        assert_eq!(opts.sofa_traceid.as_deref(), Some("sofa-trace"));
        assert_eq!(opts.sofa_rpcid.as_deref(), Some("0.1"));
    }

    #[test]
    fn exact_realtime_context_missing_sofa_uses_matching_turn_seed_sofa() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let _dirs = crate::common::dirs::override_runtime_dirs_for_test(
            dir.path().join("state"),
            dir.path().join("spool"),
        );
        let env = std::collections::BTreeMap::from([
            ("SOFA_TRACEID".to_string(), "seed-sofa-trace".to_string()),
            ("SOFA_RPCID".to_string(), "seed-sofa-rpc".to_string()),
        ]);
        crate::common::env::with_worker_env(&env, || {
            crate::common::turn_seed::replace_for_prompt(
                "sess",
                &json!({"turn_id":"turn-1"}),
                Some("00-11111111111111111111111111111111-3333333333333333-01".to_string()),
            )
            .unwrap();
        });
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "context_sequence": 1,
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "2222222222222222",
                        "turn_id": "turn-1",
                        "created_at": "2026-09-05T00:00:00Z",
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "trace": {
                            "traceparent": "00-11111111111111111111111111111111-3333333333333333-01",
                            "upstream_parent_span_id": "4444444444444444"
                        },
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {}
                    }
                }
            }
        });

        let opts = build_turn_trace_opts(&state, "sess", "turn-1").expect("realtime opts");

        assert_eq!(
            opts.trace_id.as_deref(),
            Some("11111111111111111111111111111111")
        );
        assert_eq!(opts.root_span_id.as_deref(), Some("2222222222222222"));
        assert_eq!(opts.parent_span_id.as_deref(), Some("4444444444444444"));
        assert_eq!(opts.sofa_traceid.as_deref(), Some("seed-sofa-trace"));
        assert_eq!(opts.sofa_rpcid.as_deref(), Some("seed-sofa-rpc"));
    }

    #[test]
    fn exact_trace_root_metadata_ignores_same_trace_different_root() {
        let state = json!({
            "sess": {
                "realtime_export": {
                    "pending": [{
                        "schema_version": 1,
                        "context_id": "context-other",
                        "context_sequence": 1,
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "9999999999999999",
                        "created_at": "2026-09-05T00:00:00Z",
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "trace": {
                            "traceparent": "00-11111111111111111111111111111111-aaaaaaaaaaaaaaaa-01",
                            "sofa_traceid": "wrong-sofa"
                        },
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {}
                    }],
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-exact",
                        "context_sequence": 2,
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "2222222222222222",
                        "created_at": "2026-09-05T00:00:01Z",
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "trace": {
                            "traceparent": "00-11111111111111111111111111111111-bbbbbbbbbbbbbbbb-01",
                            "sofa_traceid": "right-sofa"
                        },
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {}
                    }
                }
            }
        });
        let mut opts = None;

        apply_exact_trace_root_metadata(
            &state,
            "sess",
            &mut opts,
            "11111111111111111111111111111111".to_string(),
            "2222222222222222".to_string(),
        );
        let opts = opts.expect("opts");

        assert_eq!(opts.parent_span_id.as_deref(), Some("bbbbbbbbbbbbbbbb"));
        assert_eq!(opts.sofa_traceid.as_deref(), Some("right-sofa"));
    }

    #[test]
    fn exact_trace_root_preserves_existing_metadata_only_for_same_identity() {
        let mut opts = Some(TurnReplayOpts {
            trace_id: Some("11111111111111111111111111111111".to_string()),
            root_span_id: Some("2222222222222222".to_string()),
            parent_span_id: Some("3333333333333333".to_string()),
            sofa_traceid: Some("sofa-trace".to_string()),
            sofa_rpcid: Some("0.1".to_string()),
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        });

        apply_exact_trace_root_metadata(
            &json!({}),
            "sess",
            &mut opts,
            "11111111111111111111111111111111".to_string(),
            "2222222222222222".to_string(),
        );
        let same_identity = opts.take().expect("opts");
        assert_eq!(
            same_identity.parent_span_id.as_deref(),
            Some("3333333333333333")
        );
        assert_eq!(same_identity.sofa_traceid.as_deref(), Some("sofa-trace"));

        let mut opts = Some(TurnReplayOpts {
            trace_id: Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string()),
            root_span_id: Some("bbbbbbbbbbbbbbbb".to_string()),
            parent_span_id: Some("3333333333333333".to_string()),
            sofa_traceid: Some("sofa-trace".to_string()),
            sofa_rpcid: Some("0.1".to_string()),
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        });
        apply_exact_trace_root_metadata(
            &json!({}),
            "sess",
            &mut opts,
            "11111111111111111111111111111111".to_string(),
            "2222222222222222".to_string(),
        );
        let changed_identity = opts.expect("opts");
        assert_eq!(changed_identity.parent_span_id, None);
        assert_eq!(changed_identity.sofa_traceid, None);
        assert_eq!(changed_identity.sofa_rpcid, None);
    }

    #[test]
    fn traceparent_metadata_requires_matching_trace_id() {
        let matched = propagation_from_traceparent(
            "00-11111111111111111111111111111111-2222222222222222-01",
            "11111111111111111111111111111111",
        )
        .expect("matching traceparent");
        assert_eq!(matched.parent_span_id.as_deref(), Some("2222222222222222"));

        assert_eq!(
            propagation_from_traceparent(
                "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-2222222222222222-01",
                "11111111111111111111111111111111",
            ),
            None
        );
    }

    #[test]
    fn matching_seed_preserves_upstream_but_stale_or_invalid_seed_does_not() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let _dirs = crate::common::dirs::override_runtime_dirs_for_test(
            dir.path().join("state"),
            dir.path().join("spool"),
        );
        let saved_sofa_traceid = std::env::var("SOFA_TRACEID").ok();
        let saved_sofa_rpcid = std::env::var("SOFA_RPCID").ok();
        std::env::set_var("SOFA_TRACEID", "seed-sofa-trace");
        std::env::set_var("SOFA_RPCID", "seed-sofa-rpc");
        let seed = crate::common::turn_seed::replace_for_prompt(
            "seed-session",
            &json!({"turn_id":"seed-turn"}),
            Some("00-11111111111111111111111111111111-2222222222222222-01".to_string()),
        )
        .unwrap();
        let opts = build_turn_trace_opts(&json!({}), "seed-session", "seed-turn").unwrap();
        assert_eq!(opts.trace_id.as_deref(), Some(seed.trace_id.as_str()));
        assert_eq!(opts.parent_span_id.as_deref(), Some("2222222222222222"));
        assert_eq!(opts.sofa_traceid.as_deref(), Some("seed-sofa-trace"));
        assert_eq!(opts.sofa_rpcid.as_deref(), Some("seed-sofa-rpc"));
        let stale = build_turn_trace_opts(&json!({}), "seed-session", "different-turn").unwrap();
        assert_ne!(stale.trace_id, opts.trace_id);
        assert_eq!(stale.parent_span_id, None);
        assert_eq!(stale.sofa_traceid, None);
        assert_eq!(stale.sofa_rpcid, None);
        let path = crate::common::dirs::session_turn_seed_file("seed-session").unwrap();
        let mut invalid = serde_json::to_value(seed).unwrap();
        invalid["trace_id"] = json!("00000000000000000000000000000000");
        std::fs::write(path, invalid.to_string()).unwrap();
        let invalid_opts = build_turn_trace_opts(&json!({}), "seed-session", "seed-turn").unwrap();
        assert_eq!(
            invalid_opts.trace_id,
            Some(stable_trace_id("seed-session", "seed-turn"))
        );
        assert_eq!(invalid_opts.parent_span_id, None);
        assert_eq!(invalid_opts.sofa_traceid, None);
        assert_eq!(invalid_opts.sofa_rpcid, None);

        match saved_sofa_traceid {
            Some(value) => std::env::set_var("SOFA_TRACEID", value),
            None => std::env::remove_var("SOFA_TRACEID"),
        }
        match saved_sofa_rpcid {
            Some(value) => std::env::set_var("SOFA_RPCID", value),
            None => std::env::remove_var("SOFA_RPCID"),
        }
    }

    #[test]
    fn sofa_only_seed_survives_serialization_without_cross_turn_fallback() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let _dirs = crate::common::dirs::override_runtime_dirs_for_test(
            dir.path().join("state"),
            dir.path().join("spool"),
        );
        let saved_sofa_traceid = std::env::var("SOFA_TRACEID").ok();
        let saved_sofa_rpcid = std::env::var("SOFA_RPCID").ok();
        std::env::set_var("SOFA_TRACEID", "sofa-only-trace");
        std::env::set_var("SOFA_RPCID", "sofa-only-rpc");
        let seed = crate::common::turn_seed::replace_for_prompt(
            "sofa-session",
            &json!({"turn_id":"turn-1"}),
            None,
        )
        .unwrap();
        assert_eq!(seed.sofa_traceid.as_deref(), Some("sofa-only-trace"));
        assert_eq!(seed.sofa_rpcid.as_deref(), Some("sofa-only-rpc"));

        std::env::set_var("SOFA_TRACEID", "future-turn-trace");
        std::env::set_var("SOFA_RPCID", "future-turn-rpc");
        let opts = build_turn_trace_opts(&json!({}), "sofa-session", "turn-1").unwrap();
        assert_eq!(opts.trace_id.as_deref(), Some(seed.trace_id.as_str()));
        assert_eq!(opts.parent_span_id, None);
        assert_eq!(opts.sofa_traceid.as_deref(), Some("sofa-only-trace"));
        assert_eq!(opts.sofa_rpcid.as_deref(), Some("sofa-only-rpc"));

        let next = build_turn_trace_opts(&json!({}), "sofa-session", "turn-2").unwrap();
        assert_eq!(next.sofa_traceid, None);
        assert_eq!(next.sofa_rpcid, None);

        let path = crate::common::dirs::session_turn_seed_file("sofa-session").unwrap();
        let contents = std::fs::read_to_string(path).unwrap();
        let restored: crate::common::turn_seed::TurnSeed = serde_json::from_str(&contents).unwrap();
        assert_eq!(restored.sofa_traceid.as_deref(), Some("sofa-only-trace"));
        assert_eq!(restored.sofa_rpcid.as_deref(), Some("sofa-only-rpc"));

        match saved_sofa_traceid {
            Some(value) => std::env::set_var("SOFA_TRACEID", value),
            None => std::env::remove_var("SOFA_TRACEID"),
        }
        match saved_sofa_rpcid {
            Some(value) => std::env::set_var("SOFA_RPCID", value),
            None => std::env::remove_var("SOFA_RPCID"),
        }
    }

    #[test]
    fn old_seed_without_sofa_fields_still_loads() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let _dirs = crate::common::dirs::override_runtime_dirs_for_test(
            dir.path().join("state"),
            dir.path().join("spool"),
        );
        let path = crate::common::dirs::session_turn_seed_file("old-seed-session").unwrap();
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            json!({
                "schema_version": 1,
                "turn_id": "turn-1",
                "trace_id": "11111111111111111111111111111111",
                "upstream_traceparent": null,
                "created_at": "2026-09-05T00:00:00Z"
            })
            .to_string(),
        )
        .unwrap();

        let opts = build_turn_trace_opts(&json!({}), "old-seed-session", "turn-1").unwrap();
        assert_eq!(
            opts.trace_id.as_deref(),
            Some("11111111111111111111111111111111")
        );
        assert_eq!(opts.sofa_traceid, None);
        assert_eq!(opts.sofa_rpcid, None);
    }

    #[test]
    fn blank_session_or_turn_has_no_identity() {
        assert!(build_turn_trace_opts(&json!({}), "", "turn").is_none());
        assert!(build_turn_trace_opts(&json!({}), "sess", "").is_none());
    }
}
