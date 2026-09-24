"""MCP server exposing read-only GA4 reporting and admin lookups."""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
from collections.abc import Mapping
from typing import Any

import google.auth.exceptions
from google.analytics import admin_v1beta as admin
from google.analytics import data_v1beta as data
from google.api_core import exceptions as gexc
from google.oauth2.credentials import Credentials as UserCredentials
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .auth import load_credentials

INSTRUCTIONS = """\
Read-only access to Google Analytics 4.
Typical flow: list_account_summaries -> pick a property ID -> get_metadata to
discover valid dimension/metric API names (incl. custom ones) -> run_report.
Dates accept YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'.
Filters and order_bys use the GA4 Data API JSON shapes (camelCase or snake_case).
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
TOOLS = []


# --------------------------------------------------------------------------- clients
#
# Where the Google credential comes from, first match wins:
# 1. Built-in OAuth server (`ga4-mcp remote`): the verified bearer token carries the
#    caller's Google access token.
# 2. Horizon delegated authorization: Horizon's gateway swaps the caller's Horizon
#    token for their Google access token in the `Authorization` header.
# 3. Server-wide credentials from auth.load_credentials() (GA4_MCP_TOKEN_JSON, the
#    local token file, or ADC).
# Per-caller clients are cached by token, so one user's client never serves another.

_request_headers: contextvars.ContextVar[Mapping[str, str] | None] = contextvars.ContextVar(
    "ga4_request_headers", default=None
)


def _google_token() -> str | None:
    access = get_access_token()
    if access is not None and getattr(access, "google_token", None):
        return access.google_token
    headers = _request_headers.get() or {}
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    # Only Google OAuth access tokens (ya29.*). Horizon "fails open" and may forward
    # its own token when the exchange fails; that must not be sent to Google.
    if scheme.lower() == "bearer" and token.startswith("ya29."):
        return token
    return None


@functools.lru_cache(maxsize=32)
def _clients(google_token: str | None) -> tuple[data.BetaAnalyticsDataClient, admin.AnalyticsAdminServiceClient]:
    if google_token:
        creds, transport = UserCredentials(token=google_token), "rest"
    else:
        creds, transport = load_credentials(), None
    return (
        data.BetaAnalyticsDataClient(credentials=creds, transport=transport),
        admin.AnalyticsAdminServiceClient(credentials=creds, transport=transport),
    )


def _data_client() -> data.BetaAnalyticsDataClient:
    return _clients(_google_token())[0]


def _admin_client() -> admin.AnalyticsAdminServiceClient:
    return _clients(_google_token())[1]


# --------------------------------------------------------------------------- helpers


def property_name(property_id: str | int) -> str:
    pid = str(property_id).strip()
    pid = pid.removeprefix("properties/")
    if not pid.isdigit():
        raise ToolError(f"Invalid GA4 property ID {property_id!r}; expected digits like '123456789'.")
    return f"properties/{pid}"


def _proto_to_dict(msg: Any) -> dict:
    return type(msg).to_dict(msg, preserving_proto_field_name=True, use_integers_for_enums=False)


def _parse(msg_type: Any, value: dict | str | None, field: str) -> Any:
    if value is None:
        return None
    try:
        return msg_type.from_json(value if isinstance(value, str) else json.dumps(value))
    except Exception as e:  # noqa: BLE001 - surface a readable error to the model
        raise ToolError(f"Invalid {field}: {e}") from e


def _date_ranges(date_ranges: list[dict] | None) -> list[data.DateRange]:
    ranges = date_ranges or [{"start_date": "28daysAgo", "end_date": "yesterday"}]
    return [_parse(data.DateRange, r, "date_ranges") for r in ranges]


def _aggregations(names: list[str] | None) -> list[data.MetricAggregation]:
    try:
        return [data.MetricAggregation[n.upper()] for n in names or []]
    except KeyError as e:
        raise ToolError(f"Unknown metric aggregation {e}; use TOTAL, MAXIMUM or MINIMUM.") from e


def tool(fn):
    """Register a read-only tool and turn Google/auth errors into readable tool errors.

    The wrapper also takes the MCP request Context (hidden from the tool's schema) so
    the tool can see HTTP request headers.
    """

    @functools.wraps(fn)
    def wrapper(*args, ctx: Context | None = None, **kwargs):
        headers = ctx.headers if ctx is not None else None
        reset = _request_headers.set(headers)
        try:
            return fn(*args, **kwargs)
        except gexc.GoogleAPICallError as e:
            raise ToolError(f"Google Analytics API error ({e.code}): {e.message}") from e
        except google.auth.exceptions.RefreshError as e:
            if _google_token():  # per-request token without a refresh token
                raise ToolError(
                    "Google rejected the access token sent with this request (expired or revoked). "
                    "Re-authorize Google (Horizon: the server's /authorize page) or reconnect the MCP client."
                ) from e
            raise ToolError(f"Authentication problem: {e}") from e
        except (RuntimeError, google.auth.exceptions.GoogleAuthError) as e:
            raise ToolError(f"Authentication problem: {e}") from e
        finally:
            _request_headers.reset(reset)

    sig = inspect.signature(fn)
    ctx_param = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Context)
    wrapper.__signature__ = sig.replace(parameters=[*sig.parameters.values(), ctx_param])
    wrapper.__annotations__ = {**fn.__annotations__, "ctx": Context}
    TOOLS.append(wrapper)
    return wrapper


def format_report(resp: Any) -> dict:
    """Flatten a RunReport/RunRealtimeReport response into row dicts."""
    dims = [h.name for h in resp.dimension_headers]
    mets = [h.name for h in resp.metric_headers]
    types = {h.name: data.MetricType(h.type_).name for h in resp.metric_headers}

    def rows(rs):
        out = []
        for r in rs:
            row = {d: v.value for d, v in zip(dims, r.dimension_values)}
            row.update({m: _num(v.value, types[m]) for m, v in zip(mets, r.metric_values)})
            out.append(row)
        return out

    result: dict[str, Any] = {
        "row_count": resp.row_count,
        "dimensions": dims,
        "metrics": {m: types[m] for m in mets},
        "rows": rows(resp.rows),
    }
    for key in ("totals", "maximums", "minimums"):
        if getattr(resp, key, None):
            result[key] = rows(getattr(resp, key))
    meta = getattr(resp, "metadata", None)
    if meta is not None:
        m = _proto_to_dict(meta)
        m = {k: v for k, v in m.items() if v not in (None, "", [], False, {})}
        if m:
            result["metadata"] = m
    if "property_quota" in resp and resp.property_quota:
        result["property_quota"] = _proto_to_dict(resp.property_quota)
    return result


def _num(value: str, metric_type: str) -> Any:
    if metric_type == "TYPE_INTEGER":
        try:
            return int(value)
        except ValueError:
            return value
    try:
        return float(value)
    except ValueError:
        return value


# --------------------------------------------------------------------------- admin (read-only)


@tool
def list_account_summaries() -> list[dict]:
    """List every GA4 account and property the signed-in user can access (IDs + display names)."""
    out = []
    for acc in _admin_client().list_account_summaries():
        out.append(
            {
                "account": acc.account,
                "account_name": acc.display_name,
                "properties": [
                    {
                        "property_id": p.property.removeprefix("properties/"),
                        "display_name": p.display_name,
                        "property_type": admin.PropertyType(p.property_type).name,
                    }
                    for p in acc.property_summaries
                ],
            }
        )
    return out


@tool
def get_property(property_id: str) -> dict:
    """Get a GA4 property's settings (time zone, currency, industry, service level, ...)."""
    return _proto_to_dict(_admin_client().get_property(name=property_name(property_id)))


@tool
def list_data_streams(property_id: str) -> list[dict]:
    """List web/app data streams of a property (measurement IDs, app IDs, URLs)."""
    pager = _admin_client().list_data_streams(parent=property_name(property_id))
    return [_proto_to_dict(s) for s in pager]


@tool
def list_custom_definitions(property_id: str) -> dict:
    """List a property's custom dimensions and custom metrics as configured in Admin."""
    parent = property_name(property_id)
    client = _admin_client()
    return {
        "custom_dimensions": [_proto_to_dict(d) for d in client.list_custom_dimensions(parent=parent)],
        "custom_metrics": [_proto_to_dict(m) for m in client.list_custom_metrics(parent=parent)],
    }


@tool
def list_key_events(property_id: str) -> list[dict]:
    """List key events (formerly conversions) configured on a property."""
    pager = _admin_client().list_key_events(parent=property_name(property_id))
    return [_proto_to_dict(e) for e in pager]


@tool
def list_google_ads_links(property_id: str) -> list[dict]:
    """List Google Ads accounts linked to a property."""
    pager = _admin_client().list_google_ads_links(parent=property_name(property_id))
    return [_proto_to_dict(link) for link in pager]


# --------------------------------------------------------------------------- reporting


@tool
def get_metadata(property_id: str, search: str | None = None) -> dict:
    """List dimensions and metrics usable in reports for a property, including its custom ones.

    Args:
        property_id: GA4 property ID (digits).
        search: Optional case-insensitive substring to filter by API name, UI name or category.
    """
    meta = _data_client().get_metadata(name=f"{property_name(property_id)}/metadata")
    needle = (search or "").lower()

    def keep(*fields: str) -> bool:
        return not needle or any(needle in (f or "").lower() for f in fields)

    return {
        "dimensions": [
            {"api_name": d.api_name, "ui_name": d.ui_name, "category": d.category, "custom": d.custom_definition}
            for d in meta.dimensions
            if keep(d.api_name, d.ui_name, d.category)
        ],
        "metrics": [
            {
                "api_name": m.api_name,
                "ui_name": m.ui_name,
                "category": m.category,
                "type": data.MetricType(m.type_).name,
                "custom": m.custom_definition,
            }
            for m in meta.metrics
            if keep(m.api_name, m.ui_name, m.category)
        ],
    }


@tool
def run_report(
    property_id: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    date_ranges: list[dict] | None = None,
    dimension_filter: dict | None = None,
    metric_filter: dict | None = None,
    order_bys: list[dict] | None = None,
    limit: int = 100,
    offset: int = 0,
    metric_aggregations: list[str] | None = None,
    currency_code: str | None = None,
    keep_empty_rows: bool = False,
) -> dict:
    """Run a GA4 Data API report (the core reporting tool).

    Args:
        property_id: GA4 property ID (digits).
        metrics: Metric API names, e.g. ["activeUsers", "sessions", "keyEvents"].
        dimensions: Dimension API names, e.g. ["date", "sessionDefaultChannelGroup"].
        date_ranges: Up to 4 ranges, e.g. [{"start_date": "28daysAgo", "end_date": "yesterday", "name": "current"}].
            Defaults to the last 28 days.
        dimension_filter: FilterExpression, e.g.
            {"filter": {"field_name": "country", "string_filter": {"value": "France"}}}
            or {"and_group": {"expressions": [...]}} / {"not_expression": {...}}.
        metric_filter: FilterExpression on metrics, e.g.
            {"filter": {"field_name": "sessions", "numeric_filter": {"operation": "GREATER_THAN", "value": {"int64_value": "100"}}}}.
        order_bys: e.g. [{"metric": {"metric_name": "sessions"}, "desc": true}] or [{"dimension": {"dimension_name": "date"}}].
        limit: Max rows to return (default 100, API max 250000). Use with offset to page; see row_count.
        offset: Row offset for pagination.
        metric_aggregations: Any of "TOTAL", "MAXIMUM", "MINIMUM".
        currency_code: ISO 4217 code for revenue metrics; defaults to the property currency.
        keep_empty_rows: Include rows where all metrics are zero.
    """
    req = data.RunReportRequest(
        property=property_name(property_id),
        metrics=[data.Metric(name=m) for m in metrics],
        dimensions=[data.Dimension(name=d) for d in dimensions or []],
        date_ranges=_date_ranges(date_ranges),
        dimension_filter=_parse(data.FilterExpression, dimension_filter, "dimension_filter"),
        metric_filter=_parse(data.FilterExpression, metric_filter, "metric_filter"),
        order_bys=[_parse(data.OrderBy, o, "order_bys") for o in order_bys or []],
        limit=limit,
        offset=offset,
        metric_aggregations=_aggregations(metric_aggregations),
        currency_code=currency_code or "",
        keep_empty_rows=keep_empty_rows,
        return_property_quota=True,
    )
    return format_report(_data_client().run_report(req))


@tool
def run_realtime_report(
    property_id: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    dimension_filter: dict | None = None,
    metric_filter: dict | None = None,
    order_bys: list[dict] | None = None,
    limit: int = 100,
    minute_ranges: list[dict] | None = None,
) -> dict:
    """Run a realtime report (last 30 minutes; 60 for GA4 360).

    Only realtime-compatible fields work, e.g. dimensions country, city, deviceCategory,
    unifiedScreenName, eventName, minutesAgo; metrics activeUsers, eventCount, screenPageViews, keyEvents.

    Args:
        minute_ranges: e.g. [{"start_minutes_ago": 29, "end_minutes_ago": 0, "name": "last30"}].
        Other args behave like run_report.
    """
    req = data.RunRealtimeReportRequest(
        property=property_name(property_id),
        metrics=[data.Metric(name=m) for m in metrics],
        dimensions=[data.Dimension(name=d) for d in dimensions or []],
        dimension_filter=_parse(data.FilterExpression, dimension_filter, "dimension_filter"),
        metric_filter=_parse(data.FilterExpression, metric_filter, "metric_filter"),
        order_bys=[_parse(data.OrderBy, o, "order_bys") for o in order_bys or []],
        limit=limit,
        minute_ranges=[_parse(data.MinuteRange, r, "minute_ranges") for r in minute_ranges or []],
        return_property_quota=True,
    )
    return format_report(_data_client().run_realtime_report(req))


@tool
def check_compatibility(
    property_id: str,
    metrics: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> dict:
    """Check which dimensions/metrics can be combined in one report before running it."""
    req = data.CheckCompatibilityRequest(
        property=property_name(property_id),
        metrics=[data.Metric(name=m) for m in metrics or []],
        dimensions=[data.Dimension(name=d) for d in dimensions or []],
    )
    resp = _data_client().check_compatibility(req)
    return {
        "dimensions": [
            {"api_name": c.dimension_metadata.api_name, "compatibility": data.Compatibility(c.compatibility).name}
            for c in resp.dimension_compatibilities
        ],
        "metrics": [
            {"api_name": c.metric_metadata.api_name, "compatibility": data.Compatibility(c.compatibility).name}
            for c in resp.metric_compatibilities
        ],
    }


def build_server(**kwargs: Any) -> MCPServer:
    server = MCPServer("ga4", instructions=INSTRUCTIONS, **kwargs)
    for fn in TOOLS:
        server.tool(annotations=READ_ONLY)(fn)
    return server


mcp = build_server()
