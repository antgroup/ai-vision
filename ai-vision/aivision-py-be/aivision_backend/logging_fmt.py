import logging


class OTelFormatter(logging.Formatter):
    """Formatter that injects OTel trace/span IDs with fallback defaults.

    Python 3.11 dictConfig does not support the ``defaults`` parameter,
    so we provide the fallback values here instead.

    When no active span exists (e.g. Django dev server access logs),
    trace_id/span_id are omitted rather than showing meaningless "0".
    """

    def __init__(self, **kwargs):
        fmt = kwargs.pop("format", None)
        datefmt = kwargs.pop("datefmt", None)
        style = kwargs.pop("style", "{")
        kwargs.pop("class", None)
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def format(self, record):
        if not hasattr(record, "otelTraceID"):
            record.otelTraceID = "-"
        if not hasattr(record, "otelSpanID"):
            record.otelSpanID = "-"
        # Treat the "all zeros" non-sampled trace as no-trace
        if record.otelTraceID == "0" * 32 or record.otelTraceID == "0":
            record.otelTraceID = "-"
        if record.otelSpanID == "0" * 16 or record.otelSpanID == "0":
            record.otelSpanID = "-"
        return super().format(record)
