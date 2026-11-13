import CreateServerForm from "../components/create-server-form";

export default function Home() {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[230px_1fr]">
      <aside className="flex flex-col bg-slate-950 px-7 py-7 text-white lg:min-h-screen">
        <a
          href="/"
          className="flex items-center gap-3 text-xl font-bold tracking-tight"
        >
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-teal-400 text-slate-950">
            C
          </span>
          CloudOps<span className="text-teal-400">.</span>
        </a>
        <p className="mt-10 hidden text-[10px] font-semibold tracking-[.2em] text-slate-500 lg:block">
          WORKSPACE
        </p>
        <div className="mt-3 flex items-center gap-3 rounded-lg bg-white/10 px-4 py-3 text-sm text-teal-300">
          <span aria-hidden="true">▤</span> Server requests
        </div>
        <div className="mt-auto hidden border-t border-white/10 pt-5 lg:block">
          <p className="text-sm">Local workspace</p>
          <p className="mt-1 text-xs text-slate-500">CloudOps development</p>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-5 lg:px-12">
          <p className="text-sm text-slate-500">
            Workspace <span className="mx-3 text-slate-300">/</span>
            <span className="text-slate-800">Server requests</span>
          </p>
          <span className="rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-medium text-teal-800">
            Local development
          </span>
        </header>
        <main className="mx-auto max-w-6xl px-5 py-9 sm:px-8 lg:px-12 lg:py-12">
          <p className="mb-3 text-xs font-semibold tracking-[.18em] text-teal-700">
            SERVER REQUESTS
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
            Create a server request
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-500">
            Choose your server configuration. We’ll validate your settings and
            save a request for your workspace.
          </p>
          <CreateServerForm />
          <footer className="mt-8 flex flex-wrap justify-between gap-3 border-t border-slate-200 pt-5 text-xs text-slate-500">
            <span>CloudOps · Infrastructure, thoughtfully managed.</span>
            <span>Local MVP · Requests only</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
