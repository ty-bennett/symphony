# Symphony app

The Symphony front end: Next.js (App Router) + TypeScript, styled with the
Symphony design system from the design canvas (CSS variables in
`src/app/globals.css`, light by default, dark from the sidebar toggle or the OS
setting).

## Coding agent tab

`/coding-agent` embeds [opencode](https://opencode.ai). The browser only talks
to this app's `/api/oc/*` routes, which call `opencode serve` through
`@opencode-ai/sdk` (pinned to 1.18.35, the version it was checked against).

```
browser (tab) → /api/oc/* (this app) → opencode serve (http://127.0.0.1:4096)
```

### Run it

1. In the repository you want the agent to work on, start opencode:

   ```sh
   opencode serve --port 4096
   ```

   To get an approval card before every edit or shell command, add this to
   that repository's `opencode.json`:

   ```json
   { "permission": { "edit": "ask", "bash": "ask" } }
   ```

2. In this folder:

   ```sh
   cp .env.local.example .env.local   # adjust OPENCODE_URL if needed
   npm install
   npm run dev
   ```

3. Open http://localhost:3000/coding-agent. Link straight to a session with
   `/coding-agent?session=<id>`.

### Settings

| Variable | Default | |
|---|---|---|
| `OPENCODE_URL` | `http://127.0.0.1:4096` | Where `opencode serve` listens |
| `OPENCODE_SERVER_USERNAME` | `opencode` | Basic auth user, if opencode has a password |
| `OPENCODE_SERVER_PASSWORD` | unset | Basic auth password (`OPENCODE_SERVER_PASSWORD` on the opencode side) |
| `OC_ALLOW_UNAUTHENTICATED` | unset | Set to `1` to allow `/api/oc/*` in production before login exists. Only for a trusted local demo |

**Security.** The agent can edit files and run shell commands. `/api/oc/*`
refuses requests in production until `checkAuth()` in
`src/lib/opencode/server.ts` is wired to the app's login.

### API routes

| Route | Does |
|---|---|
| `GET /api/oc/health` | opencode health and version |
| `GET /api/oc/providers` | providers and model names (API keys are stripped) |
| `GET, POST /api/oc/sessions` | list (newest first), create |
| `DELETE /api/oc/sessions/:id` | delete |
| `GET /api/oc/sessions/:id/messages` | history as `[{ info, parts }]` |
| `POST /api/oc/sessions/:id/prompt` | `{ text, model?: "provider/model", agent? }` → 202 |
| `POST /api/oc/sessions/:id/abort` | stop the current run |
| `GET /api/oc/sessions/:id/diff` | changed files with unified patches |
| `GET /api/oc/sessions/:id/todo` | the agent's to-do list |
| `POST /api/oc/sessions/:id/permissions/:permissionID` | `{ response: "once" \| "always" \| "reject" }` |
| `GET /api/oc/permissions` | approvals already waiting (so a reload keeps them) |
| `POST /api/oc/tasks` | create a session and send its first prompt → `{ sessionId }`. Entry point for the orchestration engine |
| `GET /api/oc/events` | opencode's event stream, proxied, with a `: ping` every 15s |

### Notes on opencode 1.18

- The SDK's `@opencode-ai/sdk/v2` client is used: it has `global.health()`
  and takes flat `{ sessionID }` parameters.
- `GET /session/:id/diff` returns `[]` for the whole session; changes are
  recorded per turn on each user message (`summary.diffs`, unified patches).
  The diff route and the tab combine those turns.
- Text streams in as `message.part.delta` events, which the tab appends.

### Checks

```sh
npm run lint
npm run typecheck
npm run check:logic   # event handling, history merge, diffs
npm run build
```
