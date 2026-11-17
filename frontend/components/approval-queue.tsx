"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  listReviewHistory,
  historyPageSize,
  type ApprovalRequest,
} from "../lib/api";
import { HistoryPagination } from "./request-history";

export default function ApprovalQueue({ currentSubject }: { currentSubject: string }) {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"pending" | "decided">("pending");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoading(true); setMessage(""); setApprovals([]);
    listReviewHistory(mode, offset, controller.signal)
      .then((items) => active && setApprovals(items))
      .catch((error) => active && setMessage(error.message))
      .finally(() => { if (active) setLoading(false); });
    return () => {
      active = false;
      controller.abort();
    };
  }, [mode, offset, refresh, currentSubject]);

  return (
    <section className="mb-8 rounded-xl border border-indigo-200 bg-indigo-50 p-5" aria-label="Approval review queue">
      <h2 className="font-semibold text-indigo-950">Approval review queue</h2>
      <div className="my-3 flex gap-3">
        <button aria-pressed={mode === "pending"} onClick={() => { setMode("pending"); setOffset(0); }}>Pending reviews</button>
        <button aria-pressed={mode === "decided"} onClick={() => { setMode("decided"); setOffset(0); }}>My decisions</button>
        <button onClick={() => setRefresh(refresh + 1)}>Refresh reviews</button>
      </div>
      <p className="mt-1 text-xs text-indigo-800">
        Decisions record an audit event. They never execute Terraform.
      </p>
      {message && <p role="alert" className="mt-3 text-sm text-red-800">{message}</p>}
      {loading ? <p role="status">Loading reviews…</p> : !message && approvals.length === 0 ? (
        <p className="mt-3 text-sm text-indigo-900">{mode === "pending" ? "No pending approvals." : "No decisions on this page."}</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {approvals.map((approval) => (
            <li key={approval.id} className="rounded-lg bg-white p-4 text-sm">
              <p>Requester: <code>{approval.requested_by}</code></p>
              <p>Approval: {approval.status} · Requested: {new Date(approval.requested_at).toLocaleString()}</p>
              {approval.decided_at && <p>Decided: {new Date(approval.decided_at).toLocaleString()}</p>}
              <Link className="mt-2 block text-teal-800 underline" href={`/requests/${encodeURIComponent(approval.server_request_id)}`}>View request and plan history</Link>
              <p className="mt-1 break-all text-xs">Revision: <code>{approval.plan_revision}</code></p>
              {approval.requested_by === currentSubject && (
                <p className="mt-2 text-xs text-amber-800">Self-approval is prohibited.</p>
              )}
            </li>
          ))}
        </ul>
      )}
      <HistoryPagination offset={offset} more={approvals.length === historyPageSize} busy={loading} onChange={setOffset} />
    </section>
  );
}
