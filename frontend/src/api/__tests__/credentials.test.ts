/**
 * How the client handles credentials.
 *
 * Two properties, both of which a plausible "cleanup" would break:
 *
 * 1. **No token is ever held in JavaScript.** The session is an HttpOnly
 *    cookie. If a future change starts reading it, or starts stashing anything
 *    in `localStorage`, the XSS blast radius goes from "act while the page is
 *    open" to "copy the credential and use it later".
 * 2. **Unsafe methods carry the CSRF header; safe ones do not.** Sending it on
 *    a GET is harmless but reveals the token in more places; omitting it on a
 *    POST breaks every write with a 403 that looks like a permissions bug.
 */

import { describe, expect, summary, test } from '../../utils/__tests__/harness'
import { CSRF_HEADER } from '../client'

/* -- source-level invariants ------------------------------------------------
 * Read from the file rather than exercised at runtime: the failure being
 * guarded against is somebody *adding* a line, and only a scan sees that.
 */

import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

/**
 * Resolve `src/` from wherever this file is executing.
 *
 * The compiled copy runs from `.test-build/api/__tests__/`, so a fixed number
 * of `..` hops lands in the build output rather than the source — and the
 * runner writes a `package.json` into `.test-build`, so anchoring on that
 * finds the wrong root. Anchoring on a TypeScript source file that only exists
 * under the real `src/` is unambiguous, and it makes the failure mode loud: if
 * the layout changes, this throws rather than silently reading compiled
 * JavaScript and passing for the wrong reason.
 */
function sourceRoot(): string {
  let dir = dirname(fileURLToPath(import.meta.url))
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(join(dir, 'src', 'api', 'client.ts'))) return join(dir, 'src')
    dir = dirname(dir)
  }
  throw new Error('could not locate src/ from ' + import.meta.url)
}

const srcDir = sourceRoot()

function read(relative: string): string {
  return readFileSync(join(srcDir, relative), 'utf8')
}

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
}

describe('no credential is stored in the browser', () => {
  // `Hero.tsx` joined this list in v0.6.2: it now owns the sign-in screen, so
  // it is a file where somebody "helpfully" caching a login hint would land.
  const files = [
    'api/client.ts',
    'hooks/useAuth.tsx',
    'components/auth/AuthGate.tsx',
    'components/marketing/Hero.tsx',
  ]

  for (const file of files) {
    test(`${file} uses no web storage`, () => {
      const code = stripComments(read(file))
      expect(code.includes('localStorage')).toBe(false)
      expect(code.includes('sessionStorage')).toBe(false)
      expect(code.includes('indexedDB')).toBe(false)
    })
  }

  test('the session cookie is never read by JavaScript', () => {
    // Reading it would require it not to be HttpOnly, which is the entire
    // protection. The CSRF cookie IS read — deliberately — and it is a
    // different cookie carrying a different, non-credential value.
    const code = stripComments(read('api/client.ts'))
    expect(code.includes('codeguard_session_csrf')).toBe(true)
    expect(/['"]codeguard_session['"]/.test(code)).toBe(false)
  })

  test('no auth file holds a token in state', () => {
    const code = stripComments(read('hooks/useAuth.tsx'))
    expect(/useState[^)]*token/i.test(code)).toBe(false)
    expect(code.includes('access_token')).toBe(false)
  })
})

describe('credentials are sent by the browser, not by us', () => {
  test('every request opts into cookies', () => {
    const code = stripComments(read('api/client.ts'))
    expect(code).toContain("credentials: 'include'")
  })

  test('there is no Authorization header', () => {
    // A bearer header would mean a token in JavaScript, which is the thing
    // this design exists to avoid.
    const code = stripComments(read('api/client.ts'))
    expect(code.includes('Authorization')).toBe(false)
  })
})

describe('CSRF', () => {
  test('the header name matches the backend constant', () => {
    // `app.auth.cookies.CSRF_HEADER`. A mismatch here fails every write with a
    // 403 and no obvious cause.
    expect(CSRF_HEADER).toBe('X-CSRF-Token')
  })

  test('the token is attached to unsafe methods only', () => {
    const code = stripComments(read('api/client.ts'))
    expect(code).toContain('SAFE_METHODS')
    expect(code).toContain('GET')
    expect(code).toContain('HEAD')
    expect(code).toContain('OPTIONS')
  })

  test('logout is a POST, so it cannot be triggered by an image tag', () => {
    const code = stripComments(read('api/endpoints.ts'))
    const logoutCall = code.slice(code.indexOf('export const logout'))
    expect(logoutCall.slice(0, 200)).toContain("method: 'POST'")
  })
})

describe('a 401 ends the session locally', () => {
  test('the client notifies a single subscriber', () => {
    const code = stripComments(read('api/client.ts'))
    expect(code).toContain('response.status === 401')
    expect(code).toContain('onUnauthorized')
  })

  test('the auth provider subscribes and drops to anonymous', () => {
    const code = stripComments(read('hooks/useAuth.tsx'))
    expect(code).toContain('setUnauthorizedHandler')
    expect(code).toContain("status: 'anonymous'")
  })
})

describe('the gate is presentation, not enforcement', () => {
  test('it renders three distinct states rather than a boolean', () => {
    const code = stripComments(read('components/auth/AuthGate.tsx'))
    expect(code).toContain("state.status === 'loading'")
    expect(code).toContain("state.status === 'anonymous'")
  })

  test('it distinguishes "not signed in" from "sign-in unavailable"', () => {
    // A deployment with no OAuth credentials cannot be fixed by the visitor.
    // Showing them a sign-in button that will never work is worse than saying
    // what is actually wrong.
    //
    // **Widened in v0.6.2.** The gate used to render both screens itself, so
    // one grep over `AuthGate.tsx` saw the whole distinction. The screens moved
    // into `components/marketing/Hero.tsx` when they were redesigned; the
    // invariant did not move, so the assertion follows it across the seam
    // instead of the seam quietly disabling the assertion. Both halves are
    // checked, because either half alone can regress: the gate can stop
    // branching on `signInAvailable`, or the screen can stop saying why the
    // button is missing.
    const gate = read('components/auth/AuthGate.tsx')
    expect(gate).toContain('signInAvailable')
    // The branch must produce two different screens, not one screen with a
    // disabled button — that is the failure this whole test exists to catch.
    expect(gate).toContain("'sign-in'")
    expect(gate).toContain("'not-configured'")

    const screen = read('components/marketing/Hero.tsx')
    expect(screen).toContain("mode === 'sign-in'")
    expect(screen).toContain('Sign-in is not configured')
  })
})

process.exit(summary())
