// Start the opencode server version this app is built against (1.18.35, pinned in
// package.json), with its own data folder so a newer opencode install (v2 moved the
// API under /api and changed the database) can't break it, or be broken by it.
//
//   npm run opencode                    # agent works in the current folder
//   npm run opencode -- /path/to/repo   # agent works in that repository
import { spawn } from "node:child_process"
import { mkdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const repo = path.resolve(process.argv[2] ?? process.cwd())
const port = process.env.OPENCODE_PORT ?? "4096"
const home = path.join(process.env.SYMPHONY_DATA_DIR ?? path.join(app, ".symphony"), "opencode")
const dirs = { XDG_DATA_HOME: "data", XDG_STATE_HOME: "state", XDG_CACHE_HOME: "cache" }
for (const sub of Object.values(dirs)) mkdirSync(path.join(home, sub), { recursive: true })

const bin = path.join(app, "node_modules", ".bin", process.platform === "win32" ? "opencode.cmd" : "opencode")
console.log(`opencode 1.18.35 for Symphony on http://127.0.0.1:${port}, working in ${repo}`)
const child = spawn(bin, ["serve", "--hostname", "127.0.0.1", "--port", port], {
  cwd: repo,
  stdio: "inherit",
  shell: process.platform === "win32",
  env: { ...process.env, ...Object.fromEntries(Object.entries(dirs).map(([k, sub]) => [k, path.join(home, sub)])) },
})
child.on("exit", (code) => process.exit(code ?? 0))
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal))
