"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  getApprovalAudit, getRequestHistory, historyPageSize, listRequestHistory,
  requestPlanApproval, decideApproval, type ApprovalAuditEvent, type CurrentUser,
  type JobHistoryPage, type RequestHistoryPage,
} from "../lib/api";

const button = "rounded border border-slate-300 px-3 py-2 text-sm disabled:opacity-50";
export function HistoryPagination({ offset, more, busy, onChange }: {
  offset: number; more: boolean; busy: boolean; onChange: (offset: number) => void;
}) {
  return <div className="my-3 flex items-center gap-3">
    <button className={button} disabled={busy || offset === 0} onClick={() => onChange(Math.max(0, offset - historyPageSize))}>Previous page</button>
    <span className="text-sm">Page {offset / historyPageSize + 1}</span>
    <button className={button} disabled={busy || !more} onClick={() => onChange(offset + historyPageSize)}>Next page</button>
  </div>;
}

function AuditHistory({ approvalId }: { approvalId: string }) {
  const [events, setEvents] = useState<ApprovalAuditEvent[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setEvents(null); setError("");
    getApprovalAudit(approvalId, controller.signal)
      .then((items) => { if (active) setEvents(items); })
      .catch(() => { if (active) setError("Audit history could not be loaded."); });
    return () => { active = false; controller.abort(); };
  }, [approvalId, retry]);
  if (error) return <div role="alert">{error} <button className={button} onClick={() => setRetry(retry + 1)}>Retry audit</button></div>;
  if (!events) return <p role="status">Loading audit history…</p>;
  return <div className="mt-3"><h4 className="font-semibold">Approval audit history</h4>
    {events.length === 0 ? <p>No audit events.</p> : <ol className="space-y-1">{events.map((event) =>
      <li key={event.id}>{event.event_type} · {event.actor_subject} · <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time></li>,
    )}</ol>}
  </div>;
}

export function RequestHistoryDetail({ requestId, canEngineer = false, reviewerSubject }: {
  requestId: string; canEngineer?: boolean; reviewerSubject?: string;
}) {
  const [page, setPage] = useState<JobHistoryPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setPage(null); setError(""); setActionError("");
    getRequestHistory(requestId, offset, controller.signal)
      .then((result) => { if (active) setPage(result); })
      .catch((failure) => { if (active) setError(failure.message); });
    return () => { active = false; controller.abort(); };
  }, [requestId, offset, refresh]);
  async function requestApproval(jobId: string) {
    setPending(true); setActionError("");
    try { await requestPlanApproval(jobId); setRefresh((value) => value + 1); }
    catch (failure) { setActionError(failure instanceof Error ? failure.message : "Approval request failed."); }
    finally { setPending(false); }
  }
  async function decide(approvalId: string, decision: "approved" | "rejected") {
    setPending(true); setActionError("");
    try { await decideApproval(approvalId, decision); setRefresh((value) => value + 1); }
    catch (failure) { setActionError(failure instanceof Error ? failure.message : "Decision failed."); }
    finally { setPending(false); }
  }
  return <section className="mt-4 rounded-xl border border-slate-300 bg-white p-5" aria-label="Request details">
    <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Request details</h2>
      <button className={button} onClick={() => setRefresh(refresh + 1)}>Refresh details</button></div>
    {error ? <p role="alert">{error}</p> : !page ? <p role="status">Loading request details…</p> : <>
      <h3 className="mt-3 font-semibold">{page.request.name}</h3>
      <p>{page.request.environment} · {page.request.region} · {page.request.instance_type}</p>
      <p>Created: <time dateTime={page.request.created_at}>{new Date(page.request.created_at).toLocaleString()}</time></p>
      <p className="break-all text-xs">Request ID: {page.request.id}</p>
      <p className="mt-2 text-sm">Plans and approvals are records only. Decisions never execute Terraform.</p>
      {actionError && <p role="alert">{actionError}</p>}
      {page.items.length === 0 && <p className="mt-3">No plan jobs on this page.</p>}
      <ul className="mt-3 space-y-4">{page.items.map(({ job, approval, is_current_plan, can_request_approval }) => <li key={job.id} className="rounded border p-4 text-sm">
        <h3 className="font-semibold">{job.plan_mode} plan · {job.status}</h3>
        <p className="break-all text-xs">Job: {job.id}</p>
        <p>{is_current_plan ? "Current completed plan" : "Historical or incomplete plan; not eligible for a new approval."}</p>
        {job.status === "failed" && <p>Planning failed. Review the worker configuration before retrying.</p>}
        {job.plan_summary && <div className="mt-2">
          <p>{job.plan_summary.to_add} to add · {job.plan_summary.to_change} to change · {job.plan_summary.to_destroy} to destroy</p>
          <p className="break-all">Revision: {job.plan_revision ?? "Unavailable for legacy result. Generate a new plan to request approval."}</p>
          {job.plan_summary.resource && <p>{job.plan_summary.resource}</p>}
          <ul>{job.plan_summary.resource_changes.map((change) => <li key={change.address}>{change.address}: {change.actions.join(", ")}</li>)}</ul>
        </div>}
        {approval ? <div className="mt-2">
          <p>Approval: {approval.status}</p>
          <p className="break-all">Approval revision: {approval.plan_revision}</p>
          {approval.decided_by && <p>Decided by: {approval.decided_by} · {approval.decided_at && new Date(approval.decided_at).toLocaleString()}</p>}
          <AuditHistory key={`${approval.id}-${approval.status}`} approvalId={approval.id} />
        </div> : <p>Approval: not requested</p>}
        {canEngineer && can_request_approval && is_current_plan && job.status === "plan_ready" && !approval && <button className={button} disabled={pending} onClick={() => requestApproval(job.id)}>Request approval</button>}
        {reviewerSubject && approval?.status === "pending" && is_current_plan && <div className="mt-3 flex gap-3">
          <button className={button} disabled={pending || approval.requested_by === reviewerSubject} onClick={() => decide(approval.id, "approved")}>Approve reviewed plan</button>
          <button className={button} disabled={pending || approval.requested_by === reviewerSubject} onClick={() => decide(approval.id, "rejected")}>Reject reviewed plan</button>
          {approval.requested_by === reviewerSubject && <p>Self-approval is prohibited.</p>}
        </div>}
      </li>)}</ul>
      <HistoryPagination offset={offset} more={page.has_more} busy={false} onChange={setOffset} />
    </>}
  </section>;
}

export default function RequestHistory({ currentUser }: { currentUser: CurrentUser }) {
  const [page, setPage] = useState<RequestHistoryPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setPage(null); setError("");
    listRequestHistory(offset, controller.signal)
      .then((result) => { if (active) setPage(result); })
      .catch((failure) => { if (active) setError(failure.message); });
    return () => { active = false; controller.abort(); };
  }, [offset, refresh, currentUser.id]);
  return <section id="request-history" className="my-8 rounded-xl border bg-slate-50 p-5" aria-label="Requests and history">
    <div className="flex justify-between"><h2 className="text-xl font-semibold">Requests / History</h2>
      <button className={button} onClick={() => setRefresh(refresh + 1)}>Refresh history</button></div>
    <p className="mt-2 text-sm">Your saved requests, newest first. Resetting the form never deletes history.</p>
    {error ? <p role="alert" className="mt-3">{error}</p> : !page ? <p role="status">Loading requests…</p> : <>
      {page.items.length === 0 ? <p className="mt-3">No saved requests on this page.</p> : <ul className="mt-3 space-y-2">
        {page.items.map(({ request, latest_job, approval }) => <li key={request.id} className="rounded border bg-white p-3 text-sm">
          <Link className="font-semibold text-teal-800 underline" href={`/requests/${encodeURIComponent(request.id)}`}>{request.name}</Link>
          <p><time dateTime={request.created_at}>{new Date(request.created_at).toLocaleString()}</time> · {request.environment}</p>
          <p>Plan: {latest_job?.status ?? "not started"} · Approval: {approval?.status ?? "not requested"}</p>
          {latest_job?.plan_summary && <p>{latest_job.plan_summary.to_add} to add · {latest_job.plan_summary.to_change} to change · {latest_job.plan_summary.to_destroy} to destroy</p>}
        </li>)}
      </ul>}
      <HistoryPagination offset={offset} more={page.has_more} busy={false} onChange={setOffset} />
    </>}
  </section>;
}
