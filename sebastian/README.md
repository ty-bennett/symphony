# Symphony app

The Symphony front end: Next.js (App Router) + TypeScript, styled with the
Symphony design system from the design canvas (CSS variables in
`src/app/globals.css`, light by default, dark from the sidebar toggle or the OS
setting).

| Tab | Path | What it is |
|---|---|---|
| Today | `/` | Start a job, see what needs your OK, recent work |
| Work | `/work`, `/work/:id` | Every job, and one job's steps, result, changes and Undo |
| Coding agent | `/coding-agent` | opencode itself, for people who want the full view |
| Connected apps | `/apps` | Gmail, Mailchimp, QuickBooks, Square and more, through n8n |
| Privacy and spending | `/privacy` | Where jobs may run, what stays private, monthly limit, report |

All four run on opencode: a Symphony **job** is an opencode session. Start
opencode as described under [Run it](#run-it), then open
http://localhost:3000.

## Connected apps (n8n)

The agent gets full use of the apps a business runs on (Gmail, Mailchimp,
QuickBooks, Square, and more) through [n8n](https://n8n.io), running next to
Symphony. Anything that isn't reading asks the owner first.

```
job (opencode session) → gmail_send_email → n8n "Gmail for Symphony" workflow → Gmail
```

**One workflow per app.** Each app is an n8n workflow whose **MCP Server
Trigger** has that app's tool nodes attached, with their settings left to the
AI (`$fromAI`). Ready-made ones are in `public/n8n/` and download from the
page:

| Workflow | Tools |
|---|---|
| Gmail for Symphony | search_emails, read_email, send_email, reply_to_email, create_draft, mark_as_read, delete_email |
| Mailchimp for Symphony | list_subscribers, get_subscriber, add_subscriber, unsubscribe, list_campaigns, get_campaign, send_campaign |
| QuickBooks for Symphony | list_invoices, get_invoice, create_invoice, send_invoice, list_customers, get_customer, create_customer, list_items, list_payments, list_bills |
| Square for Symphony | get_sales, search_customers, list_items, list_locations, create_customer, create_payment_link (HTTP Request tools + a Square access token) |

Set up per app: in n8n create a new workflow and use ⋯ › Import from File (or paste the file's contents onto the canvas), sign in to the app in its tools (for
Mailchimp also pick the audience), set **Bearer Auth** with a token of your
choice on "Symphony connects here", publish it (older n8n: activate), and paste the trigger's
**Production URL** and the token into Connected apps. Symphony lists the
tools to test it, saves it to `.symphony/app-servers.json` (mode 600, never
sent to the browser), and registers it with opencode under the app's id, so
tools are named `gmail_send_email` and so on. Any app works this way: build a
workflow with an MCP Server Trigger and that app's tool nodes.

**Reads run, writes ask.** Tools named `search_…`, `read_…`, `get_…`,
`list_…`, `find_…` run on their own; everything else (send, create, delete,
unsubscribe…) waits for the owner's OK on the approval card, which shows the
tool's inputs (the email's recipient, subject and text, say). "Ask me before
an app changes anything" turns this off.

**Privacy.** A job that mentions a connected app counts as touching that
app's data (Gmail: customer contact info; QuickBooks and Square: customers
and money), so it stays on the box or in the private cloud unless the owner
agrees otherwise.

**More n8n workflows (optional).** Connecting n8n itself (Settings ›
Instance-level MCP, address + token, or `N8N_URL`/`N8N_MCP_TOKEN`) also lets
the agent find and run any workflow marked "Available in MCP". n8n's MCP
tools for building, publishing or deleting workflows and listing credentials
are hidden from every session; running a workflow asks first. Works with
n8n 2.8 (which lists only enabled workflows and reports their nodes, so a
workflow with a Gmail node shows under Gmail) and later versions.

Tested against stand-ins for n8n's MCP servers built from n8n 2.8.4's source
(same tool names, transports and result shapes) and the real opencode. The
workflow files follow n8n 2.8.4's node definitions but haven't been imported
into a real n8n yet.

## On a phone

Colors follow the official palette: Onyx `#141414` and Ivory `#ffffed`, with
Saddle Brown `#814618` for "On your Symphony", Deep Forest `#1a3d26` for the
private cloud and Pacific Cyan `#588d93` for the open cloud (tokens in
`globals.css`; text uses same-hue shades where a color would be too faint).

Symphony installs like an app: on an iPhone, open it in Safari, tap **Share**,
then **Add to Home Screen**. It opens full screen with its own icon, and the
layout leaves room for the notch and home bar. Chrome and Edge (Android or
desktop) show an **Install** button instead. Either way, the app shows a small
hint until it's installed or dismissed.

To try it from your phone while running `npm run dev`:

1. Put the phone on the same Wi-Fi as the computer.
2. Find the computer's Wi-Fi address (Windows: `ipconfig`, the IPv4 Address
   under "Wireless LAN adapter Wi-Fi", usually `192.168.x.x` or `10.x.x.x`).
   Ignore a `169.254.x.x` address; that's an adapter with no network.
3. On the phone, open `http://<that address>:3000`.

If it doesn't load: allow Node.js through Windows Firewall on private
networks (and make sure the Wi-Fi is set to Private), or, on a school or
office network that blocks devices from seeing each other, connect the
computer to the phone's hotspot and use its address on that network.
`next.config.ts` already allows this computer's network addresses in dev.

Don't share the dev server through a public tunnel: there's no login yet,
and the coding agent can run commands.

The offline page ("Can't reach your Symphony") comes from a small service
worker, which browsers only run over HTTPS or on localhost. Everything else
works over plain HTTP on the local network.

## Today, Work, Privacy and spending

### How a job runs

1. **Routing.** `routeTask()` in `src/lib/symphony/routing.ts` picks one of
   three places and a plain-language reason:
   - **On your Symphony** (the box): private and quick jobs.
   - **Private cloud**: private but big jobs.
   - **Open cloud**: big jobs with nothing private, and anything that needs the web.

   **This is a stand-in for the JEV model.** It uses keywords and the owner's
   "Always keep private" list. Replace the body of `routeTask()` with a call to
   the JEV model and keep its return shape (`{ place, reason, privateHits,
   needsConsent }`). Nothing else needs to change.
2. **Consent.** If a job would send something private to the open cloud, Today
   asks first. The choices are: remove emails and phone numbers, send as
   written, keep it in the private cloud, or keep it on the box. The server
   enforces the same rule (`409`), so it can't be skipped. Removed details are
   put back when the result is shown.
3. **Model.** Each place maps to an opencode model chosen in Privacy and
   spending (`provider/model`). Empty means opencode's default.
4. **Check with me first.** This becomes the session's opencode permission
   rules: edits, shell commands and web fetches either `ask` or `allow`.
   Approvals show on the job and under "Needs your OK".
5. **Spending.** Costs come from opencode's per-session `cost`, totalled per
   month and per place. At the limit, cloud jobs either pause (`402`) or run on
   the box instead, whichever the owner picked.
6. **Saved.** For every job kept off the open cloud, Symphony prices the
   tokens it used (opencode records them per session) at open-cloud rates
   and subtracts what the job actually cost. "Compare with" in Privacy and
   spending picks the rates: your open-cloud model's price from opencode
   (the default), a specific priced model, or typical frontier prices
   ($3 in / $15 out per million tokens), which are also the fallback when a
   model has no price. It's an estimate and doesn't count the box or its
   electricity. Shown on Today, each job, Privacy and spending, and in the CSV
   report (`src/lib/symphony/savings.ts`).
7. **Undo all** reverts the session to before its first message (opencode's
   revert), which puts changed files back. **Redo** un-reverts.

Where each job ran, and why, is saved on the opencode session as
`metadata.symphony`. That makes the job list, the privacy table and the CSV
report all come straight from opencode. Jobs started from the coding agent show
their place from the model's provider.

Settings are saved to `.symphony/settings.json` in this folder (git-ignored).
Set `SYMPHONY_DATA_DIR` to keep them somewhere else.

### Symphony API routes

| Route | Does |
|---|---|
| `GET, PUT /api/symphony/settings` | Privacy and spending settings |
| `GET /api/symphony/jobs` | `{ jobs, spending, savings }`; each job carries `tokens`, `wouldHaveCost` and `saved` |
| `POST /api/symphony/jobs` | `{ text, checkFirst?, place?, consent?, redact? }` → 202 `{ jobId, place, reason }`, or 409 `{ needsConsent, decision }` |
| `GET /api/symphony/apps` | Connected apps: n8n status, each app's ready actions (`?fresh=1` skips the 30s cache) |
| `PUT, DELETE /api/symphony/apps/connection` | `{ url, token }`: test, save and register n8n itself; or disconnect |
| `PUT, DELETE /api/symphony/apps/:id` | `{ url, token }`: connect one app's MCP Server Trigger workflow (tools are listed to test it); or disconnect |
| `GET /api/symphony/jobs/:id` | One job, the comparison `rates`, and its removed details (to restore them on screen) |
| `POST /api/symphony/jobs/:id/undo`, `/redo` | Revert or un-revert the job's changes |

These routes use the same auth hook as `/api/oc/*`.

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

3. Open http://localhost:3000. The coding agent is at `/coding-agent`. Link
   straight to a session with `/coding-agent?session=<id>`.

### Settings

| Variable | Default | |
|---|---|---|
| `OPENCODE_URL` | `http://127.0.0.1:4096` | Where `opencode serve` listens |
| `OPENCODE_SERVER_USERNAME` | `opencode` | Basic auth user, if opencode has a password |
| `OPENCODE_SERVER_PASSWORD` | unset | Basic auth password (`OPENCODE_SERVER_PASSWORD` on the opencode side) |
| `OC_ALLOW_UNAUTHENTICATED` | unset | Set to `1` to allow `/api/oc/*` in production before login exists. Only for a trusted local demo |
| `SYMPHONY_DATA_DIR` | `./.symphony` | Where Privacy and spending settings are saved |
| `N8N_URL` | unset | n8n's address for Connected apps, e.g. `http://localhost:5678` (or set it in the app) |
| `N8N_MCP_TOKEN` | unset | n8n access token (Settings › Instance-level MCP › API key) |

**Security.** The agent can edit files and run shell commands. `/api/oc/*`
refuses requests in production until `checkAuth()` in
`src/lib/opencode/server.ts` is wired to the app's login.

### API routes

| Route | Does |
|---|---|
| `GET /api/oc/health` | opencode health and version |
| `GET /api/oc/providers` | providers, model names and prices (API keys are stripped) |
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
npm run check:logic   # event handling, history merge, diffs, routing, redaction, spending, savings
npm run build
```
