import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

// 0490 T0007 §6: proves ContinuousWorkDialog's restart-count select reflects an injected
// executionPolicy prop (§3.4), and falls back to the historical 0..3 ceiling when the prop is
// omitted — the same fallback contract every other repeat-count select uses (§3.2).
//
// rev4 반려("각종"이란 말이 무슨 뜻인지 모르니? 한군데만 처리하면 끝나는거냐): 이 파일 안에는
// 반복 횟수 select 가 두 개다 — 위 재시작 횟수(RESTART_COUNT_OPTIONS, .cwd-restart-select)와
// [검수] 탭의 단계별 검수 횟수(REVIEW_COUNT_OPTIONS, .cwd-review-count-select). 앞선 리비전은
// 앞의 것만 executionPolicy 를 반영하도록 고치고 뒤의 것은 고정 배열([-1,0,1,2,3])로 남겨
// 두었다 — 아래 두 번째 describe 블록이 그 자리도 같은 SSOT(repeatCountChoices)를 쓰는지
// 직접 증명한다.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
}))

const items = [
  {
    id: 1, item_seq: 1, type: 'D', label: 'Step', status: 'pending',
    provider_id: null, provider_display_name: null, provider_registered: null,
  },
]

function response() {
  return {
    data: {
      doc_id: 'flowgate.default.0490.0001-B',
      doc_class: 'B',
      decided: true,
      items: JSON.parse(JSON.stringify(items)),
      head: items[0],
    },
  }
}

function mountDialog(executionPolicy?: Record<string, number>) {
  return mount(ContinuousWorkDialog, {
    props: {
      visible: true,
      docRef: 'flowgate.default.0490.0001-B',
      providers: [{ id: 'default', name: 'Default Provider' }],
      selectedProvider: 'default',
      ...(executionPolicy ? { executionPolicy } : {}),
    },
    global: { plugins: [i18n] },
  })
}

function restartOptionValues(): string[] {
  return Array.from(document.querySelectorAll('.cwd-restart-select option')).map(
    (node) => (node as HTMLOptionElement).value,
  )
}

beforeEach(() => {
  getRequest.mockReset().mockResolvedValue(response())
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ContinuousWorkDialog restart-count select follows the injected execution policy (0490 T0007)', () => {
  it('reflects an injected ceiling of 5', async () => {
    mountDialog({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 })
    await flushPromises()
    expect(restartOptionValues()).toEqual(['-1', '0', '1', '2', '3', '4', '5'])
  })

  it('falls back to the historical 0..3 ceiling when the prop is omitted', async () => {
    mountDialog()
    await flushPromises()
    expect(restartOptionValues()).toEqual(['-1', '0', '1', '2', '3'])
  })

  it('keeps the restart DEFAULT at 1 regardless of the ceiling (structural min already 1, §3.5 — no clamp needed)', async () => {
    mountDialog({ repeat_count_max: 10, repeat_count_min: 1, repeat_count_hard_max: 30 })
    await flushPromises()
    const select = document.querySelector('.cwd-restart-select') as HTMLSelectElement
    expect(select.value).toBe('1')
  })
})

// rev4 두 번째 select: [검수] 탭의 단계별 검수 횟수(REVIEW_COUNT_OPTIONS). 이 탭에 행이 뜨려면
// executionSteps 가 비어 있지 않아야 하므로, 위의 단일 'D' 스텝 픽스처 대신
// ContinuousWorkDialog.review.0414.spec.ts 와 같은 N~TS 시퀀스를 재사용한다 — 그 스펙이 이미
// 증명한 대로, [자동승인] 기본값 그대로 열어도(모드 전환·타겟 확장 없이) TR·TS 두 워커 칸이
// 뜬다.
const REVIEW_ROOT = 'flowgate.default.0490.0003-R'
const REVIEW_PROVIDERS = [{ id: 'aip_codex', name: 'Codex GPT' }]

function reviewSeqResponse() {
  return {
    data: {
      doc_id: REVIEW_ROOT,
      doc_class: 'R',
      decided: true,
      items: [
        { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
        { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
        { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
        { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 5, item_seq: 5, type: 'TS', label: '테스트시나리오', status: 'pending' },
        { id: 6, item_seq: 6, type: 'TSR', label: '테스트레포트', status: 'pending' },
      ],
      head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

function mountReviewDialog(executionPolicy?: Record<string, number>) {
  return mount(ContinuousWorkDialog, {
    props: {
      visible: true,
      docRef: REVIEW_ROOT,
      providers: REVIEW_PROVIDERS,
      selectedProvider: 'aip_codex',
      ...(executionPolicy ? { executionPolicy } : {}),
    },
    global: { plugins: [i18n] },
  })
}

function tabs(): HTMLButtonElement[] {
  return [...document.querySelectorAll('.cwd-tab')] as HTMLButtonElement[]
}

async function openReviewTab() {
  tabs()[3].click()
  await flushPromises()
}

function reviewCountOptionValues(rowIndex = 0): string[] {
  const selects = [...document.querySelectorAll('.cwd-review-count-select')] as HTMLSelectElement[]
  return [...selects[rowIndex].querySelectorAll('option')].map(option => option.value)
}

describe('ContinuousWorkDialog per-step review-count select follows the injected execution policy (0490 T0007 rev4)', () => {
  beforeEach(() => {
    getRequest.mockReset().mockResolvedValue(reviewSeqResponse())
  })

  it('reflects an injected ceiling of 5 in the [검수] tab, not just the restart-count select', async () => {
    const wrapper = mountReviewDialog({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 })
    await flushPromises()
    await openReviewTab()
    expect(document.querySelectorAll('.cwd-review-count-select').length).toBeGreaterThan(0)
    expect(reviewCountOptionValues()).toEqual(['-1', '0', '1', '2', '3', '4', '5'])
    wrapper.unmount()
  })

  it('falls back to the historical -1..3 ceiling when the prop is omitted', async () => {
    const wrapper = mountReviewDialog()
    await flushPromises()
    await openReviewTab()
    expect(reviewCountOptionValues()).toEqual(['-1', '0', '1', '2', '3'])
    wrapper.unmount()
  })
})
