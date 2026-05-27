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
        query_fn: Callable[[int], Awaitable[bool]],
        test_config: LoadTestConfig,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> PipelineTestResult:
        """Run a load test using a fixed worker pool pattern."""
        result = PipelineTestResult(system_name=system_name, config=test_config)
        metrics: list = []

        logger.info(f"Starting load test for {system_name}: target {test_config.target_qps} QPS for {test_config.duration_seconds}s")

        queue: asyncio.Queue = asyncio.Queue(maxsize=test_config.concurrent_workers * 2)
        stop_event = asyncio.Event()
        request_counter = [0]

        async def worker(worker_id: int) -> None:
            """Consumer: pull request IDs from queue, execute, record metrics."""
            while not stop_event.is_set() or not queue.empty():
                try:
                    req_id = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                req_start = time.time()
                try:
                    success = await asyncio.wait_for(
                        query_fn(req_id),
                        timeout=test_config.timeout_ms / 1000
                    )
                    req_end = time.time()
                    metrics.append(RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=bool(success),
                        latency_ms=(req_end - req_start) * 1000,
                    ))
                except asyncio.TimeoutError:
                    req_end = time.time()
                    metrics.append(RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=False,
                        latency_ms=test_config.timeout_ms,
                        error="timeout",
                    ))
                except Exception as e:
                    req_end = time.time()
                    metrics.append(RequestMetrics(
                        start_time=req_start,
                        end_time=req_end,
                        success=False,
                        latency_ms=(req_end - req_start) * 1000,
                        error=str(e)[:100],
                    ))
                finally:
                    queue.task_done()

        async def producer() -> None:
            """Producer: enqueue requests at the target rate."""
            start = time.time()
            req_id = 0

            while True:
                elapsed = time.time() - start
                if elapsed >= test_config.duration_seconds:
                    break

                # Ramp up QPS
                if elapsed < test_config.ramp_up_seconds:
                    current_qps = test_config.target_qps * (elapsed / max(test_config.ramp_up_seconds, 0.01))
                else:
                    current_qps = test_config.target_qps

                if current_qps > 0:
                    await queue.put(req_id)
                    req_id += 1
                    request_counter[0] = req_id

                    if progress_callback:
                        progress_callback(elapsed / test_config.duration_seconds)

                    # Sleep to hit target QPS; cap to avoid multi-second stalls during ramp-up
                    inter_request_delay = min(1.0 / current_qps, 0.5)
                    await asyncio.sleep(inter_request_delay)
                else:
                    await asyncio.sleep(0.05)

            stop_event.set()

        start_time = time.time()

        # Launch fixed worker pool + producer
        workers = [asyncio.create_task(worker(i)) for i in range(test_config.concurrent_workers)]
        producer_task = asyncio.create_task(producer())

        # Wait for producer to finish
        await producer_task

        # Wait for queue to drain (max 5s after stop)
        try:
            await asyncio.wait_for(queue.join(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Queue drain timeout for {system_name}")

        # Cancel workers
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        end_time = time.time()

        # Aggregate
        result.total_requests = len(metrics)
        result.successful_requests = sum(1 for m in metrics if m.success)
        result.failed_requests = sum(1 for m in metrics if not m.success)
        result.total_duration_seconds = end_time - start_time
        result.latencies_ms = [m.latency_ms for m in metrics if m.success]

        logger.info(
            f"Load test complete for {system_name}: "
            f"{result.total_requests} requests, {result.error_rate_pct:.1f}% errors, "
            f"p95={result.p95_ms:.0f}ms, actual QPS={result.actual_qps:.1f}"
        )
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
