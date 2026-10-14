# CodeGuard AI — Premium Frontend Experience Upgrade

**Version:** 0.6.2
**Scope:** frontend presentation only. No backend file was modified except the
version string in `pyproject.toml` / `config.py`, so that `/health` and the
System page do not disagree about which release is running.

**What was explicitly not touched:** API contracts, authentication logic,
database models, GitHub integration, AI review logic. No analytics were added.
No backend data was invented.

---

## 1. Areas upgraded

| Area | What changed |
| --- | --- |
| Design tokens | Motion (4 durations, 3 easings), elevation (3 shadows), z-index scale and a type scale added to `tokens.css` and bound into Tailwind |
| Surfaces | One `.panel` treatment replaces per-page card styling; 1px top highlight so cards read as surfaces in dark mode instead of outlined rectangles |
| Navigation | Icons on all 7 items, a 2px active rail, sticky full-height sidebar, mobile disclosure, tooltip on the theme toggle |
| Unauthenticated entry | New hero replacing a centred card; new API-unavailable screen with the actual command to start the backend |
| Findings | New `FindingCard` — severity rail, expandable anchor detail, cross-highlighting with the changed-files table |
| In-flight reviews | New `AnalysisState` (see §5 — it deliberately has no progress bar) |
| Loading | Five layout-shaped skeletons replacing generic grey blocks |
| Empty / error | Rewritten (§5) |
| Icons | 22 inline SVG icons in one file; four hand-rolled `↗` links replaced by a real `ExternalLink` primitive |
| Numbers | Stat tiles count up once, on first scroll into view, never on first paint |

## 2. Motion system

Everything is CSS. There is no animation library, no JS timeline, and no
`requestAnimationFrame` loop except the one count-up.

- **Durations:** `--dur-instant` 120ms (hover), `--dur-quick` 200ms (state),
  `--dur-panel` 300ms (disclosure), `--dur-entrance` 640ms.
- **Easings:** `--ease-out` for things arriving, `--ease-soft` for continuous
  motion, `--ease-in` for things leaving.
- **Only `transform` and `opacity` are animated.** No width, height, top, left
  or margin appears in any transition or keyframe, so nothing in the motion
  system can trigger layout.
- **Stagger without JavaScript:** an element sets `--stagger: <index>` and the
  delay falls out of `calc(var(--stagger) * 45ms)`.
- **Disclosure without measurement:** `grid-template-rows: 0fr → 1fr`.
- Four keyframes remain (`rise-in`, `fade-in`, `sweep`, `pulse-ring`), each with
  exactly one animation entry — verified programmatically, no orphans in either
  direction. A `drift` animation written during the task was removed as unused.

## 3. 3D / depth

Depth is rationed on purpose, and the ration turned out to be one surface.

`TiltCard` (pointer-tracked rotation capped at 3.2°, plus a sheen following the
cursor) was written expecting to go on the repository, review and finding cards.
It went on none of them: the list pages are dense `Table`s rather than card
grids, and `FindingCard` carries twelve fields — a rotating substrate under that
makes text harder to read for no gain. Rather than force it onto data or leave it
unused, it is applied to the one surface in the product that is genuinely a
picture: the hero's pipeline illustration (`aria-hidden`, six short lines).

- Pointer moves write four CSS custom properties directly to the node's
  `style`, outside React. A pointer crossing the card causes **zero** re-renders.
- Mouse pointers only — a touch drag scrolls the page.
- `interactive={false}` there, so it does not lift like a button: nothing on it
  is clickable.
- Under reduced motion it renders as a flat `Panel`, not a panel with a dead
  handler.

Everywhere else depth is a 1px lit top edge and a contact shadow. Charts, tables
and metric rows do not move.

## 4. Hero / landing

Real content only. No testimonials, no logo wall, no metrics — there are no
customers and no benchmark, so those slots have nothing honest in them and are
absent rather than filled.

The three guarantees describe the actual pipeline (bounded context, fourteen
validation gates, rejected claims retained). The illustration beside them is
isolated in `components/marketing/PipelineDemo.tsx`, which imports nothing from
`@/api`, prefixes its strings `DEMO_`, is `aria-hidden`, and is labelled
**Illustration** on screen. Its progress bar carries no percentage because there
is no percentage to carry.

## 5. Loading, empty, error, success

**Loading.** Five skeletons shaped like the layout they replace
(`SkeletonStatGrid`, `SkeletonChart`, `SkeletonTable`, `SkeletonCards`,
`SkeletonRows`). Each shape is `aria-hidden` with one label on the container, so
a screen reader hears "Loading" once rather than per box.

**Empty.** Now a sentence plus a glyph. In this product empty is usually
*correct* — a signed-in user with no App installations genuinely has no
repositories — so the title says what is absent and the hint says which
condition produced it. The two "not found" states use an alert mark and a
navigational action instead of the empty tray, because a failed id lookup is a
different fact from an empty list.

**Error.** `ErrorState` now distinguishes **network** from **server**, taken from
the error object via a new `errorKind()` helper (`ApiError` exists only when a
real HTTP response was parsed — nothing is inferred from message text). Each kind
gets its own glyph and its own remedy line, because "start the backend" and "retry
or quote the correlation id" are different instructions. `useHealth` was widened
to carry the kind alongside the message; it had been discarding the error object.

**Success.** There is none, deliberately. A `SuccessNote` with a drawn check and
a `done` state on `Button` were both written and then **removed**: this frontend
has exactly one write in it (`POST /auth/logout`) and its success unmounts the
tree that would have shown the confirmation. A success affordance with nothing to
confirm is an invitation to attach it to something that has not succeeded. The
`Button` docstring records where the state goes when a real mutation lands.

## 6. Responsive

- Sidebar collapses to a disclosure button below `md` rather than shrinking; a
  horizontal strip of seven links is unusable at phone width.
- Tables scroll horizontally with `min-w-[640px]` inside an `overflow-x-auto`
  wrapper; low-priority columns are hidden below `md`. Hiding a column is honest,
  crushing one is not.
- Charts are inline SVG with a `viewBox` and no fixed width.
- All `<pre>` blocks are `overflow-x-auto`.
- Every page container is `max-w-*`, not `w-*` — audited, no fixed widths in
  layout.
- Hero stacks below `lg`; its headline's hard break is dropped below `sm` so the
  first line is not left short and ragged.

## 7. Accessibility

- Visible focus ring on every interactive element (`:focus-visible`, 2px accent,
  2px offset).
- Skip link, semantic `<table>` with `<caption class="sr-only">`, `role="status"`
  + `aria-live` on async regions, `role="alert"` on errors.
- **Colour is never the only channel.** Every severity and run status carries a
  glyph and a text label; enforced by 17 tests in `wording.test.ts`.
- The finding expander now sets `aria-controls`, and the collapsed region is
  `invisible` — a zero-height region with `overflow-hidden` is still in the
  accessibility tree and its link is still tabbable, so twenty collapsed
  findings meant twenty invisible tab stops.
- Animated numbers carry the real value in `aria-label`, not the interpolated
  frame.
- Tooltips open instantly on focus and after 380ms on mouse hover only, are
  always in the DOM with `aria-describedby`, and close on Escape.
- `ExternalLink` announces "(opens in a new tab)" in screen-reader-only text —
  the `↗` character it replaces was read aloud as "north east arrow" mid-label.
- Decorative SVG is `aria-hidden` + `focusable="false"` throughout.

## 8. Performance safeguards

- `prefers-reduced-motion` handled at the CSS level with `!important` overrides,
  including iteration count, tilt transforms, and the sheen (`display: none`).
  `useReducedMotion()` also reads the query live so components can render a
  simpler tree rather than a disabled one.
- **`will-change` removed from `.panel-interactive`.** It had been added there,
  and it was wrong: that class lands on every card in a list, so a page of forty
  findings asked the compositor for forty permanently promoted layers. It is kept
  only on `.tilt-plane` — one element, rewritten on every pointer move, which is
  the case the property exists for.
- Tilt writes CSS properties directly, bypassing React entirely.
- `IntersectionObserver` is once-only and self-disconnecting; the count-up never
  animates on first paint and exits early when the value is already correct.
- `pulse-ring` bounded to 3 iterations instead of `infinite`.
- No canvas, WebGL, particle system, or animated background behind dense data.
- Zero runtime dependencies added (§11).

## 9. Correctness defects found and fixed during the task

Two of these are bugs I introduced earlier in this task and then caught:

1. **Runtime-assembled Tailwind class.** `FindingCard` built its severity rail
   colour with `severity.className.replace('text-', 'bg-')`. Tailwind generates
   utilities by scanning source text, so a class assembled at runtime is never
   emitted. It appeared to work *only* because `DashboardPage` happened to list
   all five `bg-severity-*` classes literally for its bar chart — the rail's
   colour depended on an unrelated chart helper on another page continuing to
   exist. Both spellings now live in `utils/severity.ts` as `className` and
   `fillClassName`, and `DashboardPage` reads that map instead of repeating it. A
   repo-wide scan found no other dynamically constructed class names.
2. **A regression in `credentials.test.ts`** (see §12).
3. **A broken `tailwind.config.ts`** left by my own edit removing the `drift`
   keyframe; caught by evaluating the config rather than trusting that
   typechecking covered it (it does not — the config is outside the project).

## 10. Files changed

34 files. New: `hooks/useMotion.ts`, `components/ui/icons.tsx`,
`components/ui/Tooltip.tsx`, `components/ui/Surface.tsx`,
`components/marketing/Hero.tsx`, `components/marketing/PipelineDemo.tsx`,
`components/domain/FindingCard.tsx`, `components/domain/AnalysisState.tsx`.
Modified: `styles/tokens.css`, `styles/globals.css`, `tailwind.config.ts`,
`components/ui/primitives.tsx`, `components/ui/Card.tsx`,
`components/ui/Table.tsx`, `components/data/AsyncSection.tsx`,
`components/auth/AuthGate.tsx`, `layouts/AppShell.tsx`, `hooks/useApiQuery.ts`,
`hooks/useHealth.ts`, `utils/severity.ts`, all 10 pages,
`api/__tests__/credentials.test.ts`, both files under `verification/types/`,
`package.json`, plus the two backend version strings.

## 11. Dependencies added

**None.** `package.json` dependencies are unchanged: `react`, `react-dom`,
`react-router-dom`. Icons are hand-written SVG in one file; there is no icon
package and no second icon system. No emoji are used as UI icons — the geometric
shapes in `severity.ts` / `status.ts` are the tested "never colour alone"
accessibility glyphs, not emoji.

## 12. Tests, typecheck, build

| Check | Command | Result |
| --- | --- | --- |
| Typecheck (ambient stubs) | `npx tsc --noEmit -p verification/tsconfig.stubs.json` | **exit 0** — clean, with `strict`, `noUnusedLocals`, `noUnusedParameters`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` |
| Test compile | `npx tsc -p tsconfig.test.json` | **exit 0** |
| Tests | `node scripts/run-tests.mjs` | **68 passed, 0 failed** (was 67 before this task) |
| Tailwind config | evaluated in Node; keyframes ↔ animations cross-checked | no orphans either direction |
| Typecheck (real config) | `npx tsc --noEmit -p tsconfig.json` | **BLOCKED** — cannot resolve `react`; `node_modules` does not exist |
| Lint | `npx eslint src` | **BLOCKED** — npm registry 403 |
| Build | `npm run build` | **BLOCKED** — npm registry 403, no `node_modules` |
| Browser verification | — | **BLOCKED** — no dev server can start |

### The one test I changed, and why

`credentials.test.ts` went to **13 passed / 1 failed** after the hero refactor.
The failing assertion greps `AuthGate.tsx` for the literal string
`"Sign-in is not configured"`; that screen moved into
`components/marketing/Hero.tsx`.

The brief says not to modify tests merely to make failures disappear, so this
needs stating plainly: **I did edit that test.** The assertion was a
source-location proxy for a behavioural invariant — "a deployment with no OAuth
credentials must not be shown a sign-in button that cannot work". The invariant
held; the proxy pointed at the wrong file. I widened the assertion to follow it
across the seam rather than let the refactor quietly disable it, and made it
strictly stronger than before:

- `AuthGate.tsx` must still reference `signInAvailable`, **and** must produce two
  distinct modes (`'sign-in'`, `'not-configured'`) — the original test did not
  check that the branch produced two different screens rather than one screen
  with a disabled button, which is the actual failure mode.
- `Hero.tsx` must branch on the mode **and** contain the explanatory copy.

I also added `Hero.tsx` to the no-web-storage scan in the same file, since it now
owns the sign-in screen and is where somebody caching a login hint would land.
That is 1 test added (15 in that file, up from 14) and 4 assertions where there
were 2.

## 13. Limitations — what is NOT verified

These are environment limits, not findings, and none of them were worked around
by weakening anything:

- **The application has never been rendered in a browser during this task.** The
  npm registry returns 403, so `node_modules` does not exist, Vite cannot start,
  and `npm run build` cannot run. Every visual claim in this document is
  reasoned from the source and the token system, **not observed**. Hover states,
  the tilt, the stagger timings, dark/light parity, and the actual appearance of
  the hero at any viewport width are all **unverified by inspection**.
- **No screenshots exist,** for the same reason. None are included rather than
  substituting mock-ups.
- **Tailwind has never compiled this CSS.** Utility names were verified by
  extracting them from source and checking each against the config's scales
  programmatically — which catches typos and missing scale entries, but is not a
  substitute for a build. The `bg-severity-*` bug in §9 is exactly the class of
  defect this gap hides, and it was found by reasoning, not by tooling.
- **ESLint has not run,** so any lint-only rule (exhaustive-deps in particular)
  is unchecked. `noUnusedLocals` / `noUnusedParameters` under the stub typecheck
  cover unused imports and locals, which is the subset that matters most here.
- **The ambient type stubs are hand-written** stand-ins for `@types/react` and
  the router's types. They were extended during this task, always to match the
  real signatures and never loosened to `any`, but a clean typecheck against
  stubs is weaker evidence than a clean typecheck against the real packages.
- **No runtime test of any component.** There is no DOM test environment; the 68
  tests are pure-module and source-invariant tests. Nothing here proves a
  component mounts.

To lift these: restore npm registry access, `npm install`, then
`npm run lint && npm run typecheck && npm run build && npm run dev`.
