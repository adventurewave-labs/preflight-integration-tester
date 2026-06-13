"""
Additional tests for domain events to improve event coverage.
"""
import pytest
from preflight.core.domain.events import (
    DomainEvent,
    DiagnosticRunStarted,
    DiagnosticRunCompleted,
    ConnectionEstablished,
    ConnectionFailed,
    SchemaAnalysisCompleted,
    PipelineTestCompleted,
    MiddlewareAssessmentCompleted,
    ReadinessReportGenerated,
)


class TestDomainEventBase:
    def test_base_event_has_id_and_timestamp(self):
        event = DomainEvent()
        assert event.event_id is not None
        assert len(event.event_id) > 0
        assert event.occurred_at is not None

    def test_base_event_to_dict(self):
        event = DomainEvent()
        d = event.to_dict()
        assert "event_id" in d
        assert "event_type" in d
        assert "occurred_at" in d
        assert d["event_type"] == "DomainEvent"

    def test_unique_event_ids(self):
        e1 = DomainEvent()
        e2 = DomainEvent()
        assert e1.event_id != e2.event_id


class TestConnectionFailed:
    def test_event_type(self):
        event = ConnectionFailed(
            run_id="run-1",
            connection_id="conn-1",
            system_name="Salesforce",
            error_message="Timeout",
        )
        assert event.event_type == "ConnectionFailed"

    def test_to_dict_includes_all_fields(self):
        event = ConnectionFailed(
            run_id="run-2",
            connection_id="conn-2",
            system_name="SAP",
            error_message="Connection refused",
        )
        d = event.to_dict()
        assert d["run_id"] == "run-2"
        assert d["connection_id"] == "conn-2"
        assert d["system_name"] == "SAP"
        assert d["error_message"] == "Connection refused"
        assert d["event_type"] == "ConnectionFailed"


class TestPipelineTestCompleted:
    def test_event_type(self):
        event = PipelineTestCompleted(run_id="run-1")
        assert event.event_type == "PipelineTestCompleted"

    def test_to_dict_includes_all_fields(self):
        event = PipelineTestCompleted(
            run_id="run-xyz",
            bottleneck_count=3,
            systems_tested=4,
        )
        d = event.to_dict()
        assert d["run_id"] == "run-xyz"
        assert d["bottleneck_count"] == 3
        assert d["systems_tested"] == 4
        assert d["event_type"] == "PipelineTestCompleted"

    def test_default_values(self):
        event = PipelineTestCompleted()
        assert event.bottleneck_count == 0
        assert event.systems_tested == 0


class TestMiddlewareAssessmentCompleted:
    def test_event_type(self):
        event = MiddlewareAssessmentCompleted(run_id="run-1")
        assert event.event_type == "MiddlewareAssessmentCompleted"

    def test_to_dict_includes_all_fields(self):
        event = MiddlewareAssessmentCompleted(
            run_id="run-5",
            gap_count=2,
            blocking_count=1,
        )
        d = event.to_dict()
        assert d["run_id"] == "run-5"
        assert d["gap_count"] == 2
        assert d["blocking_count"] == 1
        assert d["event_type"] == "MiddlewareAssessmentCompleted"

    def test_default_values(self):
        event = MiddlewareAssessmentCompleted()
        assert event.gap_count == 0
        assert event.blocking_count == 0


class TestReadinessReportGenerated:
    def test_event_type(self):
        event = ReadinessReportGenerated(run_id="run-1")
        assert event.event_type == "ReadinessReportGenerated"

    def test_to_dict_includes_all_fields(self):
        event = ReadinessReportGenerated(
            run_id="run-99",
            score=78.5,
            verdict="NOT_YET",
            remediation_count=5,
        )
        d = event.to_dict()
        assert d["run_id"] == "run-99"
        assert d["score"] == 78.5
        assert d["verdict"] == "NOT_YET"
        assert d["remediation_count"] == 5
        assert d["event_type"] == "ReadinessReportGenerated"

    def test_default_values(self):
        event = ReadinessReportGenerated()
        assert event.score == 0.0
        assert event.verdict == ""
        assert event.remediation_count == 0
