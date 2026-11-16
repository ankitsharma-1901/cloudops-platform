import { authConfigured, handlers } from "../../../../auth";
import type { NextRequest } from "next/server";

function unavailable() {
  return Response.json(
    { detail: "OIDC authentication is not configured." },
    { status: 503 },
  );
}

export function GET(request: NextRequest) {
  return authConfigured ? handlers.GET(request) : unavailable();
}

export function POST(request: NextRequest) {
  return authConfigured ? handlers.POST(request) : unavailable();
}
