"""Shared diagnostic pipeline used by the CLI and the API.

This module owns the *real* end-to-end flow: create connectors from connection
definitions, connect read-only, introspect schemas, run the (cross-name) entity
matching and schema analysis, stress-test pipelines with the scenario's
workload, then compute the readiness score and report payload.

The ``mock`` branch reproduces the historical demo exactly (frozen dataset,
same-name schema analysis, hardcoded pipeline results) so the README's demo
GIF and its 97.0/GO numbers stay reproducible.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from preflight.core.infrastructure.connectors.connector_factory import (
    create_connector,
    list_connector_types,
)

logger = logging.getLogger(__name__)

# Connector type aliases understood by the connector registry.
_CONNECTOR_ALIASES = {
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "pg": "postgresql",
    "salesforce": "salesforce",
    "sfdc": "salesforce",
    "snowflake": "snowflake",
    "mock": "mock",
}

# Category used for middleware heuristics, per config section key.
SYSTEM_TYPE_MAP = {
    "salesforce": "CRM",
    "hubspot": "CRM",
    "sap": "ERP",
    "oracle_erp": "ERP",
    "dynamics_365": "ERP",
    "snowflake": "DATA_WAREHOUSE",
    "databricks": "DATA_WAREHOUSE",
    "redshift": "DATA_WAREHOUSE",
    "postgresql": "DATABASE",
    "postgres": "DATABASE",
    "mysql": "DATABASE",
}


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot run at all (e.g. nothing connectable)."""


def resolve_connector_type(connector_type: str) -> Optional[str]:
    """Map a user-supplied connector type to its registry name, or None."""
    return _CONNECTOR_ALIASES.get((connector_type or "").lower().strip())


# Config keys that can name a remote host for some connector family.
_HOST_KEYS = (
    "host", "account", "instance_url", "login_url", "url", "hostname",
    "domain",  # simple-salesforce domain (login/test)
)


def allowed_hosts() -> set:
    """Hosts permitted when PREFLIGHT_ALLOWED_HOSTS is set (lower-cased).

    Empty set = unrestricted (the intended single-tenant pre-purchase
    usage). When the list IS set, enforcement is fail-closed: a config
    that names no host at all is refused, not waved through.
    """
    import os

    return {
        h.strip().lower()
        for h in os.environ.get("PREFLIGHT_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }


def check_host_allowed(config: Dict[str, Any]) -> Optional[str]:
    """Return an error string when the host allowlist blocks this config.

    Checked on BOTH the connection-test path and every diagnostic run —
    the run is the path that opens connections, introspects schemas and
    generates load, so testing-only enforcement would guard the wrong door.
    """
    allowed = allowed_hosts()
    if not allowed:
        return None
    # Check EVERY present host-like key, not just the first: a config with
    # an allowed `host` and a disallowed `instance_url` must not pass just
    # because the loop stopped early.
    seen_any = False
    for key in _HOST_KEYS:
        val = (config or {}).get(key)
        if not (isinstance(val, str) and val.strip()):
            continue
        seen_any = True
        candidate = val.strip().lower()
        if "://" in candidate:
            from urllib.parse import urlparse

            candidate = (urlparse(candidate).hostname or "").lower()
        if candidate not in allowed:
            return f"host '{candidate}' (from '{key}') is not in the allowed hosts list"
    if not seen_any:
        return (
            "connection config declares no host while PREFLIGHT_ALLOWED_HOSTS "
            "is set; refusing (fail closed)"
        )
    return None


@dataclass
class ConnectedSystem:
    name: str
    connector_type: str
    connector: Any
    is_mock_fallback: bool = False
    # Type declared by the caller (API system_type), preferred over the
    # connector-type-derived default: a Postgres-stored ERP replica should
    # be analyzed as ERP, not DATABASE.
    declared_type: Optional[str] = None


@dataclass
class RealInputs:
    systems: List[ConnectedSystem] = field(default_factory=list)
    schemas: Dict[str, Dict[str, List[Dict]]] = field(default_factory=dict)
    declared_entities: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def connection_summaries(self) -> List[Dict[str, str]]:
        return [
            {
                "name": s.name,
                "type": s.declared_type
                or SYSTEM_TYPE_MAP.get(s.connector_type, s.connector_type.upper()),
            }
            for s in self.systems
        ]


# ---------------------------------------------------------------------------
# Frozen demo dataset (historical mock path)
# ---------------------------------------------------------------------------

def demo_schemas() -> Dict[str, Dict[str, List[Dict]]]:
    """The frozen demo dataset used by ``--mock`` (and the README GIF)."""
    return {
        'salesforce': {
            'Account': [
                {'name': 'Id', 'type': 'varchar(18)', 'nullable': False},
                {'name': 'Name', 'type': 'varchar(255)', 'nullable': False},
                {'name': 'BillingStreet', 'type': 'text', 'nullable': True},
                {'name': 'Phone', 'type': 'varchar(40)', 'nullable': True},
                {'name': 'AnnualRevenue', 'type': 'decimal', 'nullable': True},
            ],
            'Contact': [
                {'name': 'Id', 'type': 'varchar(18)', 'nullable': False},
                {'name': 'AccountId', 'type': 'varchar(18)', 'nullable': True},
                {'name': 'FirstName', 'type': 'varchar(40)', 'nullable': True},
                {'name': 'LastName', 'type': 'varchar(80)', 'nullable': False},
                {'name': 'Email', 'type': 'varchar(80)', 'nullable': True},
            ],
        },
        'sap': {
            'KUNNR': [  # Customer master
                {'name': 'KUNNR', 'type': 'varchar(10)', 'nullable': False},  # Key mismatch!
                {'name': 'NAME1', 'type': 'nvarchar(35)', 'nullable': False},
                {'name': 'TELF1', 'type': 'varchar(16)', 'nullable': True},  # Phone format mismatch
                {'name': 'UMSAV', 'type': 'numeric(15,2)', 'nullable': True},
            ],
            'VBELN': [  # Sales order
                {'name': 'VBELN', 'type': 'varchar(10)', 'nullable': False},
                {'name': 'KUNNR', 'type': 'varchar(10)', 'nullable': False},
                {'name': 'ERDAT', 'type': 'varchar(8)', 'nullable': False},  # Date as string!
            ],
        },
    }


def demo_pipeline_results() -> List[Dict]:
    """The frozen pipeline results used by ``--mock``."""
    return [
        {'system': 'SAP ERP', 'error_rate_pct': 2.5, 'p95_ms': 380, 'actual_qps': 45, 'severity': 'LOW'},
        {'system': 'Salesforce', 'error_rate_pct': 0.5, 'p95_ms': 180, 'actual_qps': 100, 'severity': 'INFO'},
    ]


# ---------------------------------------------------------------------------
# Real path: connection + introspection
# ---------------------------------------------------------------------------

async def collect_real_inputs(
    connections: List[Dict[str, Any]],
    scenario: Dict[str, Any],
    connect_timeout: int = 30,
) -> RealInputs:
    """Connect to every defined system and introspect its schema.

    ``connections`` is a list of ``{"name", "connector_type", "config"}``
    dicts (built from the CLI's YAML config or the API's connection store).

    Failed connections and systems with no implemented connector are skipped
    with a warning; if nothing connects at all, :class:`PipelineError` is
    raised.
    """
    inputs = RealInputs(declared_entities=scenario.get('entities') or [])
    supported = set(list_connector_types())

    seen_names: Dict[str, int] = {}
    try:
        for conn in connections:
            name = conn.get('name') or conn.get('connector_type', 'system')
            # Duplicate names would silently overwrite each other's schemas
            # and data_sources entries (API connection names are
            # user-supplied and not unique) — deduplicate with a suffix.
            if name in seen_names or name in inputs.schemas:
                seen_names[name] = seen_names.get(name, 1) + 1
                new_name = f"{name} ({seen_names[name]})"
                # The suffix itself may collide with a caller-supplied name
                # ('prod', 'prod (2)', 'prod') — keep generating until free.
                while new_name in inputs.schemas or any(
                    new_name == c.get("name") for c in connections
                ):
                    seen_names[name] += 1
                    new_name = f"{name} ({seen_names[name]})"
                inputs.warnings.append(
                    f"duplicate connection name '{name}' — renamed to '{new_name}'"
                )
                name = new_name
            registry_type = resolve_connector_type(conn.get('connector_type') or '')
            if registry_type is None or registry_type not in supported:
                inputs.warnings.append(
                    f"{name}: no connector implemented for type "
                    f"'{conn.get('connector_type')}' yet — skipped"
                )
                continue
            allow_error = check_host_allowed(conn.get('config') or {})
            if allow_error is not None:
                inputs.warnings.append(f"{name}: skipped — {allow_error}")
                continue
            declared_type = conn.get('system_type')
            try:
                connector = create_connector(
                    registry_type, conn.get('config') or {}, timeout_seconds=connect_timeout
                )
            except Exception:  # pragma: no cover - defensive
                logger.warning("%s: connector creation failed", name, exc_info=True)
                inputs.warnings.append(
                    f"{name}: connector could not be created from the given config"
                )
                continue

            is_fallback = registry_type != 'mock' and type(connector).__name__.startswith('Mock')
            if is_fallback:
                inputs.warnings.append(
                    f"{name}: client library for '{registry_type}' not installed — "
                    f"using MOCK data for this system"
                )
            try:
                try:
                    connected = await asyncio.wait_for(
                        connector.connect(), timeout=connect_timeout
                    )
                except Exception as exc:
                    logger.warning("%s: connection failed: %s", name, exc)
                    inputs.warnings.append(
                        f"{name}: connection failed (check host/credentials)"
                    )
                    await _safe_disconnect(connector)
                    continue
                if not connected:
                    inputs.warnings.append(f"{name}: connection failed")
                    await _safe_disconnect(connector)
                    continue

                try:
                    meta = await asyncio.wait_for(
                        connector.get_metadata(), timeout=connect_timeout * 2
                    )
                except Exception as exc:
                    logger.warning("%s: schema introspection failed: %s", name, exc)
                    inputs.warnings.append(
                        f"{name}: schema introspection failed (permissions or version)"
                    )
                    await _safe_disconnect(connector)
                    continue
            except BaseException:
                # The IN-FLIGHT connector (connected but not yet collected)
                # must be disconnected on cancellation too — the outer
                # handler only sees systems already appended to
                # inputs.systems.
                await _safe_disconnect(connector)
                raise

            system = ConnectedSystem(
                name=name, connector_type=registry_type, connector=connector,
                is_mock_fallback=is_fallback, declared_type=declared_type,
            )
            inputs.systems.append(system)
            inputs.schemas[name] = {
                table.name: [
                    {
                        'name': col.get('name', ''),
                        'type': col.get('type', 'unknown'),
                        'nullable': bool(col.get('nullable', True)),
                    }
                    for col in table.columns
                ]
                for table in meta.tables
            }
            logger.info("Collected %d tables from %s", len(meta.tables), name)
    except BaseException:
        # BaseException, not Exception: a cancelled task (CancelledError)
        # after some connects succeeded must still disconnect them.
        for system in inputs.systems:
            await _safe_disconnect(system.connector)
        raise

    if not inputs.systems:
        raise PipelineError(
            "No systems could be connected. Enable connections in the config "
            "(or install the missing client libraries), or use --mock for the "
            "demo dataset."
        )
    return inputs


async def _safe_disconnect(connector) -> None:
    try:
        await connector.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


def _bottleneck_severity(error_rate_pct: float, p95_ms: float) -> str:
    if error_rate_pct > 5 or p95_ms > 1000:
        return 'HIGH'
    if p95_ms > 500:
        return 'MEDIUM'
    if error_rate_pct > 2 or p95_ms > 250:
        return 'LOW'
    return 'INFO'


# Lightweight liveness probe per connector dialect: the statement must be
# valid for the target system (SOQL requires FROM/LIMIT; there is no bare
# "SELECT 1" in Salesforce) and cheap enough to repeat at the scenario's QPS.
_STRESS_PROBE_SQL = {
    "salesforce": "SELECT Id FROM Account LIMIT 1",
    "snowflake": "SELECT 1",
    "postgresql": "SELECT 1",
    "mock": "SELECT 1",
}


async def run_pipeline_tests(
    inputs: RealInputs,
    scenario: Dict[str, Any],
    duration_seconds: Optional[int] = None,
) -> List[Dict]:
    """Stress-test every connected real system with the scenario workload.

    Mock-fallback systems are skipped (stress-testing synthetic latency
    tells us nothing). Each result is converted to the pipeline_bottlenecks
    row shape used by the readiness calculator.
    """
    from preflight.analysis.pipeline_tester import PipelineTester

    rows: List[Dict] = []
    tester = PipelineTester()
    duration = duration_seconds or 10

    for system in inputs.systems:
        if system.is_mock_fallback or system.connector_type == "mock":
            # Synthetic latency tells us nothing, whichever way the mock
            # arrived (explicit choice or missing client library).
            continue
        connector = system.connector
        probe_sql = _STRESS_PROBE_SQL.get(system.connector_type, "SELECT 1")

        async def query_fn(_req_id: int) -> bool:
            result = await connector.execute_read_query(probe_sql)
            return bool(result is not None and not getattr(result, 'error', None))

        stress_scenario = dict(scenario)
        stress_scenario['duration_seconds'] = duration
        if system.connector_type == 'salesforce':
            # Every SOQL probe counts against the org's daily API quota:
            # cap the worst case at ~100 calls per run (10 s * 10 QPS)
            # regardless of the scenario's QPM/peak settings.
            stress_scenario['queries_per_minute'] = min(
                stress_scenario.get('queries_per_minute', 60), 600
            )
            stress_scenario['peak_multiplier'] = 1.0
            stress_scenario['duration_seconds'] = min(duration, 10)
        try:
            result = await tester.simulate_ai_agent_workload(
                system.name, query_fn, stress_scenario
            )
        except Exception as exc:
            logger.warning(
                "%s: pipeline stress test failed: %s", system.name, exc
            )
            inputs.warnings.append(
                f"{system.name}: pipeline stress test failed"
            )
            continue
        rows.append(
            {
                'system': system.name,
                'error_rate_pct': round(result.error_rate_pct, 2),
                'p95_ms': round(result.p95_ms, 1),
                'actual_qps': round(result.actual_qps, 1),
                'severity': _bottleneck_severity(result.error_rate_pct, result.p95_ms),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_diagnostic(
    config: Dict[str, Any],
    mock: bool = False,
    progress_cb: Optional[Callable[[Dict], None]] = None,
    connections: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the full diagnostic and return a neutral report payload.

    ``config`` is the parsed YAML config (CLI) or an equivalent dict built by
    the API. ``connections`` overrides the config's connections block (the API
    passes its connection-store records). The returned dict contains raw
    analysis outputs; callers map it to their own presentation models. File
    output and terminal UI are the callers' responsibility.
    """
    from preflight.analysis.schema_analyzer import SchemaAnalyzer
    from preflight.analysis.entity_matcher import EntityMatcher
    from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer
    from preflight.analysis.readiness_calculator import ReadinessCalculator
    from preflight.reporting.executive_summary import ExecutiveSummaryGenerator

    def progress(stage: str, description: str) -> None:
        if progress_cb:
            progress_cb({'stage': stage, 'description': description})

    scenario = config.get('scenario', {})
    analysis_cfg = config.get('analysis', {}) or {}
    schema_cfg = analysis_cfg.get('schema_consistency', {}) or {}
    entity_matches: List[Dict] = []
    warnings: List[str] = []
    inputs: Optional[RealInputs] = None

    try:
        # 1. Collect inputs -------------------------------------------------------
        if mock:
            schemas = demo_schemas()
            pipeline_results = demo_pipeline_results()
            connections_list = [
                {'name': s, 'type': SYSTEM_TYPE_MAP.get(s, 'ERP' if 'sap' in s else 'CRM')}
                for s in scenario.get('systems', [])
            ]
            progress('connect', 'Systems connected')
        else:
            conn_defs = connections if connections is not None else _connections_from_config(config)
            inputs = await collect_real_inputs(
                conn_defs,
                scenario,
                connect_timeout=(config.get('performance', {}) or {}).get('connection_timeout', 30),
            )
            warnings.extend(inputs.warnings)
            schemas = inputs.schemas
            connections_list = inputs.connection_summaries()
            fallbacks = sum(1 for s in inputs.systems if s.is_mock_fallback)
            suffix = f" ({fallbacks} on mock data)" if fallbacks else ""
            progress('connect', f'Systems connected: {len(inputs.systems)}{suffix}')

        # 2. Schema analysis ------------------------------------------------------
        analyzer = SchemaAnalyzer(
            similarity_threshold=schema_cfg.get('entity_matching_threshold', 0.8)
        )
        if mock:
            # Frozen demo semantics: same-name entities only (reproduces 97.0/GO).
            schema_results = analyzer.analyze_all(schemas)
            inconsistencies = analyzer.generate_inconsistency_report(schema_results)
            progress('schema', f'Schema analysis: {len(inconsistencies)} issues found')
            progress('pipeline', 'Pipeline test complete')
        else:
            matcher = EntityMatcher(
                # Same default as EntityMatcher itself (0.8) so tested
                # behavior == shipped behavior unless config overrides it.
                field_similarity_threshold=schema_cfg.get('field_similarity_threshold', 0.8),
            )
            # Matching is CPU-bound and quadratic-ish on large estates; keep
            # it off the event loop so API health checks stay responsive.
            groups = await asyncio.to_thread(
                matcher.build_entity_groups, schemas, scenario.get('entities') or []
            )
            if getattr(matcher, "matching_was_capped", False):
                warnings.append(
                    "schema matching was PARTIAL: the number of tables exceeded "
                    "the field-overlap budget; findings may be incomplete"
                )
            entity_matches = [g.as_match_info() for g in groups]
            schema_results = analyzer.analyze_entity_groups(schemas, groups)
            inconsistencies = analyzer.generate_inconsistency_report(schema_results)
            progress('schema', f'Schema analysis: {len(inconsistencies)} issues found')

            stress_cfg = analysis_cfg.get('pipeline_stress_test', {}) or {}
            if stress_cfg.get('enabled', True):
                pipeline_results = await run_pipeline_tests(
                    inputs, scenario, duration_seconds=stress_cfg.get('duration_seconds')
                )
            else:
                pipeline_results = []
            # run_pipeline_tests appends failure warnings to inputs.warnings
            # AFTER the earlier copy — refresh so they reach the payload.
            warnings[:] = list(inputs.warnings)
            progress('pipeline', 'Pipeline test complete')

        # 3. Middleware analysis --------------------------------------------------
        mw_analyzer = MiddlewareAnalyzer()
        gaps = mw_analyzer.analyze(
            connections_list or [{'name': s, 'type': 'ERP'} for s in scenario.get('systems', [])],
            scenario,
            {'inconsistencies': inconsistencies},
        )
        progress('middleware', f'Middleware: {len(gaps)} gaps found')

        # 4. Score ----------------------------------------------------------------
        calc = ReadinessCalculator(weights=(config.get('reporting', {}) or {}).get('risk_weights'))
        breakdown = calc.calculate(inconsistencies, pipeline_results, gaps, [])
        progress('score', f'Score calculated: {breakdown.overall_score:.1f}%')

        # 5. Executive summary + report payload ------------------------------------
        executive_summary = ExecutiveSummaryGenerator().generate(
            verdict=breakdown.verdict,
            score=breakdown.overall_score,
            schema_issues=inconsistencies,
            pipeline_issues=pipeline_results,
            middleware_gaps=gaps,
            scenario=scenario,
            remediation_weeks=breakdown.estimated_remediation_weeks,
        )
        progress('report', 'Report generated')

        return {
            'readiness_score': breakdown.overall_score,
            'verdict': breakdown.verdict,
            'scenario_name': scenario.get('name', 'AI Deployment'),
            'executive_summary': executive_summary,
            'schema_inconsistencies': inconsistencies,
            'middleware_gaps': gaps,
            'pipeline_bottlenecks': pipeline_results,
            'remediation_plan': build_remediation_plan(inconsistencies, gaps),
            'critical_count': breakdown.critical_issues,
            'total_issues': breakdown.total_issues,
            'remediation_weeks': (
                f"{breakdown.estimated_remediation_weeks:.0f}"
                if breakdown.estimated_remediation_weeks
                else "TBD"
            ),
            'entity_matches': entity_matches,
            'warnings': warnings,
            'mock': mock,
            # Which systems contributed real vs synthetic (mock-fallback)
            # data, so reports can say when a 'real' score mixes synthetic
            # schemas.
            'data_sources': (
                {
                    s.name: (
                        'mock'
                        if (s.is_mock_fallback or s.connector_type == 'mock')
                        else 'real'
                    )
                    for s in inputs.systems
                }
                if inputs else {}
            ),
        }

    finally:
        if inputs is not None:
            for system in inputs.systems:
                await _safe_disconnect(system.connector)


def _connections_from_config(config: Dict[str, Any]) -> List[Dict]:
    """Build connection definitions from the YAML config's connections block."""
    conns = []
    for key, cfg in (config.get('connections') or {}).items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get('enabled', False):
            continue
        conns.append({'name': key, 'connector_type': key, 'config': cfg})
    return conns


def build_remediation_plan(inconsistencies: List[Dict], gaps: List[Dict]) -> List[Dict]:
    """Build a prioritized remediation plan (shared by CLI and API)."""
    items: List[Dict] = []
    seq = 1

    for gap in gaps:
        if gap.get('blocking', False):
            effort = gap.get('effort_days', (5, 20))
            items.append({
                'id': f"rem_{gap.get('id', seq)}",
                'title': f"Implement {gap.get('type', 'middleware').replace('_', ' ').title()}",
                'description': gap.get('description', ''),
                'category': 'middleware',
                'priority': 9 if gap.get('severity') == 'CRITICAL' else 7,
                'severity': gap.get('severity', 'HIGH'),
                'effort_min_days': effort[0] if isinstance(effort, tuple) else 5,
                'effort_max_days': effort[1] if isinstance(effort, tuple) else 20,
                'recommended_sequence': seq,
            })
            seq += 1

    critical_schema = [i for i in inconsistencies if i.get('severity') == 'CRITICAL']
    if critical_schema:
        items.append({
            'id': 'rem_schema_critical',
            'title': 'Resolve Critical Schema Mismatches',
            'description': f'Fix {len(critical_schema)} critical key/schema mismatches across systems',
            'category': 'schema',
            'priority': 10,
            'severity': 'CRITICAL',
            'effort_min_days': len(critical_schema) * 3,
            'effort_max_days': len(critical_schema) * 7,
            'recommended_sequence': seq,
        })
        seq += 1

    high_schema = [i for i in inconsistencies if i.get('severity') == 'HIGH']
    if high_schema:
        items.append({
            'id': 'rem_schema_high',
            'title': 'Resolve High-Severity Schema Issues',
            'description': f'Fix {len(high_schema)} high-severity schema issues',
            'category': 'schema',
            'priority': 8,
            'severity': 'HIGH',
            'effort_min_days': len(high_schema) * 2,
            'effort_max_days': len(high_schema) * 5,
            'recommended_sequence': seq,
        })

    return items
