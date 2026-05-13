"""
Performance dashboard integration for the 4S1T Agent AI framework.

Provides a simple web-based dashboard for viewing performance metrics.
"""
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

from utils.logger import setup_logger
from components.performance.monitor import get_performance_monitor, Metric, MetricType

logger = setup_logger(__name__)


@dataclass
class DashboardConfig:
    """Configuration for the performance dashboard."""
    title: str = "4S1T Agent AI Performance Dashboard"
    refresh_interval: int = 30  # seconds
    max_history_points: int = 100
    theme: str = "light"
    show_system_metrics: bool = True
    show_custom_metrics: bool = True


class PerformanceDashboard:
    """Simple web-based dashboard for performance metrics."""
    
    def __init__(self, config: Optional[DashboardConfig] = None):
        """
        Initialize the performance dashboard.
        
        Args:
            config: Dashboard configuration
        """
        self.config = config or DashboardConfig()
        self._monitor = get_performance_monitor()
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        logger.info("Performance dashboard initialized")
    
    def get_dashboard_html(self) -> str:
        """
        Generate the dashboard HTML.
        
        Returns:
            Dashboard HTML content
        """
        metrics = self._monitor.collect_metrics()
        metrics_data = self._prepare_metrics_data(metrics)
        
        # Generate charts data
        charts_data = self._generate_charts_data(metrics)
        
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.config.title}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: {'#f5f5f5' if self.config.theme == 'light' else '#333'};
            color: {'#333' if self.config.theme == 'light' else '#fff'};
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .metric-card {{
            background: {'#fff' if self.config.theme == 'light' else '#444'};
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .metric-title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: bold;
            color: #007acc;
        }}
        .metric-description {{
            font-size: 14px;
            color: #666;
            margin-top: 5px;
        }}
        .charts-container {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
        }}
        .chart-card {{
            background: {'#fff' if self.config.theme == 'light' else '#444'};
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .chart-title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 15px;
        }}
        canvas {{
            width: 100% !important;
            height: 300px !important;
        }}
        .refresh-info {{
            text-align: center;
            margin-top: 20px;
            font-size: 14px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{self.config.title}</h1>
        <p>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    
    <div class="metrics-grid">
        {self._generate_metric_cards(metrics_data)}
    </div>
    
    <div class="charts-container">
        {self._generate_chart_elements(charts_data)}
    </div>
    
    <div class="refresh-info">
        <p>Auto-refresh every {self.config.refresh_interval} seconds</p>
    </div>
    
    <script>
        // Chart data
        const chartsData = {json.dumps(charts_data)};
        
        // Render charts
        Object.keys(chartsData).forEach(chartId => {{
            const chartData = chartsData[chartId];
            const ctx = document.getElementById(chartId).getContext('2d');
            
            new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: chartData.labels,
                    datasets: [{{
                        label: chartData.label,
                        data: chartData.data,
                        borderColor: '#007acc',
                        backgroundColor: 'rgba(0, 122, 204, 0.1)',
                        tension: 0.1
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        y: {{
                            beginAtZero: true
                        }}
                    }}
                }}
            }});
        }});
        
        // Auto-refresh
        setTimeout(() => {{
            location.reload();
        }}, {self.config.refresh_interval * 1000});
    </script>
</body>
</html>
        """
        
        return html
    
    def _prepare_metrics_data(self, metrics: List[Metric]) -> List[Dict[str, Any]]:
        """
        Prepare metrics data for display.
        
        Args:
            metrics: List of metrics
            
        Returns:
            Prepared metrics data
        """
        prepared_data = []
        
        for metric in metrics:
            # Format value based on unit
            formatted_value = self._format_metric_value(metric)
            
            # Add to history
            self._add_to_history(metric.name, metric.value, metric.timestamp)
            
            prepared_data.append({
                "name": metric.name,
                "value": formatted_value,
                "raw_value": metric.value,
                "type": metric.type.value,
                "unit": metric.unit.value,
                "description": metric.description,
                "labels": metric.labels,
                "timestamp": metric.timestamp.isoformat()
            })
        
        return prepared_data
    
    def _format_metric_value(self, metric: Metric) -> str:
        """
        Format a metric value for display.
        
        Args:
            metric: Metric to format
            
        Returns:
            Formatted value string
        """
        value = metric.value
        
        if metric.unit == "percent":
            return f"{value:.1f}%"
        elif metric.unit == "bytes":
            return self._format_bytes(value)
        elif metric.unit == "seconds":
            return f"{value:.3f}s"
        elif isinstance(value, float):
            return f"{value:.2f}"
        else:
            return str(value)
    
    def _format_bytes(self, bytes_value: Union[int, float]) -> str:
        """
        Format bytes value for display.
        
        Args:
            bytes_value: Bytes value to format
            
        Returns:
            Formatted bytes string
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024.0:
                return f"{bytes_value:.1f}{unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f}PB"
    
    def _add_to_history(self, metric_name: str, value: Union[int, float], timestamp: datetime) -> None:
        """
        Add a metric value to history.
        
        Args:
            metric_name: Metric name
            value: Metric value
            timestamp: Timestamp
        """
        if metric_name not in self._history:
            self._history[metric_name] = []
        
        self._history[metric_name].append({
            "value": value,
            "timestamp": timestamp.isoformat()
        })
        
        # Limit history size
        if len(self._history[metric_name]) > self.config.max_history_points:
            self._history[metric_name] = self._history[metric_name][-self.config.max_history_points:]
    
    def _generate_charts_data(self, metrics: List[Metric]) -> Dict[str, Any]:
        """
        Generate data for charts.
        
        Args:
            metrics: List of metrics
            
        Returns:
            Charts data
        """
        charts_data = {}
        
        # Get metrics with history
        for metric_name, history in self._history.items():
            if len(history) > 1:  # Need at least 2 points for a chart
                chart_id = f"chart_{metric_name.replace('.', '_').replace('-', '_')}"
                
                # Extract labels and data
                labels = [point["timestamp"].split("T")[1].split(":")[0] + ":" + point["timestamp"].split("T")[1].split(":")[1] 
                         for point in history[-20:]]  # Last 20 points
                data = [point["value"] for point in history[-20:]]
                
                charts_data[chart_id] = {
                    "labels": labels,
                    "data": data,
                    "label": metric_name
                }
        
        return charts_data
    
    def _generate_metric_cards(self, metrics_data: List[Dict[str, Any]]) -> str:
        """
        Generate HTML for metric cards.
        
        Args:
            metrics_data: Prepared metrics data
            
        Returns:
            HTML for metric cards
        """
        cards_html = ""
        
        # Filter metrics based on config
        if self.config.show_system_metrics:
            system_metrics = [m for m in metrics_data if m["name"].startswith("system_")]
            cards_html += self._generate_cards_for_metrics(system_metrics, "System Metrics")
        
        if self.config.show_custom_metrics:
            custom_metrics = [m for m in metrics_data if not m["name"].startswith("system_")]
            cards_html += self._generate_cards_for_metrics(custom_metrics, "Custom Metrics")
        
        return cards_html
    
    def _generate_cards_for_metrics(self, metrics: List[Dict[str, Any]], section_title: str) -> str:
        """
        Generate HTML for metric cards in a section.
        
        Args:
            metrics: List of metrics
            section_title: Section title
            
        Returns:
            HTML for metric cards
        """
        if not metrics:
            return ""
        
        cards_html = f'<h2 style="grid-column: 1 / -1;">{section_title}</h2>'
        
        for metric in metrics:
            cards_html += f"""
<div class="metric-card">
    <div class="metric-title">{metric['name']}</div>
    <div class="metric-value">{metric['value']}</div>
    <div class="metric-description">{metric['description']}</div>
    {self._format_labels(metric['labels'])}
</div>
            """
        
        return cards_html
    
    def _format_labels(self, labels: Dict[str, str]) -> str:
        """
        Format labels for display.
        
        Args:
            labels: Labels dictionary
            
        Returns:
            HTML for labels
        """
        if not labels:
            return ""
        
        labels_html = '<div class="metric-labels">'
        for key, value in labels.items():
            labels_html += f'<span style="background: #e9ecef; padding: 2px 6px; border-radius: 4px; margin-right: 4px; font-size: 12px;">{key}: {value}</span>'
        labels_html += '</div>'
        
        return labels_html
    
    def _generate_chart_elements(self, charts_data: Dict[str, Any]) -> str:
        """
        Generate HTML for chart elements.
        
        Args:
            charts_data: Charts data
            
        Returns:
            HTML for chart elements
        """
        if not charts_data:
            return '<p style="grid-column: 1 / -1; text-align: center;">No historical data available for charts</p>'
        
        charts_html = ""
        for chart_id, chart_data in charts_data.items():
            charts_html += f"""
<div class="chart-card">
    <div class="chart-title">{chart_data['label']}</div>
    <canvas id="{chart_id}"></canvas>
</div>
            """
        
        return charts_html
    
    def get_metrics_json(self) -> str:
        """
        Get metrics data as JSON.
        
        Returns:
            JSON string with metrics data
        """
        metrics = self._monitor.collect_metrics()
        metrics_data = self._prepare_metrics_data(metrics)
        
        return json.dumps({
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics_data
        }, indent=2)


# Convenience functions
def get_performance_dashboard(config: Optional[DashboardConfig] = None) -> PerformanceDashboard:
    """Get the performance dashboard instance."""
    return PerformanceDashboard(config)


def generate_dashboard_html(config: Optional[DashboardConfig] = None) -> str:
    """Generate dashboard HTML."""
    dashboard = get_performance_dashboard(config)
    return dashboard.get_dashboard_html()


def get_metrics_json(config: Optional[DashboardConfig] = None) -> str:
    """Get metrics as JSON."""
    dashboard = get_performance_dashboard(config)
    return dashboard.get_metrics_json()
