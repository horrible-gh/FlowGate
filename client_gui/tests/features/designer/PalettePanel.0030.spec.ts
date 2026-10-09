/**
 * flowgate.v02.0030 — Palette part screen (R0001 §2, §6): category sections and cards, the add
 * dialog, and the select / drag intents handed to the Designer.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import PalettePanel from '@/features/designer/palette/PalettePanel.vue'
import { PALETTE_DRAG_MIME, type CustomPaletteItem, type PaletteItemDefinition } from '@/features/designer/palette/paletteCatalog'
import { blockedRequests } from '../../setup/blockNetwork'

registerAppMessages(i18n)

let wrapper: VueWrapper<any> | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

function mountPalette(props: Record<string, unknown> = {}) {
  i18n.global.locale.value = 'ko'
  wrapper = mount(PalettePanel, { attachTo: document.body, props, global: { plugins: [i18n] } })
  return wrapper
}

const sectionLabels = (w: VueWrapper<any>) =>
  w.findAll('[data-test="designer-palette-section"]').map((s) => [
    s.attributes('data-category'),
    s.findAll('[data-test="designer-palette-item-label"]').map((l) => l.text()),
  ])

const body = () => document.body
const q = <T extends Element = HTMLElement>(sel: string) => body().querySelector<T>(sel)

async function openAdd(w: VueWrapper<any>) {
  await w.get('[data-test="designer-palette-add"]').trigger('click')
  await flushPromises()
  await nextTick()
}

async function setName(value: string) {
  const input = q<HTMLInputElement>('[data-test="designer-palette-add-name"]')!
  input.value = value
  input.dispatchEvent(new Event('input'))
  await nextTick()
}

async function setSelect(test: string, value: string) {
  const select = q<HTMLSelectElement>(`[data-test="${test}"]`)!
  select.value = value
  select.dispatchEvent(new Event('change'))
  await nextTick()
}

async function click(sel: string) {
  q(sel)!.click()
  await flushPromises()
  await nextTick()
}

function fakeDragEvent() {
  const data = new Map<string, string>()
  return {
    data,
    dataTransfer: {
      effectAllowed: 'none',
      setData: (type: string, value: string) => data.set(type, value),
      getData: (type: string) => data.get(type) ?? '',
    },
  }
}

describe('PalettePanel (flowgate.v02.0030)', () => {
  it('shows the default items of 03-designer-shell per category', () => {
    const w = mountPalette()
    expect(sectionLabels(w)).toEqual([
      ['design', ['기본 설계', '프로토콜', '로직', 'DB설계']],
      ['workflow', ['조사', '작업계획', '작업', '테스트']],
      ['control', ['확인']],
    ])
    expect(w.findAll('[data-test="designer-palette-section-title"]').map((t) => t.text())).toEqual([
      '📐 설계',
      '⚡ 업무 플로우',
      '⚙ 제어',
    ])
  })

  it('draws each card with its icon and color and makes it a drag source', () => {
    const w = mountPalette()
    const card = w.get('[data-item-id="db_design"]')
    expect(card.attributes('draggable')).toBe('true')
    // flowgate.v02.0045 T0019: every 설계 card is purple.
    expect(card.attributes('style')).toContain('--palette-accent: #9b72ff')
    expect(card.attributes('data-node-kind')).toBe('design')
    expect(card.find('svg path').attributes('d')).toMatch(/^M5 5a7 3/)
  })

  it('owns no tab buttons, Conversation or Git and has a single add entry point', () => {
    const w = mountPalette()
    expect(w.find('[role="tab"]').exists()).toBe(false)
    expect(w.text()).not.toMatch(/대화|Git/)
    expect(w.findAll('[data-test="designer-palette-add"]')).toHaveLength(1)
    expect(w.get('[data-test="designer-palette-add"]').attributes('aria-label')).toBe('팔레트 종류 추가')
  })

  it('emits a select intent with the item definition and marks the item selected', async () => {
    const w = mountPalette()
    await w.get('[data-item-id="task"]').trigger('click')
    const [definition] = w.emitted('select')![0] as [PaletteItemDefinition]
    expect(definition).toEqual({
      itemId: 'task',
      category: 'workflow',
      nodeKind: 'task',
      docType: 'T',
      label: '작업',
      icon: 'pencil',
      accent: '#f59e0b',
      custom: false,
    })
    expect(w.get('[data-item-id="task"]').attributes('aria-pressed')).toBe('true')
    expect(w.get('[data-item-id="test"]').attributes('aria-pressed')).toBe('false')
  })

  it('emits a drag-start intent and writes the definition into the DataTransfer', async () => {
    const w = mountPalette()
    const event = fakeDragEvent()
    await w.get('[data-item-id="research"]').trigger('dragstart', { dataTransfer: event.dataTransfer })
    const [definition] = w.emitted('drag-start')![0] as [PaletteItemDefinition]
    expect(definition).toMatchObject({ itemId: 'research', nodeKind: 'research', docType: 'N', label: '조사' })
    expect(JSON.parse(event.data.get(PALETTE_DRAG_MIME)!)).toEqual(definition)
    expect(event.data.get('text/plain')).toBe('조사')
    expect(event.dataTransfer.effectAllowed).toBe('copy')
  })

  it('adds a user-defined item to the chosen category through the dialog', async () => {
    const w = mountPalette()
    await openAdd(w)
    expect(q('[data-test="designer-palette-add-form"]')).not.toBeNull()
    expect(document.activeElement).toBe(q('[data-test="designer-palette-add-name"]'))
    expect(q('[data-test="designer-palette-add-icons"]')!.querySelectorAll('[role="radio"]')).toHaveLength(34)

    await setSelect('designer-palette-add-category', 'control')
    await setName('  API 설계 ')
    await click('[data-icon="api"]')
    await setSelect('designer-palette-add-color', '#ec4899')
    await click('[data-test="designer-palette-add-submit"]')

    expect(q('[data-test="designer-palette-add-form"]')).toBeNull()
    expect(sectionLabels(w)[2]).toEqual(['control', ['확인', 'API 설계']])
    const card = w.get('[data-item-id="custom.1"]')
    expect(card.attributes('data-custom')).toBe('true')
    expect(card.attributes('style')).toContain('--palette-accent: #ec4899')
    const [item] = w.emitted('add')![0] as [CustomPaletteItem]
    expect(item).toMatchObject({ id: 'custom.1', category: 'control', icon: 'api', label: 'API 설계' })
    expect(w.emitted('update:customItems')![0]).toEqual([[item]])

    await card.trigger('click')
    expect((w.emitted('select')!.at(-1) as [PaletteItemDefinition])[0]).toMatchObject({
      itemId: 'custom.1',
      nodeKind: 'custom.1',
      docType: null,
      label: 'API 설계',
      custom: true,
    })
  })

  it('refuses an empty name and keeps the dialog open', async () => {
    const w = mountPalette()
    await openAdd(w)
    await click('[data-test="designer-palette-add-submit"]')
    expect(q('[data-test="designer-palette-add-name-error"]')?.textContent).toContain('이름을 입력하세요.')
    expect(q('[data-test="designer-palette-add-form"]')).not.toBeNull()
    expect(w.emitted('add')).toBeUndefined()
    await setName('x')
    expect(q('[data-test="designer-palette-add-name-error"]')).toBeNull()
  })

  it('adds on Enter in the name field and resets the form on the next open', async () => {
    const w = mountPalette()
    await openAdd(w)
    await setName('메모')
    await click('[data-icon="star"]')
    q('[data-test="designer-palette-add-name"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flushPromises()
    expect(sectionLabels(w)[0][1]).toContain('메모')

    await openAdd(w)
    expect(q<HTMLInputElement>('[data-test="designer-palette-add-name"]')!.value).toBe('')
    expect(q('[data-icon="document"]')!.getAttribute('aria-checked')).toBe('true')
    expect(q<HTMLSelectElement>('[data-test="designer-palette-add-category"]')!.value).toBe('design')
  })

  it('closes on cancel and on Escape without adding', async () => {
    const w = mountPalette()
    await openAdd(w)
    await setName('x')
    await click('[data-test="designer-palette-add-cancel"]')
    expect(q('[data-test="designer-palette-add-form"]')).toBeNull()

    await openAdd(w)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(q('[data-test="designer-palette-add-form"]')).toBeNull()
    expect(w.emitted('add')).toBeUndefined()
  })

  it('shows bound custom items and leaves the list to the parent when v-model is bound', async () => {
    const bound: CustomPaletteItem[] = [
      { id: 'custom.7', category: 'workflow', nodeKind: 'custom.7', docType: null, icon: 'bug', accent: '#9b72ff', custom: true, label: '버그 분석' },
    ]
    const w = mountPalette({ customItems: bound })
    expect(sectionLabels(w)[1][1]).toEqual(['조사', '작업계획', '작업', '테스트', '버그 분석'])

    await openAdd(w)
    await setName('새 항목')
    await click('[data-test="designer-palette-add-submit"]')
    const [next] = w.emitted('update:customItems')![0] as [CustomPaletteItem[]]
    expect(next.map((i) => i.id)).toEqual(['custom.7', 'custom.8'])
    // Not applied until the parent passes the new list back.
    expect(w.find('[data-item-id="custom.8"]').exists()).toBe(false)
    await w.setProps({ customItems: next })
    expect(w.find('[data-item-id="custom.8"]').exists()).toBe(true)
  })

  it('makes no API request', async () => {
    const w = mountPalette()
    await openAdd(w)
    await setName('x')
    await click('[data-test="designer-palette-add-submit"]')
    await w.get('[data-item-id="custom.1"]').trigger('click')
    expect(blockedRequests).toEqual([])
  })
})
