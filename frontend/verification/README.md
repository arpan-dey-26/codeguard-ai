# Frontend verification without npm

**You almost certainly do not want this directory.** If `npm install` works on
your machine, use the real thing:

```bash
cd frontend
npm install
npm run typecheck   # tsc against the real @types/react and react-router-dom
npm run test
npm run build
```

That is the authoritative check, and it is what CI runs.

## What this is for

This project was built and verified in an environment where the npm registry
returns **403** — React, react-router-dom, Vite and their type packages could
not be installed at all. Rather than declare the frontend unverifiable, two
things were built:

- `types/` — hand-written ambient declarations for the parts of `react`,
  `react-dom/client` and `react-router-dom` this application actually uses.
- `tsconfig.stubs.json` — the real `tsconfig.json`'s compiler options, pointed
  at those stubs.

```bash
cd frontend
npx tsc --noEmit -p verification/tsconfig.stubs.json
```

`../scripts/run-tests.mjs` (invoked by `npm run test`) is a separate,
Vitest-free runner over the pure-logic modules, and it runs under plain Node
with no registry access at all.

## What this does and does not prove

**It does** catch a wrong prop, a wrong hook argument, a missing field, a bad
union, an unhandled `null`, and every error the strict flags are for — in
*application* code. Those flags are copied verbatim from the real
`tsconfig.json`, including `noUncheckedIndexedAccess` and
`exactOptionalPropertyTypes`.

**It does not** typecheck against React's real declarations. The stubs are an
approximation written by reading the API; a mistake in them is invisible here
and would surface the moment you run `npm run typecheck`. It also proves
nothing about rendering — no component in this project has ever been mounted in
a browser or a test renderer.

`docs/final-verification-matrix.md` records this distinction rather than
blurring it. A stub is not a framework, and a typecheck is not a render.

## Keeping the stubs honest

If you add a React or react-router API the stubs do not declare, `tsc` fails
here with `has no exported member`. Add the declaration, copying the real
signature from the published types — do not loosen it to `unknown` or `any` to
make the error go away, because a stub that accepts anything checks nothing.
