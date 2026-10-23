/**
 * flowgate.v02.0088 (D0004 §6.2) — the client_gui i18n fixes that no screen spec covers on its own:
 * - the app catalog: ko / ja / en have the same keys and the same named interpolation params, the 0088 keys included;
 * - the UI message model (core/i18n/uiText) and the server-diagnostic boundary (core/i18n/serverDetail);
 * - F13: a default Palette card is labelled in the language of the placing moment, a custom one keeps its words;
 * - F14/F15: every slot list that offers line actions hands the kind's key on, and openCardLineAction passes it to both
 *   windows (UI only — not a part of the window identity);
 * - F11/F12: an AI / Conversation failure without server words is the localized fallback, never an English default.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { APP_MESSAGES, registerAppMessages } from '@/app/i18n'
import { failureText, renderUiText, uiKey, uiRaw } from '@/core/i18n/uiText'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), patchRequest: vi.fn(), deleteRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@shared/api')>()), ...api }))

const { serverDetailOf } = await import('@/core/i18n/serverDetail')
const { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, placedPaletteDefinition } = await import('@/features/designer/palette/paletteCatalog')
const { lineKindName } = await import('@/features/documentWindow/lineKindName')
const { openCardLineAction } = await import('@/features/designer/inspector/openDocument')
const { closeSlotWriteWindow, slotWriteWindowList } = await import('@/features/documentWindow/slotWriteWindows')
const { closeAllDocumentWindows, documentAiWindowList } = await import('@/features/documentWindow')
const { listAiGroups, AiRequestError } = await import('@/features/ai/api')
const { ConversationRequestError } = await import('@/features/conversation/api')
const conversationApi = await import('@/features/conversation/api')
const { createProject } = await import('@/features/project/api')

registerAppMessages(i18n)
const setLocale = (locale: 'ko' | 'ja' | 'en') => {
  ;(i18n.global.locale as unknown as { value: string }).value = locale
}
const t = (key: string, params?: Record<string, unknown>) => String(params ? i18n.global.t(key, params) : i18n.global.t(key))

beforeEach(() => {
  setLocale('ko')
  for (const fn of Object.values(api)) fn.mockReset()
})

afterEach(() => setLocale('ko'))

const refusal = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

function leaves(value: unknown, prefix = '', out = new Map<string, string>()): Map<string, string> {
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) leaves(child, prefix ? `${prefix}.${key}` : key, out)
  } else {
    out.set(prefix, String(value))
  }
  return out
}

const params = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')

describe('the app catalog (ko / ja / en)', () => {
  const ko = leaves(APP_MESSAGES.ko)
  const ja = leaves(APP_MESSAGES.ja)
  const en = leaves(APP_MESSAGES.en)

  it('has the same keys in every language', () => {
    expect([...ja.keys()].sort()).toEqual([...ko.keys()].sort())
    expect([...en.keys()].sort()).toEqual([...ko.keys()].sort())
  })

  it('has the same named interpolation params for every key', () => {
    for (const [key, text] of ko) {
      expect(params(ja.get(key) ?? ''), `ja ${key}`).toBe(params(text))
      expect(params(en.get(key) ?? ''), `en ${key}`).toBe(params(text))
    }
  })

  it('carries the keys the 0088 fixes read', () => {
    const keys = [
      'common.minimize', 'common.restore',
      'project.dialog.title', 'project.dialog.created_toast', 'project.list.load_failed', 'project.row.updated',
      'project.create.title', 'project.create.color_option', 'project.create.failed',
      'ai.providers.kind_custom', 'ai.request_failed.settings_load', 'ai.request_failed.role_delete',
    ]
    for (const key of keys) for (const catalog of [ko, ja, en]) expect(catalog.has(key), key).toBe(true)
    expect(params(ko.get('project.dialog.created_toast')!)).toBe('name')
    expect(params(ko.get('project.row.updated')!)).toBe('date')
  })
})

describe('the UI message model and the server diagnostic boundary', () => {
  it('renders a key in the current language and a raw string as it came', () => {
    const key = uiKey('gui.project.dialog.created_toast', { name: '가나다' })
    expect(renderUiText(t, key)).toBe('가나다 프로젝트를 생성했습니다.')
    setLocale('en')
    expect(renderUiText(t, key)).toBe('Created project 가나다.')
    expect(renderUiText(t, uiRaw('서버 원문'))).toBe('서버 원문')
    expect(renderUiText(t, null)).toBe('')
  })

  it('takes an Error message as the server words, an empty one as the localized fallback', () => {
    expect(failureText(new Error('중복된 ID'), 'gui.project.create.failed')).toEqual({ raw: '중복된 ID' })
    expect(failureText(new Error(''), 'gui.project.create.failed')).toEqual({ key: 'gui.project.create.failed' })
    expect(failureText('not an error', 'gui.project.create.failed')).toEqual({ key: 'gui.project.create.failed' })
  })

  it('keeps a server message, and drops a network failure, an empty body and the transport line', () => {
    expect(serverDetailOf(refusal(409, { ok: false, error: { code: 'project_exists', message: '이미 있는 프로젝트입니다.' } }))).toBe('이미 있는 프로젝트입니다.')
    expect(serverDetailOf({ isAxiosError: true, request: {}, message: 'Network Error' })).toBe('')
    expect(serverDetailOf(refusal(500, ''))).toBe('')
    const transport = Object.assign(new Error('Request failed with status code 502'), { isAxiosError: true, response: { status: 502, data: {} } })
    expect(serverDetailOf(transport)).toBe('')
  })

  // TR0007 rework: told apart by where the text came from — a body message the Error repeats is kept.
  it('keeps a body message the Error repeats (Error.message == response message, message != code)', () => {
    const message = 'The group is archived; restore it before creating a chat.'
    const repeated = Object.assign(new Error(message), { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'group_archived', message } } } })
    expect(serverDetailOf(repeated)).toBe(message)
    const codeOnly = Object.assign(new Error('group_archived'), { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'group_archived', message: 'group_archived' } } } })
    expect(serverDetailOf(codeOnly)).toBe('')
  })
})

describe('F13 — the label of a placed Palette card', () => {
  const research = DEFAULT_PALETTE_ITEMS.find((item) => item.id === 'research')!

  it('is made in the language of the placing moment, not of the selection', () => {
    const selected = paletteItemDefinition(research, t('gui.designer.palette.items.research'))
    expect(selected.label).toBe('조사')
    setLocale('ja')
    const spec = paletteNodeSpec(placedPaletteDefinition(selected, t))
    expect(spec.label).toBe(t('gui.designer.palette.items.research'))
    expect(spec.label).not.toBe('조사')
    expect(spec.paletteItemId).toBe('research')
    expect(spec.docType).toBe('N')
    setLocale('en')
    expect(paletteNodeSpec(placedPaletteDefinition(selected, t)).label).toBe(t('gui.designer.palette.items.research'))
  })

  it('keeps a custom card and an unknown item as they came', () => {
    const custom = { itemId: 'custom_1', category: 'workflow' as const, nodeKind: 'custom', docType: null, label: '내 카드', icon: 'x', accent: '#000', custom: true }
    setLocale('en')
    expect(placedPaletteDefinition(custom, t).label).toBe('내 카드')
    const unknown = { ...custom, itemId: 'gone_item', custom: false, label: '옛 라벨' }
    expect(placedPaletteDefinition(unknown, t).label).toBe('옛 라벨')
  })
})

describe('F14/F15 — the kind key of a slot line', () => {
  const SRC = resolve(__dirname, '../../src/features/designer/inspector')

  function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
      entry.isDirectory() ? sources(join(dir, entry.name)) : entry.name.endsWith('.vue') ? [join(dir, entry.name)] : [],
    )
  }

  it('every slot list that offers line actions hands nameKey on next to name (no other window opener)', () => {
    const callers = sources(SRC).filter((file) => readFileSync(file, 'utf8').includes('openCardLineAction('))
    expect(callers.map((file) => file.split(/[\\/]/).pop()).sort()).toEqual([
      'DesignSlots.vue', 'GateSlots.vue', 'RequirementSlots.vue', 'ResearchSlots.vue', 'TaskSlots.vue', 'TestSpecTab.vue', 'WorkPlanTab.vue',
    ])
    for (const file of callers) {
      const src = readFileSync(file, 'utf8')
      const names = src.match(/\bname: t\(k\(/g)?.length ?? 0
      const keys = src.match(/\bnameKey: k\(/g)?.length ?? 0
      expect(keys, file).toBe(names)
      expect(src, file).not.toMatch(/openSlotWriteWindow|openDocumentAiWindow/)
    }
  })

  it('openCardLineAction passes the key to both windows; the windows stay keyed by slot / docId', () => {
    const target = {
      nodeType: 'document', nodeId: 'gn_1', flowId: 'p.m.0088', projectId: 'p', title: '카드 제목', nodeRun: null, readOnly: null,
      cardDocuments: [{ slot: 'report', docType: 'TR', docId: 'p.m.0088.0002-TR', title: '레포트 원제목' }],
    } as unknown as Parameters<typeof openCardLineAction>[0]
    const line = { name: '레포트', nameKey: 'gui.inspector.taskCard.slot.report', cardSlot: 'report', id: 'p.m.0088.0002-TR' }
    try {
      openCardLineAction(target, line, 'write', null)
      openCardLineAction(target, { ...line, nameKey: null }, 'write', null)
      expect(slotWriteWindowList()).toHaveLength(1)
      expect(slotWriteWindowList()[0].request).toMatchObject({ name: '레포트', nameKey: null, cardTitle: '카드 제목', slot: 'report' })
      openCardLineAction(target, line, 'ai_invoke', null)
      expect(documentAiWindowList()).toHaveLength(1)
      expect(documentAiWindowList()[0].request).toMatchObject({ name: '레포트', nameKey: 'gui.inspector.taskCard.slot.report', title: '레포트 원제목' })
    } finally {
      for (const win of [...slotWriteWindowList()]) closeSlotWriteWindow(win.key)
      closeAllDocumentWindows()
    }
  })

  it('lineKindName: the key in the current language, else the name as it came (no reverse lookup)', () => {
    const te = (key: string) => i18n.global.te(key)
    expect(lineKindName({ name: '지시서', nameKey: 'gui.common.close' }, t, te)).toBe('닫기')
    expect(lineKindName({ name: '지시서' }, t, te)).toBe('지시서')
    expect(lineKindName({ name: '지시서', nameKey: 'gui.no.such.key' }, t, te)).toBe('지시서')
    expect(lineKindName({ name: 'Instruction', nameKey: null }, t, te)).toBe('Instruction')
  })
})

describe('F11/F12 — failures without server words', () => {
  it.each([
    ['ja', 'AI グループを読み込めませんでした。'],
    ['en', 'Could not load AI groups.'],
  ] as const)('an AI request reports its own localized fallback (%s), not an English default', async (locale, expected) => {
    setLocale(locale)
    api.getRequest.mockRejectedValueOnce({ isAxiosError: true, request: {}, message: 'Network Error' })
    const error = await listAiGroups().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(AiRequestError)
    expect((error as Error).message).toBe(expected)
    api.getRequest.mockRejectedValueOnce(refusal(500, { error: { code: 'brand_new_code' } }))
    expect(((await listAiGroups().catch((e: unknown) => e)) as Error).message).toBe(`${expected} (brand_new_code)`)
    api.getRequest.mockRejectedValueOnce(refusal(409, { error: { code: 'brand_new_code', message: '서버가 보낸 설명' } }))
    expect(((await listAiGroups().catch((e: unknown) => e)) as Error).message).toBe('서버가 보낸 설명 (brand_new_code)')
  })

  it('Project and AI keep a body message the Error repeats (message != code)', async () => {
    const message = 'The group is archived; restore it before creating a chat.'
    const repeated = () => Object.assign(new Error(message), { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'group_archived', message } } } })
    api.postRequest.mockRejectedValueOnce(repeated())
    expect(((await createProject({ name: '프로젝트 가', id: 'demo' }).catch((e: unknown) => e)) as Error).message).toBe(message)
    api.getRequest.mockRejectedValueOnce(repeated())
    expect(((await listAiGroups().catch((e: unknown) => e)) as Error).message).toBe(`${message} (group_archived)`)
    api.postRequest.mockRejectedValueOnce(Object.assign(new Error('Request failed with status code 502'), { isAxiosError: true, response: { status: 502, data: {} } }))
    expect(((await createProject({ name: '프로젝트 가', id: 'demo' }).catch((e: unknown) => e)) as Error).message).toBe('')
  })

  it('a Conversation failure carries only the server words, so the notice is not followed by an English line', async () => {
    api.patchRequest.mockRejectedValueOnce({ isAxiosError: true, request: {}, message: 'Network Error' })
    const silent = await conversationApi.renameChat('p.m.0088.0003-CH', '새 제목').catch((e: unknown) => e)
    expect(silent).toBeInstanceOf(ConversationRequestError)
    expect((silent as Error).message).toBe('')
    api.patchRequest.mockRejectedValueOnce(refusal(403, { ok: false, error: { code: 'forbidden', message: '권한이 없습니다.' } }))
    const said = await conversationApi.renameChat('p.m.0088.0003-CH', '새 제목').catch((e: unknown) => e)
    expect((said as InstanceType<typeof ConversationRequestError>).message).toBe('권한이 없습니다.')
    expect((said as InstanceType<typeof ConversationRequestError>).code).toBe('forbidden')
  })
})
