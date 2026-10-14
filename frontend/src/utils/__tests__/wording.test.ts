/**
 * The wording rules, asserted.
 *
 * Phase 4's hardest requirement is not technical: it is that the UI must never
 * overstate what the platform measured. Those rules are easy to state in a
 * review and easy to lose in a later edit, so they are tests.
 */

import { describe, expect, summary, test } from './harness'
import {
  ANCHOR_BUCKET,
  NOT_REVIEWED,
  RUN_ERROR,
  RUN_STATUS,
  SKIP_REASON,
  VALIDATION,
  publicationOf,
  runStatusOf,
} from '../status'
import { SEVERITY, SEVERITY_ORDER } from '../severity'

const FORBIDDEN = [
  'false positive',
  'false-positive',
  'accuracy',
  'proven',
  'confirmed bug',
  'guaranteed',
]

function allWording(): string[] {
  const strings: string[] = []
  for (const value of Object.values(RUN_STATUS)) {
    strings.push(value.label, value.description)
  }
  strings.push(NOT_REVIEWED.label, NOT_REVIEWED.description)
  for (const value of Object.values(VALIDATION)) {
    strings.push(value.label, value.description)
  }
  strings.push(...Object.values(ANCHOR_BUCKET))
  strings.push(...Object.values(SKIP_REASON))
  strings.push(...Object.values(RUN_ERROR))
  return strings
}

describe('no semantic overclaiming', () => {
  test('no status wording claims accuracy, proof, or a false positive', () => {
    for (const text of allWording()) {
      const lower = text.toLowerCase()
      for (const banned of FORBIDDEN) {
        // The one legitimate use is an explicit denial, which always contains
        // "not" — e.g. "This is not a judgement about whether the issue is real".
        const claims = lower.includes(banned) && !lower.includes('not ')
        expect(claims).toBeFalsy()
      }
    }
  })

  test('the rejected-claim wording says what a rejection is and is not', () => {
    const description = VALIDATION.rejected.description.toLowerCase()
    expect(description).toContain('structural')
    expect(description).toContain('not')
  })

  test('the validated wording does not claim the issue is real', () => {
    const description = VALIDATION.validated.description.toLowerCase()
    expect(description).toContain('not that the issue has been independently proven')
  })

  test('the label is "Rejected claim", never "False positive"', () => {
    expect(VALIDATION.rejected.label).toBe('Rejected claim')
  })
})

describe('run outcomes are never collapsed', () => {
  test('partial success is not labelled as a pass', () => {
    expect(RUN_STATUS.partial_success.label).toBe('Completed, partial')
    expect(RUN_STATUS.partial_success.label).notToContain('Passed')
  })

  test('partial success explains what did and did not happen', () => {
    const description = RUN_STATUS.partial_success.description
    expect(description).toContain('stored')
    expect(description).toContain('not all')
  })

  test('a superseded run is described as overtaken, not as a result', () => {
    expect(RUN_STATUS.superseded.label).toBe('Superseded')
    expect(RUN_STATUS.superseded.description).toContain('newer commit')
  })

  test('a pull request with no review shows "Not reviewed", not a pass', () => {
    const presentation = runStatusOf(null)
    expect(presentation.label).toBe('Not reviewed')
    expect(presentation.description).toContain('not a pass')
  })

  test('undefined is treated the same as null', () => {
    expect(runStatusOf(undefined).label).toBe('Not reviewed')
  })

  test('every run status has a distinct label', () => {
    const labels = Object.values(RUN_STATUS).map((value) => value.label)
    expect(new Set(labels).size).toBe(labels.length)
  })
})

describe('publication reflects stored state only', () => {
  test('not published explains the possible reasons without guessing one', () => {
    const description = publicationOf(false).description
    expect(description).toContain('No GitHub comment is recorded')
    expect(description).toContain('may')
  })

  test('published claims only that a comment exists', () => {
    expect(publicationOf(true).description).toContain('exists on GitHub')
  })
})

describe('accessibility: never colour alone', () => {
  test('every severity carries a glyph and a text label', () => {
    for (const severity of SEVERITY_ORDER) {
      const presentation = SEVERITY[severity]
      expect(presentation.glyph.length > 0).toBeTruthy()
      expect(presentation.label.length > 0).toBeTruthy()
      expect(presentation.className).toContain('text-severity-')
    }
  })

  test('every run status carries a glyph and a text label', () => {
    for (const presentation of Object.values(RUN_STATUS)) {
      expect(presentation.glyph.length > 0).toBeTruthy()
      expect(presentation.label.length > 0).toBeTruthy()
    }
  })

  test('severity glyphs are distinguishable from one another', () => {
    const glyphs = SEVERITY_ORDER.map((severity) => SEVERITY[severity].glyph)
    expect(new Set(glyphs).size).toBe(glyphs.length)
  })
})

describe('anchor wording', () => {
  test('anchor buckets describe resolution, not correctness', () => {
    expect(ANCHOR_BUCKET.exact).toBe('Exact line')
    expect(ANCHOR_BUCKET.off_by_2).toContain('Resolved')
    expect(ANCHOR_BUCKET.unresolved).toContain('No resolved line')
  })

  test('no anchor bucket is phrased as an error rate', () => {
    for (const label of Object.values(ANCHOR_BUCKET)) {
      expect(label.toLowerCase()).notToContain('error')
      expect(label.toLowerCase()).notToContain('wrong')
    }
  })
})

process.exit(summary())
