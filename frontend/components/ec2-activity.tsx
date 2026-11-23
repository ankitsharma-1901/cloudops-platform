"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

type Activity = { instance: null | { id: string | null; name?: string | null; status: string }; history?: { id: string; name?: string; status: string; completed_at: string }[] };

export default function EC2Activity() {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/cloudops/api/v1/ec2-demo", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) throw new Error("Your session expired. Sign in again.");
        if (!response.ok) throw new Error("EC2 activity is unavailable.");
        setActivity(await response.json());
      }).catch((failure) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "EC2 activity is unavailable."); });
    return () => controller.abort();
  }, []);
  return <section className="my-5 rounded-xl border bg-white p-5" aria-label="EC2 demo activity"><h2 className="font-semibold">EC2 demo activity</h2>
    {error ? <p role="alert">{error}</p> : !activity ? <p role="status">Loading EC2 activity…</p> : <>
      {activity.instance && <p>Current: {activity.instance.name || "Unnamed instance"} · {activity.instance.id || "ID not confirmed"} · {activity.instance.status}</p>}
      {!activity.instance && !activity.history?.length && <p>No demo activity yet.</p>}
      {!!activity.history?.length && <ul>{activity.history.map((item) => <li key={`${item.id}-${item.completed_at}`}>{item.name || "Unnamed instance"} · {item.id} · {item.status}</li>)}</ul>}
    </>}
    <Link className="text-teal-800 underline" href="/infrastructure#ec2-demo">Manage demo instance</Link>
  </section>;
}
