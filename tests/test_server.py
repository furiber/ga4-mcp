import asyncio

import pytest
from google.analytics import data_v1beta as data
from google.api_core import exceptions as gexc
from mcp.server.mcpserver.exceptions import ToolError

from ga4_mcp import server


class FakeData:
    def __init__(self):
        self.requests = []

    def run_report(self, req):
        self.requests.append(req)
        return data.RunReportResponse(
            dimension_headers=[data.DimensionHeader(name="country")],
            metric_headers=[
                data.MetricHeader(name="sessions", type_=data.MetricType.TYPE_INTEGER),
                data.MetricHeader(name="bounceRate", type_=data.MetricType.TYPE_FLOAT),
            ],
            rows=[
                data.Row(
                    dimension_values=[data.DimensionValue(value="France")],
                    metric_values=[data.MetricValue(value="42"), data.MetricValue(value="0.5")],
                )
            ],
            row_count=1,
        )


@pytest.fixture
def fake(monkeypatch):
    f = FakeData()
    monkeypatch.setattr(server, "_data_client", lambda: f)
    return f


def test_property_name():
    assert server.property_name("123") == "properties/123"
    assert server.property_name("properties/123") == "properties/123"
    with pytest.raises(ToolError):
        server.property_name("G-ABC123")


def test_run_report_builds_request_and_flattens(fake):
    out = server.run_report(
        property_id="123",
        metrics=["sessions", "bounceRate"],
        dimensions=["country"],
        dimension_filter={"filter": {"fieldName": "country", "stringFilter": {"value": "France"}}},
        order_bys=[{"metric": {"metric_name": "sessions"}, "desc": True}],
        metric_aggregations=["total"],
    )
    req = fake.requests[0]
    assert req.property == "properties/123"
    assert req.dimension_filter.filter.string_filter.value == "France"
    assert req.order_bys[0].desc
    assert req.date_ranges[0].start_date == "28daysAgo"
    assert list(req.metric_aggregations) == [data.MetricAggregation.TOTAL]
    assert out["rows"] == [{"country": "France", "sessions": 42, "bounceRate": 0.5}]
    assert out["metrics"] == {"sessions": "TYPE_INTEGER", "bounceRate": "TYPE_FLOAT"}


def test_bad_filter_is_tool_error(fake):
    with pytest.raises(ToolError, match="dimension_filter"):
        server.run_report(property_id="1", metrics=["sessions"], dimension_filter={"nope": 1})


def test_api_error_becomes_tool_error(monkeypatch):
    class Boom:
        def run_report(self, req):
            raise gexc.PermissionDenied("no access")

    monkeypatch.setattr(server, "_data_client", lambda: Boom())
    with pytest.raises(ToolError, match="403"):
        server.run_report(property_id="1", metrics=["sessions"])


def test_tools_registered_and_read_only():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"run_report", "run_realtime_report", "get_metadata", "list_account_summaries"} <= names
    assert all(t.annotations.read_only_hint for t in tools)
    run_report = next(t for t in tools if t.name == "run_report")
    assert "metrics" in run_report.input_schema["required"]
