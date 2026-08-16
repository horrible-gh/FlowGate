import { describe, expect, it } from 'vitest'
import i18n from '../../shared/i18n'
import { extractApiErrorMessage, localizeApiError } from '../../shared/api'

describe('AI-running 423 API error localization (0378)', () => {
  it('extracts detail, structured error message, then fallback in order', () => {
    expect(extractApiErrorMessage(
      { response: { data: { detail: 'detail', error: { message: 'nested' } } } },
      'fallback',
    )).toBe('detail')
    expect(extractApiErrorMessage(
      { response: { data: { error: { message: 'nested' } } } },
      'fallback',
    )).toBe('nested')
    expect(extractApiErrorMessage(new Error('boom'), 'fallback')).toBe('fallback')
  })

  it('localizes both legacy detail and the structured lease error', () => {
    i18n.global.locale.value = 'ko'
    const error = {
      response: {
        status: 423,
        data: {
          error: {
            code: 'GROUP_AI_RUN_LOCKED',
            message: 'server text',
            group_id: 'flowgate.default.0378',
            run_id: 'aiv_1',
          },
        },
      },
    } as any

    expect(localizeApiError(error)).toBe(error)
    const localized = '이 그룹에서 AI 실행이 진행 중입니다. 끝날 때까지 동작이 잠깁니다.'
    expect(error.response.data.detail).toBe(localized)
    expect(error.response.data.error.message).toBe(localized)
    expect(error.response.data.error.code).toBe('GROUP_AI_RUN_LOCKED')
    expect(error.response.data.error.run_id).toBe('aiv_1')
  })

  it('normalizes non-object 423 payloads and leaves other statuses unchanged', () => {
    i18n.global.locale.value = 'en'
    const locked = { response: { status: 423, data: 'locked' } } as any
    localizeApiError(locked)
    const detail = 'An AI run is in progress for this group. Actions are locked until it finishes.'
    expect(locked.response.data).toEqual({
      detail,
      error: { code: 'GROUP_AI_RUN_LOCKED', message: detail },
    })

    const conflict = { response: { status: 409, data: { detail: 'conflict' } } } as any
    localizeApiError(conflict)
    expect(conflict.response.data.detail).toBe('conflict')
  })
})