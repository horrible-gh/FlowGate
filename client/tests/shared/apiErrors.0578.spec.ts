import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '../../shared/i18n'
import { normalizeApiError, resolveApiError, resolveApiErrorWithFallbackText } from '../../shared/apiErrors'

const t = i18n.global.t

describe('apiErrors 0578 common resolver', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })

  // T0010 §2.1: response.data.error.code (Git-style) wins over response.data.detail.code.
  it('prefers the Git-style error envelope over detail.code', () => {
    const input = {
      response: {
        status: 423,
        data: {
          error: { code: 'GROUP_AI_RUN_LOCKED', message: 'server text', run_id: 'aiv_1', run_live: false },
          detail: { code: 'validation_failed', errors: [] },
        },
      },
    }
    const before = JSON.stringify(input)
    expect(normalizeApiError(input).code).toBe('GROUP_AI_RUN_LOCKED')
    expect(JSON.stringify(input)).toBe(before)
  })

  it('falls back to detail.code when there is no error envelope', () => {
    const input = {
      response: { data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 1, max: 30 } } } },
    }
    expect(normalizeApiError(input).code).toBe('ai_repeat_count_out_of_range')
  })

  // §2.1: a string or array detail (plain HTTPException(detail=str), RequestValidationError) has no code.
  it('treats a string or array detail as no code', () => {
    expect(normalizeApiError({ response: { data: { detail: 'plain text' } } }).code).toBeNull()
    expect(
      normalizeApiError({ response: { data: { detail: [{ loc: ['body'], msg: 'x', type: 'y' }] } } }).code,
    ).toBeNull()
  })

  it('renders validation_failed for a known field/reason combo in ko/en/ja', () => {
    const input = {
      response: {
        data: { detail: { code: 'validation_failed', errors: [{ field: 'kind', reason: 'unsupported_kind' }] } },
      },
    }
    i18n.global.locale.value = 'ko'
    expect(resolveApiError(input, t)).toBe('지원하지 않는 CLI 종류입니다.')
    i18n.global.locale.value = 'en'
    expect(resolveApiError(input, t)).toBe('This CLI kind is not supported.')
    i18n.global.locale.value = 'ja'
    expect(resolveApiError(input, t)).toBe('このCLI種別はサポートされていません。')
  })

  it('renders the common input error for an unknown validation_failed combo', () => {
    const input = {
      response: {
        data: { detail: { code: 'validation_failed', errors: [{ field: 'name', reason: 'duplicate_name' }] } },
      },
    }
    i18n.global.locale.value = 'ko'
    expect(resolveApiError(input, t)).toBe('입력값을 확인해 주세요.')
  })

  it('renders ai_repeat_count_out_of_range with interpolated params in ko/en/ja', () => {
    const input = {
      response: { data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 1, max: 30 } } } },
    }
    i18n.global.locale.value = 'ko'
    expect(resolveApiError(input, t)).toBe('반복 횟수는 1에서 30 사이여야 합니다.')
    i18n.global.locale.value = 'en'
    expect(resolveApiError(input, t)).toBe('The repeat count must be between 1 and 30.')
    i18n.global.locale.value = 'ja'
    expect(resolveApiError(input, t)).toBe('繰り返し回数は1〜30の範囲で指定してください。')
  })

  it('falls back to the screen fallback when registered-code params are malformed', () => {
    const input = { response: { data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 'a' } } } } }
    i18n.global.locale.value = 'ko'
    expect(resolveApiError(input, t, 'main.work_plan_create_dialog.create_failed')).toBe(
      t('main.work_plan_create_dialog.create_failed'),
    )
  })

  // T0010 tasks 2.3/7.2: min/max present but the wrong scalar type (string, boolean) must not
  // be treated as valid bounds -- cleanParams lets strings/booleans through unchanged, so the
  // resolver itself must reject non-finite-number bounds before interpolating.
  it('falls back to the screen fallback when both bounds are present but wrong-typed', () => {
    const input = {
      response: { data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 'RAW', max: true } } } },
    }
    i18n.global.locale.value = 'ko'
    expect(resolveApiError(input, t, 'main.work_plan_create_dialog.create_failed')).toBe(
      t('main.work_plan_create_dialog.create_failed'),
    )
  })

  // §2.4: run_live drives the message, read directly from the code — not from localizeApiError's mutation.
  it('renders GROUP_AI_RUN_LOCKED from run_live independent of localizeApiError', () => {
    i18n.global.locale.value = 'ko'
    const running = { response: { status: 423, data: { error: { code: 'GROUP_AI_RUN_LOCKED', run_live: true } } } }
    const orphaned = { response: { status: 423, data: { error: { code: 'GROUP_AI_RUN_LOCKED', run_live: false } } } }
    const absent = { response: { status: 423, data: { error: { code: 'GROUP_AI_RUN_LOCKED' } } } }
    expect(resolveApiError(running, t)).toBe(t('main.review_action_bar.ai_running_hint'))
    expect(resolveApiError(orphaned, t)).toBe(t('main.review_action_bar.ai_lease_orphaned_hint'))
    expect(resolveApiError(absent, t)).toBe(t('main.review_action_bar.ai_running_hint'))
  })

  it('uses the common network message for axios network failures', () => {
    expect(resolveApiError({ isAxiosError: true, request: {} }, t)).toBe(t('error.network'))
  })

  it('falls back to the caller key, then common guidance, for unregistered codes', () => {
    const input = { response: { data: { error: { code: 'some_other_code' } } } }
    expect(resolveApiError(input, t, 'main.work_plan_create_dialog.create_failed')).toBe(
      t('main.work_plan_create_dialog.create_failed'),
    )
    expect(resolveApiError(input, t)).toBe(t('error.server'))
  })

  // extractApiErrorMessage's contract: fallback is an already-resolved string, not a key.
  it('resolveApiErrorWithFallbackText returns the literal fallback for unregistered codes', () => {
    const input = { response: { data: { detail: 'raw server text', error: { message: 'raw nested' } } } }
    expect(resolveApiErrorWithFallbackText(input, t, 'SCREEN FALLBACK')).toBe('SCREEN FALLBACK')
  })

  it('resolveApiErrorWithFallbackText still renders a registered code over the literal fallback', () => {
    i18n.global.locale.value = 'ko'
    const input = { response: { data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 1, max: 30 } } } } }
    expect(resolveApiErrorWithFallbackText(input, t, 'SCREEN FALLBACK')).toBe('반복 횟수는 1에서 30 사이여야 합니다.')
  })

  it('renders TR2 document-delete detail codes through the fallback-text resolver in ko/en/ja', () => {
    const recovery = { response: { status: 409, data: { detail: { code: 'TR_HISTORY_RECOVERY_REQUIRED' } } } }
    const active = { response: { status: 409, data: { detail: { code: 'TR2_ACTIVE_SOURCE_EFFECT' } } } }

    i18n.global.locale.value = 'ko'
    expect(resolveApiErrorWithFallbackText(recovery, t, 'SCREEN FALLBACK')).toBe(
      'Time Machine 소스/Git/원장 복구가 끝나지 않아 삭제 또는 일반 변경을 진행할 수 없습니다.',
    )
    expect(resolveApiErrorWithFallbackText(active, t, 'SCREEN FALLBACK')).toBe(
      '현재 소스에 활성 TR2 반영 효과가 남아 있어 문서를 삭제할 수 없습니다. 먼저 Time Machine으로 해당 반영을 취소하세요.',
    )

    i18n.global.locale.value = 'en'
    expect(resolveApiErrorWithFallbackText(recovery, t, 'SCREEN FALLBACK')).toBe(
      'Deletion or ordinary source changes are blocked until Time Machine source/Git/ledger recovery finishes.',
    )
    expect(resolveApiErrorWithFallbackText(active, t, 'SCREEN FALLBACK')).toBe(
      'This document cannot be deleted while an active TR2 source effect remains. Cancel that effect with Time Machine first.',
    )

    i18n.global.locale.value = 'ja'
    expect(resolveApiErrorWithFallbackText(recovery, t, 'SCREEN FALLBACK')).toBe(
      'Time Machine のソース/Git/台帳の復旧が完了するまで、削除や通常の変更は実行できません。',
    )
    expect(resolveApiErrorWithFallbackText(active, t, 'SCREEN FALLBACK')).toBe(
      '有効な TR2 のソース反映が残っているため、この文書は削除できません。先に Time Machine で反映を取り消してください。',
    )
  })

  it('never mutates the original input object', () => {
    const input = { response: { data: { error: { code: 'GROUP_AI_RUN_LOCKED', run_live: false } } } }
    const before = JSON.stringify(input)
    resolveApiError(input, t)
    expect(JSON.stringify(input)).toBe(before)
  })

  it('never throws and returns the common guidance for malformed input', () => {
    for (const input of [null, undefined, 'RAW STRING', [], 42]) {
      expect(() => resolveApiError(input, t)).not.toThrow()
      expect(resolveApiError(input, t)).toBe(t('error.server'))
    }
  })
})
