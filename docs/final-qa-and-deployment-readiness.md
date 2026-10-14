# CodeGuard AI — Final QA, Browser Verification & Vercel Readiness

**Version:** 0.6.2 · **Date:** 2026-09-27

# FINAL QA STATUS

**READY WITH ENVIRONMENT-SPECIFIC REQUIREMENTS**

One gate remains and I could not perform it: **`npm install` and `npm run build`
have never been run.** Every package source is blocked by this account's egress
policy, in the cloud sandbox *and* on the linked Windows machine. So Vite has
never bundled this app and Tailwind has never compiled its CSS.

Read that as: nothing is known to be broken, several real defects were found and
fixed, and the remaining risk is concentrated in the one step no one has taken.
Run `npm install && npm run build` on a machine with registry access before you
deploy. If it passes, this is ready.

What I *did* do instead of claiming a build: I mounted the project's real
components in real Chromium, on the project's real `index.html`, and drove 44
page loads across 4 viewports plus an 18-check interaction pass. That found two
genuine accessibility defects, both now fixed and re-verified in the browser.

# ENVIRONMENT VERIFIED

| | |
|---|---|
| Node (sandbox) | v22.22.2 |
| npm (sandbox) | 10.9.7 |
| Node (linked device VM) | v22.23.2 · npm 10.9.8 |
| Dependency installation | **FAILED — blocked by egress policy**, see below |
| Browser | Chromium 1194 (Playwright 1.56.0), headed context, real rendering engine |
| React used | react 19.2.6 / react-dom 19.2.6 (the real packages, installed globally in the image) |
| Viewports | 1680×1000, 1280×860, 834×1000, 375×780 |

## Why installation failed — this is an environment limitation, not a project defect

Probed every route, in both environments:

| Source | Sandbox | Device VM |
|---|---|---|
| `registry.npmjs.org` | 403 (direct **and** via proxy) | 403 from proxy after CONNECT |
| `artifactory.infra.ant.dev` (the image's own cache origin) | 401 | 401 |
| `cdn.jsdelivr.net`, `unpkg.com`, `registry.npmmirror.com`, `registry.yarnpkg.com` | no route | no route |
| `github.com` / `codeload.github.com` | 400 / 403 | no route |
| Offline npm cache | present but 0 entries for this project's deps | 24 KB, empty |

The agent proxy reports `recentRelayFailures: []`, so the 403 is an
organisation egress policy above the proxy, not a proxy fault. Its README says
to report such denials rather than route around them, so I did not.

# COMMAND VERIFICATION

| Check | Result |
|---|---|
| npm install | **BLOCKED** — registry 403 (environment) |
| typecheck | **PASS** — `tsc --noEmit -p verification/tsconfig.stubs.json`, exit 0 |
| tests | **PASS** — 68 passed, 0 failed |
| lint | **BLOCKED** — eslint not installable |
| production build | **BLOCKED** — vite not installable |
| dev server (`npm run dev`) | **BLOCKED** — vite not installable |
| browser verification | **PASS (scoped)** — 44 real page loads, 0 errors; see scope note |
| console inspection | **PASS** — 0 page exceptions, 0 React errors, 0 failed assets |
| responsive verification | **PARTIAL** — structure yes, layout not assessable (no Tailwind) |

Supporting checks that did run:

| Check | Result |
|---|---|
| Test compile (`tsc -p tsconfig.test.json`) | exit 0 |
| esbuild bundle of the full module graph | **PASS** — every import resolves, no missing modules |
| Tailwind config integrity (evaluated in Node) | **PASS** — 4 animations, 4 keyframes, 0 orphans either direction |
| `vercel.json` parses | **PASS** |
| Secrets / `.env` hygiene | **PASS** — only `.env.example`; `.gitignore` covers `.env`, `.env.*`, `dist/`, `node_modules/` |

## What the browser verification actually covered — read this before trusting it

I could not install Tailwind or `react-router-dom`, so the harness supplied:

- **the real `index.html`** (theme script, boot fallback, noscript, meta — all under test)
- **the real components, unmodified**, bundled with esbuild
- **the real `tokens.css` and `globals.css`**, minus the `@tailwind`/`@apply`
  directives that only Tailwind can expand
- **a faithful 11-export stand-in for `react-router-dom`**
- **fixtures generated from the project's own `src/types/api.ts`** — the zero
  value of each endpoint's declared type. No content was invented; every value
  is `0`, `''`, `false`, `null` or `[]`.

So **utility classes have no styles in the harness and the pages render
unstyled.** That means:

- **Verified for real:** that components mount, that nothing throws, console
  cleanliness, DOM structure and semantics, heading hierarchy, ARIA wiring, tab
  order, routing and redirects, deep-link behaviour, theme persistence, the
  mobile menu, reduced-motion behaviour.
- **NOT verified:** visual design, spacing, colour, dark/light appearance,
  hover and tilt effects, animation timing, and whether anything overflows or
  clips at a given width. **I am not reporting visual QA as passed.**

The 135 screenshots are genuine Chromium screenshots of the real components —
and they are unstyled, so they are evidence about structure, not appearance.

Everything the harness generated lives outside the repository, in the session
scratchpad. Nothing harness-related was added to `frontend/` (verified by grep).

# BUGS FOUND AND FIXED

Two genuine defects, both found by the browser pass and invisible to static
review. Both are accessibility defects on states a user reaches normally.

### 1. All four detail pages rendered with no `<h1>` while loading, on error, and on 404

- **Symptom.** `/repositories/:id`, `/pull-requests/:id`, `/review-runs/:id` and
  `/findings/:id` reported `h1Count = 0` in the browser whenever the fetch had
  not succeeded. A screen-reader user navigating by heading landed on nothing; a
  sighted user following a stale deep link got a bare error box with no
  indication of which page they were on.
- **Root cause.** Each page builds its `PageHeader` from the fetched object
  (`finding.title`, `repository.full_name`), so the header lives inside
  `AsyncSection`'s success child and does not exist in any other state.
- **Fix.** Added an optional `placeholderHeading` to `AsyncSection`
  (`components/data/AsyncSection.tsx`), rendered for the loading, error and empty
  states and deliberately *not* for the ready state, where the real
  data-derived header takes over. Each detail page passes the resource name,
  which is known from the route. Fixed in the shared layer, so one change covers
  all four; the success design is untouched.
- **Verification.** Rebuilt and re-ran the sweep: **44/44 page loads now report
  exactly one `<h1>`**, was 40/44.

### 2. The API-unavailable screen — a whole page — had no `<h1>` at all

- **Symptom.** With the backend down (the state a developer hits first), the
  entire page rendered with zero headings.
- **Root cause.** `ApiUnavailable` in `components/marketing/Hero.tsx` used a
  `<p role="alert">` for "Cannot reach the API". An element cannot be both an
  alert and a heading, so the page had neither.
- **Fix.** Moved `role="alert"` up to the containing panel and made the message
  an `<h1>`. Same visual result; the block is still announced, and the page now
  has a heading.
- **Verification.** Re-ran the `no-api` scenario in Chromium: `h1count: 1`,
  `alerts: 1`, `h1: "Cannot reach the API"`.

### Two harness bugs that looked exactly like product bugs — and were not

Recording these because they are the reason I do not trust a red result until
I have traced it:

1. My first fixtures were hand-written from memory and **every shape was wrong**
   (`runs.partial_success` not `partial`, `funnel.claims_emitted` not `claims`,
   `anchors.exact` not `verified`, `ai_provider.available` missing). That
   produced five convincing "crashes" in the project's pages. I replaced the
   hand-written fixtures with a generator that reads the project's own type
   declarations, which makes the mistake structurally impossible.
2. The generator then collapsed `/repositories?query` and `/repositories/:id`
   into one key, so every list endpoint served a single object and every list
   page "crashed". Also mine. Fixed by distinguishing collection from detail
   routes.

Neither was a project defect and neither is counted above.

# VISUAL QA

**Not performed, and not claimable.** Tailwind never compiled, so the rendered
pages carry no utility styles. Judging spacing, colour, overflow or animation
from that would be judging the harness.

What the same pass *does* establish, at all four viewports:

- **Structure** — every route renders its own content; correct heading
  hierarchy (one `h1`, `h2` per section) on all 14 routes.
- **Dark/light** — `data-theme` flips on toggle, persists to
  `localStorage['codeguard-theme']`, and survives a reload via the pre-paint
  script in `index.html`. Whether the two themes *look* right is unverified.
- **Loading / error / empty** — all three reached and rendered without error.
  Error states were reached through genuine API failures (503 and a destroyed
  socket), not simulated.
- **Root error boundary** — caught every induced render failure and showed its
  recovery screen rather than a blank page. Verified by accident, thoroughly,
  via the fixture bugs above.
- **Accessibility** — 0 buttons without an accessible name, 0 unlabelled SVGs
  exposed to assistive tech, 0 `aria-hidden` subtrees containing focusable
  elements, and 24 consecutive Tab stops with none landing on a hidden element.
  First Tab stop is the skip link. Exactly one nav item carries `aria-current`.
- **Reduced motion** — renders clean under `prefers-reduced-motion: reduce`, and
  the animated number shows its final value immediately rather than counting.

# VERCEL READINESS

| Item | Value |
|---|---|
| Root directory | `frontend` — set this in the Vercel project, the repo is a monorepo |
| Build command | `npm run build` (`tsc --noEmit && vite build`) |
| Output directory | `dist` |
| SPA routing | **was missing — added** `frontend/vercel.json` |
| Framework preset | Vite |

`vercel.json` (the minimum, no serverless functions, no headers added):

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
}
```

Without that rewrite, a direct hit on `/findings/42` returns Vercel's 404
instead of the app. Vercel's filesystem check runs before rewrites, so hashed
assets under `/assets/*` still serve as files. The harness served the app under
exactly these semantics — catch-all to `index.html` — and every deep link
rendered correctly, so the behaviour the rewrite has to reproduce is confirmed.

### Environment variables

| Variable | Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | the backend's public origin | **Required in Production.** It defaults to `''` (same-origin), which works in dev via the Vite proxy but on Vercel would point the SPA at the Vercel domain, where no API exists. |

Only `VITE_` variables reach the browser, and this is the only one the code
reads (`src/api/client.ts:32`). No secret belongs here — `.env.example` already
says so explicitly.

### Cross-origin session cookies

The SPA on `*.vercel.app` and the API on another host is a cross-site pair, so
on the backend:

- `SESSION_COOKIE_SAMESITE=none` (the backend already refuses this unless secure
  cookies are on — `config.py:446`)
- add the exact Vercel origin to `cors_origins`; `allow_credentials=True` is
  already set (`main.py:170`)

If instead you put both on one registrable domain (`app.example.com` /
`api.example.com`), the default `lax` is fine and no change is needed.

### Security notes

- No `dangerouslySetInnerHTML`, no `innerHTML`, no `eval` anywhere in `src/`.
- The only browser storage is the theme preference, guarded by `try/catch` on
  both read and write, and it stores only `'light'` or `'dark'`.
- No credential is held in JavaScript; the session is an HttpOnly cookie.
- The sign-in redirect is already hardened against open redirect —
  `useAuth.tsx:138` rejects any `next` that is not a relative path, and the
  destination is a fixed same-origin path rather than the API-supplied
  `login_url`.
- Both `target="_blank"` links carry `rel="noreferrer noopener"`.
- **One thing to decide, not a defect:** `vite.config.ts` sets
  `build.sourcemap: true`, which publishes readable source to production. There
  are no secrets in the bundle so this is not a vulnerability, but for a public
  deployment of a security product you may prefer `false` or `'hidden'`. I left
  it as-is because it is a deliberate existing choice.

# FILES CHANGED

Six source files and one new config file:

| File | Change |
|---|---|
| `frontend/src/components/data/AsyncSection.tsx` | added `placeholderHeading` (bug 1) |
| `frontend/src/pages/ReviewRunPage.tsx` | passes `placeholderHeading` |
| `frontend/src/pages/PullRequestDetailPage.tsx` | passes `placeholderHeading` |
| `frontend/src/pages/RepositoryDetailPage.tsx` | passes `placeholderHeading` |
| `frontend/src/pages/FindingDetailPage.tsx` | passes `placeholderHeading` |
| `frontend/src/components/marketing/Hero.tsx` | `ApiUnavailable` heading + alert role (bug 2) |
| `frontend/vercel.json` | **new** — SPA rewrite, build command, output dir |

No backend file was changed. No API contract, auth logic, database model, GitHub
integration or AI review logic was touched. No dependency was added. No test was
weakened. No fake data was introduced into the application.

# FINAL VERIFICATION

Run after all fixes:

```
$ npx tsc --noEmit -p verification/tsconfig.stubs.json
exit=0

$ rm -rf .test-build && npx tsc -p tsconfig.test.json && node scripts/run-tests.mjs
15 passed, 0 failed     (api/__tests__/credentials.test.js)
 6 passed, 0 failed     (api/__tests__/network-failure.test.js)
10 passed, 0 failed     (api/__tests__/query.test.js)
20 passed, 0 failed     (utils/__tests__/format.test.js)
17 passed, 0 failed     (utils/__tests__/wording.test.js)
runner exit=0           → 68 passed, 0 failed

$ node drive.mjs empty          # real Chromium, 14 routes × 4 viewports
44 page loads, 0 with observations

$ node interact.mjs             # real Chromium, behaviour
18 checks, 0 failed

$ node -e "<evaluate tailwind.config.ts>"
animations: rise-in,fade-in,sweep,pulse-ring | orphans: 0

$ npm install
npm error 403 Forbidden — GET https://registry.npmjs.org/...     [BLOCKED]
```

The 18 interaction checks, in full: client-side navigation without a document
reload · route actually changes · new page renders its heading · exactly one
`aria-current` · browser Back works · no exceptions during navigation · first Tab
stop is the skip link · 24 Tab stops, none hidden · theme toggle flips
`data-theme` · choice persisted · theme survives reload · menu button exposes
`aria-expanded` · names its controlled region · that region exists · menu opens ·
menu closes after navigating · renders under reduced motion · animated number
shows its real value immediately.

## Before you deploy

```
cd frontend
npm install
npm run lint          # never run — expect to fix lint-only findings
npm run typecheck     # expect clean; it is clean against the ambient stubs
npm run test          # expect 68 passed
npm run build         # never run — this is the real remaining gate
npm run dev           # then look at it, in both themes, at phone width
```

The visual QA in section "VISUAL QA" is the part only you can close. Tailwind has
never compiled this stylesheet, so the first `npm run dev` is the first time
anyone will have seen this interface styled.
