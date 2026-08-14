// flowgate.default.0410 TR0009 rev2 — 문서 헤더 [작성자] 칸.
//
// 반려: "아무것도 변한게 없다 / 작성자가 계속 test라고 나오는데?" 개발기 8080 에서
// gpt-luna 가 만든 문서를 열어도 [작성자] 는 등록 계정 이름('test')만 보였다. 스냅샷
// (documents.origin_provider_name / origin_ai_run_id)은 이미 그 행에 저장돼 있었고,
// 배지가 그룹 정보 모달 안에만 있어서 정작 사람이 보는 칸이 그대로였던 것이다.
//
// 그래서 이 파일이 고정하는 것은 "그 칸에 무엇이 보이는가" 하나다.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const DOC_ID = 'test.test.0011.0005-TR'
// 개발기 8080 의 실제 계정 이름. 반려문의 'test' 가 바로 이 값이다.
const ACCOUNT_ID = 'b6f3e67a-35a2-4635-a667-505d550b3cb0'
const ACCOUNT_NAME = 'test'

function detailResponse(extra: Record<string, unknown>) {
  return {
    data: {
      doc_id: DOC_ID,
      title: '작업레포트',
      status: 'open',
      type_code: 'TR',
      doc_review_status: 'pending_review',
      project_id: 'test',
      group_id: 'test.test.0011',
      created_at: '2026-08-13T16:20:42+09:00',
      owner_id: ACCOUNT_ID,
      ...extra,
    },
  }
}

function makeTab() {
  return { id: DOC_ID, title: '작업레포트', path: '', type: 'md', typeCode: 'TR' }
}

function mountWith(extra: Record<string, unknown>) {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse(extra))
    if (url.includes('/users/')) return Promise.resolve({ data: { username: ACCOUNT_NAME } })
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
  return shallowMount(DocHeader, { props: { tab: makeTab() as any }, global: { plugins: [i18n] } })
}

// 헤더 메타 줄의 두 번째 칸이 [작성자] 다. 라벨로 찾아 다른 칸과 헷갈리지 않게 한다.
function authorCell(wrapper: ReturnType<typeof mountWith>) {
  const label = i18n.global.t('main.doc_header.label_author')
  const cell = wrapper.findAll('.doc-meta-item').find((c) => c.find('label').text() === label)
  expect(cell).toBeTruthy()
  return cell!
}

// 칸 전체 텍스트에는 라벨('작성자')이 섞이므로, 값 단언은 값 span 만 본다.
function authorValue(wrapper: ReturnType<typeof mountWith>): string {
  return authorCell(wrapper).find('span').text()
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
})

describe('DocHeader [작성자] — 문서를 만든 AI', () => {
  it('AI 스냅샷이 있으면 계정 이름이 아니라 그 AI 를 보여 준다', async () => {
    const wrapper = mountWith({
      origin_provider_name: 'gpt-luna',
      origin_ai_run_id: 'aiv_20260813_000002',
    })
    await flushPromises()

    const cell = authorCell(wrapper)
    expect(authorValue(wrapper)).toBe(
      i18n.global.t('main.doc_header.author_ai', { provider: 'gpt-luna' }),
    )
    expect(authorValue(wrapper)).toContain('gpt-luna')
    // 반려의 핵심: 이 칸에 더 이상 계정 이름이 서 있지 않다.
    expect(authorValue(wrapper)).not.toContain(ACCOUNT_NAME)
    expect(cell.find('.doc-author-ai').exists()).toBe(true)
    wrapper.unmount()
  })

  it('실행 ID 와 등록 계정은 행을 늘리지 않고 title 로만 따라온다', async () => {
    const wrapper = mountWith({
      origin_provider_name: 'gpt-luna',
      origin_ai_run_id: 'aiv_20260813_000002',
    })
    await flushPromises()

    const pill = authorCell(wrapper).find('.doc-author-ai')
    const title = pill.attributes('title') ?? ''
    expect(title).toContain('gpt-luna')
    expect(title).toContain('aiv_20260813_000002')
    expect(title).toContain(ACCOUNT_NAME)
    // 설명 줄을 따로 만들지 않는다 — 배지 하나뿐이다.
    expect(authorCell(wrapper).findAll('span').length).toBe(1)
    wrapper.unmount()
  })

  it('스냅샷이 없는 기존 문서는 예전처럼 등록 계정 이름을 보여 준다', async () => {
    const wrapper = mountWith({ origin_provider_name: null, origin_ai_run_id: null })
    await flushPromises()

    const cell = authorCell(wrapper)
    expect(authorValue(wrapper)).toBe(ACCOUNT_NAME)
    expect(cell.find('.doc-author-ai').exists()).toBe(false)
    wrapper.unmount()
  })

  it('공백뿐인 공급자 이름은 빈 배지를 만들지 않는다', async () => {
    const wrapper = mountWith({ origin_provider_name: '   ', origin_ai_run_id: 'aiv_x' })
    await flushPromises()

    const cell = authorCell(wrapper)
    expect(cell.find('.doc-author-ai').exists()).toBe(false)
    expect(authorValue(wrapper)).toBe(ACCOUNT_NAME)
    wrapper.unmount()
  })

  it('계정 조회가 실패해도 AI 이름은 그대로 보인다', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) {
        return Promise.resolve(detailResponse({
          origin_provider_name: 'Claude Sonnet 5',
          origin_ai_run_id: 'aiv_20260813_000008',
        }))
      }
      if (url.includes('/users/')) return Promise.reject(new Error('403'))
      if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
      return Promise.resolve({ data: {} })
    })
    const wrapper = shallowMount(DocHeader, {
      props: { tab: makeTab() as any },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    expect(authorValue(wrapper)).toContain('Claude Sonnet 5')
    wrapper.unmount()
  })

  // jsdom 은 CSS 를 적용하지 않으므로 긴 이름이 헤더를 밀어내지 않는다는 계약은
  // 스타일 블록에서 직접 확인한다(DocHeader.discardChip.spec.ts 와 같은 수법).
  it('긴 공급자 이름은 한 줄 말줄임 계약을 갖는다', () => {
    const sfc = readFileSync(
      join(__dirname, '..', '..', 'src', 'main', 'components', 'DocHeader.vue'),
      'utf-8',
    )
    const rule = sfc.slice(sfc.indexOf('.doc-author-ai {'))
    const block = rule.slice(0, rule.indexOf('}'))
    expect(block).toContain('overflow: hidden')
    expect(block).toContain('text-overflow: ellipsis')
    expect(block).toContain('white-space: nowrap')
  })
})
