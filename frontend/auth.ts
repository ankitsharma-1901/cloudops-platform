import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

const issuer = process.env.AUTH_ISSUER?.replace(/\/$/, "");
const clientId = process.env.AUTH_CLIENT_ID;
const clientSecret = process.env.AUTH_CLIENT_SECRET;
const sessionSecret = process.env.AUTH_SECRET;

export const authConfigured = Boolean(
  sessionSecret &&
    sessionSecret.length >= 32 &&
    issuer &&
    clientId &&
    clientSecret &&
    !clientSecret.startsWith("replace-with-"),
);

function configuredRoles(stableSubject: string): string[] {
  try {
    const mapping = JSON.parse(
      process.env.CLOUDOPS_ROLE_ASSIGNMENTS || "{}",
    ) as Record<string, unknown>;
    const roles = mapping[stableSubject];
    if (!Array.isArray(roles)) return [];
    return roles.filter(
      (role): role is string =>
        typeof role === "string" &&
        ["engineer", "approver", "administrator"].includes(role),
    );
  } catch {
    return [];
  }
}

export const { handlers, auth } = NextAuth({
  secret: sessionSecret,
  trustHost: true,
  session: { strategy: "jwt", maxAge: 60 * 60 },
  providers: authConfigured
    ? [
        Keycloak({
          issuer,
          clientId,
          clientSecret,
          checks: ["pkce", "state", "nonce"],
          authorization: { params: { scope: "openid profile email" } },
        }),
      ]
    : [],
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        if (!account.access_token || !account.expires_at || !profile?.sub || !issuer) {
          return { ...token, authenticationError: "invalid_oidc_response" };
        }
        const stableSubject = `${issuer}|${profile.sub}`;
        return {
          ...token,
          stableSubject,
          roles: configuredRoles(stableSubject),
          accessToken: account.access_token,
          accessTokenExpiresAt: account.expires_at,
        };
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = typeof token.stableSubject === "string" ? token.stableSubject : "";
        session.user.roles = Array.isArray(token.roles)
          ? token.roles.filter((role): role is string => typeof role === "string")
          : [];
      }
      return session;
    },
  },
});
