#!/usr/bin/env bash
# Deploy ga4-mcp to Google Cloud Run with per-user Google sign-in.
#
# Usage (from the repo root, with gcloud installed and logged in):
#   PROJECT_ID=my-project GOOGLE_CLIENT_ID=123-abc.apps.googleusercontent.com \
#   ALLOWED_EMAILS=me@gmail.com ./deploy/cloud-run.sh
#
# Prompts for the OAuth client secret (or read GOOGLE_CLIENT_SECRET). Safe to re-run:
# later runs redeploy the code and keep the existing secrets.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${GOOGLE_CLIENT_ID:?Set GOOGLE_CLIENT_ID (Web application OAuth client)}"
: "${ALLOWED_EMAILS:?Set ALLOWED_EMAILS, e.g. me@gmail.com,@mycompany.com}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ga4-mcp}"
RUNTIME_SA="${SERVICE}-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
SECRET_CLIENT="${SERVICE}-google-client-secret"
SECRET_KEY="${SERVICE}-token-encryption-key"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
# Cloud Run's deterministic service URL.
PUBLIC_URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

echo "==> Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com analyticsdata.googleapis.com analyticsadmin.googleapis.com

echo "==> Runtime service account (no project roles; it can only read its two secrets)"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "${SERVICE}-runtime" --display-name="ga4-mcp Cloud Run runtime"

echo "==> Secrets"
secret_exists() { gcloud secrets describe "$1" >/dev/null 2>&1; }
if ! secret_exists "$SECRET_CLIENT"; then
  if [[ -z "${GOOGLE_CLIENT_SECRET:-}" ]]; then
    read -rsp "OAuth client secret: " GOOGLE_CLIENT_SECRET; echo
  fi
  printf '%s' "$GOOGLE_CLIENT_SECRET" | gcloud secrets create "$SECRET_CLIENT" --data-file=-
elif [[ -n "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  printf '%s' "$GOOGLE_CLIENT_SECRET" | gcloud secrets versions add "$SECRET_CLIENT" --data-file=-
fi
if ! secret_exists "$SECRET_KEY"; then
  # Changing this key later signs everyone out, so it's only created once.
  head -c 32 /dev/urandom | base64 | tr -d "\n" | gcloud secrets create "$SECRET_KEY" --data-file=-
fi
for s in "$SECRET_CLIENT" "$SECRET_KEY"; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "==> Building and deploying (Cloud Build uses the Dockerfile)"
# `^;^` switches gcloud's list delimiter so ALLOWED_EMAILS may contain commas.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --cpu 1 --memory 512Mi \
  --min-instances 0 --max-instances 2 --concurrency 20 --timeout 300 \
  --set-env-vars "^;^GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID};ALLOWED_EMAILS=${ALLOWED_EMAILS};PUBLIC_URL=${PUBLIC_URL}" \
  --set-secrets "GOOGLE_CLIENT_SECRET=${SECRET_CLIENT}:latest,TOKEN_ENCRYPTION_KEY=${SECRET_KEY}:latest"

cat <<MSG

Deployed: ${PUBLIC_URL}
  1. In the Google Cloud console, add this redirect URI to the Web OAuth client (if not done yet):
       ${PUBLIC_URL}/oauth/google/callback
  2. In Claude, add a custom connector with the URL:
       ${PUBLIC_URL}/mcp
MSG
