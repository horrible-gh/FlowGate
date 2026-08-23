import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, patchRequest: vi.fn(), postRequest, putRequest: vi.fn(),
}))
const WP_DOC = 'flowgate.default.0408.0004-WP'

const items = [
  {
    id: 1, item_seq: 1, type: 'D', label: 'Stored', status: 'pending',
    provider_id: 'stored', provider_display_name: 'Stored Provider', provider_registered: true,
  },
  {
    id: 2, item_seq: 2, type: 'P', label: 'Default', status: 'pending',
    provider_id: null, provider_display_name: null, provider_registered: null,
  },
  {
    id: 3, item_seq: 3, type: 'M', label: 'Unavailable', status: 'pending',
    provider_id: 'deleted', provider_display_name: 'Deleted Snapshot', provider_registered: false,
  },
]

const pairedItems = [
  {
    id: 11, item_seq: 1, type: 'N', label: 'Instruction N', status: 'pending',
    provider_id: null, provider_display_name: null, provider_registered: null,
    note: 'N plan handoff', source_doc_id: 'flowgate.default.0408.0004-WP', source_revision_no: 8,
  },
  {
    id: 12, item_seq: 2, type: 'NR', label: 'Report N', status: 'pending',
    provider_id: 'stored', provider_display_name: 'Stored Provider', provider_registered: true,
    note: 'NR plan handoff', source_doc_id: 'flowgate.default.0408.0004-WP', source_revision_no: 8,
  },
  {
    id: 13, item_seq: 3, type: 'T', label: 'Instruction T', status: 'pending',
    provider_id: 'other', provider_display_name: 'Other Provider', provider_registered: true,
    note: 'T plan handoff', source_doc_id: 'flowgate.default.0408.0004-WP', source_revision_no: 8,
  },
  {
    id: 14, item_seq: 4, type: 'TR', label: 'Report T', status: 'pending',
    provider_id: 'other', provider_display_name: 'Other Provider', provider_registered: true,
    note: 'TR plan handoff', source_doc_id: 'flowgate.default.0408.0004-WP', source_revision_no: 8,
  },
]

function response(rows = items) {
  return { data: { doc_id: 'flowgate.default.0408.0001-B', doc_class: 'B', decided: true,
    items: JSON.parse(JSON.stringify(rows)), head: rows[0] } }
}
function mountDialog(selectedProvider = 'default', rows = items, providerPinned = false) {
  getRequest.mockResolvedValue(response(rows))
  return mount(ContinuousWorkDialog, {
    props: {
      visible: true, docRef: 'flowgate.default.0408.0001-B', selectedProvider, providerPinned,
      providers: [
        { id: 'stored', name: 'Stored Provider' },
        { id: 'default', name: 'Default Provider' },
        { id: 'other', name: 'Other Provider' },
      ],
    },
    global: { plugins: [i18n] },
  })
}
async function openProviders() {
  ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
  await flushPromises()
}
async function openMessages() {
  ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
  await flushPromises()
}
async function switchInstructionMode(mode: 'auto_approved' | 'ai_direct') {
  ;(document.querySelectorAll('.cwd-tab')[0] as HTMLButtonElement).click()
  await flushPromises()
  const input = document.querySelector(`input[type="radio"][value="${mode}"]`) as HTMLInputElement
  input.click()
  await flushPromises()
  await openProviders()
}

function selects() {
  return document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
}
async function confirm(wrapper: ReturnType<typeof mountDialog>) {
  ;([...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement).click()
  await flushPromises()
  return wrapper.emitted('confirm')![0][0] as any
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockResolvedValue(response())
  // No plan read by default: the dialog must work off the stored sequence alone.
  postRequest.mockReset().mockRejectedValue(new Error('no plan read in this test'))
})
afterEach(() => { document.body.innerHTML = '' })

describe('ContinuousWorkDialog stored provider states (0408)', () => {
  it('renders stored/default/unavailable as three distinct states without blocking', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openProviders()
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'default', 'default'])
    const badges = document.querySelectorAll('.cwd-filled-badge')
    expect(badges).toHaveLength(1)
    expect(badges[0].textContent).toContain(i18n.global.t('main.continuous_work.sequence_provider_unavailable'))
    expect(badges[0].classList.contains('cwd-stored-provider--unavailable')).toBe(true)
    expect((document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).disabled).toBe(false)
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
  })

  // 0448 T0005 §6: 0444's single `shows a pinned provider on every row while keeping per-step
  // overrides highest` is split in two. The pin-on-all + pin-overrode-the-stored-value badge
  // half is deleted with the copy it asserted; the per-step-override-is-highest half is a test
  // of its own, because it is a contract in its own right and outlives the pin question.
  it('keeps a per-step override highest, above both the stored value and the run selection', async () => {
    const wrapper = mountDialog('default', items, false)
    await flushPromises()
    await openProviders()

    // Row 1 shows its stored provider (an ordinary selection does not displace it), row 2
    // stores nothing and takes the selection, row 3's stored provider is unusable.
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'default', 'default'])

    selects()[0].value = 'other'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    // The explicit per-step choice is what rides the request, for the row that had a stored
    // provider of its own.
    expect((await confirm(wrapper)).providerOverrides).toEqual({ 1: 'other' })
  })

  it('keeps a per-step override highest even under an explicit force-all', async () => {
    const wrapper = mountDialog('default', items, true)
    await flushPromises()
    await openProviders()

    selects()[0].value = 'other'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect((await confirm(wrapper)).providerOverrides).toEqual({ 1: 'other' })

    // 0442 B0001: 프로바이더 탭은 셀렉터와 단계 행만 남는다 — 실행 요약 줄도, 고정 배지도,
    // 해제 단추도 렌더링되지 않는다.
    expect(document.querySelector('.cwd-provider-row')).not.toBeNull()
    expect(document.querySelector('.cwd-provider-summary')).toBeNull()
    expect(document.querySelector('.cwd-provider-pin-badge')).toBeNull()
    expect(document.querySelector('.cwd-provider-pin-clear')).toBeNull()
  })

  // 0442 B0001 재반려 2 ("예전처럼 되돌리라고"): 없앤 문구가 어떤 형태로도 돌아오면 안 된다.
  // 대조군은 고정된 공급자가 여전히 모든 단계 셀렉터에 실제로 적용된다는 것이다.
  it('leaves no run-summary or pin copy in the Korean provider tab', async () => {
    i18n.global.locale.value = 'ko'
    mountDialog('default', items, true)
    await flushPromises()
    await openProviders()

    const panel = document.querySelector('.cwd-provider-block') as HTMLElement
    expect(panel).not.toBeNull()
    expect(panel.querySelector('.cwd-provider-select')).not.toBeNull()
    expect([...selects()].map(select => select.value)).toEqual(['default', 'default', 'default'])

    expect(panel.textContent).not.toContain('이번 실행에 사용할 공급자')
    expect(panel.textContent).not.toContain('이번 실행에 고정')
    expect(panel.textContent).not.toContain('고정 해제')
    expect(panel.textContent).not.toContain('저장된 단계 값을 따름')
  })

  it('sends only edits and restores untouched state when the stored value is reselected', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openProviders()
    selects()[0].value = 'other'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect(document.querySelectorAll('.cwd-filled-badge')).toHaveLength(1)
    selects()[0].value = 'stored'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
  })

  it('treats choosing the header default on a stored row as an explicit override', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openProviders()
    selects()[0].value = 'default'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect((await confirm(wrapper)).providerOverrides).toEqual({ 1: 'default' })
  })

  it('does not move stored rows when the header default changes', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await openProviders()
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'default', 'default'])
    await wrapper.setProps({ selectedProvider: 'other' })
    await flushPromises()
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'other', 'other'])
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
  })
})

describe('ContinuousWorkDialog paired instruction providers (0408 T0017)', () => {
  // 0408 TR0021 재반려 2 ("왜 프로바이더는 안고치냐? 자동승인 상태면 N/T 빼야지... 이번 실행
  // 미사용 이것떄문에 스크롤 생기니까 다 빼라"): reverts TR0018 rev1's "keep every in-range row,
  // read-only for the auto-approved ones" design — that row cost table height for a value
  // nobody could act on. Only the run's own rows show now, in BOTH tabs.
  // 0451 T0007 rev1: rev0 answered that by printing a provider name on every picker row. The
  // rejection removed it outright (좌측단에 프로바이더는 출력하지 않는다 — the [Providers] tab on
  // the right already names one per step), so under auto-approve an N/T step names no provider
  // anywhere. That is the accepted result of TR0021 + this change, not a regression.
  it('shows only the rows this run hands to a worker under auto-approve, all four under ai_direct, and never moves a value across round trips', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await openProviders()
    expect([...document.querySelectorAll('.cwd-override-badge')].map(node => node.textContent))
      .toEqual(['NR', 'TR'])
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'other'])
    expect(document.querySelector('.cwd-override-readonly')).toBeNull()
    expect(document.querySelector('.cwd-auto-provider-badge')).toBeNull()
    expect(document.querySelector('.cwd-scope-note')).toBeNull()
    const provTags = () => [...document.querySelectorAll('.wsp-prov-tag')]
    // The step list names no provider in either mode. Positive control for that zero: the four
    // rows themselves ARE rendered, and the table above draws its own two.
    expect(document.querySelectorAll('.wsp-step')).toHaveLength(4)
    expect(provTags()).toHaveLength(0)

    const shown = () => [...document.querySelectorAll('.cwd-override-badge')].map(node => node.textContent)
    const autoApproved = shown()
    expect(autoApproved).toEqual(['NR', 'TR'])

    await switchInstructionMode('ai_direct')
    expect(shown()).toEqual(['N', 'NR', 'T', 'TR'])
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'stored', 'other', 'other'])
    // Still nothing on the left under ai_direct either — the four names above are read off the
    // [Providers] table, which is now the only place they appear.
    expect(provTags()).toHaveLength(0)

    await switchInstructionMode('auto_approved')
    expect(shown()).toEqual(autoApproved)
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'other'])
    await switchInstructionMode('ai_direct')
    expect([...selects()].map(select => select.value)).toEqual(['stored', 'stored', 'other', 'other'])
    await switchInstructionMode('auto_approved')
    expect(shown()).toEqual(autoApproved)
  })

  // rev2 (TR0018) / 0408 TR0021 재반려 2: the row's 실행단계 number is the run position, and
  // since the table now draws only run rows, every row it has IS numbered — in both tabs alike.
  it('numbers rows by run position, identically in the provider and mention tabs', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await openProviders()
    const numbered = () => [...document.querySelectorAll('.cwd-override-row')].map(row => [
      row.querySelector('.cwd-override-badge')?.textContent,
      row.querySelector('.cwd-override-step-no')?.textContent,
    ])
    const step = (n: number) => i18n.global.t('main.continuous_work.step_no_label', { n })

    expect(numbered()).toEqual([['NR', step(1)], ['TR', step(2)]])
    await openMessages()
    expect(numbered()).toEqual([['NR', step(1)], ['TR', step(2)]])

    await switchInstructionMode('ai_direct')
    expect(numbered()).toEqual([
      ['N', step(1)], ['NR', step(2)], ['T', step(3)], ['TR', step(4)],
    ])
    await openMessages()
    expect(numbered()).toEqual([
      ['N', step(1)], ['NR', step(2)], ['T', step(3)], ['TR', step(4)],
    ])
  })

  it('drops N/T rows from the table under auto-approve, and their providers are never sent', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openProviders()
    expect(document.querySelectorAll('.cwd-override-row')).toHaveLength(2)
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
    expect((wrapper.emitted('confirm')![0][0] as any).stepCount).toBe(4)
  })

  it('marks an N provider inherited from NR and does not emit it when reselected', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openProviders()
    await switchInstructionMode('ai_direct')
    expect([...document.querySelectorAll('.cwd-filled-badge')].map(node => node.textContent))
      .toContain(i18n.global.t('main.continuous_work.sequence_provider_inherited'))
    selects()[0].value = 'stored'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
  })

  it('emits only the N row when its inherited provider is changed', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openProviders()
    await switchInstructionMode('ai_direct')
    selects()[0].value = 'other'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect((await confirm(wrapper)).providerOverrides).toEqual({ 1: 'other' })
  })

  it('ignores an inactive paired provider and falls back to the header default', async () => {
    const inactivePair = JSON.parse(JSON.stringify(pairedItems))
    inactivePair[1].provider_registered = false
    const wrapper = mountDialog('default', inactivePair)
    await flushPromises()
    await openProviders()
    await switchInstructionMode('ai_direct')
    expect(selects()[0].value).toBe('default')
    expect([...document.querySelectorAll('.cwd-filled-badge')].map(node => node.textContent))
      .not.toContain(i18n.global.t('main.continuous_work.sequence_provider_inherited'))
    expect((await confirm(wrapper)).providerOverrides).toEqual({})
  })
})

// 0451 T0007 rev1: rev0's four-variant step-list provider badge is gone — the rejection asked
// for the pre-T0007 left column back (state only, right-aligned, nothing else). What is checked
// here is that absence, each time against a positive control, plus the state badge's shape.
describe('ContinuousWorkDialog step list state badges (0451 T0007 rev1)', () => {
  it('names no provider on a stored, an unregistered or a per-step-override row', async () => {
    mountDialog('default', items)
    await flushPromises()
    await openProviders()
    selects()[0].value = 'other'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()

    // Positive control for the zeroes below: three rows and three selects really did render.
    expect(document.querySelectorAll('.wsp-step')).toHaveLength(3)
    expect(selects()).toHaveLength(3)

    expect(document.querySelectorAll('.wsp-prov-tag')).toHaveLength(0)
    const list = document.querySelector('.wsp-steps')!.textContent ?? ''
    expect(list).not.toContain('Other Provider')    // the override just picked
    expect(list).not.toContain('Stored Provider')   // row 1's stored value
    expect(list).not.toContain('Deleted Snapshot')  // row 3's unregistered stored value
  })

  // The N row here is both the head AND auto-handled (auto_approved default), which before
  // T0007 drew TWO separate state spans ("현재" + "자동 승인") on one row — the "글씨가 들쭉날쭉
  // 하다" complaint (CH0006 turn 5). stateTag()'s merge stays: auto wins over head, one badge.
  it('shows exactly one state tag per row, as a bare span with no slot wrapper', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()

    const nRow = document.querySelectorAll('.wsp-step')[0]
    expect(nRow.querySelectorAll('.wsp-step-tag')).toHaveLength(1)
    expect(nRow.querySelector('.wsp-step-tag--auto')?.textContent).toBe(
      i18n.global.t('main.continuous_work.auto_step_tag'),
    )
    // rejection 1 (좌측단의 완료/대기/현재는 기존(우측 정렬)으로 되돌린다): the badge is a bare
    // span right after the flex:1 label — that is what right-aligns it — not a fixed-width slot
    // inside a `.wsp-step-end` wrapper.
    expect(document.querySelector('.wsp-step-end')).toBeNull()
    expect(document.querySelector('.wsp-step-state-slot')).toBeNull()
    expect(document.querySelector('.wsp-step-prov-slot')).toBeNull()
    const tag = nRow.querySelector('.wsp-step-tag')!
    expect(tag.parentElement!.classList.contains('wsp-step')).toBe(true)
    expect(tag.previousElementSibling!.classList.contains('wsp-step-label')).toBe(true)
  })

  it('keeps the head class on the current row and leaves a plain pending row untagged', async () => {
    mountDialog('default', items)
    await flushPromises()

    const rows = document.querySelectorAll('.wsp-step')
    expect(rows[0].querySelector('.wsp-step-tag--head')?.textContent).toBe(
      i18n.global.t('main.continuous_work.head_tag'),
    )
    expect(rows[1].querySelector('.wsp-step-tag')).toBeNull()
  })
})

describe('ContinuousWorkDialog paired instruction mentions (0408 T0020)', () => {
  function messageRows() {
    return [...document.querySelectorAll('.cwd-override-row')].map(row => ({
      type: row.querySelector('.cwd-override-badge')?.textContent,
      step: row.querySelector('.cwd-override-step-no')?.textContent,
      value: (row.querySelector('.cwd-override-message-input') as HTMLInputElement).value,
    }))
  }

  it('lists only the steps a worker runs, each showing its OWN note', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()

    // 재반려 1: [자동 승인] writes and approves N/T on the server — no worker, so no mention
    // row. 재반려 2: NR shows 'NR plan handoff', never N's sentence.
    expect(messageRows().map(row => [row.type, row.value])).toEqual([
      ['NR', 'NR plan handoff'], ['TR', 'TR plan handoff'],
    ])
    expect(document.querySelectorAll('.cwd-note-inherited-badge')).toHaveLength(0)
  })

  it('lists all four rows with their own notes under ai_direct', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await switchInstructionMode('ai_direct')
    await openMessages()

    expect(messageRows().map(row => [row.type, row.value])).toEqual([
      ['N', 'N plan handoff'], ['NR', 'NR plan handoff'],
      ['T', 'T plan handoff'], ['TR', 'TR plan handoff'],
    ])
  })

  it('keeps rows and values byte-identical across three mode round trips', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    const initialAuto = messageRows()
    await switchInstructionMode('ai_direct')
    await openMessages()
    const initialDirect = messageRows()

    for (let round = 0; round < 3; round += 1) {
      await switchInstructionMode('auto_approved')
      await openMessages()
      expect(messageRows()).toEqual(initialAuto)
      await switchInstructionMode('ai_direct')
      await openMessages()
      expect(messageRows()).toEqual(initialDirect)
    }
  })

  it('keeps a mention typed for an N step through a round trip and never sends it', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await switchInstructionMode('ai_direct')
    await openMessages()
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    inputs[0].value = 'N only'
    inputs[0].dispatchEvent(new Event('input'))
    await flushPromises()

    // Under [자동 승인] that step has no worker: the row goes away and the value is not sent.
    await switchInstructionMode('auto_approved')
    await openMessages()
    expect(messageRows().map(row => row.type)).toEqual(['NR', 'TR'])
    expect((await confirm(wrapper)).messageOverrides).toEqual({})

    // ...and it is still there when the radio comes back.
    await switchInstructionMode('ai_direct')
    await openMessages()
    const back = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(back[0].value).toBe('N only')
  })

  it('uses the same step numbers as the provider tab', async () => {
    mountDialog('default', pairedItems)
    await flushPromises()
    await switchInstructionMode('ai_direct')
    const providerLabels = [...document.querySelectorAll('.cwd-override-row')]
      .map(row => row.querySelector('.cwd-override-step-no')?.textContent)
    await openMessages()
    expect(messageRows().map(row => row.step)).toEqual(providerLabels)
  })

  it('does not emit stored values until the user edits one', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    const payload = await confirm(wrapper)
    expect(payload.messageOverrides).toEqual({})
    expect(payload.providerOverrides).toEqual({})
    expect(payload.stepCount).toBe(4)
  })

  it('emits only the edited row', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    inputs[0].value = 'NR direct override'
    inputs[0].dispatchEvent(new Event('input'))
    await flushPromises()
    expect((await confirm(wrapper)).messageOverrides).toEqual({ 2: 'NR direct override' })
  })

  it('emits an empty tombstone when a stored value is cleared', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    inputs[0].value = ''
    inputs[0].dispatchEvent(new Event('input'))
    await flushPromises()
    expect((await confirm(wrapper)).messageOverrides).toEqual({ 2: '' })
  })

  it('drops an edited mention only when target shrinking removes its row', async () => {
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    let inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    inputs[1].value = 'out of range'
    inputs[1].dispatchEvent(new Event('input'))
    await flushPromises()

    ;(document.querySelectorAll('.cwd-tab')[0] as HTMLButtonElement).click()
    await flushPromises()
    ;(document.querySelectorAll('.wsp-step')[1] as HTMLButtonElement).click()
    await flushPromises()
    await openMessages()
    inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(inputs).toHaveLength(1)
    expect((await confirm(wrapper)).messageOverrides).toEqual({})
  })
})

// 0408 M0019 재반려 3 — "문서에서 멘트와 프로바이더를 변경했는데 왜 다이얼로그에 적용되지 않는거지?"
// The sequence rows are the plan as it was when somebody last poured it. The dialog reads the
// plan's CURRENT projection (the same L0010 §2.6 call the apply preview makes) so the document
// stays the thing a person edits.
describe('ContinuousWorkDialog reads the live work plan (0408 M0019 재반려 3)', () => {
  const PREVIEW_URL = `/api/v1/documents/${encodeURIComponent(WP_DOC)}/work-plan/apply/preview`

  function planPreview(notes: Record<string, string>, providers: Record<string, string> = {}) {
    return {
      data: {
        wp_doc_id: WP_DOC,
        wp_revision_no: 12,
        fill_preview: { note_overrides: notes, provider_overrides: providers },
      },
    }
  }

  it('shows the plan\'s current mention and provider, not the poured snapshot', async () => {
    postRequest.mockResolvedValue(planPreview({ 2: 'edited in the document' }, { 2: 'other' }))
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()

    expect(postRequest).toHaveBeenCalledWith(PREVIEW_URL, { instruction_mode: 'auto_approved' })
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(inputs[0].value).toBe('edited in the document')
    await openProviders()
    expect(selects()[0].value).toBe('other')

    const payload = await confirm(wrapper)
    expect(payload.messageOverrides).toEqual({ 2: 'edited in the document' })
    expect(payload.providerOverrides).toEqual({ 2: 'other' })
  })

  it('re-reads the projection when the 실행 방식 changes, because the fold moves with it', async () => {
    postRequest.mockResolvedValue(planPreview({ 2: 'folded onto the report' }))
    mountDialog('default', pairedItems)
    await flushPromises()
    postRequest.mockResolvedValue(planPreview({ 1: 'the N step itself' }))
    await switchInstructionMode('ai_direct')
    await openMessages()

    expect(postRequest).toHaveBeenLastCalledWith(PREVIEW_URL, { instruction_mode: 'ai_direct' })
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(inputs[0].value).toBe('the N step itself')
  })

  it('never walks over a mention the person just typed', async () => {
    postRequest.mockResolvedValue(planPreview({ 2: 'from the plan' }))
    mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    inputs[0].value = 'typed by hand'
    inputs[0].dispatchEvent(new Event('input'))
    await flushPromises()

    await switchInstructionMode('ai_direct')
    await openMessages()
    const after = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(after[1].value).toBe('typed by hand')
  })

  it('keeps every stored value when the plan cannot be read', async () => {
    postRequest.mockRejectedValue(new Error('plan gone'))
    const wrapper = mountDialog('default', pairedItems)
    await flushPromises()
    await openMessages()

    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(inputs[0].value).toBe('NR plan handoff')
    expect((await confirm(wrapper)).messageOverrides).toEqual({})
  })
})