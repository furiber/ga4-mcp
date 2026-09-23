# ga4-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for **Google Analytics 4 reporting**.
It calls the GA4 Data API (reports) and the Admin API (read-only lookups) **as you**, with your own
Google account, so it sees exactly the properties you can see in the GA4 UI.

Only the `https://www.googleapis.com/auth/analytics.readonly` OAuth scope is requested. Google
enforces that scope, so the server can't change GA4 configuration even if a tool tried to.

## Tools

| Tool | API | What it does |
|---|---|---|
| `list_account_summaries` | Admin | Accounts and properties you can access |
| `get_property` | Admin | Property settings (time zone, currency, …) |
| `list_data_streams` | Admin | Web and app streams, measurement IDs |
| `list_custom_definitions` | Admin | Custom dimensions and metrics |
| `list_key_events` | Admin | Key events (conversions) |
| `list_google_ads_links` | Admin | Linked Google Ads accounts |
| `get_metadata` | Data | Valid dimension/metric API names for a property, with an optional `search` filter |
| `check_compatibility` | Data | Checks whether dimensions and metrics can be combined |
| `run_report` | Data | Core reporting: dimensions, metrics, date ranges, filters, ordering, paging, totals |
| `run_realtime_report` | Data | Last 30 minutes of activity |

---

## Setup

### 1. Google Cloud: one-time setup (about 5 minutes)

You need a Google Cloud project. It handles API quota and holds the OAuth client that lets the
server sign in as you. No billing is needed.

1. **Create or pick a project:** https://console.cloud.google.com/projectcreate
2. **Enable the two APIs** in that project:
   - Google Analytics Data API: https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com
   - Google Analytics Admin API: https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com
3. **Configure the OAuth consent screen** (*Google Auth Platform → Branding / Audience*):
   - **User type:** pick *Internal* if you're on Google Workspace and only your org will use it.
     Otherwise pick *External*.
   - **App name and support email:** any values.
   - **Data access / scopes:** add `.../auth/analytics.readonly`.
   - **Audience (External only):** while the app is in *Testing*, add every Google account that
     will sign in as a **test user**.
     > ⚠️ With External apps in *Testing*, refresh tokens expire after **7 days**, so you'll have
     > to re-run `ga4-mcp auth` weekly. To avoid that, choose *Internal* (Workspace), or
     > *Publish app* → *In production*. An unverified production app still works for you: you'll
     > see an "unverified app" warning that you can click through, and it's capped at 100 users.
4. **Create an OAuth client:** *Google Auth Platform → Clients → Create client*
   - **Application type:** **Desktop app**
   - Download the JSON, for example to `~/.config/ga4-mcp/client_secret.json`. Keep it private.
     Don't commit it.

Your Google user also needs at least **Viewer** access on the GA4 properties you want to report on.

### 2. Install

```bash
git clone https://github.com/furiber/ga4-mcp && cd ga4-mcp
uv sync            # or: pip install -e .
```

### 3. Sign in (stores a refreshable token)

```bash
uv run ga4-mcp auth --client-secrets ~/.config/ga4-mcp/client_secret.json
uv run ga4-mcp whoami     # verify: lists your accounts/properties
```

The token is saved at `~/.config/ga4-mcp/token.json` with mode 0600. Set `GA4_MCP_TOKEN_FILE` to
store it somewhere else. On a headless machine, add `--no-browser` and open the printed URL
yourself. The redirect goes to `localhost`, so use SSH port forwarding with `--port`.

**Alternative: gcloud Application Default Credentials.** If no token file exists, the server falls
back to ADC:

```bash
gcloud auth application-default login \
  --client-id-file=~/.config/ga4-mcp/client_secret.json \
  --scopes=https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Pass your own `--client-id-file`: gcloud's built-in client is typically blocked for Analytics scopes.

### 4. Add it to your MCP client

**Claude Code**

```bash
claude mcp add ga4 -- uv --directory /path/to/ga4-mcp run ga4-mcp
```

**Claude Desktop / other clients** (`claude_desktop_config.json`, `.mcp.json`, …)

```json
{
  "mcpServers": {
    "ga4": {
      "command": "uv",
      "args": ["--directory", "/path/to/ga4-mcp", "run", "ga4-mcp"]
    }
  }
}
```

`ga4-mcp serve --transport streamable-http` is also available. It has no auth of its own, so only
bind it locally.

## Example prompts

- "List my GA4 properties."
- "Sessions and key events by default channel group for property 123456789, last 28 days vs the previous 28."
- "Top 20 landing pages by engaged sessions last month, only for France."
- "How many active users are on the site right now, by country?"

## Example `run_report` call

```json
{
  "property_id": "123456789",
  "dimensions": ["sessionDefaultChannelGroup"],
  "metrics": ["sessions", "keyEvents", "totalRevenue"],
  "date_ranges": [{"start_date": "28daysAgo", "end_date": "yesterday"}],
  "dimension_filter": {"filter": {"field_name": "country", "string_filter": {"value": "France"}}},
  "order_bys": [{"metric": {"metric_name": "sessions"}, "desc": true}],
  "limit": 25,
  "metric_aggregations": ["TOTAL"]
}
```

## Troubleshooting

| Error | Fix |
|---|---|
| `403 ... API has not been used in project` | Enable the Data and Admin APIs in the project that owns the OAuth client, or in the ADC quota project. |
| `403 User does not have sufficient permissions` | Your Google user has no access to that GA4 property. |
| `invalid_grant` / token expired | Re-run `ga4-mcp auth`. This happens weekly if the consent screen is External + Testing (see above). |
| `No Google credentials found` | Run step 3. |

## Development

```bash
uv run pytest
```
