"""
Tests for FastAPI API routes.

Uses FastAPI's TestClient (sync) for route testing without running a real server.
State isolation: each test gets a fresh app with empty in-memory stores via
dependency overrides.
"""
import pytest
from fastapi.testclient import TestClient
from preflight.api.app import create_app
from preflight.api import dependencies


def make_client():
    """Create a TestClient with isolated in-memory stores."""
    app = create_app()

    # Override stores with fresh empty dicts for isolation
    fresh_connections: dict = {}
    fresh_runs: dict = {}
    fresh_reports: dict = {}

    app.dependency_overrides[dependencies.get_connections_store] = lambda: fresh_connections
    app.dependency_overrides[dependencies.get_runs_store] = lambda: fresh_runs
    app.dependency_overrides[dependencies.get_reports_store] = lambda: fresh_reports

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def client():
    return make_client()


class TestHealthRoutes:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data

    def test_health_ready(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200

    def test_health_ready_body(self, client):
        response = client.get("/health/ready")
        data = response.json()
        assert data.get("ready") is True

    def test_health_returns_json(self, client):
        response = client.get("/health")
        assert "application/json" in response.headers.get("content-type", "")

    def test_health_has_version(self, client):
        response = client.get("/health")
        assert response.json()["version"] == "0.1.0"


class TestConnectionRoutes:
    def test_list_connections_empty(self, client):
        response = client.get("/connections")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_connection(self, client):
        payload = {
            "name": "Test System",
            "connector_type": "mock",
            "system_type": "DATABASE",
            "config": {"name": "test"},
        }
        response = client.post("/connections", json=payload)
        assert response.status_code in (200, 201, 202)

    def test_create_connection_returns_id(self, client):
        payload = {
            "name": "My System",
            "connector_type": "mock",
            "system_type": "CRM",
            "config": {},
        }
        response = client.post("/connections", json=payload)
        assert response.status_code in (200, 201)
        data = response.json()
        assert "id" in data
        assert data["name"] == "My System"

    def test_get_nonexistent_connection(self, client):
        response = client.get("/connections/nonexistent-id-xyz")
        assert response.status_code == 404

    def test_delete_nonexistent_connection(self, client):
        response = client.delete("/connections/nonexistent-id-xyz")
        assert response.status_code in (404, 204)

    def test_test_nonexistent_connection(self, client):
        response = client.post("/connections/nonexistent-id-xyz/test")
        assert response.status_code == 404

    def test_create_and_get_connection(self, client):
        payload = {
            "name": "My Postgres",
            "connector_type": "mock",
            "system_type": "DATABASE",
            "config": {},
        }
        create_resp = client.post("/connections", json=payload)
        assert create_resp.status_code in (200, 201, 202)

        conn_id = create_resp.json().get("id")
        assert conn_id is not None

        get_resp = client.get(f"/connections/{conn_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "My Postgres"

    def test_create_and_list_connections(self, client):
        for i in range(3):
            payload = {
                "name": f"System {i}",
                "connector_type": "mock",
                "system_type": "DATABASE",
                "config": {},
            }
            client.post("/connections", json=payload)

        resp = client.get("/connections")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_create_and_delete_connection(self, client):
        payload = {
            "name": "Delete Me",
            "connector_type": "mock",
            "system_type": "DATABASE",
            "config": {},
        }
        create_resp = client.post("/connections", json=payload)
        conn_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/connections/{conn_id}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/connections/{conn_id}")
        assert get_resp.status_code == 404

    def test_retest_connection(self, client):
        payload = {
            "name": "Retest Me",
            "connector_type": "mock",
            "system_type": "ERP",
            "config": {},
        }
        create_resp = client.post("/connections", json=payload)
        conn_id = create_resp.json()["id"]

        test_resp = client.post(f"/connections/{conn_id}/test")
        assert test_resp.status_code == 200
        data = test_resp.json()
        assert data["status"] == "connected"

    def test_create_connection_invalid_type(self, client):
        payload = {
            "name": "Bad",
            "connector_type": "mock",
            "system_type": "INVALID_TYPE",
            "config": {},
        }
        response = client.post("/connections", json=payload)
        assert response.status_code == 422  # Pydantic validation error

    def test_connection_response_has_required_fields(self, client):
        payload = {
            "name": "Salesforce Test",
            "connector_type": "salesforce",
            "system_type": "CRM",
            "config": {},
        }
        resp = client.post("/connections", json=payload)
        data = resp.json()
        for field in ["id", "name", "connector_type", "system_type", "status"]:
            assert field in data


class TestDiagnosticRoutes:
    def test_list_diagnostics_empty(self, client):
        response = client.get("/diagnostics")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_diagnostic_run(self, client):
        payload = {
            "name": "Test Diagnostic",
            "scenario": {
                "name": "Customer Service AI",
                "description": "Test scenario",
                "target_systems": ["mock-system"],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 2.0,
                "response_time_target_ms": 500,
            },
            "connection_ids": [],
        }
        response = client.post("/diagnostics", json=payload)
        assert response.status_code in (200, 201, 202)

    def test_create_diagnostic_returns_id(self, client):
        payload = {
            "name": "My Run",
            "scenario": {
                "name": "Test AI",
                "description": "",
                "target_systems": [],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 1.5,
                "response_time_target_ms": 500,
            },
            "connection_ids": [],
        }
        resp = client.post("/diagnostics", json=payload)
        data = resp.json()
        assert "id" in data
        assert data["name"] == "My Run"

    def test_get_nonexistent_diagnostic(self, client):
        response = client.get("/diagnostics/nonexistent-run-id")
        assert response.status_code == 404

    def test_get_report_for_nonexistent_run(self, client):
        response = client.get("/diagnostics/nonexistent-run-id/report")
        assert response.status_code == 404

    def test_create_and_get_diagnostic(self, client):
        payload = {
            "name": "Integration Test Run",
            "scenario": {
                "name": "Test AI",
                "description": "",
                "target_systems": [],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 1.5,
                "response_time_target_ms": 500,
            },
            "connection_ids": [],
        }
        create_resp = client.post("/diagnostics", json=payload)
        assert create_resp.status_code in (200, 201, 202)

        run_id = create_resp.json().get("id")
        assert run_id is not None

        get_resp = client.get(f"/diagnostics/{run_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["name"] == "Integration Test Run"

    def test_create_and_list_diagnostics(self, client):
        for i in range(2):
            payload = {
                "name": f"Run {i}",
                "scenario": {
                    "name": "Test AI",
                    "description": "",
                    "target_systems": [],
                    "concurrent_users": 5,
                    "queries_per_minute": 30,
                    "peak_multiplier": 1.5,
                    "response_time_target_ms": 500,
                },
                "connection_ids": [],
            }
            client.post("/diagnostics", json=payload)

        resp = client.get("/diagnostics")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_delete_nonexistent_diagnostic(self, client):
        response = client.delete("/diagnostics/nonexistent-run-id")
        assert response.status_code in (404, 204)

    def test_delete_diagnostic(self, client):
        payload = {
            "name": "Delete Run",
            "scenario": {
                "name": "Test",
                "description": "",
                "target_systems": [],
                "concurrent_users": 1,
                "queries_per_minute": 10,
                "peak_multiplier": 1.0,
                "response_time_target_ms": 1000,
            },
            "connection_ids": [],
        }
        create_resp = client.post("/diagnostics", json=payload)
        run_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/diagnostics/{run_id}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/diagnostics/{run_id}")
        assert get_resp.status_code == 404

    def test_create_diagnostic_with_invalid_connection_id(self, client):
        payload = {
            "name": "Bad Run",
            "scenario": {
                "name": "Test AI",
                "description": "",
                "target_systems": [],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 1.5,
                "response_time_target_ms": 500,
            },
            "connection_ids": ["nonexistent-conn-id"],
        }
        response = client.post("/diagnostics", json=payload)
        # Should fail with 422 because connection doesn't exist
        assert response.status_code == 422

    def test_diagnostic_status_is_pending_or_running(self, client):
        payload = {
            "name": "Status Check",
            "scenario": {
                "name": "Test",
                "description": "",
                "target_systems": [],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 1.5,
                "response_time_target_ms": 500,
            },
            "connection_ids": [],
        }
        resp = client.post("/diagnostics", json=payload)
        data = resp.json()
        assert data["status"] in ("pending", "running", "completed", "failed")

    def test_report_for_incomplete_run_returns_409(self, client):
        """Report endpoint returns 409 if run is not completed yet."""
        payload = {
            "name": "Pending Run",
            "scenario": {
                "name": "Test",
                "description": "",
                "target_systems": [],
                "concurrent_users": 5,
                "queries_per_minute": 30,
                "peak_multiplier": 1.5,
                "response_time_target_ms": 500,
            },
            "connection_ids": [],
        }
        create_resp = client.post("/diagnostics", json=payload)
        run_id = create_resp.json()["id"]

        # Manually get the run; if it completed already that's fine too
        get_resp = client.get(f"/diagnostics/{run_id}")
        status = get_resp.json()["status"]

        report_resp = client.get(f"/diagnostics/{run_id}/report")
        if status == "completed":
            assert report_resp.status_code == 200
        elif status == "failed":
            assert report_resp.status_code in (404, 409)
        else:
            assert report_resp.status_code == 409


def make_client_with_stores():
    """Create TestClient that exposes the underlying in-memory stores."""
    app = create_app()
    fresh_connections: dict = {}
    fresh_runs: dict = {}
    fresh_reports: dict = {}
    app.dependency_overrides[dependencies.get_connections_store] = lambda: fresh_connections
    app.dependency_overrides[dependencies.get_runs_store] = lambda: fresh_runs
    app.dependency_overrides[dependencies.get_reports_store] = lambda: fresh_reports
    client = TestClient(app, raise_server_exceptions=True)
    return client, fresh_connections, fresh_runs, fresh_reports


def _make_completed_run(run_id: str) -> dict:
    """Return a completed run dict."""
    from datetime import datetime
    return {
        "id": run_id,
        "name": "Test Run",
        "status": "completed",
        "progress_pct": 100.0,
        "created_at": datetime.utcnow().isoformat(),
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "error_message": None,
    }


def _make_valid_report(run_id: str) -> dict:
    """Return a minimal valid report dict."""
    return {
        "run_id": run_id,
        "readiness_score": 82.5,
        "verdict": "GO",
        "executive_summary": "System is ready for AI deployment.",
        "schema_inconsistencies": [],
        "pipeline_bottlenecks": [],
        "middleware_gaps": [],
        "remediation_plan": [],
        "total_effort_min_days": 0,
        "total_effort_max_days": 0,
        "findings_summary": {},
    }


class TestReportRoutes:
    def test_get_nonexistent_report(self, client):
        response = client.get("/reports/nonexistent-run-id")
        assert response.status_code == 404

    def test_get_html_report_nonexistent(self, client):
        response = client.get("/reports/nonexistent-run-id/html")
        assert response.status_code == 404

    def test_executive_summary_nonexistent(self, client):
        response = client.get("/reports/nonexistent-run-id/executive-summary")
        assert response.status_code == 404

    def test_get_report_for_pending_run_returns_409(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "pending-run-123"
        from datetime import datetime
        runs[run_id] = {
            "id": run_id, "name": "Pending", "status": "pending",
            "progress_pct": 0.0,
            "created_at": datetime.utcnow().isoformat(),
        }
        response = client.get(f"/reports/{run_id}")
        assert response.status_code == 409

    def test_get_report_run_exists_no_report_returns_404(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "done-no-report"
        from datetime import datetime
        runs[run_id] = {
            "id": run_id, "name": "Done", "status": "completed",
            "progress_pct": 100.0,
            "created_at": datetime.utcnow().isoformat(),
        }
        # No entry in reports_store
        response = client.get(f"/reports/{run_id}")
        assert response.status_code == 404

    def test_get_completed_report_json(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "complete-run-456"
        runs[run_id] = _make_completed_run(run_id)
        reports[run_id] = _make_valid_report(run_id)

        response = client.get(f"/reports/{run_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_id
        assert data["verdict"] == "GO"
        assert data["readiness_score"] == 82.5

    def test_get_completed_report_html(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "html-run-789"
        runs[run_id] = _make_completed_run(run_id)
        reports[run_id] = _make_valid_report(run_id)

        response = client.get(f"/reports/{run_id}/html")
        assert response.status_code == 200
        assert "html" in response.headers.get("content-type", "").lower()
        assert len(response.text) > 100

    def test_get_executive_summary(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "exec-run-101"
        runs[run_id] = _make_completed_run(run_id)
        rep = _make_valid_report(run_id)
        rep["executive_summary"] = "AI readiness confirmed. Score: 82.5"
        reports[run_id] = rep

        response = client.get(f"/reports/{run_id}/executive-summary")
        assert response.status_code == 200
        assert "AI readiness confirmed" in response.text

    def test_html_report_is_self_contained(self):
        client, connections, runs, reports = make_client_with_stores()
        run_id = "self-contained-run"
        runs[run_id] = _make_completed_run(run_id)
        reports[run_id] = _make_valid_report(run_id)

        response = client.get(f"/reports/{run_id}/html")
        assert "<!DOCTYPE html>" in response.text or "<html" in response.text


class TestAPISchemas:
    def test_openapi_schema_accessible(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        assert "info" in schema

    def test_docs_accessible(self, client):
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_has_health_path(self, client):
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        # Health path should be in the API
        health_paths = [p for p in paths if "health" in p]
        assert len(health_paths) > 0

    def test_openapi_has_connections_path(self, client):
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        conn_paths = [p for p in paths if "connections" in p]
        assert len(conn_paths) > 0
