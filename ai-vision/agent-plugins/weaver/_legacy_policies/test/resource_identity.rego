# Ant AI Agent Semconv v2.0 - Resource Identity Policies
#
# Validates resource-level attributes from entity.ant.agent (entities.yaml).
# Resource attributes are set at SDK init and attached to ALL signals.
#
# Required: ant.username, ant.agent.type
# Recommended (improvement): service.name, service.version, deployment.environment

package live_check_advice

import rego.v1

#==========================================
# Violations: Resource Required Attributes
#==========================================

# ant.username MUST be in resource
deny contains make_advice(
    "resource_missing_ant_username",
    "violation",
    {"resource": "entity.ant.agent"},
    "Resource: Missing required attribute 'ant.username'. Every signal must identify the operator."
) if {
    has_span
    has_resource
    not has_resource_attr("ant.username")
}

# ant.agent.type MUST be in resource
deny contains make_advice(
    "resource_missing_ant_agent_type",
    "violation",
    {"resource": "entity.ant.agent"},
    "Resource: Missing required attribute 'ant.agent.type'. Must identify agent engine (e.g. claudecode, opencode, openclaw)."
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.type")
}

#==========================================
# Improvements: Resource Recommended Attrs
#==========================================

# service.name SHOULD be in resource
deny contains make_advice(
    "resource_missing_service_name",
    "improvement",
    {"resource": "entity.ant.agent"},
    "Resource: Missing recommended attribute 'service.name' (mapped from ant.agent.product)."
) if {
    has_span
    has_resource
    not has_resource_attr("service.name")
}

# service.version SHOULD be in resource
deny contains make_advice(
    "resource_missing_service_version",
    "improvement",
    {"resource": "entity.ant.agent"},
    "Resource: Missing recommended attribute 'service.version'. Version info is important for debugging."
) if {
    has_span
    has_resource
    not has_resource_attr("service.version")
}

# service.instance.id SHOULD be in resource
deny contains make_advice(
    "resource_missing_service_instance_id",
    "improvement",
    {"resource": "entity.ant.agent"},
    "Resource: Missing recommended attribute 'service.instance.id' (mapped from ant.agent.instance)."
) if {
    has_span
    has_resource
    not has_resource_attr("service.instance.id")
}

# deployment.environment SHOULD be in resource
deny contains make_advice(
    "resource_missing_deployment_env",
    "improvement",
    {"resource": "entity.ant.agent"},
    "Resource: Missing recommended attribute 'deployment.environment' (e.g. production, staging)."
) if {
    has_span
    has_resource
    not has_resource_attr("deployment.environment")
}

#==========================================
# Information: Resource has no data
#==========================================

# No resource attributes at all
deny contains make_advice(
    "resource_empty",
    "violation",
    {},
    "Resource: No resource attributes found. SDK must set resource identity at initialization."
) if {
    has_span
    not has_resource
}
