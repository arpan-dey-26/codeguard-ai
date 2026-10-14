/**
 * A ~40-line test harness.
 *
 * Vitest cannot be installed here (the npm registry returns 403), so rather
 * than skip frontend testing entirely, the pure modules are compiled with
 * `tsc` and executed under Node against this. It gives `describe`, `test`,
 * `expect().toBe/toEqual/toContain/toThrow` and a summary — enough to test
 * behaviour, which is what these tests do.
 *
 * What it cannot do is render a component: that needs React and a DOM, neither
 * of which is installable. The report says so plainly rather than implying
 * component coverage that does not exist.
 */

let passed = 0
let failed = 0
const failures: string[] = []
let currentSuite = ''

export function describe(name: string, body: () => void): void {
  currentSuite = name
  body()
  currentSuite = ''
}

export function test(name: string, body: () => void): void {
  const label = currentSuite ? `${currentSuite} → ${name}` : name
  try {
    body()
    passed += 1
    console.log(`  PASS  ${label}`)
  } catch (error) {
    failed += 1
    const message = error instanceof Error ? error.message : String(error)
    failures.push(`${label}: ${message}`)
    console.log(`  FAIL  ${label}\n          ${message}`)
  }
}

export function expect<T>(actual: T) {
  return {
    toBe(expected: T): void {
      if (!Object.is(actual, expected)) {
        throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
      }
    },
    toEqual(expected: unknown): void {
      const a = JSON.stringify(actual)
      const b = JSON.stringify(expected)
      if (a !== b) throw new Error(`expected ${b}, got ${a}`)
    },
    toContain(needle: string): void {
      if (typeof actual !== 'string' || !actual.includes(needle)) {
        throw new Error(`expected ${JSON.stringify(actual)} to contain ${JSON.stringify(needle)}`)
      }
    },
    notToContain(needle: string): void {
      if (typeof actual === 'string' && actual.includes(needle)) {
        throw new Error(`expected ${JSON.stringify(actual)} not to contain ${JSON.stringify(needle)}`)
      }
    },
    toBeTruthy(): void {
      if (!actual) throw new Error(`expected a truthy value, got ${JSON.stringify(actual)}`)
    },
    toBeFalsy(): void {
      if (actual) throw new Error(`expected a falsy value, got ${JSON.stringify(actual)}`)
    },
  }
}

export function expectThrows(body: () => unknown, label = 'an error'): void {
  try {
    body()
  } catch {
    return
  }
  throw new Error(`expected ${label} to be thrown`)
}

export function summary(): number {
  console.log(`\n${passed} passed, ${failed} failed`)
  for (const failure of failures) console.log(`  - ${failure}`)
  return failed === 0 ? 0 : 1
}
