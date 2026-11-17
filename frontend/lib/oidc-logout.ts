// Only trusted server configuration may choose the provider and return origin.
function configuredUrl(value: string | undefined): URL {
  if (!value) throw new Error("Logout configuration is incomplete.");
  const url = new URL(value);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(local && url.protocol === "http:")) ||
      url.username || url.password || url.search || url.hash) {
    throw new Error("Logout configuration is invalid.");
  }
  return url;
}

export function logoutConfiguration(env: NodeJS.ProcessEnv = process.env) {
  const app = configuredUrl(env.AUTH_URL);
  const issuer = configuredUrl(env.AUTH_ISSUER);
  if (app.pathname !== "/" || !env.AUTH_CLIENT_ID) {
    throw new Error("Logout configuration is invalid.");
  }
  return {
    origin: app.origin,
    callback: `${app.origin}/auth/logout/callback`,
    endpoint: `${issuer.href.replace(/\/$/, "")}/protocol/openid-connect/logout`,
    clientId: env.AUTH_CLIENT_ID,
    secure: app.protocol === "https:",
  };
}

export function keycloakLogoutUrl(config: ReturnType<typeof logoutConfiguration>, state: string) {
  const url = new URL(config.endpoint);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("post_logout_redirect_uri", config.callback);
  url.searchParams.set("state", state);
  // Deliberately omit id_token_hint: no token in a browser URL, form or JS.
  // OIDC requires the provider to ask for confirmation in this mode.
  return url.href;
}
