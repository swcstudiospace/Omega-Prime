"""Behavioral coverage for injected event-stream observability."""

from omega_prime.eventstream.observability import EventStreamObserver


class Metrics:
    def __init__(self):
        self.records = []

    def increment(self, name, value, attributes):
        self.records.append(("increment", name, value, dict(attributes)))

    def observe(self, name, value, attributes):
        self.records.append(("observe", name, value, dict(attributes)))

    def set(self, name, value, attributes):
        self.records.append(("set", name, value, dict(attributes)))


class Tracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, traceparent, attributes):
        self.spans.append((name, traceparent, dict(attributes)))
        return None


def test_publish_and_consume_emit_named_metrics_and_link_spans():
    metrics, tracer = Metrics(), Tracer()
    observer = EventStreamObserver(metrics, tracer, lambda: None)
    observer.publish(
        latency=0.25,
        traceparent="00-abc-def-01",
        attributes={"ce_type": "omega.job.queued"},
    )
    observer.consume(
        "ack",
        0.5,
        traceparent="00-abc-def-01",
        attributes={"subscription": "gate"},
    )
    assert [(row[0], row[1], row[2]) for row in metrics.records] == [
        ("increment", "eventstream.publish.count", 1),
        ("observe", "eventstream.publish.latency", 0.25),
        ("increment", "consume.count", 1),
        ("observe", "consume.latency", 0.5),
    ]
    assert metrics.records[2][3]["outcome"] == "ack"
    assert tracer.spans[0][1] == tracer.spans[1][1] == "00-abc-def-01"
    assert metrics.records[0][3] == {}
    assert metrics.records[2][3] == {"outcome": "ack"}
    assert tracer.spans[0][2] == {"ce_type": "omega.job.queued"}


def test_outcome_dimensions_and_other_metric_families():
    metrics = Metrics()
    observer = EventStreamObserver(metrics, Tracer(), lambda: None)
    observer.consume("retry", 0.1)
    observer.outbox(3, 80, 4.5)
    observer.retry()
    observer.dead_letter()
    observer.handler_in_flight(2)
    observer.connection_state("connected")
    observed = {(kind, name, value) for kind, name, value, _ in metrics.records}
    assert ("increment", "consume.count", 1) in observed
    assert ("observe", "consume.latency", 0.1) in observed
    assert ("set", "outbox.depth", 3) in observed
    assert ("set", "outbox.bytes", 80) in observed
    assert ("set", "outbox.oldest_age", 4.5) in observed
    assert ("increment", "retry.count", 1) in observed
    assert ("increment", "dlq.count", 1) in observed
    assert ("set", "handler.in_flight", 2) in observed
    assert ("set", "connection.state", 1.0) in observed
    assert metrics.records[0][3] == {"outcome": "retry"}
    assert metrics.records[-1][3] == {"state": "connected"}


def test_drops_payload_and_unbounded_attributes_from_metrics_and_spans():
    metrics, tracer = Metrics(), Tracer()
    observer = EventStreamObserver(metrics, tracer, lambda: None)
    payload = "non-secret private message body"
    unknown_value = "tenant-specific-" + "x" * 80
    attributes = {
        "ce_type": "omega.job.queued",
        "payload": payload,
        "body": payload,
        "data": payload,
        "token": "private-token",
        "event_id": unknown_value,
        "trace_id": unknown_value,
        "custom_label": unknown_value,
    }
    traceparent = "00-abc-def-01"

    observer.publish(traceparent=traceparent, attributes=attributes)
    observer.consume("ack", 0.1, traceparent=traceparent, attributes=attributes)

    assert metrics.records[0][3] == {}
    assert metrics.records[1][3] == {"outcome": "ack"}
    assert metrics.records[2][3] == {"outcome": "ack"}
    assert tracer.spans[0][1] == tracer.spans[1][1] == traceparent
    assert all(span[2] == {"ce_type": "omega.job.queued"} for span in tracer.spans)
    assert payload not in str(metrics.records)
    assert payload not in str(tracer.spans)
    assert unknown_value not in str(metrics.records)
    assert unknown_value not in str(tracer.spans)


def test_unknown_outcomes_and_connection_states_use_safe_enums():
    metrics = Metrics()
    observer = EventStreamObserver(metrics, Tracer(), lambda: None)

    observer.consume("tenant-specific-outcome", 0.1)
    observer.connection_state("connection-for-customer-123")

    assert metrics.records[0][3] == {"outcome": "unknown"}
    assert metrics.records[1][3] == {"outcome": "unknown"}
    assert metrics.records[2][3] == {"state": "unknown"}
    assert metrics.records[2][2] == 0.0


def test_redacts_secret_shaped_attributes_and_known_secrets():
    metrics, tracer = Metrics(), Tracer()
    observer = EventStreamObserver(
        metrics, tracer, lambda: None, known_secrets=("private-value",)
    )
    observer.publish(
        attributes={
            "ce_type": "private-value",
            "diagnostic": "token=private-value " + ("ghp" + "_" + "abcdefgh123456"),
        }
    )
    assert "private-value" not in str(metrics.records)
    assert ("ghp" + "_" + "abcdefgh123456") not in str(metrics.records)
    assert "private-value" not in str(tracer.spans)
    assert ("ghp" + "_" + "abcdefgh123456") not in str(tracer.spans)


def test_readiness_strict_80_percent_boundary_and_liveness():
    ready = EventStreamObserver.readiness
    assert ready(True, 799, 1000, "closed")
    assert not ready(True, 800, 1000, "closed")
    assert not ready(False, 0, 1000, "closed")
    assert not ready(True, 0, 1000, "open")
    assert not ready(True, 0, 0, "closed")
    assert EventStreamObserver.liveness()


def test_readiness_conditions_are_independent_and_liveness_is_separate():
    ready = EventStreamObserver.readiness
    assert not ready(False, 0, 1000, "closed")
    assert not ready(True, 800, 1000, "closed")
    assert not ready(True, 0, 1000, "open")
    assert not ready(True, 0, 1000, "half-open")
    assert ready(True, 799, 1000, "closed")
    assert EventStreamObserver.liveness()


def test_metrics_use_only_bounded_diagnostic_dimensions():
    metrics = Metrics()
    observer = EventStreamObserver(metrics, Tracer(), lambda: None)
    observer.consume(
        "ack",
        0.25,
        attributes={
            "subscription": "private-subscription",
            "ce_type": "omega.job.queued",
            "payload": "private-body",
        },
    )
    observer.connection_state("connected")

    assert metrics.records == [
        ("increment", "consume.count", 1, {"outcome": "ack"}),
        ("observe", "consume.latency", 0.25, {"outcome": "ack"}),
        ("set", "connection.state", 1.0, {"state": "connected"}),
    ]


def test_observer_construction_and_recording_do_not_start_export_work():
    calls = []

    def export_once():
        calls.append("export")

    observer = EventStreamObserver(Metrics(), Tracer(), export_once)
    observer.publish()
    observer.consume("ack", 0.0)
    observer.outbox(0, 0, 0.0)
    observer.retry()
    observer.dead_letter()
    assert calls == []


def test_export_only_happens_when_explicitly_requested():
    calls = []
    observer = EventStreamObserver(
        Metrics(), Tracer(), lambda: calls.append("exported")
    )
    assert calls == []
    observer.publish()
    assert calls == []
    observer.export()
    assert calls == ["exported"]
