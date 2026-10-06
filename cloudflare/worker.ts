// Cloudflare Worker that forwards every request to the ga4-mcp Docker container.
// The server is stateless, so a single container instance is enough.
import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  GA4_MCP: DurableObjectNamespace<Ga4McpContainer>;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  TOKEN_ENCRYPTION_KEY: string;
  ALLOWED_EMAILS: string;
  PUBLIC_URL: string;
}

export class Ga4McpContainer extends Container<Env> {
  defaultPort = 8080; // matches the Dockerfile
  sleepAfter = "30m"; // longer idle timeout = fewer cold starts

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.envVars = {
      GOOGLE_CLIENT_ID: env.GOOGLE_CLIENT_ID,
      GOOGLE_CLIENT_SECRET: env.GOOGLE_CLIENT_SECRET,
      TOKEN_ENCRYPTION_KEY: env.TOKEN_ENCRYPTION_KEY,
      ALLOWED_EMAILS: env.ALLOWED_EMAILS,
      PUBLIC_URL: env.PUBLIC_URL,
    };
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return getContainer(env.GA4_MCP, "main").fetch(request);
  },
};
