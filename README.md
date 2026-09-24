# ga4-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for **Google Analytics 4 reporting**, built
with [FastMCP](https://gofastmcp.com). [Host it free on Prefect Horizon](#deploy-on-prefect-horizon-free-hosted)
or run it locally (stdio). It calls the GA4 Data API (reports) and the Admin API (read-only lookups).

> This is the **Horizon** branch: Horizon handles sign-in. For self-hosting (e.g. Render) with the
> server's own Google OAuth, use the `claude/gracious-hypatia-kcwhl3` branch.

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
bind it locally. For a hosted server, see the next sections.

---

## Deploy on Prefect Horizon (free, hosted)

[Horizon](https://horizon.prefect.io) hosts the server and handles sign-in: MCP clients log in with
your Horizon account, so nobody else can connect. `fastmcp.json` tells Horizon how to build it.

Horizon's sign-in only proves who is connecting *to Horizon*. Calling GA4 still needs a **Google**
credential, and Google only issues those to a Google Cloud project, so some Google Cloud setup is
unavoidable. Pick one:

| Option | Google identity used | Google Cloud setup | Best for |
|---|---|---|---|
| **A. Service account** | A robot account you grant Viewer on GA4 | Project + 2 APIs + a key. No consent screen, nothing to run locally. | **Quickest first test** |
| B. Your Google account | You, for every call | Project + 2 APIs + consent screen + Desktop client, then `ga4-mcp auth` locally | Seeing exactly what you see |
| C. Each caller's account | Each caller | Like B, plus Horizon's delegated authorization. **Paid Developer plan.** | Teams |

### 1A. Service account (quickest)

1. **Create or pick a project:** https://console.cloud.google.com/projectcreate
2. **Enable** the [Google Analytics Data API](https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com)
   and the [Google Analytics Admin API](https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com).
3. **Create a service account:** *IAM & Admin → Service Accounts → Create*. It needs no roles.
   Then open it, go to *Keys → Add key → JSON*, and download the file. Treat it like a password.
4. **Give it GA4 access:** in GA4, go to *Admin → Property access management → +*. Add the service
   account's email (`…@….iam.gserviceaccount.com`) as **Viewer** on each property you want. Use
   *Account access management* instead to cover every property in an account.
5. Use the **whole JSON file contents** as `GA4_MCP_TOKEN_JSON` in step 2 below.

If key creation is blocked, your Google Workspace organization has the policy
`iam.disableServiceAccountKeyCreation` enabled. Use option B instead.

### 1B. Your own Google account

Complete [Setup](#setup) steps 1–3 above: the Google Cloud project, the Desktop OAuth client, and
`ga4-mcp auth`. Then print the token as one line:

```bash
uv run ga4-mcp token
```

It contains a refresh token for read-only access to your GA4 data. Treat it like a password.

> ⚠️ If your consent screen is External and still in *Testing*, this token stops working after
> 7 days. Publish the app first (see the Google Cloud setup above).

### 2. Deploy

1. Sign in at https://horizon.prefect.io with GitHub and create a server from this repo.
2. **Entrypoint:** `main.py:mcp`. Horizon reads `fastmcp.json` for the Python version and
   dependencies.
3. Under **Settings → Environment Variables**, add `GA4_MCP_TOKEN_JSON` with the value from step 1
   (Production).
4. Deploy, or redeploy if the server already existed before you added the variable.

### 3. Connect

Use the server URL Horizon shows, e.g. `https://<server-name>.fastmcp.app/mcp`:

- **Claude (web, desktop, mobile):** *Settings → Connectors → Add custom connector*. Sign in with
  Horizon when prompted.
- **Claude Code:** `claude mcp add --transport http ga4 https://<server-name>.fastmcp.app/mcp`,
  then run `/mcp` to sign in.

### Option C: per-user Google accounts (Horizon Developer plan)

Horizon's [delegated authorization](https://docs.horizon.prefect.io/platform/external-authentication#delegated-authorization)
lets each person authorize Google once. Horizon then passes their Google token to the server on
every call. The server uses it automatically: an incoming `Authorization: Bearer ya29.…` header
takes precedence over `GA4_MCP_TOKEN_JSON`.

1. In Google Cloud, create a **Web application** OAuth client with redirect URI
   `https://horizon.prefect.io/oauth/external/callback`.
2. In Horizon, open the server's *Access → Authentication → Delegated authentication → Link auth
   source → OAuth* and enter the details manually:
   - **Authorization URL:** `https://accounts.google.com/o/oauth2/v2/auth?access_type=offline&prompt=consent`.
     The query string asks Google for a refresh token.
   - **Token URL:** `https://oauth2.googleapis.com/token`
   - **Scopes:** `https://www.googleapis.com/auth/analytics.readonly`
   - **Client ID / secret** from step 1. Token endpoint authentication: request body.
   - **Token to forward upstream:** access token.
3. Each user authorizes Google on first connect, or at `https://horizon.prefect.io/<server-slug>/authorize`.
4. Optionally remove `GA4_MCP_TOKEN_JSON`. If you keep it, calls fall back to *your* account
   whenever a user has no valid Google authorization, because Horizon fails open.

---

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
| Horizon: `No Google credentials found` | Set `GA4_MCP_TOKEN_JSON` for Production, then redeploy. |
| Horizon: "Google rejected the access token" | With delegated authorization, re-authorize Google at `https://horizon.prefect.io/<server-slug>/authorize`. |
| `invalid_grant` with `GA4_MCP_TOKEN_JSON` | The refresh token expired or was revoked. Re-run `ga4-mcp auth` and `ga4-mcp token`, then update the secret. |
| `403 User does not have sufficient permissions` with a service account | Add the service account's email as Viewer in GA4 *Property access management*. |

## Development

```bash
uv run pytest
```
