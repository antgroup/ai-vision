from opentelemetry import trace


def _get_trace_id_header() -> dict[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return {"X-Trace-Id": format(ctx.trace_id, "032x")}
    return {}
