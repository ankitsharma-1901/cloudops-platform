"use client";

import { useEffect, useState } from "react";
import { deployApprovedPlan, getDeployment, type DeploymentView } from "../lib/api";

const button = "rounded border border-slate-300 px-3 py-2 text-sm disabled:opacity-50";

export default function DeploymentPanel({ jobId, stack = "aws_development" }: { jobId: string; stack?: "aws_development" | "aws_ec2_only" }) {
  const [view, setView] = useState<DeploymentView | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setView(null); setError(""); setConfirming(false); setAcknowledged(false);
    async function load() {
      try {
        const data = await getDeployment(jobId, controller.signal);
        if (!active) return;
        setView(data);
        if (data.deployment?.status === "queued" || data.deployment?.status === "running") {
          timer = setTimeout(load, 2000);
        }
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "Deployment history could not be loaded.");
      }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [jobId, refresh]);

  async function deploy() {
    if (!view?.eligible || !view.enabled || !view.artifact_digest || !view.plan_revision || !acknowledged || pending) return;
    setPending(true); setError("");
    try {
      await deployApprovedPlan(jobId, view.artifact_digest, view.plan_revision);
      setRefresh((value) => value + 1);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Deployment request failed.");
    } finally { setPending(false); }
  }

  return <section aria-label="AWS deployment" className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
    <div className="flex items-center justify-between gap-3">
      <h4 className="font-semibold">{stack === "aws_ec2_only" ? "AWS — EC2 Only deployment" : "Legacy AWS deployment"}</h4>
      <button className={button} disabled={pending} onClick={() => setRefresh((value) => value + 1)}>Refresh deployment</button>
    </div>
    {error && <p role="alert" className="my-2 text-red-800">{error}</p>}
    {!view && !error && <p role="status">Loading deployment eligibility…</p>}
    {view && <>
      {view.reason && <p className="my-2">Deployment unavailable: {view.reason}</p>}
      {view.artifact_digest && <p className="break-all text-xs">Executable plan SHA-256: {view.artifact_digest}</p>}
      {view.enabled && view.eligible && !confirming && <button className={`${button} mt-3`} disabled={pending} onClick={() => setConfirming(true)}>Deploy Approved Plan</button>}
      {confirming && view.enabled && view.eligible && <div className="my-3 rounded border border-amber-500 p-3" role="group" aria-label="Confirm AWS deployment">
        <p>Stack: {stack === "aws_ec2_only" ? "AWS — EC2 Only" : "Legacy AWS development"}.</p>
        <p>This action creates real AWS resources in the configured development account in ca-central-1 and may incur charges. It executes only the exact saved approved plan. Approval alone does not deploy.</p>
        <label className="my-3 flex gap-2"><input type="checkbox" checked={acknowledged} disabled={pending} onChange={(event) => setAcknowledged(event.target.checked)} />I understand this creates real AWS resources and may incur charges.</label>
        <div className="flex gap-3">
          <button className={button} disabled={!acknowledged || pending} onClick={deploy}>{pending ? "Queuing deployment…" : "Confirm deployment"}</button>
          <button className={button} disabled={pending} onClick={() => { setConfirming(false); setAcknowledged(false); }}>Cancel deployment</button>
        </div>
      </div>}
      {view.deployment && <div className="mt-3">
        <p role="status">Deployment: {view.deployment.status}</p>
        {view.deployment.phase && <p>Execution phase: {view.deployment.phase}</p>}
        {view.deployment.reason_code && <p>Operator reason: {view.deployment.reason_code}</p>}
        {view.deployment.message && <p className="my-2 font-semibold">{view.deployment.message}</p>}
        <p>Requester: {view.deployment.requester}</p><p>Approver: {view.deployment.approver}</p><p>Initiator: {view.deployment.initiator}</p>
        <p>Queued: {new Date(view.deployment.created_at).toLocaleString()}</p>
        {view.deployment.finished_at && <p>Finished: {new Date(view.deployment.finished_at).toLocaleString()}</p>}
        <ul>{Object.entries(view.deployment.result).map(([name, value]) => <li key={name}>{name}: {value}</li>)}</ul>
        <h5 className="mt-3 font-semibold">Deployment audit history</h5>
        <ol>{view.events.map((event) => <li key={event.id}>{event.event_type} · {event.actor_subject} · {new Date(event.occurred_at).toLocaleString()}</li>)}</ol>
      </div>}
    </>}
  </section>;
}
