from opentelemetry import trace


class TraceIdMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            response["X-Trace-Id"] = format(ctx.trace_id, "032x")
        return response
