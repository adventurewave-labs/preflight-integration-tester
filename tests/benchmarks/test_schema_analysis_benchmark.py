"""
Performance benchmarks for schema analysis.

Run with: pytest tests/benchmarks/ -v -m benchmark
"""
import time
import pytest
from preflight.analysis.schema_analyzer import SchemaAnalyzer
from preflight.analysis.readiness_calculator import ReadinessCalculator


pytestmark = pytest.mark.benchmark


def generate_large_schema(num_systems: int, num_entities: int, fields_per_entity: int) -> dict:
    """Generate a large multi-system schema for benchmarking."""
    schemas = {}
    for sys_idx in range(num_systems):
        sys_name = f'system_{sys_idx}'
        schemas[sys_name] = {}
        for ent_idx in range(num_entities):
            ent_name = f'entity_{ent_idx}'
            schemas[sys_name][ent_name] = [
                {
                    'name': f'field_{field_idx}' if field_idx > 0 else 'id',
                    'type': ['varchar', 'integer', 'decimal', 'timestamp', 'boolean'][field_idx % 5],
                    'nullable': field_idx > 0,
                }
                for field_idx in range(fields_per_entity)
            ]
    return schemas


class TestSchemaAnalyzerPerformance:
    """Benchmarks for SchemaAnalyzer."""

    def test_small_schema_analysis_performance(self, multi_system_schemas):
        """Small schema should analyze in under 100ms."""
        analyzer = SchemaAnalyzer()

        start = time.perf_counter()
        for _ in range(100):
            results = analyzer.analyze_all(multi_system_schemas)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 100) * 1000
        print(f"\nSmall schema analysis: {avg_ms:.2f}ms avg (100 runs)")
        assert avg_ms < 100, f"Too slow: {avg_ms:.2f}ms"

    def test_medium_schema_analysis_performance(self):
        """Medium schema (5 systems, 20 entities, 30 fields) - performance baseline."""
        analyzer = SchemaAnalyzer()
        schemas = generate_large_schema(5, 20, 30)

        start = time.perf_counter()
        results = analyzer.analyze_all(schemas)
        elapsed = time.perf_counter() - start

        elapsed_ms = elapsed * 1000
        print(f"\nMedium schema analysis: {elapsed_ms:.2f}ms (5 systems, 20 entities, 30 fields)")
        # Threshold: C(5,2)=10 pairs × 20 entities × 30² comparisons with cache
        assert elapsed_ms < 10000, f"Too slow: {elapsed_ms:.2f}ms"

    def test_large_schema_analysis_performance(self):
        """Large schema (10 systems, 50 entities, 50 fields) - performance baseline."""
        analyzer = SchemaAnalyzer()
        schemas = generate_large_schema(10, 50, 50)

        start = time.perf_counter()
        results = analyzer.analyze_all(schemas)
        elapsed = time.perf_counter() - start

        print(f"\nLarge schema analysis: {elapsed:.2f}s (10 systems, 50 entities, 50 fields)")
        print(f"  Entities analyzed: {len(results)}")
        # This is a heavy workload — C(10,2)=45 pairs; document actual perf
        assert elapsed < 120.0, f"Too slow: {elapsed:.2f}s"

    def test_field_similarity_performance(self):
        """Field similarity scoring should be fast for bulk operations."""
        analyzer = SchemaAnalyzer()
        field_names = [f'field_{i}' for i in range(100)]

        start = time.perf_counter()
        for name1 in field_names:
            for name2 in field_names:
                analyzer.field_similarity(name1, name2)
        elapsed = time.perf_counter() - start

        total_comparisons = len(field_names) ** 2
        avg_us = (elapsed / total_comparisons) * 1_000_000
        print(f"\nField similarity: {avg_us:.2f}µs avg ({total_comparisons} comparisons)")
        assert avg_us < 100, f"Too slow: {avg_us:.2f}µs per comparison"

    def test_inconsistency_report_generation_performance(self, multi_system_schemas):
        """Report generation from analysis results should be fast."""
        analyzer = SchemaAnalyzer()
        analysis_results = analyzer.analyze_all(multi_system_schemas)

        start = time.perf_counter()
        for _ in range(1000):
            report = analyzer.generate_inconsistency_report(analysis_results)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 1000) * 1000
        print(f"\nInconsistency report: {avg_ms:.4f}ms avg (1000 runs)")
        assert avg_ms < 10, f"Too slow: {avg_ms:.4f}ms"


class TestReadinessCalculatorPerformance:
    """Benchmarks for ReadinessCalculator."""

    def test_score_calculation_performance(self):
        """Score calculation should complete in under 1ms."""
        calc = ReadinessCalculator()
        schema_issues = [{'severity': 'HIGH'} for _ in range(50)]
        pipeline = [{'error_rate_pct': 5.0, 'p95_ms': 800} for _ in range(10)]
        gaps = [{'severity': 'MEDIUM', 'blocking': False, 'effort_days': (5, 20)} for _ in range(20)]

        start = time.perf_counter()
        for _ in range(10000):
            breakdown = calc.calculate(schema_issues, pipeline, gaps, [])
        elapsed = time.perf_counter() - start

        avg_us = (elapsed / 10000) * 1_000_000
        print(f"\nScore calculation: {avg_us:.2f}µs avg (10000 runs)")
        assert avg_us < 1000, f"Too slow: {avg_us:.2f}µs"

    def test_large_issue_set_performance(self):
        """Should handle large issue sets efficiently."""
        calc = ReadinessCalculator()
        large_schema = [{'severity': 'MEDIUM'} for _ in range(500)]
        large_gaps = [{'severity': 'LOW', 'blocking': False, 'effort_days': (1, 5)} for _ in range(200)]

        start = time.perf_counter()
        breakdown = calc.calculate(large_schema, [], large_gaps, [])
        elapsed = time.perf_counter() - start

        elapsed_ms = elapsed * 1000
        print(f"\nLarge issue set ({len(large_schema)} schema + {len(large_gaps)} gaps): {elapsed_ms:.2f}ms")
        assert elapsed_ms < 50, f"Too slow: {elapsed_ms:.2f}ms"
