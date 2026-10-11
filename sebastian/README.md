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
| Team | `/team` | Who can sign in; invite links |
| Privacy and spending | `/privacy` | Where jobs may run, what stays private, monthly limit, report |

All of them run on opencode: a Symphony **job** is an opencode session. Start
opencode as described under [Run it](#run-it), then open
http://localhost:3000.

The logo is a treble clef (`Logo` in `src/components/ui/Icon.tsx`; app icons in
`src/app/icon.svg`, `src/app/apple-icon.png` and `public/icons/`).

## Accounts and first run

The first time anyone opens Symphony, `/setup` asks them to create the
**owner** account, then walks through three steps: the business (name and
type), what to keep private, and the monthly spending limit. Nothing else
opens until that's done.

The owner invites staff from **Team**: **Invite someone** makes a link
(`/join?code=…`) that works once, for 7 days. The person opens it, picks their
own name, email and password, and is signed in as a **team member**. No email
is sent and nothing leaves the box, so it works offline. Make the invite while
you're on Symphony's network address, not `localhost`, or the link only works
on your own computer.

| | Owner | Team member |
|---|---|---|
| Start jobs, see Today and Work | Yes | Yes |
| Approve, undo or redo a job | Any job | Only jobs they started |
| Privacy and spending, Team, Coding agent | Yes | No (pages and API both refuse) |

Forgot a password? The owner removes the person and sends a new invite. If
the owner forgets theirs, stop the app and delete `.symphony/auth.json`; the
next visit starts setup again (settings and jobs are kept).

How it's stored and checked:

- Accounts live in `.symphony/auth.json` (mode 600). Passwords are hashed with
  scrypt; session tokens and invite codes are stored only as SHA-256 hashes.
- Signing in sets an HttpOnly, SameSite=Lax cookie (`symphony_session`) good
  for 30 days, marked Secure when the app is served over HTTPS.
- Five wrong passwords for an email lock it for 30 seconds.
- Every API route goes through `checkAuth()` in `src/lib/opencode/server.ts`:
  it refuses requests from another site (Origin check), then signed-out
  requests (401), then members on owner-only routes (403). Jobs remember who
  started them (`by` in the session's Symphony metadata).

## On a phone

Colors follow the official palette: Onyx `#141414` and Ivory `#ffffed`, with
Saddle Brown `#814618` for Soloist (the box) and Deep Forest `#1a3d26` for
Orchestra (the private cloud) (tokens in
`globals.css`; text uses same-hue shades where a color would be too faint).
The typeface is [Syne](https://fonts.google.com/specimen/Syne) (SIL Open Font
License) for headings and text, with lining figures turned on so counts and
prices sit at full height.

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

Sign-in is plain HTTP on your own network. Don't share the dev server
through a public tunnel: the coding agent can run commands, so put Symphony
behind HTTPS before it's reachable from the internet.

The offline page ("Can't reach your Symphony") comes from a small service
worker, which browsers only run over HTTPS or on localhost. Everything else
works over plain HTTP on the local network.

## Today, Work, Privacy and spending

### How a job runs

1. **Routing.** `routeTask()` in `src/lib/symphony/routing.ts` picks one of
   two places and a plain-language reason:
   - **Soloist** (the model on the box): quick jobs, and anything private that
     isn't big. Nothing leaves the building.
   - **Orchestra** (Symphony's agent in the business's own private cloud
     account): big jobs and anything that needs the web. Never used to train
     AI, and nothing is kept.

   If the owner switches Orchestra off, every job runs on Soloist.

   **This is a stand-in for the JEV model.** It uses keywords and the owner's
   "Always keep private" list. Replace the body of `routeTask()` with a call to
   the JEV model and keep its return shape (`{ place, reason, privateHits }`).
   Nothing else needs to change. (Internally the places are still `device` and
   `private`, so saved settings and old jobs keep working.)
2. **Model.** Each place maps to an opencode model chosen in Privacy and
   spending (`provider/model`). Empty means opencode's default.
3. **Check with me first.** This becomes the session's opencode permission
   rules: edits, shell commands and web fetches either `ask` or `allow`.
   Approvals show on the job and under "Needs your OK".
4. **Spending.** Costs come from opencode's per-session `cost`, totalled per
   month and per place. At the limit, Orchestra jobs either pause (`402`) or
   run on Soloist instead, whichever the owner picked.
5. **Saved.** For every Soloist and Orchestra job, Symphony prices the tokens
   it used (opencode records them per session) at frontier-model rates and
   subtracts what the job actually cost. "Compare with" in Privacy and
   spending picks the rates: typical frontier prices ($3 in / $15 out per
   million tokens, the default and the fallback when a model has no price) or
   a specific priced model. Coding agent sessions on any other provider save
   nothing. It's an estimate and doesn't count the box or its electricity.
   Shown on Today, each job, Privacy and spending, and in the CSV report
   (`src/lib/symphony/savings.ts`).
6. **Undo all** reverts the session to before its first message (opencode's
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
| `POST /api/symphony/jobs` | `{ text, checkFirst?, place? }` → 202 `{ jobId, place, reason }` (`place` is `device` for Soloist or `private` for Orchestra) |
| `GET /api/symphony/jobs/:id` | One job, the comparison `rates`, and its removed details (to restore them on screen) |
| `POST /api/symphony/jobs/:id/undo`, `/redo` | Revert or un-revert the job's changes |

These routes use the same auth hook as `/api/oc/*`. `PUT /api/symphony/settings`
is owner-only and keeps any field you leave out.

### Account API routes

| Route | Who | Does |
|---|---|---|
| `GET /api/auth/me` | anyone | `{ state: "setup" }`, `{ state: "signed-out" }` or `{ state: "signed-in", user, onboarded }` |
| `POST /api/auth/setup` | first run only | `{ name, email, password }` → owner account, signed in |
| `POST /api/auth/login` | anyone | `{ email, password }` → session cookie |
| `POST /api/auth/logout` | anyone | ends the session |
| `GET /api/auth/join?code=` | anyone | `{ valid }` |
| `POST /api/auth/join` | invite holder | `{ code, name, email, password }` → member account, signed in |
| `GET, POST /api/auth/invites` | owner | list; create → `{ invite, code }` (the code is shown once) |
| `DELETE /api/auth/invites/:id` | owner | cancel an invite |
| `GET /api/auth/users` | owner | everyone on the team |
| `DELETE /api/auth/users/:id` | owner | remove a member and end their sessions |

## Coding agent tab

`/coding-agent` embeds [opencode](https://opencode.ai). The browser only talks
to this app's `/api/oc/*` routes, which call `opencode serve` through
`@opencode-ai/sdk` (pinned to 1.18.35, the version it was checked against).

```
browser (tab) → /api/oc/* (this app) → opencode serve (http://127.0.0.1:4096)
```

### Run it

1. Start opencode **1.18.35**, the version this app is built against, pointed
   at the repository you want the agent to work on:

   ```sh
   npm install            # once; installs the pinned opencode too
   npm run opencode -- /path/to/the/repository
   ```

   This runs the pinned opencode from `node_modules` on port 4096, with its own
   data folder in `.symphony/opencode`. Don't use a global `opencode serve`
   from Homebrew or the installer: opencode **v2** moved its API under `/api`,
   requires a login, and changed its database. The app then gets opencode's web
   page instead of its API, and nothing loads.

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
| `OC_ALLOW_UNAUTHENTICATED` | unset | Set to `1` to skip sign-in: everyone is treated as the owner. Only for a trusted local demo |
| `SYMPHONY_DATA_DIR` | `./.symphony` | Where settings and accounts are saved |

**Security.** The agent can edit files and run shell commands, so every
`/api/oc/*` route needs a signed-in owner (reading jobs and approving your
own needs any signed-in account). See [Accounts and first run](#accounts-and-first-run).

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
npm run check:logic   # event handling, history merge, diffs, routing, spending, savings
npm run check:auth    # passwords, sessions, invites, lockout, roles, origin check
npm run build
```
