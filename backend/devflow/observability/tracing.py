"""OpenTelemetry tracing.

The default exporter appends finished spans as JSON lines to ``<data_dir>/traces.jsonl``
so traces can be inspected without running a collector. ``otlp`` sends spans to a
collector at ``OTEL_EXPORTER_OTLP_ENDPOINT`` if the OTLP exporter package is installed.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)

from devflow import __version__

log = logging.getLogger(__name__)
_MAX_TRACE_FILE_BYTES = 50 * 1024 * 1024


class JsonlFileSpanExporter(SpanExporter):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        lines = []
        for span in spans:
            ctx = span.get_span_context()
            if ctx is None:
                continue
            lines.append(
                json.dumps(
                    {
                        "trace_id": f"{ctx.trace_id:032x}",
                        "span_id": f"{ctx.span_id:016x}",
                        "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
                        "name": span.name,
                        "start": span.start_time,
                        "end": span.end_time,
                        "duration_ms": ((span.end_time or 0) - (span.start_time or 0)) / 1e6,
                        "status": span.status.status_code.name,
                        "attributes": dict(span.attributes or {}),
                    },
                    default=str,
                )
            )
        with self._lock:
            if self.path.exists() and self.path.stat().st_size > _MAX_TRACE_FILE_BYTES:
                self.path.replace(self.path.with_suffix(".jsonl.1"))
            with self.path.open("a") as handle:
                handle.write("".join(line + "\n" for line in lines))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def configure_tracing(exporter: str, data_dir: Path) -> TracerProvider | None:
    if exporter == "none":
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": "devflow-ai", "service.version": __version__})
    )
    span_exporter: SpanExporter
    if exporter == "console":
        span_exporter = ConsoleSpanExporter()
    elif exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )
        except ImportError:
            log.warning("OTLP exporter not installed; falling back to file exporter")
            span_exporter = JsonlFileSpanExporter(data_dir / "traces.jsonl")
        else:
            span_exporter = OTLPSpanExporter()
    else:
        span_exporter = JsonlFileSpanExporter(data_dir / "traces.jsonl")
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    return provider


tracer = trace.get_tracer("devflow")
