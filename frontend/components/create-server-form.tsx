"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  createServer,
  enqueueTerraformPlan,
  environments,
  getProvisioningJob,
  regions,
  instanceTypes,
  type FieldErrors,
  type ProvisioningJob,
  type SavedRequest,
  type ServerConfiguration,
} from "../lib/api";

const initial: ServerConfiguration = {
  name: "",
  environment: "development",
  region: "ca-central-1",
  instance_type: "t3.micro",
  install_nginx: true,
  install_docker: true,
};

export default function CreateServerForm() {
  const [config, setConfig] = useState(initial);
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState("");
  const [saved, setSaved] = useState<SavedRequest | null>(null);
  const [job, setJob] = useState<ProvisioningJob | null>(null);
  const [planPending, setPlanPending] = useState(false);
  const [planMessage, setPlanMessage] = useState("");
  const feedback = useRef<HTMLDivElement>(null);
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!jobId || (jobStatus !== "queued" && jobStatus !== "planning")) return;
    const activeJobId = jobId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const updated = await getProvisioningJob(activeJobId);
        if (cancelled) return;
        setPlanMessage("");
        setJob(updated);
        if (updated.status === "queued" || updated.status === "planning") {
          timer = setTimeout(poll, 1000);
        }
      } catch {
        if (!cancelled) {
          setPlanMessage("Could not refresh the plan status. Retrying shortly.");
          timer = setTimeout(poll, 3000);
        }
      }
    }

    timer = setTimeout(poll, 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId, jobStatus]);

  function update<K extends keyof ServerConfiguration>(
    field: K,
    value: ServerConfiguration[K],
  ) {
    setConfig((previous) => ({ ...previous, [field]: value }));
    setErrors((previous) => ({ ...previous, [field]: undefined }));
    setMessage("");
    setSaved(null);
    setJob(null);
    setPlanMessage("");
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    setSaved(null);
    setJob(null);
    setPlanMessage("");
    setErrors({});
    setMessage("");
    if (!/^[a-z][a-z0-9-]{1,30}[a-z0-9]$/.test(config.name)) {
      setErrors({
        name: "Use 3–32 lowercase letters, digits or hyphens. Start with a letter and end with a letter or digit.",
      });
      return;
    }
    submitting.current = true;
    setPending(true);
    try {
      setSaved(await createServer(config));
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Something went wrong. Please try again.",
      );
      if (error instanceof ApiError) setErrors(error.fields);
    } finally {
      submitting.current = false;
      setPending(false);
    }
    feedback.current?.focus();
  }
  async function requestPlan() {
    if (!saved || planPending) return;
    setPlanPending(true);
    setPlanMessage("");
    try {
      setJob(await enqueueTerraformPlan(saved.id));
    } catch (error) {
      setPlanMessage(
        error instanceof Error
          ? error.message
          : "The Terraform plan could not be requested.",
      );
    } finally {
      setPlanPending(false);
    }
  }
  function fieldError(field: keyof ServerConfiguration) {
    return errors[field] ? (
      <p id={`${field}-error`} className="mt-2 text-xs text-red-700">
        {errors[field]}
      </p>
    ) : null;
  }
  return (
    <div className="mt-8 grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_290px]">
      <form
        onSubmit={submit}
        noValidate
        aria-label="Create server"
        aria-busy={pending}
        className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="border-b border-slate-100 px-6 py-5">
          <h2 className="font-semibold">Server configuration</h2>
          <p className="mt-1 text-xs text-slate-500">
            Define where your server belongs and what it needs.
          </p>
        </div>
        <fieldset
          disabled={pending}
          className="space-y-7 p-6 disabled:opacity-70"
        >
          <legend className="sr-only">Server settings</legend>
          <div>
            <label htmlFor="name" className="mb-2 block text-sm font-medium">
              Server name <span className="text-slate-400">*</span>
            </label>
            <input
              id="name"
              name="name"
              placeholder="web-dev-01"
              autoComplete="off"
              required
              minLength={3}
              maxLength={32}
              value={config.name}
              onChange={(e) => update("name", e.target.value)}
              aria-invalid={!!errors.name}
              aria-describedby={
                errors.name ? "name-help name-error" : "name-help"
              }
            />
            <p id="name-help" className="mt-2 text-xs leading-5 text-slate-500">
              3–32 lowercase letters, digits or hyphens. Start with a letter;
              end with a letter or digit.
            </p>
            {fieldError("name")}
          </div>
          <div className="grid gap-6 sm:grid-cols-2">
            <div>
              <label
                htmlFor="environment"
                className="mb-2 block text-sm font-medium"
              >
                Environment
              </label>
              <select
                id="environment"
                value={config.environment}
                onChange={(e) =>
                  update(
                    "environment",
                    e.target.value as ServerConfiguration["environment"],
                  )
                }
                aria-invalid={!!errors.environment}
                aria-describedby={
                  errors.environment ? "environment-error" : undefined
                }
              >
                {environments.map((value) => (
                  <option key={value} value={value}>
                    {value === "development" ? "Development" : "Staging"}
                  </option>
                ))}
              </select>
              {fieldError("environment")}
            </div>
            <div>
              <label
                htmlFor="region"
                className="mb-2 block text-sm font-medium"
              >
                AWS region
              </label>
              <select
                id="region"
                value={config.region}
                onChange={(e) =>
                  update(
                    "region",
                    e.target.value as ServerConfiguration["region"],
                  )
                }
                aria-invalid={!!errors.region}
                aria-describedby={errors.region ? "region-error" : undefined}
              >
                {regions.map((value) => (
                  <option key={value} value={value}>
                    {value === "ca-central-1"
                      ? "Canada (Central)"
                      : "US East (N. Virginia)"}{" "}
                    · {value}
                  </option>
                ))}
              </select>
              {fieldError("region")}
            </div>
          </div>
          <div>
            <label
              htmlFor="instance_type"
              className="mb-2 block text-sm font-medium"
            >
              Instance type
            </label>
            <select
              id="instance_type"
              value={config.instance_type}
              onChange={(e) =>
                update(
                  "instance_type",
                  e.target.value as ServerConfiguration["instance_type"],
                )
              }
              aria-invalid={!!errors.instance_type}
              aria-describedby={
                errors.instance_type ? "instance_type-error" : undefined
              }
            >
              {instanceTypes.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs text-slate-500">
              Choose an approved instance type for your workload.
            </p>
            {fieldError("instance_type")}
          </div>
          <div className="border-t border-slate-100 pt-6">
            <h3 className="text-sm font-medium">Software configuration</h3>
            <p className="mt-1 text-xs text-slate-500">
              Record the software you want on this server.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {(["install_nginx", "install_docker"] as const).map((field) => (
                <div key={field}>
                  <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 p-4">
                    <input
                      type="checkbox"
                      checked={config[field]}
                      onChange={(e) => update(field, e.target.checked)}
                      aria-invalid={!!errors[field]}
                      aria-describedby={
                        errors[field] ? `${field}-error` : undefined
                      }
                    />
                    <span>
                      <span className="block text-sm font-medium">
                        {field === "install_nginx"
                          ? "Install Nginx"
                          : "Install Docker"}
                      </span>
                      <span className="mt-1 block text-xs text-slate-500">
                        {field === "install_nginx"
                          ? "Web server & reverse proxy"
                          : "Container runtime"}
                      </span>
                    </span>
                  </label>
                  {fieldError(field)}
                </div>
              ))}
            </div>
          </div>
        </fieldset>
        <div ref={feedback} tabIndex={-1} className="mx-6 outline-none">
          {message && (
            <div
              role="alert"
              className="mb-5 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
            >
              {message}
            </div>
          )}
          {saved && (
            <div
              role="status"
              className="mb-5 rounded-lg border border-teal-200 bg-teal-50 p-4"
            >
              <p className="font-semibold text-teal-900">Request saved</p>
              <p className="mt-2 text-sm text-teal-900">
                Status: <strong>{saved.status}</strong>
              </p>
              <p className="mt-2 text-xs text-teal-800">Request ID</p>
              <code className="mt-1 block break-all text-sm text-teal-950">
                {saved.id}
              </code>
              <p className="mt-3 text-xs text-teal-800">
                Your configuration is saved. No server has been provisioned.
              </p>
              <button
                type="button"
                onClick={requestPlan}
                disabled={
                  planPending ||
                  job?.status === "queued" ||
                  job?.status === "planning" ||
                  job?.status === "plan_ready"
                }
                className="mt-4 rounded-lg bg-teal-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              >
                {planPending
                  ? "Requesting plan…"
                  : job?.status === "failed"
                    ? "Retry Terraform plan"
                    : "Generate Terraform plan"}
              </button>
              {planMessage && (
                <p role="alert" className="mt-3 text-sm text-red-800">
                  {planMessage}
                </p>
              )}
              {job && (
                <section
                  aria-label="Terraform plan status"
                  className="mt-5 rounded-lg border border-slate-200 bg-white p-4 text-slate-800"
                >
                  <h3 className="font-semibold">Terraform plan</h3>
                  <p className="mt-2 text-sm">
                    Status: <strong>{job.status}</strong>
                  </p>
                  {job.status === "failed" && job.error_message && (
                    <p role="alert" className="mt-2 text-sm text-red-800">
                      {job.error_message}
                    </p>
                  )}
                  {job.status === "plan_ready" && job.plan_summary && (
                    <>
                      <p className="mt-3 text-xs text-slate-600">
                        Local-only resource: {job.plan_summary.resource}. No AWS
                        resources are managed by this plan.
                      </p>
                      <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-sm">
                        <div>
                          <dt className="text-slate-500">Add</dt>
                          <dd className="font-semibold">
                            {job.plan_summary.to_add}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Change</dt>
                          <dd className="font-semibold">
                            {job.plan_summary.to_change}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Destroy</dt>
                          <dd className="font-semibold">
                            {job.plan_summary.to_destroy}
                          </dd>
                        </div>
                      </dl>
                      <div className="mt-4 flex gap-2">
                        <button
                          type="button"
                          disabled
                          className="rounded border border-slate-300 px-3 py-2 text-xs text-slate-500"
                        >
                          Apply disabled
                        </button>
                        <button
                          type="button"
                          disabled
                          className="rounded border border-slate-300 px-3 py-2 text-xs text-slate-500"
                        >
                          Destroy disabled
                        </button>
                      </div>
                    </>
                  )}
                </section>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-slate-100 bg-slate-50/70 px-6 py-5">
          <p className="text-xs text-slate-500">
            Settings are validated before saving.
          </p>
          <button
            disabled={pending}
            type="submit"
            className="inline-flex items-center gap-3 rounded-lg bg-teal-700 px-5 py-3 text-sm font-semibold text-white transition hover:bg-teal-800 disabled:opacity-60"
          >
            {pending ? "Saving request…" : "Create server request"}
            <span aria-hidden="true">{pending ? "◌" : "→"}</span>
          </button>
        </div>
      </form>
      <aside className="space-y-5">
        <section className="rounded-xl border border-slate-200 bg-white p-6">
          <p className="text-xs font-semibold tracking-widest text-slate-500">
            REQUEST SUMMARY
          </p>
          <h2 className="mt-4 break-all text-lg font-semibold">
            {config.name || "Your new server"}
          </h2>
          <span className="mt-3 inline-block rounded-md bg-slate-100 px-2 py-1 text-xs capitalize text-slate-600">
            {config.environment}
          </span>
          <dl className="mt-6 space-y-4 text-sm">
            <div className="flex justify-between gap-2">
              <dt className="text-slate-500">Region</dt>
              <dd className="font-mono text-xs">{config.region}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Instance</dt>
              <dd className="font-mono text-xs">{config.instance_type}</dd>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <dt className="text-slate-500">Software</dt>
              <dd className="mt-2">
                {[
                  config.install_nginx && "Nginx",
                  config.install_docker && "Docker",
                ]
                  .filter(Boolean)
                  .join(" + ") || "None selected"}
              </dd>
            </div>
          </dl>
        </section>
        <section className="rounded-xl border border-teal-100 bg-teal-50 p-5">
          <h2 className="text-sm font-semibold text-teal-900">
            A request, then a review.
          </h2>
          <p className="mt-2 text-xs leading-6 text-teal-800">
            This workspace saves configurations locally. Creating a request does
            not provision infrastructure or create cloud charges.
          </p>
        </section>
      </aside>
    </div>
  );
}
