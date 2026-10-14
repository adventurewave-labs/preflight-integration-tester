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
    from preflight.analysis import pipeline_runner
    from preflight.analysis.pipeline_runner import PipelineError

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

        tasks = {
            'connect': progress.add_task("[cyan]Connecting to systems...", total=100),
            'schema': progress.add_task("[cyan]Analyzing schema consistency...", total=100),
            'pipeline': progress.add_task("[cyan]Stress testing pipelines...", total=100),
            'middleware': progress.add_task("[cyan]Analyzing middleware gaps...", total=100),
            'score': progress.add_task("[cyan]Calculating readiness score...", total=100),
            'report': progress.add_task("[cyan]Generating report...", total=100),
        }

        def on_progress(event: dict) -> None:
            task = tasks.get(event.get('stage'))
            if task is not None:
                progress.update(task, completed=100, description=f"[green]{event.get('description', '')}")

        try:
            payload = await pipeline_runner.run_diagnostic(config, mock=mock, progress_cb=on_progress)
        except PipelineError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            sys.exit(1)

        inconsistencies = payload['schema_inconsistencies']
        gaps = payload['middleware_gaps']
        warnings = payload.get('warnings') or []

        # Complete any tasks the (mock) fast path did not fire
        for task in tasks.values():
            progress.update(task, completed=100)

        report_data = {
            'readiness_score': payload['readiness_score'],
            'verdict': payload['verdict'],
            'scenario_name': payload['scenario_name'],
            'executive_summary': payload['executive_summary'],
            'schema_inconsistencies': [_map_inconsistency(i) for i in inconsistencies],
            'middleware_gaps': [_map_gap(g) for g in gaps],
            'pipeline_bottlenecks': payload['pipeline_bottlenecks'],
            'remediation_plan': payload['remediation_plan'],
            'critical_count': payload['critical_count'],
            'total_issues': payload['total_issues'],
            'remediation_weeks': payload['remediation_weeks'],
            'entity_matches': payload.get('entity_matches') or [],
            'warnings': warnings,
            'mock': payload.get('mock', mock),
            'data_sources': payload.get('data_sources') or {},
        }

        if format == 'html' or format == 'text':
            from preflight.reporting.html_reporter import HTMLReporter
            reporter = HTMLReporter()
            report_path = Path(output_dir) / 'readiness-assessment.html'
            reporter.save(report_data, str(report_path))

        json_path = Path(output_dir) / 'readiness-assessment.json'
        with open(json_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)

    for warning in warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    if any('MOCK data' in w for w in warnings):
        console.print(
            "[yellow]Results above include mock data for the flagged systems — "
            "install the missing client libraries and re-run for real data.[/yellow]"
        )

    # Display results
    _display_results(report_data, inconsistencies, gaps, output_dir)

def _display_results(report_data, inconsistencies, gaps, output_dir):
    """Display results in a rich terminal output."""
    # Score panel — color and icon follow the VERDICT (a NOT_YET verdict must
    # never show a green OK icon just because the score crossed 80).
    score = report_data['readiness_score']
    verdict = report_data['verdict']
    if verdict == 'GO':
        color = "green"
        emoji = "OK"
    elif verdict == 'NOT_YET':
        color = "yellow"
        emoji = "WARN"
    else:
        color = "red"
        emoji = "FAIL"

    verdict_display = report_data['verdict'].replace('_', ' ')
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

    remediation_weeks = report_data.get('remediation_weeks')
    if remediation_weeks and remediation_weeks != "TBD":
        weeks = float(remediation_weeks)
        console.print(f"\n[bold]Estimated remediation:[/bold] {weeks:.0f}-{weeks*1.5:.0f} weeks")

def _get_mock_schemas():
    """Return the frozen demo dataset (see pipeline_runner.demo_schemas)."""
    from preflight.analysis.pipeline_runner import demo_schemas
    return demo_schemas()

def _get_mock_pipeline_results():
    """Return the frozen demo pipeline results (see pipeline_runner)."""
    from preflight.analysis.pipeline_runner import demo_pipeline_results
    return demo_pipeline_results()

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
        'entity_match': i.get('entity_match'),
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
    """Build a prioritized remediation plan (shared implementation in the runner)."""
    from preflight.analysis.pipeline_runner import build_remediation_plan
    return build_remediation_plan(inconsistencies, gaps)

if __name__ == '__main__':
    cli()
