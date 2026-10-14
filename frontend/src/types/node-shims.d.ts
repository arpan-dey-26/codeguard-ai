/**
 * Minimal ambient declarations for the Node APIs the test files use.
 *
 * `@types/node` is the right answer and cannot be installed here — the npm
 * registry returns 403 in this environment. These declarations cover exactly
 * what the test harness touches and nothing else, so they cannot mask a
 * mistake in application code: no file under `src/` outside `__tests__/`
 * imports any of this.
 *
 * `package.json` already depends on `@types/node`; in CI, where npm works,
 * the real types take precedence and this file is redundant rather than
 * conflicting.
 */

declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string
  export function existsSync(path: string): boolean
}

declare module 'node:path' {
  export function join(...parts: string[]): string
  export function dirname(path: string): string
}

declare module 'node:url' {
  export function fileURLToPath(url: string | URL): string
}

declare const process: {
  exit(code?: number): never
  argv: string[]
  env: Record<string, string | undefined>
}
