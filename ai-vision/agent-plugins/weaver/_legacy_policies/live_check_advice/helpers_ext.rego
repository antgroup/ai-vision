package live_check_advice

import rego.v1

# ==========================================
# Shared helpers for span/resource samples
# ==========================================

has_span_sample if {
    input.sample.span
}

has_resource_sample if {
    input.sample.resource
}

sample_attr_name(attr) = name if {
    name := object.get(attr, "name", "")
    name != ""
}

sample_attr_name(attr) = name if {
    name := object.get(attr, "key", "")
    name != ""
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_string(raw)
    value := raw
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_number(raw)
    value := raw
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_boolean(raw)
    value := raw
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_object(raw)
    value := object.get(raw, "stringValue", null)
    value != null
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_object(raw)
    value := object.get(raw, "intValue", null)
    value != null
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_object(raw)
    value := object.get(raw, "doubleValue", null)
    value != null
}

sample_attr_value(attr) = value if {
    raw := object.get(attr, "value", null)
    is_object(raw)
    value := object.get(raw, "boolValue", null)
    value != null
}

span_attr_exists(name) if {
    some attr in object.get(input.sample.span, "attributes", [])
    sample_attr_name(attr) == name
}

resource_attr_exists(name) if {
    some attr in object.get(input.sample.resource, "attributes", [])
    sample_attr_name(attr) == name
}

get_span_attr(name) = value if {
    some attr in object.get(input.sample.span, "attributes", [])
    sample_attr_name(attr) == name
    value := sample_attr_value(attr)
}

get_resource_attr(name) = value if {
    some attr in object.get(input.sample.resource, "attributes", [])
    sample_attr_name(attr) == name
    value := sample_attr_value(attr)
}

span_attr_number(name) = value if {
    raw := get_span_attr(name)
    value := parsed_number(raw)
}

resource_attr_number(name) = value if {
    raw := get_resource_attr(name)
    value := parsed_number(raw)
}

parsed_number(raw) = value if {
    is_number(raw)
    value := raw
}

parsed_number(raw) = value if {
    is_string(raw)
    value := to_number(raw)
}

all_span_attr_names[name] if {
    some attr in object.get(input.sample.span, "attributes", [])
    name := sample_attr_name(attr)
}

all_resource_attr_names[name] if {
    some attr in object.get(input.sample.resource, "attributes", [])
    name := sample_attr_name(attr)
}

span_name = name if {
    name := object.get(input.sample.span, "name", "unknown")
}

is_blank_string(value) if {
    is_string(value)
    trim(value, " \t\n\r") == ""
}

normalized_text(value) = text if {
    text := lower(trim(sprintf("%v", [value]), " \t\n\r"))
}
