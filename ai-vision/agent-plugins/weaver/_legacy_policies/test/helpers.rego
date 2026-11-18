# Ant AI Agent Semconv v2.0 - Shared Helpers
# Provides attribute access for both span and resource levels,
# supporting OTLP format (key/value) and Weaver format (name/value).

package live_check_advice

import rego.v1

#==========================================
# Advice Builder
#==========================================

make_advice(advice_type, advice_level, advice_context, message) := {
    "type": "advice",
    "advice_type": advice_type,
    "advice_level": advice_level,
    "advice_context": advice_context,
    "message": message,
}

#==========================================
# Span Attribute Helpers
#==========================================

# Check if span has attribute (OTLP key format)
has_span_attr(name) if {
    some attr in input.sample.span.attributes
    attr.key == name
}

# Check if span has attribute (Weaver name format)
has_span_attr(name) if {
    some attr in input.sample.span.attributes
    attr.name == name
}

# Get span attribute string value (OTLP)
get_span_attr(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := attr.value.stringValue
}

# Get span attribute string value (Weaver)
get_span_attr(name) = value if {
    some attr in input.sample.span.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

# Get span attribute int value (OTLP stringified)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := to_number(attr.value.intValue)
}

# Get span attribute int value (OTLP as string)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := to_number(attr.value.stringValue)
}

# Get span attribute int value (Weaver)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.name == name
    is_number(attr.value)
    value := attr.value
}

#==========================================
# Resource Attribute Helpers
#==========================================

# Check if resource has attribute (OTLP key format)
has_resource_attr(name) if {
    some attr in input.sample.resource.attributes
    attr.key == name
}

# Check if resource has attribute (Weaver name format)
has_resource_attr(name) if {
    some attr in input.sample.resource.attributes
    attr.name == name
}

# Get resource attribute string value (OTLP)
get_resource_attr(name) = value if {
    some attr in input.sample.resource.attributes
    attr.key == name
    value := attr.value.stringValue
}

# Get resource attribute string value (Weaver)
get_resource_attr(name) = value if {
    some attr in input.sample.resource.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

#==========================================
# Span Kind Detection
#==========================================

# Get gen_ai.span.kind value (Ant extension span type)
span_kind = value if {
    value := get_span_attr("gen_ai.span.kind")
}

# Check if this is a specific span kind
is_span_kind(kind) if {
    span_kind == kind
}

# Check if span has any span data
has_span if {
    input.sample.span
}

# Check if resource data exists
has_resource if {
    input.sample.resource
    input.sample.resource.attributes
}

#==========================================
# Collect all span attribute keys
#==========================================

all_span_attr_keys[key] if {
    some attr in input.sample.span.attributes
    key := attr.key
    is_string(key)
}

all_span_attr_keys[name] if {
    some attr in input.sample.span.attributes
    name := attr.name
    is_string(name)
}
