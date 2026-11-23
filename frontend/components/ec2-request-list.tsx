"use client";

import { useCallback, useEffect, useState } from "react";

type RequestItem = { id: string; requester: string; configuration: Record<string, string | number | boolean>; digest: string; status: string; execution_status: string; expires_at: string };

export default function EC2RequestList({ mode, currentSubject }: { mode: "engineer" | "approver"; currentSubject: string }) {
  const [items, setItems] = useState<RequestItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/cloudops/api/v1/ec2-requests", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 401 ? "Your session expired. Sign in again." : "EC2 requests are unavailable.");
      setItems(await response.json()); setError("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "EC2 requests are unavailable."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function action(id: string, decision: "approved" | "rejected" | "cancel") {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/cloudops/api/v1/ec2-requests/${encodeURIComponent(id)}/${decision === "cancel" ? "cancel" : "decision"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        ...(decision === "cancel" ? {} : { body: JSON.stringify({ decision }) }),
      });
      if (!response.ok) { const result = await response.json(); throw new Error(typeof result.detail === "string" ? result.detail : "Request update failed."); }
      await refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Request update failed."); }
    finally { setBusy(false); }
  }
  return <section aria-label="EC2 approval requests" className="my-5 rounded-xl border bg-white p-5">
    <h2 className="font-semibold">EC2 approval requests</h2><p className="text-sm">An enabled worker checks current authorization before provisioning an approved request.</p>
    <button type="button" onClick={() => void refresh()} className="mt-2 underline">Refresh EC2 requests</button>
    {error && <p role="alert">{error}</p>}{loading ? <p role="status">Loading EC2 requests…</p> : !items.length ? <p>No EC2 requests.</p> : <ul className="mt-3 space-y-3">{items.map((item) => <li className="rounded border p-3 text-sm" key={item.id}>
      <p>Request: {item.id} · Requester: {item.requester}</p><p>Status: {item.status} · Execution: {item.execution_status} · Expires: {item.expires_at}</p>
      <p>Configuration digest: <code className="break-all">{item.digest}</code></p><dl>{Object.entries(item.configuration).map(([key, value]) => <div key={key}><dt className="inline font-medium">{key}: </dt><dd className="inline">{String(value)}</dd></div>)}</dl>
      {mode === "approver" && item.status === "pending" && item.requester !== currentSubject && <div className="mt-2 flex gap-3"><button disabled={busy} onClick={() => void action(item.id, "approved")}>Approve</button><button disabled={busy} onClick={() => void action(item.id, "rejected")}>Reject</button></div>}
      {mode === "approver" && item.requester === currentSubject && item.status === "pending" && <p>Self-approval is prohibited.</p>}
      {mode === "engineer" && item.requester === currentSubject && ["pending", "approved"].includes(item.status) && <button disabled={busy} onClick={() => void action(item.id, "cancel")}>Cancel request</button>}
    </li>)}</ul>}
  </section>;
}
