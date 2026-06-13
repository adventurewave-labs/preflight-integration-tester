"""Tests for DataQualityAnalyzer."""
import pytest
from preflight.analysis.data_quality import DataQualityAnalyzer, DataQualityResult, DataQualityCheck


class TestDataQualityAnalyzer:
    def setup_method(self):
        self.analyzer = DataQualityAnalyzer()

    def test_empty_sample(self):
        results = self.analyzer.analyze_sample("system", "table", [])
        assert results == []

    def test_perfect_data_no_critical_issues(self):
        sample = [
            {"id": 1, "name": "Alice", "email": "alice@test.com"},
            {"id": 2, "name": "Bob", "email": "bob@test.com"},
            {"id": 3, "name": "Carol", "email": "carol@test.com"},
        ]
        results = self.analyzer.analyze_sample("salesforce", "Contact", sample)
        # No nulls, no duplicates → no critical issues above threshold
        critical = [r for r in results if r.severity == "CRITICAL" and not r.passed]
        assert len(critical) == 0

    def test_detects_null_ids(self):
        sample = [
            {"id": None, "name": "Alice"},
            {"id": None, "name": "Bob"},
            {"id": 3, "name": "Carol"},
        ]
        results = self.analyzer.analyze_sample("system", "table", sample)
        id_null_issues = [r for r in results if r.column == "id" and not r.passed]
        assert len(id_null_issues) > 0

    def test_null_id_is_critical_severity(self):
        sample = [
            {"id": None, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        results = self.analyzer.analyze_sample("system", "table", sample)
        id_issues = [r for r in results if r.column == "id" and not r.passed]
        assert len(id_issues) > 0
        # id nulls should be CRITICAL
        assert any(r.severity == "CRITICAL" for r in id_issues)

    def test_detects_duplicate_ids(self):
        sample = [
            {"id": 1, "name": "Alice"},
            {"id": 1, "name": "Bob"},  # Duplicate!
            {"id": 2, "name": "Carol"},
        ]
        results = self.analyzer.analyze_sample("system", "customers", sample)
        uniqueness_issues = [r for r in results if r.check_name == "uniqueness" and not r.passed]
        assert len(uniqueness_issues) > 0

    def test_duplicate_id_is_critical(self):
        sample = [
            {"id": 1, "name": "Alice"},
            {"id": 1, "name": "Duplicate"},
        ]
        results = self.analyzer.analyze_sample("system", "table", sample)
        dup_issues = [r for r in results if r.check_name == "uniqueness"]
        assert any(r.severity == "CRITICAL" for r in dup_issues)

    def test_high_null_rate_flagged(self):
        # 100% null rate for "name" field → should be flagged
        sample = [{"id": i, "name": None} for i in range(20)]
        results = self.analyzer.analyze_sample("system", "table", sample)
        null_issues = [r for r in results if r.column == "name" and not r.passed]
        assert len(null_issues) > 0
        # High null rate should be at least HIGH severity
        severities = [r.severity for r in null_issues]
        assert any(s in ("HIGH", "CRITICAL") for s in severities)

    def test_low_null_rate_not_flagged(self):
        # 0% null rate for non-id field
        sample = [{"id": i, "name": f"User {i}"} for i in range(20)]
        results = self.analyzer.analyze_sample("system", "table", sample)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 0

    def test_null_rate_threshold_5pct(self):
        """Only flags if null rate > 5%."""
        # 5 nulls in 100 rows = exactly 5% (not flagged)
        sample = [{"id": i, "value": None if i < 5 else f"v{i}"} for i in range(100)]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        value_issues = [r for r in results if r.column == "value" and not r.passed]
        # At exactly 5% it should not be flagged (> 0.05 threshold)
        assert len(value_issues) == 0

    def test_null_rate_just_above_threshold(self):
        """At 6% null rate, should be flagged."""
        sample = [{"id": i, "value": None if i < 6 else f"v{i}"} for i in range(100)]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        value_issues = [r for r in results if r.column == "value" and not r.passed]
        assert len(value_issues) > 0

    def test_score_perfect_data(self):
        sample = [{"id": i, "name": f"User{i}"} for i in range(10)]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        score = self.analyzer.calculate_overall_quality_score(results)
        assert 0.0 <= score <= 1.0
        failed_results = [r for r in results if not r.passed]
        if not failed_results:
            assert score == 1.0

    def test_score_bad_data(self):
        # Many NULLs and duplicates
        sample = [
            {"id": 1, "name": None, "email": None},
            {"id": 1, "name": None, "email": None},  # Duplicate + NULLs
            {"id": None, "name": None, "email": None},
        ]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        score = self.analyzer.calculate_overall_quality_score(results)
        assert 0.0 <= score <= 1.0

    def test_result_structure(self):
        sample = [{"id": 1, "name": "Test"}]
        results = self.analyzer.analyze_sample("system", "table", sample)
        for r in results:
            assert isinstance(r, DataQualityResult)
            assert r.system == "system"
            assert r.table == "table"
            assert r.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
            assert isinstance(r.passed, bool)
            assert 0.0 <= r.score <= 1.0

    def test_calculate_overall_score_empty(self):
        score = self.analyzer.calculate_overall_quality_score([])
        assert score == 1.0

    def test_calculate_score_all_passed(self):
        passed_results = [
            DataQualityResult(
                check_name="completeness",
                system="sys",
                table="tbl",
                column="id",
                severity="HIGH",
                passed=True,
                score=1.0,
                details="ok",
            )
        ]
        score = self.analyzer.calculate_overall_quality_score(passed_results)
        assert score == 1.0

    def test_calculate_score_critical_failures(self):
        critical_results = [
            DataQualityResult(
                check_name="uniqueness",
                system="sys",
                table="tbl",
                column="id",
                severity="CRITICAL",
                passed=False,
                score=0.0,
                details="duplicates found",
            )
        ]
        score = self.analyzer.calculate_overall_quality_score(critical_results)
        assert 0.0 <= score < 1.0  # Should be penalised

    def test_quality_checks_exist(self):
        assert len(self.analyzer.QUALITY_CHECKS) > 0
        for check in self.analyzer.QUALITY_CHECKS:
            assert isinstance(check, DataQualityCheck)
            assert check.check_name
            assert check.description
            assert check.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")

    def test_quality_checks_include_completeness(self):
        check_names = [c.check_name for c in self.analyzer.QUALITY_CHECKS]
        assert "completeness" in check_names

    def test_quality_checks_include_uniqueness(self):
        check_names = [c.check_name for c in self.analyzer.QUALITY_CHECKS]
        assert "uniqueness" in check_names

    def test_result_details_describe_issue(self):
        sample = [{"id": None, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        for r in results:
            assert isinstance(r.details, str)
            assert len(r.details) > 0

    def test_multiple_id_fields_checked(self):
        """Multiple id-like fields should each be checked."""
        sample = [
            {"customer_id": 1, "order_id": 1, "name": "Alice"},
            {"customer_id": 2, "order_id": 2, "name": "Bob"},
        ]
        results = self.analyzer.analyze_sample("sys", "tbl", sample)
        # With good data, no failures expected
        failed = [r for r in results if not r.passed]
        assert len(failed) == 0

    def test_analyze_system_name_in_result(self):
        sample = [{"id": 1, "val": "v"}]
        results = self.analyzer.analyze_sample("salesforce", "Account", sample)
        for r in results:
            assert r.system == "salesforce"
            assert r.table == "Account"

    def test_score_between_0_and_1(self):
        """Score is always clamped between 0 and 1."""
        worst_sample = [
            {"id": None},
            {"id": None},
            {"id": None},
        ]
        results = self.analyzer.analyze_sample("sys", "tbl", worst_sample)
        score = self.analyzer.calculate_overall_quality_score(results)
        assert 0.0 <= score <= 1.0
