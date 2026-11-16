import type { DefaultSession } from "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface User {
    roles?: string[];
  }

  interface Session {
    user: {
      id: string;
      roles: string[];
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    stableSubject?: string;
    roles?: string[];
    accessToken?: string;
    accessTokenExpiresAt?: number;
    authenticationError?: string;
  }
}
