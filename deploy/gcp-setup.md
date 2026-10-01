# Set up Google Cloud for ga4-mcp

<walkthrough-tutorial-duration duration="5"></walkthrough-tutorial-duration>

This creates a Google Cloud project with the Analytics APIs enabled and walks you through the
sign-in settings that ga4-mcp needs. No billing account is needed.

Click **Start**.

## Create the project and enable the APIs

Click the **Copy to Cloud Shell** icon on the command below, then press **Enter** in the terminal:

```sh
bash deploy/gcp-setup.sh
```

To use a project you already have instead, run `PROJECT_ID=your-project bash deploy/gcp-setup.sh`.

When it prints **✅ Project ready**, click **Next**. The terminal output has direct links for the
next steps.

## Branding

Open the **Branding** link from the output (*Google Auth Platform → Branding*).

1. Click **Get started**.
2. App name: `GA4 MCP`. User support email: your email.
3. Audience: **External**.
4. Contact email: your email. Agree to the policy, then click **Create**.

## Test user

Open the **Audience** link.

Leave the app in **Testing**. Under **Test users**, click **Add users** and add your own Google
account. Only test users can sign in, and they have to sign in again every 7 days.

## Scope

Open the **Scopes** link (*Data access*).

Click **Add or remove scopes**, tick `.../auth/analytics.readonly`, then click **Update** and
**Save**.

## OAuth client

Open the **Client** link.

1. Application type: **Web application**. Name: `ga4-mcp`.
2. Under **Authorized redirect URIs**, add the URI printed in the terminal:
   `https://ga4-mcp.onrender.com/oauth/google/callback`. If Render gives your service a different
   host, change it here later.
3. Click **Create**. Copy the **Client ID** and **Client secret** somewhere private.

## Done

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

Google Cloud is ready. Next, deploy on Render: *New → Blueprint*, choose this repo, and enter
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `ALLOWED_EMAILS`. The README has the details.
