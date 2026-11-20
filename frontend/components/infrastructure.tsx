"use client";

import { useEffect, useState } from "react";

type Deployment = { id: string; status: string; stack: string; created_at: string };
type Instance = { id: string; name: string; state: string; region: string; instance_type: string;
  subnet_id: string | null; security_groups: string[]; ownership: "managed" | "unmanaged" | "unknown";
  stack: string | null; latest_deployment: Deployment | null };
type Inventory = { aws_status: "available" | "unavailable"; state_status: "available" | "unknown";
  instances: Instance[]; recent_deployments: Deployment[] };

export default function Infrastructure() {
  const [data, setData] = useState<Inventory | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError("");
    fetch("/api/cloudops/api/v1/infrastructure", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const body = await response.json();
        if (!Array.isArray(body.instances) || !Array.isArray(body.recent_deployments)) throw new Error();
        setData(body);
      }).catch(() => { if (!controller.signal.aborted) setError("Infrastructure could not be loaded. Check your session and try again."); });
    return () => controller.abort();
  }, [refresh]);
  return <section className="my-8 rounded-xl border bg-slate-50 p-5" aria-label="Infrastructure inventory">
    <div className="flex items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">EC2 inventory</h2>
      <p className="mt-1 text-sm text-slate-600">Read-only AWS inventory in ca-central-1.</p></div>
      <button className="rounded border border-slate-300 bg-white px-3 py-2 text-sm" onClick={() => setRefresh(refresh + 1)}>Refresh</button></div>
    {error ? <p role="alert" className="mt-4 text-red-800">{error}</p> : !data ? <p role="status" className="mt-4">Loading infrastructure…</p> : <>
      {data.aws_status !== "available" && <p role="alert" className="mt-4 text-amber-900">AWS inventory is unavailable. Check the worker credential and account configuration.</p>}
      {data.state_status !== "available" && <p className="mt-4 text-amber-900">Terraform state could not be verified. Ownership is unknown.</p>}
      {data.recent_deployments.some((d) => d.status === "needs_operator_review" || d.status === "running") &&
        <p role="alert" className="mt-4 text-amber-900">A deployment is in progress or requires operator review. AWS resources may be partially created.</p>}
      {data.aws_status === "available" && data.instances.length === 0 && <p className="mt-4">No EC2 instances returned by AWS.</p>}
      <ul className="mt-4 space-y-3">{data.instances.map((instance) => <li key={instance.id} className="rounded-lg border bg-white p-4 text-sm">
        <h3 className="font-semibold">{instance.name || "Unnamed instance"} <span className="font-mono text-xs">{instance.id}</span></h3>
        <p className="mt-2">{instance.state} · {instance.region} · {instance.instance_type}</p>
        <p>Subnet: {instance.subnet_id || "Unavailable"} · Security groups: {instance.security_groups.join(", ") || "Unavailable"}</p>
        <p>Ownership: <strong>{instance.ownership === "managed" ? "Managed by EC2-only state" : instance.ownership === "unmanaged" ? "Not managed by EC2-only state" : "Unknown"}</strong></p>
        <p>Stack: {instance.stack || "Not established"}</p>
        <p>Latest deployment: {instance.latest_deployment ? `${instance.latest_deployment.status} · ${instance.latest_deployment.id}` : "No matching deployment record"}</p>
      </li>)}</ul>
      {data.recent_deployments.length > 0 && <div className="mt-5"><h3 className="font-semibold">Recent deployment activity</h3>
        <ul className="mt-2 space-y-1 text-sm">{data.recent_deployments.map((d) => <li key={d.id}>{d.stack} · {d.status} · {d.id}</li>)}</ul></div>}
    </>}
  </section>;
}
