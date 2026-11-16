import { describe, expect, it } from 'vitest'
import { reviewCountKind, reviewCountValues, REVIEW_COUNT_HIDDEN, visibleReviewCountOptions } from '@shared/utils/reviewCountChoices'
import { AI_REVIEW_COUNT_HIDDEN } from '@/features/designer/inspector/features/settings/cardRolesModel'
import { settingsReviewCountKey } from '@/features/designer/inspector/features/settings/settingsModel'
import { cardSettingsChoiceOptions } from '@/features/designer/inspector/features/settings/cardSettingsModel'

describe('0027 shared review-count choices', () => {
  it('hides -1 unless it is the stored value, keeping the server order', () => {
    const options = [-1, 0, 1, 2, 3].map(value => ({ value: String(value) }))
    expect(visibleReviewCountOptions(options, '2').map(o => o.value)).toEqual(['0', '1', '2', '3'])
    expect(visibleReviewCountOptions(options, '-1').map(o => o.value)).toEqual(['-1', '0', '1', '2', '3'])
    expect(visibleReviewCountOptions(options, null).map(o => o.value)).toEqual(['0', '1', '2', '3'])
    expect(visibleReviewCountOptions([{ value: 0 }, { value: -1 }], -1).map(o => o.value)).toEqual([0, -1])
  })

  it('lists the choices and appends a stored value outside them', () => {
    expect(reviewCountValues([0, 1, 2, 3], null)).toEqual([0, 1, 2, 3])
    expect(reviewCountValues([0, 1, 2, 3], 2)).toEqual([0, 1, 2, 3])
    expect(reviewCountValues([0, 1, 2, 3], 5)).toEqual([0, 1, 2, 3, 5])
    expect(reviewCountValues([0, 1, 2, 3], -1)).toEqual([0, 1, 2, 3, -1])
    expect(reviewCountValues([-1, 0, 1], null)).toEqual([0, 1])
    expect(reviewCountValues([], null)).toEqual([])
  })

  it('names what a count means', () => {
    expect([-1, 0, 1, 7].map(reviewCountKind)).toEqual(['until_pass', 'none', 'times', 'times'])
  })

  it('is the rule the Inspector uses: same hidden set, same label keys, same shown options', () => {
    expect(AI_REVIEW_COUNT_HIDDEN).toBe(REVIEW_COUNT_HIDDEN)
    expect([-1, 0, 2].map(settingsReviewCountKey)).toEqual(['count.until_pass', 'count.none', 'count.times'])
    const options = cardSettingsChoiceOptions('review_count', [-1, 0, 1, 2, 3], 5)
    expect(visibleReviewCountOptions(options, '5').map(o => o.value)).toEqual(['0', '1', '2', '3', '5'])
    expect(visibleReviewCountOptions(options, '-1').map(o => o.value)).toEqual(['-1', '0', '1', '2', '3', '5'])
  })
})
