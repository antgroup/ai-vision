package live_check_advice

import rego.v1

# ==========================================
# Resource contract
# Based on docs/semconv/entities.yaml (entity.ant.agent)
# ==========================================

forbidden_resource_signal_attrs := {
    "ant.username",
    "ant.uid",
    "session.id",
    "turn_number",
    "source",
    "prompt.id",
    "event.name",
    "event.timestamp",
    "event.sequence",
    "gen_ai.conversation.id",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
}

deny contains make_advice(
    "resource_missing_ant_agent_type",
    "violation",
    {"entity": "entity.ant.agent"},
    "Resource: Missing required attribute 'ant.agent.type' defined by entity.ant.agent."
) if {
    has_resource_sample
    not resource_attr_exists("ant.agent.type")
}

deny contains make_advice(
    "resource_blank_ant_agent_type",
    "violation",
    {"entity": "entity.ant.agent", "attribute": "ant.agent.type"},
    "Resource: Attribute 'ant.agent.type' must not be empty."
) if {
    has_resource_sample
    resource_attr_exists("ant.agent.type")
    is_blank_string(get_resource_attr("ant.agent.type"))
}

deny contains make_advice(
    "resource_contains_signal_level_gen_ai_attr",
    "violation",
    {"attribute": attr_name},
    sprintf("Resource: Attribute '%s' is a signal-level gen_ai field and must not be set on resource.", [attr_name])
) if {
    has_resource_sample
    some attr_name
    all_resource_attr_names[attr_name]
    startswith(attr_name, "gen_ai.")
}

deny contains make_advice(
    "resource_contains_signal_level_attr",
    "violation",
    {"attribute": attr_name},
    sprintf("Resource: Attribute '%s' is defined as a signal-level field and should not appear on resource.", [attr_name])
) if {
    has_resource_sample
    some attr_name
    all_resource_attr_names[attr_name]
    forbidden_resource_signal_attrs[attr_name]
}

deny contains make_advice(
    "resource_missing_service_identity",
    "improvement",
    {"entity": "entity.ant.agent"},
    "Resource: Missing recommended service identity. Set 'service.name' and/or 'ant.agent.product' for stable service attribution."
) if {
    has_resource_sample
    not resource_attr_exists("service.name")
    not resource_attr_exists("ant.agent.product")
}

deny contains make_advice(
    "resource_missing_instance_identity",
    "improvement",
    {"entity": "entity.ant.agent"},
    "Resource: Missing recommended instance identity. Set 'service.instance.id' and/or 'ant.agent.instance' to distinguish agent instances."
) if {
    has_resource_sample
    not resource_attr_exists("service.instance.id")
    not resource_attr_exists("ant.agent.instance")
}

deny contains make_advice(
    "resource_missing_service_version",
    "improvement",
    {"entity": "entity.ant.agent"},
    "Resource: Missing recommended attribute 'service.version'. Version tagging helps compare telemetry across releases."
) if {
    has_resource_sample
    not resource_attr_exists("service.version")
}

deny contains make_advice(
    "resource_service_name_mismatch",
    "violation",
    {
        "service.name": get_resource_attr("service.name"),
        "ant.agent.product": get_resource_attr("ant.agent.product"),
    },
    sprintf(
        "Resource: 'service.name' (%v) and 'ant.agent.product' (%v) should describe the same product identity.",
        [get_resource_attr("service.name"), get_resource_attr("ant.agent.product")]
    )
) if {
    has_resource_sample
    resource_attr_exists("service.name")
    resource_attr_exists("ant.agent.product")
    normalized_text(get_resource_attr("service.name")) != normalized_text(get_resource_attr("ant.agent.product"))
}

deny contains make_advice(
    "resource_instance_id_mismatch",
    "violation",
    {
        "service.instance.id": get_resource_attr("service.instance.id"),
        "ant.agent.instance": get_resource_attr("ant.agent.instance"),
    },
    sprintf(
        "Resource: 'service.instance.id' (%v) and 'ant.agent.instance' (%v) should describe the same instance identity.",
        [get_resource_attr("service.instance.id"), get_resource_attr("ant.agent.instance")]
    )
) if {
    has_resource_sample
    resource_attr_exists("service.instance.id")
    resource_attr_exists("ant.agent.instance")
    normalized_text(get_resource_attr("service.instance.id")) != normalized_text(get_resource_attr("ant.agent.instance"))
}
