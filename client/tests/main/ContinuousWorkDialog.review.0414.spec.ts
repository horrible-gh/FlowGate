import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

import i18n from '@shared/i18n'
import ko from '@shared/i18n/ko'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

// 0414 R0001 "무인체인에 [검수] ... 몇회 할 것인지 지정할 때가 됐다" → T0012.
// 승인 시안 45z739t7(= f8ri1s6k v7)의 네 번째 [검수] 탭을 제품으로 옮긴 결과를 고정한다.
// 여기서 지키는 것은 네 가지다.
//   1) 탭이 네 번째 자리에 있고, 시안이 지운 공용 [기본 횟수]·[기본 검수자] 카드는 없다.
//   2) 검수 행은 [프로바이더]·[전달멘트] 와 똑같은 executionSteps 다.
//   3) 횟수 옵션의 DOM 순서는 정확히 -1, 0, 1, 2, 3 이고 모든 행의 기본값은 0 이다
//      (시안의 예시 선택값 -1,0,1,2 는 예시일 뿐 제품 기본값이 아니다).
//   4) confirm payload 의 두 맵이 실제 item_seq 로 짝지어지고, 0 인 행은 둘 다에서 빠진다.

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
  putRequest,
}))

const ROOT = 'flowgate.default.0414.0001-R'

// N/NR done; T(head) / TR / TS / TSR pending.
// 기본 실행 방식 [자동승인] 에서 T@3 은 서버가 처리하고, 0388 NR0003 에 따라 기본 목표는
// 짝인 TSR 한 칸 앞 TS@5 에서 멈춘다 → 워커 칸은 TR@4 · TS@5 둘.
function seqResponse() {
  return {
    data: {
      doc_id: ROOT,
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

// 검수자 기본값은 "프로젝트 유효 프로바이더 체인의 첫 항목"(P0007)이다. 실행 프로바이더를
// 일부러 첫 항목이 아닌 것으로 골라 둬야 그 둘이 섞이지 않았음을 증명할 수 있다.
const PROVIDERS = [
  { id: 'aip_codex', name: 'Codex GPT' },
  { id: 'aip_sonnet', name: 'Claude Sonnet' },
  { id: 'aip_opus', name: 'Claude Opus' },
]

const T = (key: string, named?: Record<string, unknown>) =>
  (named ? i18n.global.t(key, named) : i18n.global.t(key)) as string

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(ContinuousWorkDialog, {
    props: {
      visible: true,
      docRef: ROOT,
      providers: PROVIDERS,
      selectedProvider: 'aip_opus',
      ...props,
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

function activeTabIndex(): number {
  return tabs().findIndex(tab => tab.classList.contains('cwd-tab--active'))
}

/**
 * N/T 실행 방식 라디오는 [기본 설정] 탭 안에만 그려진다. 다른 탭이 열려 있으면 잠깐
 * 되돌아갔다가 원래 탭으로 복귀한다 — 화면에서 사람이 하는 동작 그대로다.
 */
async function switchInstructionMode(index: 0 | 1) {
  const previous = activeTabIndex()
  if (previous !== 0) {
    tabs()[0].click()
    await flushPromises()
  }
  const radio = document.querySelectorAll('.cwd-mode input')[index] as HTMLInputElement
  radio.checked = true
  radio.dispatchEvent(new Event('change'))
  await flushPromises()
  if (previous > 0) {
    tabs()[previous].click()
    await flushPromises()
  }
}

const switchToAiDirect = () => switchInstructionMode(1)
const switchToAutoApproved = () => switchInstructionMode(0)

/** 기본 목표(TS@5)를 시퀀스 끝(TSR@6)까지 넓혀 워커 칸을 하나 더 만든다. */
async function extendTargetToLastStep() {
  ;(document.querySelectorAll('.wsp-step')[5] as HTMLButtonElement).click()
  await flushPromises()
}

function reviewRows(): HTMLElement[] {
  return [...document.querySelectorAll('.cwd-review-row')] as HTMLElement[]
}

function countSelects(): HTMLSelectElement[] {
  return [...document.querySelectorAll('.cwd-review-count-select')] as HTMLSelectElement[]
}

function reviewerSelects(): HTMLSelectElement[] {
  return [...document.querySelectorAll('.cwd-review-reviewer-select')] as HTMLSelectElement[]
}

async function setSelect(select: HTMLSelectElement, value: string) {
  select.value = value
  select.dispatchEvent(new Event('change'))
  await flushPromises()
}

async function proceed() {
  ;([...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement).click()
  await flushPromises()
}

beforeEach(() => {
  putRequest.mockReset()
  putRequest.mockResolvedValue({ data: { ok: true } })
  postRequest.mockReset()
  postRequest.mockResolvedValue({ data: {} })
  getRequest.mockReset()
  getRequest.mockResolvedValue(seqResponse())
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ContinuousWorkDialog [검수] 탭 화면 (0414 T0012 작업 1)', () => {
  it('네 번째 탭으로 붙고, 앞의 세 탭 순서와 문구는 그대로다', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(tabs().map(tab => tab.textContent?.trim())).toEqual([
      T('main.continuous_work.tab_basic'),
      T('main.continuous_work.tab_provider'),
      T('main.continuous_work.tab_message'),
      T('main.continuous_work.tab_review'),
    ])

    // 처음 열었을 때 활성 탭은 여전히 [기본 설정] 이다.
    expect(tabs()[0].classList.contains('cwd-tab--active')).toBe(true)
    expect(tabs()[3].classList.contains('cwd-tab--active')).toBe(false)

    await openReviewTab()
    expect(tabs()[3].classList.contains('cwd-tab--active')).toBe(true)
    expect(tabs()[0].classList.contains('cwd-tab--active')).toBe(false)

    wrapper.unmount()
  })

  it('한국어 승인 문안을 그대로 쓴다', () => {
    // 스위트는 기본 로케일(en)로 돌기 때문에 화면 텍스트만으로는 승인 문안을 못 지킨다.
    const block = (ko as any).main.continuous_work
    expect(block.tab_review).toBe('검수')
    expect(block.review_intro).toBe('각 단계마다 검수 횟수와 검수자를 지정합니다.')
    // 0414 M0020: 지정 횟수는 [검수+수정] 짝의 횟수다. 지적이 나온 라운드마다 수정이 따라
    // 붙고, 마지막 수정까지 끝나면 다음 단계로 넘어간다 — 안내문도 그렇게 말해야 한다.
    expect(block.review_legend).toBe(
      '-1: 통과할 때까지 · 0: 안 함(기본값) · 1~3: 지정 횟수만큼 검수하고 지적마다 수정 · 통과하면 즉시 종료',
    )
    expect(block.review_legend).toContain('지적마다 수정')
    expect(block.review_count_aria).toBe('실행단계{n} 검수 횟수')
    expect(block.review_reviewer_aria).toBe('실행단계{n} 검수자')
  })

  it('안내문과 단계별 행만 두고, 공용 [기본 횟수]·[기본 검수자] 카드는 없다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    const intro = document.querySelector('.cwd-review-intro') as HTMLElement
    expect(intro).not.toBeNull()
    expect(intro.textContent).toContain(T('main.continuous_work.review_intro'))
    expect(intro.textContent).toContain(T('main.continuous_work.review_legend'))

    // TR0005 반려 1 이 지운 공용 카드 — [프로바이더]/[전달멘트] 탭이 쓰는 헤더 행
    // (.cwd-provider-row)이 이 탭에는 하나도 없어야 한다. 대조군: 같은 셀렉터가
    // [프로바이더] 탭에서는 실제로 잡힌다(아래).
    expect(document.querySelectorAll('.cwd-provider-row')).toHaveLength(0)
    expect(reviewRows().length).toBeGreaterThan(0)

    tabs()[1].click()
    await flushPromises()
    expect(document.querySelectorAll('.cwd-provider-row')).toHaveLength(1)

    wrapper.unmount()
  })

  it('행 집합과 실행단계 번호가 [프로바이더]·[전달멘트] 탭과 완전히 같다', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    tabs()[1].click()
    await flushPromises()
    const providerRowCount = document.querySelectorAll('.cwd-override-row').length

    await openReviewTab()
    const rows = reviewRows()
    // [자동승인] 기본값 + 0388 기본 목표(TS@5) → 워커 칸은 TR@4 · TS@5 둘.
    expect(rows).toHaveLength(2)
    expect(rows).toHaveLength(providerRowCount)
    expect(rows.map(row => row.querySelector('.cwd-override-step-no')?.textContent?.trim()))
      .toEqual([
        T('main.continuous_work.step_no_label', { n: 1 }),
        T('main.continuous_work.step_no_label', { n: 2 }),
      ])
    expect(rows.map(row => row.querySelector('.cwd-override-badge')?.textContent?.trim()))
      .toEqual(['TR', 'TS'])
    expect(rows.map(row => row.querySelector('.cwd-override-label')?.textContent?.trim()))
      .toEqual(['작업레포트', '테스트시나리오'])

    // 실행 방식을 [지시서 작성 후 진행] 으로 바꾸면 T@3 이 워커 칸이 되어 행이 하나 는다.
    await switchToAiDirect()
    expect(reviewRows().map(row => row.querySelector('.cwd-override-badge')?.textContent?.trim()))
      .toEqual(['T', 'TR', 'TS'])

    wrapper.unmount()
  })

  it('횟수 옵션의 DOM 순서는 정확히 -1, 0, 1, 2, 3 이고 모든 행의 기본값은 0 이다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    await extendTargetToLastStep()
    await openReviewTab()

    const selects = countSelects()
    expect(selects).toHaveLength(4)
    for (const select of selects) {
      // DOM 순서 그대로 읽는다 — 0 을 앞으로 당기거나 값을 문자열 의미값으로 바꾸면 실패한다.
      expect([...select.querySelectorAll('option')].map(option => option.value))
        .toEqual(['-1', '0', '1', '2', '3'])
      expect([...select.querySelectorAll('option')].map(option => option.textContent?.trim()))
        .toEqual(['-1', '0', '1', '2', '3'])
      // 시안의 예시 선택값 -1,0,1,2 는 제품 기본값이 아니다: 모든 행이 0(안 함)에서 시작한다.
      expect(select.value).toBe('0')
    }

    wrapper.unmount()
  })

  it('검수자 셀렉트는 프로바이더 목록을 보여주고, 기본값은 유효 체인의 첫 항목이다', async () => {
    // providerPinned + selectedProvider 는 실행 프로바이더의 이야기이며 검수자와 무관하다
    // (P0007 "검수자 해석 순서": 지정값이 없으면 프로젝트 유효 체인의 첫 프로바이더).
    const wrapper = mountDialog({ providerPinned: true, selectedProvider: 'aip_opus' })
    await flushPromises()
    await openReviewTab()

    const selects = reviewerSelects()
    expect(selects).toHaveLength(2)
    for (const select of selects) {
      expect([...select.querySelectorAll('option')].map(option => option.value))
        .toEqual(['aip_codex', 'aip_sonnet', 'aip_opus'])
      expect([...select.querySelectorAll('option')].map(option => option.textContent?.trim()))
        .toEqual(['Codex GPT', 'Claude Sonnet', 'Claude Opus'])
      // 고정된 실행 프로바이더(aip_opus)가 아니라 체인의 첫 항목이다.
      expect(select.value).toBe('aip_codex')
    }

    wrapper.unmount()
  })

  it('접근성 레이블이 하드코딩이 아니라 i18n 키로 렌더링된다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    expect(countSelects().map(select => select.getAttribute('aria-label'))).toEqual([
      T('main.continuous_work.review_count_aria', { n: 1 }),
      T('main.continuous_work.review_count_aria', { n: 2 }),
    ])
    expect(reviewerSelects().map(select => select.getAttribute('aria-label'))).toEqual([
      T('main.continuous_work.review_reviewer_aria', { n: 1 }),
      T('main.continuous_work.review_reviewer_aria', { n: 2 }),
    ])

    wrapper.unmount()
  })
})

describe('ContinuousWorkDialog [검수] confirm payload (0414 T0012 작업 2)', () => {
  it('단계마다 다른 횟수·검수자를 실제 item_seq 로 짝지어 내보낸다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    await extendTargetToLastStep()
    await openReviewTab()

    // 실행단계1..4 = T@3, TR@4, TS@5, TSR@6
    await setSelect(countSelects()[0], '-1')
    await setSelect(countSelects()[1], '1')
    await setSelect(countSelects()[2], '2')
    await setSelect(countSelects()[3], '3')
    await setSelect(reviewerSelects()[0], 'aip_sonnet')
    await setSelect(reviewerSelects()[2], 'aip_opus')

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any

    expect(payload.reviewCountOverrides).toEqual({ 3: -1, 4: 1, 5: 2, 6: 3 })
    // 손대지 않은 두 행(TR@4, TSR@6)의 검수자는 체인의 첫 항목이다.
    expect(payload.reviewerOverrides).toEqual({
      3: 'aip_sonnet',
      4: 'aip_codex',
      5: 'aip_opus',
      6: 'aip_codex',
    })
    // 값은 횟수 정수와 provider id 문자열이다 — 이름도, 화면 번호도, 문서 타입도 아니다.
    expect(Object.values(payload.reviewCountOverrides).every(v => typeof v === 'number')).toBe(true)
    expect(Object.values(payload.reviewerOverrides).every(v => typeof v === 'string')).toBe(true)
    // 기존 필드에 회귀 없음.
    expect(payload.instructionMode).toBe('ai_direct')
    expect(payload.targetSeq).toBe(6)
    expect(payload.providerOverrides).toEqual({})
    expect(payload.autoApproveItemSeqs).toEqual([])

    wrapper.unmount()
  })

  it('횟수 0 인 단계는 두 맵에서 모두 빠지고, 키 공간이 일치한다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    // TS@5 에만 횟수를 준다. TR@4 는 검수자만 골라 두는데(횟수 0), 그 값은 나가지 않는다 —
    // 횟수 없는 검수자 항목은 서버 정규화가 떨어뜨리는 고아다.
    await setSelect(countSelects()[1], '2')
    await setSelect(reviewerSelects()[0], 'aip_sonnet')

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any

    expect(payload.reviewCountOverrides).toEqual({ 5: 2 })
    expect(payload.reviewerOverrides).toEqual({ 5: 'aip_codex' })
    expect(Object.keys(payload.reviewerOverrides)).toEqual(Object.keys(payload.reviewCountOverrides))

    wrapper.unmount()
  })

  it('아무 행도 건드리지 않으면 두 맵 모두 비어 있다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()
    await proceed()

    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})

    wrapper.unmount()
  })

  it('0 으로 되돌린 행은 두 맵에서 다시 빠진다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    await setSelect(countSelects()[0], '3')
    await setSelect(countSelects()[0], '0')
    expect(countSelects()[0].value).toBe('0')

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})

    wrapper.unmount()
  })
})

describe('ContinuousWorkDialog [검수] 행이 사라질 때 (0414 T0012 작업 2)', () => {
  it('목표를 줄이면 범위 밖 단계의 횟수·검수자가 사라진다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    await setSelect(countSelects()[0], '1')            // TR@4
    await setSelect(countSelects()[1], '3')            // TS@5
    await setSelect(reviewerSelects()[1], 'aip_opus')  // TS@5

    // 목표를 TR@4(스텝 목록 idx 3)로 줄인다 → TS@5 행이 사라진다.
    ;(document.querySelectorAll('.wsp-step')[3] as HTMLButtonElement).click()
    await flushPromises()
    expect(reviewRows()).toHaveLength(1)

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.targetSeq).toBe(4)
    expect(payload.reviewCountOverrides).toEqual({ 4: 1 })
    expect(payload.reviewerOverrides).toEqual({ 4: 'aip_codex' })

    wrapper.unmount()
  })

  it('auto_approved 로 되돌리면 N/T 단계의 값이 사라지고, 되살아난 행은 0 에서 시작한다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    await openReviewTab()

    // 실행단계1 = T@3 (ai_direct 에서만 워커 칸)
    await setSelect(countSelects()[0], '2')
    await setSelect(reviewerSelects()[0], 'aip_sonnet')
    expect(reviewRows()).toHaveLength(3)

    // [자동승인] 으로 되돌리면 T@3 은 서버가 처리한다 → 행도 값도 사라진다.
    await switchToAutoApproved()
    expect(reviewRows()).toHaveLength(2)

    // 다시 ai_direct 로 — 행이 돌아와도 기본값 0 · 기본 검수자에서 다시 시작한다.
    await switchToAiDirect()
    expect(reviewRows()).toHaveLength(3)
    expect(countSelects()[0].value).toBe('0')
    expect(reviewerSelects()[0].value).toBe('aip_codex')

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})

    wrapper.unmount()
  })

  it('개별 자동승인을 켠 단계의 값이 사라진다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    await openReviewTab()

    await setSelect(countSelects()[0], '2')   // T@3
    await setSelect(countSelects()[1], '1')   // TR@4

    // 왼쪽 단계 목록에서 T@3 의 [자동승인] 체크박스를 켠다 → 그 행은 워커 칸이 아니다.
    const checkbox = document.querySelector('.wsp-step-auto-toggle input') as HTMLInputElement
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(reviewRows()).toHaveLength(2)

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.autoApproveItemSeqs).toEqual([3])
    expect(payload.reviewCountOverrides).toEqual({ 4: 1 })
    expect(payload.reviewerOverrides).toEqual({ 4: 'aip_codex' })

    wrapper.unmount()
  })
})

describe('ContinuousWorkDialog [검수] 초기화와 from-decision (0414 T0012 작업 2)', () => {
  it('다이얼로그를 다시 열면 이전 실행의 검수 선택을 물려받지 않는다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openReviewTab()

    await setSelect(countSelects()[0], '-1')
    await setSelect(reviewerSelects()[1], 'aip_opus')
    expect(countSelects()[0].value).toBe('-1')

    await wrapper.setProps({ visible: false })
    await flushPromises()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    // 다시 열면 [기본 설정] 탭으로 돌아오고, 검수 상태는 초기값이다.
    expect(tabs()[0].classList.contains('cwd-tab--active')).toBe(true)
    await openReviewTab()
    expect(countSelects().map(select => select.value)).toEqual(['0', '0'])
    expect(reviewerSelects().map(select => select.value)).toEqual(['aip_codex', 'aip_codex'])

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})

    wrapper.unmount()
  })

  it('작업계획 프리셋으로 열어도 검수 상태는 초기값이다', async () => {
    // 작업계획 프리셋에는 검수 필드가 없다 — provider/message 프리셋에 검수값이 얹혀
    // 들어오거나 영속되어서는 안 된다.
    postRequest.mockResolvedValue({ data: { fill_preview: {}, wp_revision_no: 1 } })
    const wrapper = mountDialog({
      preset: {
        sourceDocId: 'flowgate.default.0414.0006-WP',
        targetSeq: 5,
        instructionMode: 'ai_direct',
        providerOverrides: { 4: 'aip_opus' },
        defaultMessage: '',
        messageOverrides: { 4: '회귀 테스트 결과 포함' },
        warnings: [],
      },
    })
    await flushPromises()
    await openReviewTab()

    expect(countSelects().length).toBeGreaterThan(0)
    expect(countSelects().every(select => select.value === '0')).toBe(true)
    expect(reviewerSelects().every(select => select.value === 'aip_codex')).toBe(true)

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})
    // 프리셋의 provider 값 자체에는 회귀가 없다.
    expect(payload.providerOverrides).toEqual({ 4: 'aip_opus' })

    wrapper.unmount()
  })

  it('워크플로 결정 전(from-decision)에는 단계별 검수 맵을 만들지 않는다', async () => {
    // 0406 T0013: 미결정 시퀀스는 400 으로 답한다. 이 상태에는 item_seq 가 아직 없다.
    getRequest.mockRejectedValue({
      response: { status: 400, data: { error: 'sequence_not_decided', doc_id: ROOT } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await openReviewTab()
    expect(reviewRows()).toHaveLength(0)
    expect(countSelects()).toHaveLength(0)

    await proceed()
    const payload = wrapper.emitted('confirm')!.at(-1)![0] as any
    expect(payload.fromDecision).toBe(true)
    expect(payload.reviewCountOverrides).toEqual({})
    expect(payload.reviewerOverrides).toEqual({})

    wrapper.unmount()
  })
})

// T0012 작업 6: 다이얼로그와 /ai-invoke/start 사이의 중간 단계(MainPanel)가 값을 흘리지
// 않는지 — 여기가 끊기면 ContinuousWorkDialog 스펙과 AiInvokeDialog 스펙이 둘 다 초록인 채로
// 화면의 선택이 서버에 영영 닿지 않는다(0346 T0005 가 같은 이유로 남긴 경계).
describe('MainPanel 검수 preset 전달 계약 (0414 T0012 작업 3)', () => {
  const source = readFileSync(join(process.cwd(), 'src/main/components/MainPanel.vue'), 'utf8')

  it('confirm payload 의 두 맵을 받아 둔다', () => {
    expect(source).toContain('continuousReviewCountOverrides.value = payload.reviewCountOverrides')
    expect(source).toContain('continuousReviewerOverrides.value = payload.reviewerOverrides')
  })

  it('경고창 승인 뒤 openAiInvokeDialog preset 에 두 맵을 싣는다', () => {
    expect(source).toContain('reviewCountOverrides: continuousReviewCountOverrides.value')
    expect(source).toContain('reviewerOverrides: continuousReviewerOverrides.value')
  })

  it('preset 이 없는 진입점에서는 빈 맵으로 초기화한다', () => {
    expect(source).toContain('aiInvokeReviewCountOverrides.value = preset?.reviewCountOverrides ?? {}')
    expect(source).toContain('aiInvokeReviewerOverrides.value = preset?.reviewerOverrides ?? {}')
  })

  it('AiInvokeDialog 에 props 로 내려보낸다', () => {
    expect(source).toContain(':review-count-overrides="aiInvokeReviewCountOverrides"')
    expect(source).toContain(':reviewer-overrides="aiInvokeReviewerOverrides"')
  })

  it('멘트 복사 기반 반자동 경로의 토큰 발급 payload 는 건드리지 않는다', () => {
    // 그 경로는 /ai-invoke/start 를 부르지 않으므로 item_seq 검수 게이트도 돌지 않는다.
    // 새 맵이 토큰 발급 요청에 섞여 들어가면 안 된다.
    const start = source.indexOf('async function issueContinuousToken(')
    expect(start).toBeGreaterThan(-1)
    const issueBlock = source.slice(start, start + 3000)
    expect(issueBlock).not.toContain('reviewCountOverrides')
    expect(issueBlock).not.toContain('reviewerOverrides')
    expect(issueBlock).not.toContain('continuation_review_count_overrides')
    expect(issueBlock).not.toContain('continuation_reviewer_overrides')
  })
})
