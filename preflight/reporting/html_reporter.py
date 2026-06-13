from jinja2 import Environment, DictLoader
from datetime import datetime
from typing import Dict, Any, List

REPORT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Preflight Readiness Report</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f7fa; color: #1a1a2e; }
  .container { max-width: 1100px; margin: 0 auto; padding: 2rem; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 2.5rem; border-radius: 12px; margin-bottom: 2rem; }
  .header h1 { font-size: 2rem; margin-bottom: 0.5rem; }
  .header p { opacity: 0.8; font-size: 1rem; }
  .score-card { display: flex; gap: 2rem; margin-bottom: 2rem; }
  .score-box { background: white; border-radius: 12px; padding: 2rem; flex: 1; box-shadow: 0 2px 12px rgba(0,0,0,0.08); text-align: center; }
  .score-number { font-size: 4rem; font-weight: 800; line-height: 1; }
  .score-label { font-size: 0.9rem; color: #666; margin-top: 0.5rem; text-transform: uppercase; letter-spacing: 1px; }
  .verdict-badge { display: inline-block; padding: 0.5rem 1.5rem; border-radius: 50px; font-weight: 700; font-size: 1.1rem; margin-top: 1rem; }
  .verdict-go { background: #d4edda; color: #155724; }
  .verdict-not-yet { background: #fff3cd; color: #856404; }
  .verdict-not-ready { background: #f8d7da; color: #721c24; }
  .score-green { color: #28a745; }
  .score-yellow { color: #ffc107; }
  .score-red { color: #dc3545; }
  .section { background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
  .section h2 { font-size: 1.3rem; margin-bottom: 1rem; color: #1a1a2e; border-bottom: 2px solid #f0f0f0; padding-bottom: 0.5rem; }
  .executive-summary { font-size: 1rem; line-height: 1.7; color: #444; white-space: pre-line; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #f8f9fa; text-align: left; padding: 0.75rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; color: #666; }
  td { padding: 0.75rem; border-bottom: 1px solid #f0f0f0; font-size: 0.9rem; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
  .badge-critical { background: #f8d7da; color: #721c24; }
  .badge-high { background: #fff3cd; color: #856404; }
  .badge-medium { background: #d1ecf1; color: #0c5460; }
  .badge-low { background: #d4edda; color: #155724; }
  .metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1rem; }
  .metric { background: #f8f9fa; border-radius: 8px; padding: 1rem; text-align: center; }
  .metric-value { font-size: 1.8rem; font-weight: 700; color: #1a1a2e; }
  .metric-label { font-size: 0.8rem; color: #666; margin-top: 0.25rem; }
  .footer { text-align: center; color: #999; font-size: 0.8rem; margin-top: 2rem; padding: 1rem; }
  @media print { body { background: white; } .container { max-width: 100%; } }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Preflight Readiness Assessment</h1>
    <p>AI Deployment Readiness Diagnostic Report &bull; Generated {{ generated_at }}</p>
    <p>Scenario: <strong>{{ scenario_name }}</strong></p>
  </div>

  <div class="score-card">
    <div class="score-box">
      <div class="score-number score-{{ score_color }}">{{ score }}%</div>
      <div class="score-label">Readiness Score</div>
      <div class="verdict-badge verdict-{{ verdict_class }}">{{ verdict }}</div>
    </div>
    <div class="score-box">
      <div class="metrics-grid">
        <div class="metric">
          <div class="metric-value score-red">{{ critical_count }}</div>
          <div class="metric-label">Critical Issues</div>
        </div>
        <div class="metric">
          <div class="metric-value score-yellow">{{ total_issues }}</div>
          <div class="metric-label">Total Issues</div>
        </div>
        <div class="metric">
          <div class="metric-value">{{ remediation_weeks }}w</div>
          <div class="metric-label">Est. Remediation</div>
        </div>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>Executive Summary</h2>
    <div class="executive-summary">{{ executive_summary }}</div>
  </div>

  {% if schema_inconsistencies %}
  <div class="section">
    <h2>Schema Inconsistencies ({{ schema_inconsistencies|length }})</h2>
    <table>
      <thead><tr><th>Entity</th><th>Systems</th><th>Issue Type</th><th>Severity</th><th>Impact</th></tr></thead>
      <tbody>
        {% for issue in schema_inconsistencies %}
        <tr>
          <td><strong>{{ issue.entity_name }}</strong></td>
          <td>{{ issue.source_system }} &rarr; {{ issue.target_system }}</td>
          <td>{{ issue.inconsistency_type }}</td>
          <td><span class="badge badge-{{ issue.severity|lower }}">{{ issue.severity }}</span></td>
          <td>{{ issue.impact_description }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  {% if middleware_gaps %}
  <div class="section">
    <h2>Middleware Gaps ({{ middleware_gaps|length }})</h2>
    <table>
      <thead><tr><th>Gap Type</th><th>Blocking</th><th>Severity</th><th>Effort</th><th>Description</th></tr></thead>
      <tbody>
        {% for gap in middleware_gaps %}
        <tr>
          <td><strong>{{ gap.gap_type }}</strong></td>
          <td>{{ 'Yes' if gap.blocking else 'No' }}</td>
          <td><span class="badge badge-{{ gap.severity|lower }}">{{ gap.severity }}</span></td>
          <td>{{ gap.effort_min_days }}-{{ gap.effort_max_days }} days</td>
          <td>{{ gap.description }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  {% if remediation_plan %}
  <div class="section">
    <h2>Remediation Plan ({{ remediation_plan|length }} items)</h2>
    <table>
      <thead><tr><th>#</th><th>Priority</th><th>Item</th><th>Category</th><th>Effort</th><th>Severity</th></tr></thead>
      <tbody>
        {% for item in remediation_plan %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ item.priority }}/10</td>
          <td><strong>{{ item.title }}</strong><br><small style="color:#666">{{ item.description }}</small></td>
          <td>{{ item.category }}</td>
          <td>{{ item.effort_min_days }}-{{ item.effort_max_days }}d</td>
          <td><span class="badge badge-{{ item.severity|lower }}">{{ item.severity }}</span></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <div class="footer">
    Preflight Integration Tester &bull; Read-only diagnostic &bull; Vendor-independent assessment<br>
    Report generated {{ generated_at }}
  </div>
</div>
</body>
</html>"""


def _access(obj: Any, key: str, default: Any = "") -> Any:
    """Access a key from either a dict or an object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class HTMLReporter:
    """Generates HTML readiness reports."""

    def __init__(self):
        self.env = Environment(loader=DictLoader({'report': REPORT_TEMPLATE}))

    def generate(self, report_data: Dict) -> str:
        """Generate HTML report from report data dict."""
        template = self.env.get_template('report')

        score = report_data.get('readiness_score', 0)
        verdict = report_data.get('verdict', 'NOT_READY')

        if isinstance(verdict, str):
            verdict_str = verdict
        else:
            # Handle enum-like objects
            verdict_str = getattr(verdict, 'value', str(verdict))

        if score >= 80:
            score_color = 'green'
        elif score >= 50:
            score_color = 'yellow'
        else:
            score_color = 'red'

        verdict_class = verdict_str.lower().replace('_', '-')

        # Normalise nested items so templates can use dot or dict access
        def _normalise_list(items: List) -> List[Dict]:
            result = []
            for item in items:
                if isinstance(item, dict):
                    result.append(item)
                else:
                    result.append(vars(item) if hasattr(item, '__dict__') else {})
            return result

        schema_inconsistencies = _normalise_list(report_data.get('schema_inconsistencies', []))
        middleware_gaps = _normalise_list(report_data.get('middleware_gaps', []))
        remediation_plan = _normalise_list(report_data.get('remediation_plan', []))

        return template.render(
            score=f"{score:.1f}",
            score_color=score_color,
            verdict=verdict_str.replace('_', ' '),
            verdict_class=verdict_class,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
            scenario_name=report_data.get('scenario_name', 'AI Deployment Assessment'),
            executive_summary=report_data.get('executive_summary', ''),
            schema_inconsistencies=schema_inconsistencies,
            pipeline_bottlenecks=_normalise_list(report_data.get('pipeline_bottlenecks', [])),
            middleware_gaps=middleware_gaps,
            remediation_plan=remediation_plan,
            critical_count=report_data.get('critical_count', 0),
            total_issues=report_data.get('total_issues', 0),
            remediation_weeks=report_data.get('remediation_weeks', 'TBD'),
        )

    def save(self, report_data: Dict, output_path: str) -> None:
        """Generate and save HTML report to file."""
        html = self.generate(report_data)
        with open(output_path, 'w') as f:
            f.write(html)
