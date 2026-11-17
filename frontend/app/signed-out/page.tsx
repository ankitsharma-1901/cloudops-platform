import Link from "next/link";

export default async function SignedOut({ searchParams }: {
  searchParams: Promise<{ provider?: string }>;
}) {
  const { provider } = await searchParams;
  return <main className="mx-auto max-w-xl p-10">
    <h1 className="text-2xl font-semibold">Signed out of CloudOps</h1>
    <p className="my-4">{provider === "unavailable"
      ? "Provider logout was unavailable. The Keycloak SSO session may still be active. Close the provider session before using a shared browser."
      : "After confirming logout at Keycloak, you can sign in as a different user. Cancelling its confirmation leaves the SSO session active."}</p>
    <Link className="text-teal-800 underline" href="/">Return to sign in</Link>
  </main>;
}
