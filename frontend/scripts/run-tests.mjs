/**
 * Compile-and-run test entry point.
 *
 * Vitest is the tool this project would normally use; it cannot be installed
 * in the current environment (the npm registry returns 403), so the pure
 * modules are compiled by `tsconfig.test.json` and executed under Node.
 *
 * Two rewrites bridge Vite's resolver and Node's: extensionless relative
 * imports get a `.js` suffix, and the `@/` alias becomes a relative path.
 * A bundler performs the same two rewrites; neither changes behaviour.
 *
 * Replace this file with `vitest` the moment the registry is reachable — the
 * tests themselves need no changes beyond importing from `vitest` instead of
 * the local harness.
 */
import { spawnSync } from 'node:child_process'
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const OUT = '.test-build'

function walk(dir) {
  const entries = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) entries.push(...walk(full))
    else if (full.endsWith('.js')) entries.push(full)
  }
  return entries
}

for (const file of walk(OUT)) {
  const depth = relative(OUT, file).split(sep).length - 1
  const prefix = depth === 0 ? './' : '../'.repeat(depth)
  const rewritten = readFileSync(file, 'utf8').replace(
    /(from\s+|import\()'([^']+)'/g,
    (_match, keyword, spec) => {
      let target = spec
      if (target.startsWith('@/')) target = prefix + target.slice(2)
      if (target.startsWith('.') && !/\.(js|json)$/.test(target)) target += '.js'
      return `${keyword}'${target}'`
    },
  )
  writeFileSync(file, rewritten)
}

let failed = 0
for (const file of walk(OUT).filter((name) => name.endsWith('.test.js'))) {
  process.stdout.write(`\n--- ${relative(OUT, file)} ---\n`)
  const result = spawnSync('node', [file], { stdio: 'inherit' })
  if (result.status !== 0) failed += 1
}
process.exit(failed === 0 ? 0 : 1)
