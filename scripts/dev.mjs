#!/usr/bin/env node
// Convenience launcher for local development: starts the API, worker (if Celery) and web dev server.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

// Load .env if present
const envPath = path.resolve(".env");
if (fs.existsSync(envPath)) {
  const content = fs.readFileSync(envPath, "utf-8");
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith("#") && trimmed.includes("=")) {
      const idx = trimmed.indexOf("=");
      const key = trimmed.slice(0, idx).trim();
      const val = trimmed.slice(idx + 1).trim();
      if (!process.env[key]) {
        process.env[key] = val;
      }
    }
  }
}

const isWin = process.platform === "win32";
const pnpmCmd = isWin ? "pnpm.cmd" : "pnpm";
const pythonPathSep = isWin ? ";" : ":";
const pythonPath = `apps/api${pythonPathSep}${process.env.PYTHONPATH || "."}`;

const procs = isWin
  ? [
      [
        "API",
        "uv",
        ["run", "uvicorn", "aegis_api.app:app", "--host", process.env.API_HOST || "0.0.0.0", "--port", process.env.API_PORT || "8000", "--reload"],
        { env: { ...process.env, PYTHONPATH: pythonPath }, shell: true },
      ],
      ["WEB", pnpmCmd, ["--filter", "@aegis/web", "dev"], { shell: true }],
    ]
  : [
      ["API", "bash", ["scripts/run-api.sh"], {}],
      ["WEB", "pnpm", ["--filter", "@aegis/web", "dev"], {}],
    ];

if (process.env.JOB_BACKEND === "celery") {
  if (isWin) {
    procs.push([
      "WORKER",
      "uv",
      ["run", "celery", "-A", "aegis_api.jobs.tasks:celery_app", "worker", "--loglevel=info", "-P", "solo"],
      { env: { ...process.env, PYTHONPATH: pythonPath }, shell: true },
    ]);
  } else {
    procs.push(["WORKER", "bash", ["scripts/run-worker.sh"], {}]);
  }
}

const children = procs.map(([name, cmd, args, opts]) => {
  const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
  const tag = `[${name}] `;
  child.stdout.on("data", (d) => process.stdout.write(tag + d.toString().replace(/\n/g, "\n" + tag).replace(new RegExp(tag + "$"), "")));
  child.stderr.on("data", (d) => process.stderr.write(tag + d));
  return child;
});
process.on("SIGINT", () => { children.forEach((c) => c.kill("SIGINT")); process.exit(0); });
