"""
Pipeline Stress Tester

Simulates AI agent workload by issuing concurrent read-only queries
and measuring throughput, latency, and error rates.
"""
import asyncio
import time
import statistics
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class LoadTestConfig:
    """Configuration for a load test run."""
    name: str
    target_qps: float  # queries per second
    duration_seconds: int = 60
    ramp_up_seconds: int = 10
    concurrent_workers: int = 10
    timeout_ms: int = 5000

@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    start_time: float
    end_time: float
    success: bool
    latency_ms: float
    error: Optional[str] = None
    query_type: str = "unknown"

@dataclass
class PipelineTestResult:
    """Results of a pipeline stress test."""
    system_name: str
    config: LoadTestConfig
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_duration_seconds: float = 0.0

    # Latency statistics
    latencies_ms: List[float] = field(default_factory=list)

    @property
    def actual_qps(self) -> float:
        return self.total_requests / max(self.total_duration_seconds, 0.001)

    @property
    def error_rate_pct(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.failed_requests / self.total_requests) * 100

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l)-1)]

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.99)
        return sorted_l[min(idx, len(sorted_l)-1)]

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def is_healthy(self) -> bool:
        return self.error_rate_pct < 5.0 and self.p95_ms < 1000.0

    def bottleneck_analysis(self) -> Dict:
        """Identify bottlenecks from the test results."""
        bottlenecks = []

        if self.error_rate_pct >= 10:
            bottlenecks.append({'type': 'error_rate', 'severity': 'CRITICAL', 'value': self.error_rate_pct})
        elif self.error_rate_pct >= 5:
            bottlenecks.append({'type': 'error_rate', 'severity': 'HIGH', 'value': self.error_rate_pct})

        if self.p95_ms >= 3000:
            bottlenecks.append({'type': 'latency_p95', 'severity': 'CRITICAL', 'value': self.p95_ms})
        elif self.p95_ms >= 1000:
            bottlenecks.append({'type': 'latency_p95', 'severity': 'HIGH', 'value': self.p95_ms})

        return {'bottlenecks': bottlenecks, 'overall_health': 'HEALTHY' if self.is_healthy else 'DEGRADED'}


class PipelineTester:
    """Runs simulated AI agent workload to stress-test data pipelines."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._results: List[PipelineTestResult] = []

    async def run_load_test(
        self,
        system_name: str,
        query_fn: Callable[[int], Awaitable[bool]],  # async function that executes one query
        test_config: LoadTestConfig,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> PipelineTestResult:
        """Run a load test against a system using the provided query function."""
        result = PipelineTestResult(system_name=system_name, config=test_config)
        metrics: List[RequestMetrics] = []

        logger.info(f"Starting load test for {system_name}: {test_config.target_qps} QPS for {test_config.duration_seconds}s")

        start_time = time.time()
        semaphore = asyncio.Semaphore(test_config.concurrent_workers)

        async def execute_request(worker_id: int) -> RequestMetrics:
            async with semaphore:
                req_start = time.time()
                try:
                    success = await asyncio.wait_for(
                        query_fn(worker_id),
                        timeout=test_config.timeout_ms / 1000
                    )
                    req_end = time.time()
                    return RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=success,
                        latency_ms=(req_end - req_start) * 1000,
                    )
                except asyncio.TimeoutError:
                    req_end = time.time()
                    return RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=False,
                        latency_ms=test_config.timeout_ms,
                        error="timeout",
                    )
                except Exception as e:
                    req_end = time.time()
                    return RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=False,
                        latency_ms=(req_end - req_start) * 1000,
                        error=str(e),
                    )

        # Ramp up + sustain load
        total_requests = 0
        tasks = []

        elapsed = 0
        while elapsed < test_config.duration_seconds:
            elapsed = time.time() - start_time

            # Calculate current target QPS (ramp up)
            if elapsed < test_config.ramp_up_seconds:
                current_qps = test_config.target_qps * (elapsed / test_config.ramp_up_seconds)
            else:
                current_qps = test_config.target_qps

            # Spawn requests at the right rate
            batch_size = max(1, int(current_qps * 0.1))  # 100ms batches
            for i in range(batch_size):
                tasks.append(asyncio.create_task(execute_request(total_requests + i)))
            total_requests += batch_size

            if progress_callback:
                progress_callback(elapsed / test_config.duration_seconds)

            await asyncio.sleep(0.1)

            # Limit in-flight tasks
            if len(tasks) > test_config.concurrent_workers * 3:
                done, tasks = await asyncio.wait(tasks[:test_config.concurrent_workers], return_when=asyncio.ALL_COMPLETED)
                for t in done:
                    metrics.append(t.result())
                tasks = list(tasks)

        # Collect remaining results
        if tasks:
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for m in done:
                if isinstance(m, RequestMetrics):
                    metrics.append(m)

        end_time = time.time()

        # Aggregate metrics
        result.total_requests = len(metrics)
        result.successful_requests = sum(1 for m in metrics if m.success)
        result.failed_requests = sum(1 for m in metrics if not m.success)
        result.total_duration_seconds = end_time - start_time
        result.latencies_ms = [m.latency_ms for m in metrics if m.success]

        logger.info(f"Load test complete: {result.total_requests} requests, {result.error_rate_pct:.1f}% errors, p95={result.p95_ms:.0f}ms")
        self._results.append(result)
        return result

    async def simulate_ai_agent_workload(
        self,
        system_name: str,
        query_fn: Callable[[int], Awaitable[bool]],
        scenario: Dict,
    ) -> PipelineTestResult:
        """Simulate the specific AI agent workload from the scenario config."""
        concurrent_users = scenario.get('concurrent_users', 10)
        qpm = scenario.get('queries_per_minute', 60)
        peak_mult = scenario.get('peak_multiplier', 2.0)

        config = LoadTestConfig(
            name=f"{system_name}_ai_simulation",
            target_qps=qpm / 60.0 * peak_mult,
            duration_seconds=min(60, scenario.get('duration_seconds', 30)),
            ramp_up_seconds=10,
            concurrent_workers=concurrent_users,
        )
        return await self.run_load_test(system_name, query_fn, config)
