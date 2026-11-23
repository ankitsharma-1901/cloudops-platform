"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

type Configuration = { instance_name: string; region: string; operating_system: string; ami: string; instance_type: string; storage_gib: number; storage_type: string; encrypted: boolean; vpc: string; subnet: string; security_group: string };
type Catalog = { defaults: Configuration; security: { public_ipv4: boolean; imdsv2_required: boolean; instance_profile: string | null } };
type Review = { review_id: string; configuration: Configuration; expires_at: string };

export default function EC2Configure() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [name, setName] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const configurationVersion = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/cloudops/api/v1/ec2-demo/catalog", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) throw new Error("Your session expired. Sign in again.");
        if (!response.ok) throw new Error("The EC2 catalog is unavailable.");
        const result: Catalog = await response.json(); setCatalog(result); setName(result.defaults.instance_name);
      }).catch((error) => { if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "The EC2 catalog is unavailable."); });
    return () => controller.abort();
  }, []);
  async function preview() {
    if (!catalog || busy) return;
    setBusy(true); setMessage(""); setReview(null);
    const version = configurationVersion.current;
    try {
      const response = await fetch("/api/cloudops/api/v1/ec2-demo/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...catalog.defaults, instance_name: name }) });
      if (response.status === 401) throw new Error("Your session expired. Sign in again.");
      if (response.status === 422) throw new Error("Use a name of 3–32 lowercase letters, digits or hyphens, starting with a letter and ending with a letter or digit.");
      if (!response.ok) throw new Error("Configuration review is unavailable.");
      const result: Review = await response.json();
      if (version === configurationVersion.current) { setReview(result); setConfirmation(""); }
    } catch (error) { setMessage(error instanceof Error ? error.message : "Configuration review failed."); }
    finally { setBusy(false); }
  }
  async function create() {
    if (!review || busy || confirmation !== "REQUEST EC2 APPROVAL") return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch("/api/cloudops/api/v1/ec2-requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ review_id: review.review_id }) });
      if (response.status === 401) throw new Error("Your session expired. Sign in again and review again.");
      if (!response.ok) { const body = await response.json(); throw new Error(typeof body.detail === "string" ? body.detail : "Creation could not be completed."); }
      setReview(null); setConfirmation(""); setMessage("Request submitted for independent approval.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Creation failed."); }
    finally { setBusy(false); }
  }
  return <section aria-label="Configure EC2" className="my-5 rounded-xl border border-teal-200 bg-white p-5">
    <h2 className="text-xl font-semibold">Configure one EC2 instance</h2>
    <p className="mt-1 text-sm text-slate-600">Direct AWS API demo. Only the name can be changed; AWS charges apply. No software is installed.</p>
    {!catalog && !message && <p role="status">Loading supported configuration…</p>}
    {catalog && <div className="mt-4 space-y-3">
      <label className="block text-sm font-medium">Instance name<input value={name} maxLength={32} onChange={(event) => { configurationVersion.current += 1; setName(event.target.value); setReview(null); setConfirmation(""); }} /></label>
      {([ ["Region", catalog.defaults.region], ["Operating system", catalog.defaults.operating_system], ["Instance type", catalog.defaults.instance_type], ["Root storage", `${catalog.defaults.storage_gib} GiB ${catalog.defaults.storage_type}, ${catalog.defaults.encrypted ? "encrypted" : "not encrypted"}`], ["Subnet", catalog.defaults.subnet], ["Security group", catalog.defaults.security_group] ] as const).map(([label, value]) => <label className="block text-sm font-medium" key={label}>{label}<select value={value} disabled><option>{value}</option></select></label>)}
      <p className="text-sm">{catalog.security.public_ipv4 ? "Public IPv4" : "No public IPv4"} · {catalog.security.imdsv2_required ? "IMDSv2 required" : "IMDSv2 not required"} · {catalog.security.instance_profile || "No instance profile"}</p>
      <button type="button" disabled={busy} onClick={() => void preview()} className="rounded bg-teal-800 px-4 py-2 text-white disabled:opacity-50">Review configuration</button>
    </div>}
    {review && <div className="mt-5 rounded-lg border bg-slate-50 p-4"><h3 className="font-semibold">Review configuration</h3><p className="text-sm">Name: {review.configuration.instance_name} · Region: {review.configuration.region} · OS: {review.configuration.operating_system} · Type: {review.configuration.instance_type}</p><p className="text-sm">AMI: {review.configuration.ami} · Storage: {review.configuration.storage_gib} GiB {review.configuration.storage_type}, encrypted</p><p className="text-sm">VPC: {review.configuration.vpc} · Subnet: {review.configuration.subnet} · Security group: {review.configuration.security_group}</p><p className="text-sm">The reviewed settings are fixed to this request. An enabled worker can launch the instance after approval. AWS charges may apply.</p><label className="mt-3 block text-sm">Type <strong>REQUEST EC2 APPROVAL</strong> to confirm<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label><button type="button" disabled={busy || confirmation !== "REQUEST EC2 APPROVAL"} onClick={() => void create()} className="mt-3 rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40">Submit approval request</button></div>}
    {message && <p role="status" className="mt-3">{message}</p>}
    <Link className="mt-4 inline-block text-teal-800 underline" href="/requests">View request activity</Link>
  </section>;
}
