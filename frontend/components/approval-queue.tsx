"use client";

import { useEffect, useState } from "react";
import {
  decideApproval,
  listPendingApprovals,
  type ApprovalRequest,
} from "../lib/api";

export default function ApprovalQueue({ currentSubject }: { currentSubject: string }) {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    listPendingApprovals()
      .then((items) => active && setApprovals(items))
      .catch((error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, []);

  async function decide(id: string, decision: "approved" | "rejected") {
    setMessage("");
    try {
      await decideApproval(id, decision);
      setApprovals((items) => items.filter((item) => item.id !== id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Decision failed.");
    }
  }

  return (
    <section className="mb-8 rounded-xl border border-indigo-200 bg-indigo-50 p-5" aria-label="Approval review queue">
      <h2 className="font-semibold text-indigo-950">Approval review queue</h2>
      <p className="mt-1 text-xs text-indigo-800">
        Decisions record an audit event. They never execute Terraform.
      </p>
      {message && <p role="alert" className="mt-3 text-sm text-red-800">{message}</p>}
      {approvals.length === 0 ? (
        <p className="mt-3 text-sm text-indigo-900">No pending approvals.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {approvals.map((approval) => (
            <li key={approval.id} className="rounded-lg bg-white p-4 text-sm">
              <p>Requester: <code>{approval.requested_by}</code></p>
              <p className="mt-1 break-all text-xs">Revision: <code>{approval.plan_revision}</code></p>
              <div className="mt-3 flex gap-2">
                <button type="button" disabled={approval.requested_by === currentSubject} onClick={() => decide(approval.id, "approved")} className="rounded bg-teal-700 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve</button>
                <button type="button" disabled={approval.requested_by === currentSubject} onClick={() => decide(approval.id, "rejected")} className="rounded border border-red-300 px-3 py-2 text-xs font-semibold text-red-800 disabled:opacity-50">Reject</button>
              </div>
              {approval.requested_by === currentSubject && (
                <p className="mt-2 text-xs text-amber-800">Self-approval is prohibited.</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
