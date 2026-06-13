"""
Unit tests for the domain model (value objects, entities, aggregates, events).
"""
import pytest
from preflight.core.domain.value_objects import (
    ReadinessScore, ReadinessVerdict, EffortEstimate, EffortLevel,
    SeverityLevel, EntityField, ConnectionCredentials, ConnectorType, SystemType
)
from preflight.core.domain.entities import (
    SchemaInconsistency, PipelineBottleneck, MiddlewareGap, RemediationItem,
    ConnectionProfile, EntityMapping
)
from preflight.core.domain.aggregates import (
    DiagnosticRun, SimulationScenario, AnalysisResults, ReadinessReport
)
from preflight.core.domain.events import (
    DiagnosticRunStarted, DiagnosticRunCompleted, ConnectionEstablished,
    SchemaAnalysisCompleted
)


class TestReadinessScore:
    """Tests for the ReadinessScore value object."""

    def test_go_verdict_at_80(self):
        score = ReadinessScore(value=80.0)
        assert score.verdict == ReadinessVerdict.GO

    def test_go_verdict_above_80(self):
        score = ReadinessScore(value=95.0)
        assert score.verdict == ReadinessVerdict.GO

    def test_not_yet_verdict_between_50_and_80(self):
        score = ReadinessScore(value=65.0)
        assert score.verdict == ReadinessVerdict.NOT_YET

    def test_not_ready_verdict_below_50(self):
        score = ReadinessScore(value=30.0)
        assert score.verdict == ReadinessVerdict.NOT_READY

    def test_boundary_at_50(self):
        score = ReadinessScore(value=50.0)
        assert score.verdict == ReadinessVerdict.NOT_YET

    def test_score_zero(self):
        score = ReadinessScore(value=0.0)
        assert score.verdict == ReadinessVerdict.NOT_READY

    def test_score_100(self):
        score = ReadinessScore(value=100.0)
        assert score.verdict == ReadinessVerdict.GO

    def test_invalid_score_above_100(self):
        with pytest.raises((ValueError, Exception)):
            ReadinessScore(value=101.0)

    def test_invalid_score_negative(self):
        with pytest.raises((ValueError, Exception)):
            ReadinessScore(value=-1.0)

    def test_string_representation(self):
        score = ReadinessScore(value=72.5)
        result = str(score)
        assert '72.5' in result
        assert 'NOT_YET' in result

    def test_immutability(self):
        score = ReadinessScore(value=75.0)
        with pytest.raises((AttributeError, TypeError)):
            score.value = 90.0  # type: ignore


class TestEffortEstimate:
    """Tests for the EffortEstimate value object."""

    def test_from_level_trivial(self):
        est = EffortEstimate.from_level(EffortLevel.TRIVIAL)
        assert est.min_days == 0
        assert est.max_days == 1

    def test_from_level_low(self):
        est = EffortEstimate.from_level(EffortLevel.LOW)
        assert est.min_days == 1
        assert est.max_days == 5

    def test_from_level_medium(self):
        est = EffortEstimate.from_level(EffortLevel.MEDIUM)
        assert est.min_days < est.max_days

    def test_from_level_high(self):
        est = EffortEstimate.from_level(EffortLevel.HIGH)
        assert est.min_days >= 21

    def test_from_level_critical(self):
        est = EffortEstimate.from_level(EffortLevel.CRITICAL)
        assert est.min_days >= 90

    def test_custom_range(self):
        est = EffortEstimate(min_days=5, max_days=15, level=EffortLevel.LOW)
        assert est.min_days == 5
        assert est.max_days == 15


class TestSchemaInconsistency:
    """Tests for SchemaInconsistency entity."""

    def test_create_with_defaults(self):
        issue = SchemaInconsistency()
        assert issue.id is not None
        assert issue.severity == SeverityLevel.MEDIUM

    def test_create_critical(self):
        issue = SchemaInconsistency(
            entity_name='Customer',
            source_system='salesforce',
            target_system='sap',
            inconsistency_type='key_mismatch',
            severity=SeverityLevel.CRITICAL,
            impact_description='Customer IDs are incompatible',
        )
        assert issue.severity == SeverityLevel.CRITICAL
        assert issue.entity_name == 'Customer'

    def test_unique_ids(self):
        issue1 = SchemaInconsistency()
        issue2 = SchemaInconsistency()
        assert issue1.id != issue2.id


class TestDiagnosticRun:
    """Tests for the DiagnosticRun root aggregate."""

    def test_initial_state(self):
        run = DiagnosticRun(name='Test Run')
        assert run.status == 'pending'
        assert run.progress_pct == 0.0
        assert run.connections == []

    def test_start_transitions_to_running(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        assert run.status == 'running'
        assert run.started_at is not None

    def test_start_emits_event(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        events = run.pop_events()
        assert len(events) >= 1
        assert any(isinstance(e, DiagnosticRunStarted) for e in events)

    def test_complete_transitions_to_completed(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        run.pop_events()  # Clear start event

        report = ReadinessReport()
        report.readiness_score = ReadinessScore(value=75.0)
        report.verdict = ReadinessVerdict.NOT_YET

        run.complete(report)
        assert run.status == 'completed'
        assert run.progress_pct == 100.0
        assert run.completed_at is not None

    def test_complete_emits_event(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        run.pop_events()

        report = ReadinessReport()
        report.readiness_score = ReadinessScore(value=75.0)
        report.verdict = ReadinessVerdict.NOT_YET
        run.complete(report)

        events = run.pop_events()
        assert any(isinstance(e, DiagnosticRunCompleted) for e in events)

    def test_fail_transitions_to_failed(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        run.fail('Connection refused')
        assert run.status == 'failed'
        assert run.error_message == 'Connection refused'

    def test_pop_events_clears_queue(self):
        run = DiagnosticRun(name='Test Run')
        run.start()
        events1 = run.pop_events()
        events2 = run.pop_events()
        assert len(events1) >= 1
        assert len(events2) == 0

    def test_unique_ids(self):
        run1 = DiagnosticRun()
        run2 = DiagnosticRun()
        assert run1.id != run2.id

    def test_add_connection(self):
        run = DiagnosticRun(name='Test')
        profile = ConnectionProfile(name='Salesforce', connector_type=ConnectorType.SALESFORCE)
        run.add_connection(profile)
        assert len(run.connections) == 1

    def test_active_connections(self):
        run = DiagnosticRun(name='Test')
        profile1 = ConnectionProfile(name='SF', status='connected', connector_type=ConnectorType.SALESFORCE)
        profile2 = ConnectionProfile(name='SAP', status='failed', connector_type=ConnectorType.SAP)
        run.add_connection(profile1)
        run.add_connection(profile2)
        active = run.active_connections
        assert len(active) == 1
        assert active[0].name == 'SF'


class TestAnalysisResults:
    """Tests for AnalysisResults aggregate."""

    def test_critical_count(self):
        results = AnalysisResults()
        results.schema_inconsistencies = [
            SchemaInconsistency(severity=SeverityLevel.CRITICAL),
            SchemaInconsistency(severity=SeverityLevel.HIGH),
            SchemaInconsistency(severity=SeverityLevel.CRITICAL),
        ]
        assert results.critical_count == 2

    def test_blocking_gaps(self):
        results = AnalysisResults()
        results.middleware_gaps = [
            MiddlewareGap(blocking=True),
            MiddlewareGap(blocking=False),
            MiddlewareGap(blocking=True),
        ]
        assert len(results.blocking_gaps) == 2


class TestReadinessReport:
    """Tests for ReadinessReport aggregate."""

    def test_calculate_score(self):
        report = ReadinessReport()
        analysis = AnalysisResults()
        analysis.schema_inconsistencies = [
            SchemaInconsistency(severity=SeverityLevel.HIGH),
        ]
        analysis.middleware_gaps = [
            MiddlewareGap(severity=SeverityLevel.MEDIUM),
        ]

        score = report.calculate_score(analysis)
        assert isinstance(score, ReadinessScore)
        assert 0 <= score.value <= 100

    def test_perfect_score_with_no_issues(self):
        report = ReadinessReport()
        analysis = AnalysisResults()  # No issues

        score = report.calculate_score(analysis)
        assert score.value == pytest.approx(100.0, abs=1.0)
        assert score.verdict == ReadinessVerdict.GO


class TestDomainEvents:
    """Tests for domain events."""

    def test_event_has_id(self):
        event = DiagnosticRunStarted(run_id='test-123')
        assert event.event_id is not None
        assert event.occurred_at is not None

    def test_event_to_dict(self):
        event = DiagnosticRunStarted(run_id='test-123')
        d = event.to_dict()
        assert d['event_type'] == 'DiagnosticRunStarted'
        assert 'event_id' in d
        assert 'occurred_at' in d

    def test_connection_established_event(self):
        event = ConnectionEstablished(
            run_id='run-1',
            connection_id='conn-1',
            system_name='Salesforce',
            connector_type='SALESFORCE',
        )
        assert event.event_type == 'ConnectionEstablished'
        assert event.system_name == 'Salesforce'

    def test_schema_analysis_completed_event(self):
        event = SchemaAnalysisCompleted(
            run_id='run-1',
            inconsistency_count=5,
            critical_count=2,
            entity_count=10,
        )
        assert event.inconsistency_count == 5
        assert event.critical_count == 2
