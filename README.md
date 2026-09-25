# ga4-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for **Google Analytics 4 reporting**.
[Host it free on Render](#deploy-on-render-free-sign-in-with-google), where everyone signs in
with their own Google account, or run it locally (stdio).
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
bind it locally. For a hosted server, see the next section.

---

## Deploy on Render (free, sign in with Google)

`ga4-mcp remote` is a complete MCP server with its own sign-in. When you add it to Claude, you're
sent to **Google's sign-in page**. Each person uses their own Google account (read-only scope) and
sees only their own GA4 properties. Only emails in `ALLOWED_EMAILS` are let in, and a consent page
names the app that's connecting. Sessions are encrypted tokens, so no database is needed.

```
Claude ──OAuth──▶ ga4-mcp.onrender.com ──▶ Google sign-in (analytics.readonly)
   ▲                     │ encrypted bearer token holds the user's Google token
   └──── MCP calls ──────┘────────▶ GA4 Data / Admin API as that user
```

### 1. Google Cloud (once)

1. Complete [Setup](#setup) step 1: create a project, enable both Analytics APIs, and configure the
   consent screen. **Publish** the consent screen (*Audience → Publish app*). Otherwise Google
   logs everyone out after 7 days and only listed test users can sign in.
2. *Google Auth Platform → Clients → Create client*, type **Web application**.
   - **Authorized redirect URI:** `https://<service-name>.onrender.com/oauth/google/callback`.
     You'll know the exact name after step 2. You can edit this field later.
   - Copy the **Client ID** and **Client secret**.

### 2. Render

1. Go to https://dashboard.render.com, then *New → Blueprint*, and pick this repo and branch.
   `render.yaml` sets up a free Python web service.
2. When prompted, fill in:

   | Variable | Value |
   |---|---|
   | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From the Web client |
   | `ALLOWED_EMAILS` | Who may sign in: `me@gmail.com,@mycompany.com`, or `*` for anyone |

   `TOKEN_ENCRYPTION_KEY` is generated automatically. The public URL comes from Render's
   `RENDER_EXTERNAL_URL`. Set `PUBLIC_URL` only if you use a custom domain.
3. After the deploy, opening `https://<service-name>.onrender.com/` should show "GA4 MCP server".
   Make sure the redirect URI from step 1.2 matches this host exactly.

### 3. Connect

- **Claude (web, desktop, mobile):** *Settings → Connectors → Add custom connector*. Use the URL
  `https://<service-name>.onrender.com/mcp`, then sign in with Google.
- **Claude Code:** `claude mcp add --transport http ga4 https://<service-name>.onrender.com/mcp`,
  then run `/mcp` to sign in.

### Notes

- **Free tier:** the service sleeps after 15 minutes without traffic, and the next request takes
  about a minute to wake it. If a connection times out, open the `/` URL first, then retry.
- **Signing out:** revoke "GA4 MCP" at https://myaccount.google.com/permissions. Changing
  `TOKEN_ENCRYPTION_KEY` signs everyone out.
- **Test locally:** set the same variables with `PUBLIC_URL=http://localhost:8000`, add
  `http://localhost:8000/oauth/google/callback` to the Web client, and run `uv run ga4-mcp remote`.

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
| Render: `redirect_uri_mismatch` from Google | The Web client's redirect URI must be exactly `https://<service-name>.onrender.com/oauth/google/callback`. |
| Render: "not allowed to use this server" | Add the email or domain to `ALLOWED_EMAILS`. Render redeploys automatically. |
| Render: `Missing environment variables` in the logs | Set them under the service's *Environment* tab. |
| Render: connection times out | The free service was asleep. Open `https://<service-name>.onrender.com/` and retry. |

## Development

```bash
uv run pytest
```
