import { cache } from "react";
import { auth, authConfigured } from "../auth";
import type { CurrentUser } from "./api";

export const getPortalUser = cache(async (): Promise<CurrentUser | null> => {
  const session = authConfigured ? await auth() : null;
  return session?.user?.id ? {
    id: session.user.id, roles: session.user.roles, name: session.user.name,
  } : null;
});
