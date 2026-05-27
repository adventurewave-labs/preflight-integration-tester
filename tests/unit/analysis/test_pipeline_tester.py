"""Tests for PipelineTester."""
import asyncio
import pytest
from preflight.analysis.pipeline_tester import (
    PipelineTester,
    LoadTestConfig,
    PipelineTestResult,
    RequestMetrics,
)


class TestLoadTestConfig:
    def test_creation(self):
        config = LoadTestConfig(name="test", target_qps=10.0)
        assert config.target_qps == 10.0
        assert config.duration_seconds == 60
        assert config.concurrent_workers == 10

    def test_custom_values(self):
        config = LoadTestConfig(
            name="custom",
            target_qps=50.0,
            duration_seconds=30,
            concurrent_workers=5,
            timeout_ms=2000,
        )
        assert config.target_qps == 50.0
        assert config.duration_seconds == 30
        assert config.concurrent_workers == 5
        assert config.timeout_ms == 2000

    def test_default_ramp_up(self):
        config = LoadTestConfig(name="test", target_qps=5.0)
        assert config.ramp_up_seconds == 10

    def test_default_timeout(self):
        config = LoadTestConfig(name="test", target_qps=5.0)
        assert config.timeout_ms == 5000

    def test_name_stored(self):
        config = LoadTestConfig(name="my_test", target_qps=1.0)
        assert config.name == "my_test"


class TestRequestMetrics:
    def test_creation(self):
        m = RequestMetrics(
            start_time=1000.0,
            end_time=1001.0,
            success=True,
            latency_ms=1000.0,
        )
        assert m.success is True
        assert m.latency_ms == 1000.0
        assert m.error is None

    def test_failed_request(self):
        m = RequestMetrics(
            start_time=1000.0,
            end_time=1000.5,
            success=False,
            latency_ms=500.0,
            error="Connection refused",
        )
        assert m.success is False
        assert m.error == "Connection refused"

    def test_default_query_type(self):
        m = RequestMetrics(start_time=0, end_time=1, success=True, latency_ms=1)
        assert m.query_type == "unknown"


class TestPipelineTestResult:
    def setup_method(self):
        self.config = LoadTestConfig(name="test", target_qps=10.0)

    def test_actual_qps(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.total_duration_seconds = 10.0
        assert result.actual_qps == pytest.approx(10.0)

    def test_actual_qps_zero_duration(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 10
        result.total_duration_seconds = 0.0
        # Should not divide by zero
        assert result.actual_qps > 0

    def test_error_rate_pct(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 5
        assert result.error_rate_pct == pytest.approx(5.0)

    def test_error_rate_zero_requests(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        assert result.error_rate_pct == 0.0

    def test_error_rate_all_failed(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 50
        result.failed_requests = 50
        assert result.error_rate_pct == pytest.approx(100.0)

    def test_percentile_calculations(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.latencies_ms = [float(i) for i in range(1, 101)]

        assert result.p50_ms == pytest.approx(50.5, rel=0.1)
        assert result.p95_ms >= 95.0
        assert result.p99_ms >= 99.0

    def test_empty_latencies(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        assert result.p50_ms == 0.0
        assert result.p95_ms == 0.0
        assert result.p99_ms == 0.0
        assert result.max_ms == 0.0

    def test_single_latency(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.latencies_ms = [250.0]
        assert result.p50_ms == 250.0
        assert result.p95_ms == 250.0
        assert result.p99_ms == 250.0
        assert result.max_ms == 250.0

    def test_max_ms(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.latencies_ms = [100.0, 200.0, 50.0, 999.0, 300.0]
        assert result.max_ms == 999.0

    def test_is_healthy_good_results(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 2  # 2% error rate
        result.latencies_ms = [100.0] * 98  # All 100ms (p95 < 1000ms)
        assert result.is_healthy is True

    def test_is_healthy_high_errors(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 10  # 10% error rate
        result.latencies_ms = [100.0] * 90
        assert result.is_healthy is False

    def test_is_healthy_high_latency(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 0
        result.latencies_ms = [2000.0] * 100  # 2s p95
        assert result.is_healthy is False

    def test_is_healthy_boundary_error_rate(self):
        """At exactly 5% error rate: boundary case."""
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 4  # 4% -> healthy
        result.latencies_ms = [200.0] * 96
        assert result.is_healthy is True

    def test_bottleneck_analysis_healthy(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 1  # 1%
        result.latencies_ms = [200.0] * 99
        analysis = result.bottleneck_analysis()
        assert analysis["overall_health"] == "HEALTHY"
        assert len(analysis["bottlenecks"]) == 0

    def test_bottleneck_analysis_unhealthy_errors(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 15  # 15% error rate
        result.latencies_ms = [200.0] * 85
        analysis = result.bottleneck_analysis()
        assert analysis["overall_health"] == "DEGRADED"
        bottleneck_types = [b["type"] for b in analysis["bottlenecks"]]
        assert "error_rate" in bottleneck_types

    def test_bottleneck_analysis_unhealthy_latency(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 0
        result.latencies_ms = [5000.0] * 100  # Very high latency
        analysis = result.bottleneck_analysis()
        assert analysis["overall_health"] == "DEGRADED"
        bottleneck_types = [b["type"] for b in analysis["bottlenecks"]]
        assert "latency_p95" in bottleneck_types

    def test_bottleneck_analysis_critical_error_rate(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 10  # 10% -> CRITICAL
        result.latencies_ms = [100.0] * 90
        analysis = result.bottleneck_analysis()
        error_bottleneck = [b for b in analysis["bottlenecks"] if b["type"] == "error_rate"]
        assert len(error_bottleneck) > 0
        assert error_bottleneck[0]["severity"] == "CRITICAL"

    def test_bottleneck_analysis_high_error_rate(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 7  # 7% -> HIGH (between 5 and 10)
        result.latencies_ms = [100.0] * 93
        analysis = result.bottleneck_analysis()
        error_bottleneck = [b for b in analysis["bottlenecks"] if b["type"] == "error_rate"]
        assert len(error_bottleneck) > 0
        assert error_bottleneck[0]["severity"] == "HIGH"

    def test_bottleneck_analysis_critical_latency(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 0
        result.latencies_ms = [3500.0] * 100  # >3000ms → CRITICAL
        analysis = result.bottleneck_analysis()
        latency_bottleneck = [b for b in analysis["bottlenecks"] if b["type"] == "latency_p95"]
        assert len(latency_bottleneck) > 0
        assert latency_bottleneck[0]["severity"] == "CRITICAL"

    def test_p95_boundary_1000ms(self):
        result = PipelineTestResult(system_name="test", config=self.config)
        result.total_requests = 100
        result.failed_requests = 0
        result.latencies_ms = [1500.0] * 100  # p95 > 1000ms but < 3000ms → HIGH latency
        analysis = result.bottleneck_analysis()
        latency_bottleneck = [b for b in analysis["bottlenecks"] if b["type"] == "latency_p95"]
        assert len(latency_bottleneck) > 0
        assert latency_bottleneck[0]["severity"] == "HIGH"

    def test_default_result_values(self):
        result = PipelineTestResult(system_name="my-sys", config=self.config)
        assert result.total_requests == 0
        assert result.successful_requests == 0
        assert result.failed_requests == 0
        assert result.total_duration_seconds == 0.0
        assert result.latencies_ms == []


class TestPipelineTesterAsync:
    """Async tests for PipelineTester.run_load_test."""

    @pytest.mark.asyncio
    async def test_basic_load_test(self):
        tester = PipelineTester()

        async def fast_query(req_id: int) -> bool:
            await asyncio.sleep(0.005)
            return True

        config = LoadTestConfig(
            name="fast_test",
            target_qps=5.0,
            duration_seconds=2,
            ramp_up_seconds=0,
            concurrent_workers=3,
        )
        result = await tester.run_load_test("test_system", fast_query, config)

        assert result.total_requests > 0
        assert result.error_rate_pct == 0.0
        assert result.p50_ms < 200  # Should be fast
        assert result.system_name == "test_system"

    @pytest.mark.asyncio
    async def test_failing_queries(self):
        tester = PipelineTester()
        call_count = [0]

        async def flaky_query(req_id: int) -> bool:
            call_count[0] += 1
            await asyncio.sleep(0.005)
            return call_count[0] % 3 != 0  # ~1/3 failure rate

        config = LoadTestConfig(
            name="flaky_test",
            target_qps=10.0,
            duration_seconds=2,
            ramp_up_seconds=0,
            concurrent_workers=5,
        )
        result = await tester.run_load_test("flaky_system", flaky_query, config)

        assert result.total_requests > 0
        assert result.failed_requests > 0  # Should have some failures

    @pytest.mark.asyncio
    async def test_simulate_ai_agent_workload(self):
        tester = PipelineTester()

        async def mock_query(req_id: int) -> bool:
            await asyncio.sleep(0.01)
            return True

        scenario = {
            "concurrent_users": 5,
            "queries_per_minute": 30,
            "peak_multiplier": 1.5,
            "duration_seconds": 3,
        }
        result = await tester.simulate_ai_agent_workload("test", mock_query, scenario)

        assert result.total_requests > 0
        assert result.system_name == "test"

    @pytest.mark.asyncio
    async def test_results_stored_in_tester(self):
        tester = PipelineTester()

        async def noop(req_id: int) -> bool:
            return True

        config = LoadTestConfig(
            name="store_test",
            target_qps=3.0,
            duration_seconds=1,
            ramp_up_seconds=0,
            concurrent_workers=2,
        )
        await tester.run_load_test("system-a", noop, config)
        assert len(tester._results) == 1

        await tester.run_load_test("system-b", noop, config)
        assert len(tester._results) == 2

    @pytest.mark.asyncio
    async def test_result_has_correct_system_name(self):
        tester = PipelineTester()

        async def noop(req_id: int) -> bool:
            return True

        config = LoadTestConfig(
            name="sys_name_test",
            target_qps=2.0,
            duration_seconds=1,
            ramp_up_seconds=0,
            concurrent_workers=1,
        )
        result = await tester.run_load_test("my-database", noop, config)
        assert result.system_name == "my-database"

    @pytest.mark.asyncio
    async def test_progress_callback_called(self):
        tester = PipelineTester()
        progress_values = []

        async def noop(req_id: int) -> bool:
            await asyncio.sleep(0.01)
            return True

        def on_progress(pct: float):
            progress_values.append(pct)

        config = LoadTestConfig(
            name="progress_test",
            target_qps=5.0,
            duration_seconds=2,
            ramp_up_seconds=0,
            concurrent_workers=2,
        )
        await tester.run_load_test("sys", noop, config, progress_callback=on_progress)
        # Progress callback should have been called at least once
        assert len(progress_values) > 0

    @pytest.mark.asyncio
    async def test_tester_with_config(self):
        config_dict = {"max_retries": 3}
        tester = PipelineTester(config=config_dict)
        assert tester.config == config_dict

    @pytest.mark.asyncio
    async def test_exception_in_query_counted_as_failure(self):
        tester = PipelineTester()

        async def bad_query(req_id: int) -> bool:
            raise RuntimeError("Database connection failed")

        config = LoadTestConfig(
            name="exception_test",
            target_qps=3.0,
            duration_seconds=1,
            ramp_up_seconds=0,
            concurrent_workers=2,
        )
        result = await tester.run_load_test("bad-system", bad_query, config)

        # Should have recorded failures for the exceptions
        assert result.failed_requests > 0

    @pytest.mark.asyncio
    async def test_simulate_uses_scenario_params(self):
        tester = PipelineTester()

        async def fast_query(req_id: int) -> bool:
            return True

        scenario = {
            "concurrent_users": 2,
            "queries_per_minute": 12,  # 0.2 QPS
            "peak_multiplier": 1.0,
            "duration_seconds": 2,
        }
        result = await tester.simulate_ai_agent_workload("sim-sys", fast_query, scenario)
        assert result is not None
        assert isinstance(result, PipelineTestResult)
