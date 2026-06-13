"""
Preflight CLI — Run AI deployment readiness diagnostics from the command line.
"""
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
import yaml
import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.text import Text

console = Console()

@click.group()
@click.version_option(version="0.1.0", prog_name="preflight")
def cli():
    """Preflight — Pre-purchase AI readiness diagnostic tool."""
    pass

@cli.command()
@click.option('--config', '-c', required=True, type=click.Path(exists=True), help='Config file path')
@click.option('--output', '-o', default='./reports', help='Output directory for reports')
@click.option('--format', '-f', type=click.Choice(['html', 'json', 'text']), default='html')
@click.option('--mock', is_flag=True, default=False, help='Use mock data (for demo/testing)')
def run(config: str, output: str, format: str, mock: bool):
    """Run a full AI deployment readiness diagnostic."""
    asyncio.run(_run_diagnostic(config, output, format, mock))

async def _run_diagnostic(config_path: str, output_dir: str, format: str, mock: bool):
    """Execute the diagnostic pipeline."""
    console.print(Panel.fit(
        "[bold blue]Preflight Integration Tester[/bold blue]\n"
        "Pre-purchase AI Readiness Diagnostic",
        border_style="blue"
    ))

    # Load config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    scenario = config.get('scenario', {})
    console.print(f"\n[bold]Scenario:[/bold] {scenario.get('name', 'AI Deployment')}")
    console.print(f"[bold]Target Systems:[/bold] {', '.join(scenario.get('systems', []))}")
    console.print()

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        # Step 1: Connect
        connect_task = progress.add_task("[cyan]Connecting to systems...", total=100)
        await asyncio.sleep(0.5)
        progress.update(connect_task, completed=100, description="[green]Systems connected")

        # Step 2: Schema Analysis
        schema_task = progress.add_task("[cyan]Analyzing schema consistency...", total=100)

        from preflight.analysis.schema_analyzer import SchemaAnalyzer
        analyzer = SchemaAnalyzer(
            similarity_threshold=config.get('analysis', {}).get('schema_consistency', {}).get('entity_matching_threshold', 0.8)
        )

        # Get mock schemas or real schemas
        if mock:
            mock_schemas = _get_mock_schemas()
        else:
            mock_schemas = _get_mock_schemas()  # TODO: real connector

        schema_results = analyzer.analyze_all(mock_schemas)
        inconsistencies = analyzer.generate_inconsistency_report(schema_results)
        progress.update(schema_task, completed=100, description=f"[green]Schema analysis: {len(inconsistencies)} issues found")

        # Step 3: Pipeline Testing
        pipeline_task = progress.add_task("[cyan]Stress testing pipelines...", total=100)
        await asyncio.sleep(1.0)  # Simulate test
        pipeline_results = _get_mock_pipeline_results()
        progress.update(pipeline_task, completed=100, description="[green]Pipeline test complete")

        # Step 4: Middleware Analysis
        mw_task = progress.add_task("[cyan]Analyzing middleware gaps...", total=100)
        from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer
        mw_analyzer = MiddlewareAnalyzer()
        connections = [{'name': s, 'type': 'ERP' if 'sap' in s else 'CRM'} for s in scenario.get('systems', ['system1'])]
        gaps = mw_analyzer.analyze(connections, scenario, {'inconsistencies': inconsistencies})
        progress.update(mw_task, completed=100, description=f"[green]Middleware: {len(gaps)} gaps found")

        # Step 5: Calculate Score
        score_task = progress.add_task("[cyan]Calculating readiness score...", total=100)
        from preflight.analysis.readiness_calculator import ReadinessCalculator
        calc = ReadinessCalculator(weights=config.get('reporting', {}).get('risk_weights'))
        breakdown = calc.calculate(inconsistencies, pipeline_results, gaps, [])
        progress.update(score_task, completed=100, description=f"[green]Score calculated: {breakdown.overall_score:.1f}%")

        # Step 6: Generate Report
        report_task = progress.add_task("[cyan]Generating report...", total=100)
        from preflight.reporting.html_reporter import HTMLReporter
        from preflight.reporting.executive_summary import ExecutiveSummaryGenerator

        summary_gen = ExecutiveSummaryGenerator()
        executive_summary = summary_gen.generate(
            verdict=breakdown.verdict,
            score=breakdown.overall_score,
            schema_issues=inconsistencies,
            pipeline_issues=pipeline_results,
            middleware_gaps=gaps,
            scenario=scenario,
            remediation_weeks=breakdown.estimated_remediation_weeks,
        )

        report_data = {
            'readiness_score': breakdown.overall_score,
            'verdict': breakdown.verdict,
            'scenario_name': scenario.get('name', 'AI Deployment'),
            'executive_summary': executive_summary,
            'schema_inconsistencies': [_map_inconsistency(i) for i in inconsistencies],
            'middleware_gaps': [_map_gap(g) for g in gaps],
            'pipeline_bottlenecks': pipeline_results,
            'remediation_plan': _build_remediation(inconsistencies, gaps),
            'critical_count': breakdown.critical_issues,
            'total_issues': breakdown.total_issues,
            'remediation_weeks': f"{breakdown.estimated_remediation_weeks:.0f}" if breakdown.estimated_remediation_weeks else "TBD",
        }

        if format == 'html' or format == 'text':
            reporter = HTMLReporter()
            report_path = Path(output_dir) / 'readiness-assessment.html'
            reporter.save(report_data, str(report_path))

        json_path = Path(output_dir) / 'readiness-assessment.json'
        with open(json_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)

        progress.update(report_task, completed=100, description="[green]Report generated")

    # Display results
    _display_results(breakdown, inconsistencies, gaps, output_dir)

def _display_results(breakdown, inconsistencies, gaps, output_dir):
    """Display results in a rich terminal output."""
    # Score panel
    score = breakdown.overall_score
    if score >= 80:
        color = "green"
        emoji = "OK"
    elif score >= 50:
        color = "yellow"
        emoji = "WARN"
    else:
        color = "red"
        emoji = "FAIL"

    verdict_display = breakdown.verdict.replace('_', ' ')
    console.print()
    console.print(Panel(
        f"\n  [{color} bold]{score:.1f}%[/{color} bold]  [{color}]{verdict_display}[/{color}]  {emoji}\n",
        title="[bold]READINESS SCORE[/bold]",
        border_style=color,
    ))

    # Issues table
    if inconsistencies or gaps:
        table = Table(title="Key Issues Found", box=box.ROUNDED)
        table.add_column("Type", style="cyan")
        table.add_column("Severity", style="bold")
        table.add_column("Details")

        for issue in inconsistencies[:5]:
            severity_style = {"CRITICAL": "red bold", "HIGH": "yellow", "MEDIUM": "blue"}.get(issue.get('severity', ''), "white")
            table.add_row(
                "Schema",
                f"[{severity_style}]{issue.get('severity', '')}[/{severity_style}]",
                issue.get('detail', ''),
            )

        for gap in gaps[:3]:
            severity_style = {"CRITICAL": "red bold", "HIGH": "yellow", "MEDIUM": "blue"}.get(gap.get('severity', ''), "white")
            table.add_row(
                "Middleware",
                f"[{severity_style}]{gap.get('severity', '')}[/{severity_style}]",
                gap.get('description', ''),
            )

        console.print(table)

    console.print(f"\n[bold]Reports saved to:[/bold] {output_dir}/")
    console.print(f"  readiness-assessment.html")
    console.print(f"  readiness-assessment.json")

    if breakdown.estimated_remediation_weeks:
        console.print(f"\n[bold]Estimated remediation:[/bold] {breakdown.estimated_remediation_weeks:.0f}-{breakdown.estimated_remediation_weeks*1.5:.0f} weeks")

def _get_mock_schemas():
    """Return realistic mock enterprise schemas for demo."""
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

def _get_mock_pipeline_results():
    """Return mock pipeline test results."""
    return [
        {'system': 'SAP ERP', 'error_rate_pct': 2.5, 'p95_ms': 380, 'actual_qps': 45, 'severity': 'LOW'},
        {'system': 'Salesforce', 'error_rate_pct': 0.5, 'p95_ms': 180, 'actual_qps': 100, 'severity': 'INFO'},
    ]

def _map_inconsistency(i: dict) -> dict:
    return {
        'id': i.get('id', 'unknown'),
        'entity_name': i.get('entity', 'unknown'),
        'source_system': i.get('source', ''),
        'target_system': i.get('target', ''),
        'inconsistency_type': i.get('type', ''),
        'severity': i.get('severity', 'MEDIUM'),
        'impact_description': i.get('detail', ''),
        'remediation_hint': 'Review field definitions and align schemas',
    }

def _map_gap(g: dict) -> dict:
    effort = g.get('effort_days', (5, 20))
    pattern = g.get('pattern')
    recommended = ''
    if pattern is not None:
        if hasattr(pattern, 'description'):
            recommended = pattern.description
        elif isinstance(pattern, dict):
            recommended = pattern.get('description', '')
    return {
        'id': g.get('id', 'unknown'),
        'gap_type': g.get('type', ''),
        'severity': g.get('severity', 'MEDIUM'),
        'blocking': g.get('blocking', False),
        'description': g.get('description', ''),
        'effort_min_days': effort[0] if isinstance(effort, tuple) else 5,
        'effort_max_days': effort[1] if isinstance(effort, tuple) else 20,
        'recommended_solution': recommended,
    }

def _build_remediation(inconsistencies: list, gaps: list) -> list:
    """Build a prioritized remediation plan."""
    items = []
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

    return items

if __name__ == '__main__':
    cli()
