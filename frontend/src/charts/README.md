# Charts

Empty in Phase 0, and deliberately so.

Recharts is **not** installed yet. The dependency rule for this project is
"add it when code uses it" — a charting library sitting unused in
`package.json` is a bundle-size and audit-surface cost with no benefit.

From Phase 5 this directory holds:

- `theme.ts` — the series palette, bound to the same CSS custom properties as
  the rest of the UI so charts stay correct in both themes;
- `ChartFrame.tsx` — the shared accessibility wrapper: `<title>`/`<desc>`,
  a keyboard-reachable tooltip, and a "view as table" fallback;
- one component per chart from architecture.md §18.3.
