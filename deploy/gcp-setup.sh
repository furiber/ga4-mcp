#!/usr/bin/env bash
# One-time Google Cloud setup for ga4-mcp: creates (or reuses) a project and enables
# the two Analytics APIs, then prints the console pages for the steps gcloud can't do
# (consent screen and OAuth client). Safe to re-run.
#
#   PROJECT_ID=my-project ./deploy/gcp-setup.sh        # reuse or create a specific project
#   ./deploy/gcp-setup.sh                              # create ga4-mcp-<random>
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ga4-mcp-$(head -c 4 /dev/urandom | od -An -tu4 | tr -d ' ' | cut -c1-6)}"
REDIRECT_HOST="${REDIRECT_HOST:-ga4-mcp.onrender.com}"

if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "Using existing project $PROJECT_ID"
else
  echo "Creating project $PROJECT_ID"
  gcloud projects create "$PROJECT_ID" --name="GA4 MCP"
fi
gcloud config set project "$PROJECT_ID" >/dev/null

echo "Enabling Google Analytics Data and Admin APIs"
gcloud services enable analyticsdata.googleapis.com analyticsadmin.googleapis.com

ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
q="?project=$PROJECT_ID"
cat <<EOF

✅ Project ready: $PROJECT_ID

Finish in the console (gcloud can't do these):

1. Branding:  https://console.cloud.google.com/auth/branding$q
   Get started → App name "GA4 MCP", support email $ACCOUNT → Audience: External → Create
2. Audience:  https://console.cloud.google.com/auth/audience$q
   Keep "Testing" → Test users → Add users → $ACCOUNT
3. Scopes:    https://console.cloud.google.com/auth/scopes$q
   Add or remove scopes → tick ".../auth/analytics.readonly" → Update → Save
4. Client:    https://console.cloud.google.com/auth/clients/create$q
   Type "Web application", name "ga4-mcp"
   Authorized redirect URI: https://$REDIRECT_HOST/oauth/google/callback
   Create → keep the Client ID and Client secret for Render (don't share them)
EOF
