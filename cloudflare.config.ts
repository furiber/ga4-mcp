// Cloudflare Containers deploy (Workers Paid plan). See README, "Cloudflare".
import { bindings, defineConfig, defineContainer, exports } from "cf/config";

// Docker image for `ga4-mcp remote`. The server is stateless, so one instance is enough.
// By default `cf deploy` builds ./Dockerfile. To deploy a prebuilt image instead (for example
// one pushed with `cf containers push`), set GA4_MCP_IMAGE to its registry reference.
const prebuiltImage = process.env.GA4_MCP_IMAGE;

const ga4McpContainer = defineContainer({
	// Matches the application name that Wrangler created, so deploys update it in place.
	name: "ga4-mcp-ga4mcpcontainer",
	image: prebuiltImage ? { reference: prebuiltImage } : { dockerfile: "./Dockerfile" },
	instanceType: "basic",
	maxInstances: 1,
});

export default defineConfig({
	containers: [ga4McpContainer],
	worker: {
		name: "ga4-mcp",
		compatibilityDate: "2026-10-06",
		entrypoint: "cloudflare/worker.ts",
		observability: { enabled: true },
		env: {
			// The Worker's public URL (workers.dev or custom domain). Set PUBLIC_URL when you deploy.
			// Google redirects to <PUBLIC_URL>/oauth/google/callback.
			PUBLIC_URL: bindings.text(process.env.PUBLIC_URL ?? "https://ga4-mcp.YOUR-SUBDOMAIN.workers.dev"),
			GOOGLE_CLIENT_ID: bindings.secret(),
			GOOGLE_CLIENT_SECRET: bindings.secret(),
			TOKEN_ENCRYPTION_KEY: bindings.secret(),
			ALLOWED_EMAILS: bindings.secret(),
			GA4_MCP: bindings.durableObject({
				worker: "ga4-mcp",
				exportName: "Ga4McpContainer",
			}),
		},
		exports: {
			Ga4McpContainer: exports.durableObject({
				storage: "sqlite",
				container: ga4McpContainer,
			}),
		},
	},
});
