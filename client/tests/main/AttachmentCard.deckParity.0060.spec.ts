// flowgate.default.0060 — 승인 시안 `wdkcvrmk` 대조 회귀.
//
// 시안이 등록한 네 화면이 곧 첨부 카드의 네 상태다(D0010 6-5).
//   ① empty     — 첨부 0개: 큰 드롭존 + 빈 안내문 + 배지 "0개"
//   ② main      — 첨부 N개: 얇은 한 줄 드롭존 + 목록 N줄 + 배지 "N개"
//   ③ deleting  — 삭제를 누른 줄이 `.removing` 전환으로 옅어지며 밀리고, 전환이 끝난 뒤 목록이 다시 그려짐
//   ④ collapsed — 제목 줄만 남고 배지·요약은 유지, 파일을 끌어오면 자동으로 펼쳐짐
//
// 함께 못박는 것이 하나 더 있다: **시안에 없는 것이 붙어 있어도 반려**라는 것.
// 그래서 [빈 상태 보기](시안 안의 데모 장치), [복사], 별도 [다운로드] 버튼이 없다는 것도 검사한다
// (D0010 6-2 / 6-7, DS0009 5절). 목록 줄의 다운로드 진입점은 파일명 자체다.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import AttachmentCard from '@main/components/AttachmentCard.vue'
import { mountMainPanel } from '../helpers/mountMainPanel'

const { getRequest, deleteRequest, postFormRequest, downloadBlobRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  deleteRequest,
  postFormRequest,
  downloadBlobRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const DOC_ID = 'flowgate.default.0060.0001-R'

function attachment(filename: string, size: number) {
  return {
    doc_id: DOC_ID,
    original_filename: filename,
    filename,
    size,
    content_type: 'application/octet-stream',
    content_sha256: 'a'.repeat(64),
    path: `documents/flowgate/main/default/0060/0001-R/${filename}`,
    path_base: 'storage',
    uploaded_by: 'usr_admin',
    uploaded_at: '2026-08-15T00:40:12Z',
  }
}

// 시안 `main` 화면이 열릴 때 들고 있는 세 파일과 같은 구성(표 문서 / 이미지 / PDF).
const THREE = [
  attachment('요구사항_정리.xlsx', 862_000),
  attachment('화면_캡처_최종.png', 1_430_000),
  attachment('킥오프_회의록.pdf', 233_000),
]

function listResponse(items: unknown[]) {
  return { data: { data: { doc_id: DOC_ID, attachments: items, count: items.length } } }
}

async function mountCard(items: unknown[], readOnly = false) {
  getRequest.mockResolvedValue(listResponse(items))
  const wrapper = mount(AttachmentCard, {
    props: { docId: DOC_ID, readOnly },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  deleteRequest.mockReset()
  postFormRequest.mockReset()
  downloadBlobRequest.mockReset()
})

describe('시안 wdkcvrmk ① 빈 상태 (empty.html)', () => {
  it('배지가 "0개"이고, 큰 드롭존과 빈 안내문이 함께 보인다', async () => {
    const wrapper = await mountCard([])

    expect(wrapper.find('.attach-count-pill').text()).toBe('0개')
    // has-files 가 없어야 CSS 가 큰 드롭존을 보여 준다(1개 이상이면 얇은 바로 바뀐다).
    expect(wrapper.find('.attach-card').classes()).not.toContain('has-files')
    expect(wrapper.find('.attach-dz-empty').exists()).toBe(true)
    expect(wrapper.find('.attach-dz-icon').exists()).toBe(true)
    expect(wrapper.find('.attach-dz-text').text()).not.toBe('')
    expect(wrapper.find('.attach-select-btn').text()).toContain('파일 선택')
    expect(wrapper.find('.attach-dz-hint').text()).toContain('20MB')
    expect(wrapper.find('.attach-empty-note').exists()).toBe(true)
    expect(wrapper.findAll('.attach-item')).toHaveLength(0)
  })

  it('접힘 요약은 "· 첨부 없음"이다', async () => {
    const wrapper = await mountCard([])
    expect(wrapper.find('.attach-fold-summary').text()).toBe('· 첨부 없음')
  })
})

describe('시안 wdkcvrmk ② 다건 (main)', () => {
  it('배지가 "3개"이고, 얇은 드롭존과 목록 3줄이 보인다', async () => {
    const wrapper = await mountCard(THREE)

    expect(wrapper.find('.attach-count-pill').text()).toBe('3개')
    expect(wrapper.find('.attach-card').classes()).toContain('has-files')
    expect(wrapper.find('.attach-dz-compact').exists()).toBe(true)
    expect(wrapper.findAll('.attach-item')).toHaveLength(3)
    expect(wrapper.find('.attach-empty-note').exists()).toBe(false)
  })

  it('목록 줄은 시안대로 [종류 아이콘 · 파일명 · 크기 · 삭제] 네 칸이다', async () => {
    const wrapper = await mountCard(THREE)
    const row = wrapper.findAll('.attach-item')[0]

    expect(row.find('.attach-item-ico').exists()).toBe(true)
    expect(row.find('.attach-item-name').text()).toBe('요구사항_정리.xlsx')
    expect(row.find('.attach-item-size').text()).toBe('841.8KB')
    expect(row.find('.attach-item-del').exists()).toBe(true)
    // 줄 안의 단추는 딱 둘 — 파일명과 삭제. 시안에 없는 세 번째 단추가 끼면 여기서 걸린다.
    expect(row.findAll('button')).toHaveLength(2)
  })

  it('종류 아이콘 색은 파일 종류로 갈린다 (표 문서 / 이미지 / PDF)', async () => {
    const wrapper = await mountCard(THREE)
    const kinds = wrapper.findAll('.attach-item-ico').map((i) => i.classes())
    expect(kinds[0]).toContain('xls')
    expect(kinds[1]).toContain('img')
    expect(kinds[2]).toContain('pdf')
  })

  it('파일명을 누르면 그 첨부를 내려받는다 — 별도 다운로드 버튼이 없는 이유다', async () => {
    downloadBlobRequest.mockResolvedValue({ data: new Blob(['x']) })
    const wrapper = await mountCard(THREE)

    await wrapper.findAll('.attach-item-name')[2].trigger('click')
    await flushPromises()

    expect(downloadBlobRequest).toHaveBeenCalledTimes(1)
    expect(downloadBlobRequest.mock.calls[0][0]).toBe(
      `/api/v1/documents/${encodeURIComponent(DOC_ID)}/attachments/${encodeURIComponent('킥오프_회의록.pdf')}`,
    )
  })

  it('접힘 요약은 "· {첫 파일명} 외 N개" 이고, 1개일 때는 "외 N개"를 붙이지 않는다', async () => {
    const many = await mountCard(THREE)
    expect(many.find('.attach-fold-summary').text()).toBe('· 요구사항_정리.xlsx 외 2개')

    const one = await mountCard([THREE[0]])
    expect(one.find('.attach-fold-summary').text()).toBe('· 요구사항_정리.xlsx')
  })

  it('다건 업로드는 같은 이름 `file` part를 반복해 한 요청으로 보낸다', async () => {
    postFormRequest.mockResolvedValue({
      data: { data: { doc_id: DOC_ID, attachments: [attachment('새파일.txt', 12)], count: 1 } },
    })
    const wrapper = await mountCard([])

    const input = wrapper.find('input[type="file"]')
    const files = [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')]
    Object.defineProperty(input.element, 'files', { value: files, configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(postFormRequest).toHaveBeenCalledTimes(1)
    const [url, form] = postFormRequest.mock.calls[0]
    expect(url).toBe(`/api/v1/documents/${encodeURIComponent(DOC_ID)}/attachments`)
    expect((form as FormData).getAll('file')).toHaveLength(2)
  })
})

describe('시안 wdkcvrmk ③ 삭제 진행 중 (deleting.html)', () => {
  it('삭제를 누른 줄에 `.removing` 이 붙고, 전환이 끝난 뒤에 목록에서 사라진다', async () => {
    deleteRequest.mockResolvedValue({ data: { data: { file_deleted: true } } })
    const wrapper = await mountCard(THREE)

    await wrapper.findAll('.attach-item-del')[1].trigger('click')
    await flushPromises()

    // 전환 중: 줄은 아직 목록에 있고 `.removing` 만 붙어 있다 — 시안 ③ 화면이 고정해 둔 순간.
    let rows = wrapper.findAll('.attach-item')
    expect(rows).toHaveLength(3)
    expect(rows[1].classes()).toContain('removing')
    expect(rows[0].classes()).not.toContain('removing')

    await rows[1].trigger('transitionend')
    await flushPromises()

    // 전환이 끝난 뒤에 다시 그려진다.
    rows = wrapper.findAll('.attach-item')
    expect(rows).toHaveLength(2)
    expect(wrapper.find('.attach-count-pill').text()).toBe('2개')
    expect(wrapper.text()).not.toContain('화면_캡처_최종.png')
  })

  it('서버가 거절하면 전환을 되돌리고 원래 항목을 다시 표시한다', async () => {
    deleteRequest.mockRejectedValue({
      response: { data: { error: { code: 'DOCUMENT_NOT_MUTABLE' } } },
    })
    const wrapper = await mountCard(THREE)

    await wrapper.findAll('.attach-item-del')[1].trigger('click')
    await flushPromises()

    const rows = wrapper.findAll('.attach-item')
    expect(rows).toHaveLength(3)
    expect(rows[1].classes()).not.toContain('removing')
  })
})

describe('시안 wdkcvrmk ④ 접힘 (collapsed.html)', () => {
  it('기본값은 접힘이다 (0420 R0001)', async () => {
    const wrapper = await mountCard(THREE)
    expect(wrapper.find('.attach-card').classes()).toContain('collapsed')
    expect(wrapper.find('.card-hd-toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.card-hd-caret').exists()).toBe(true)   // 회전은 .collapsed 가 건다
    expect(wrapper.find('.attach-count-pill').text()).toBe('3개')
    expect(wrapper.find('.attach-fold-summary').text()).toBe('· 요구사항_정리.xlsx 외 2개')
  })

  it('제목 줄을 누르면 펼쳐진다', async () => {
    const wrapper = await mountCard(THREE)

    await wrapper.find('.card-hd-toggle').trigger('click')

    expect(wrapper.find('.attach-card').classes()).not.toContain('collapsed')
    expect(wrapper.find('.card-hd-toggle').attributes('aria-expanded')).toBe('true')
  })

  it('접힌 채로 파일을 카드 위로 끌면 자동으로 펼쳐진다', async () => {
    const wrapper = await mountCard(THREE)
    expect(wrapper.find('.attach-card').classes()).toContain('collapsed')

    await wrapper.find('.attach-card').trigger('dragenter')

    expect(wrapper.find('.attach-card').classes()).not.toContain('collapsed')
  })

  it('아코디언은 DocHeader 의 접기 규칙(캐럿 + aria-expanded)을 그대로 쓴다', async () => {
    const wrapper = await mountCard(THREE)
    const toggle = wrapper.find('.card-hd-toggle')
    expect(toggle.exists()).toBe(true)
    expect(toggle.attributes('aria-controls')).toBeTruthy()
    expect(wrapper.find(`#${toggle.attributes('aria-controls')}`).exists()).toBe(true)
  })
})

describe('시안에 없는 요소는 만들지 않는다', () => {
  it('[빈 상태 보기] 데모 버튼이 없다', async () => {
    const wrapper = await mountCard(THREE)
    const demo = wrapper.findAll('button').find((b) => b.text().includes('빈 상태 보기'))
    expect(demo).toBeUndefined()
  })

  it('복사 버튼이 없다 — copy 는 소스를 다루는 쪽이 부르는 API 통로다', async () => {
    const wrapper = await mountCard(THREE)
    const copy = wrapper.findAll('button').find((b) => /복사|copy/i.test(b.text()))
    expect(copy).toBeUndefined()
  })

  it('별도 다운로드 버튼이 없다 — 진입점은 파일명 자체다', async () => {
    const wrapper = await mountCard(THREE)
    const dl = wrapper.findAll('button').find((b) => /내려받|다운로드|download/i.test(b.text()))
    expect(dl).toBeUndefined()
  })
})

describe('AI 실행 중 읽기 전용 (D0010 6-1)', () => {
  it('올리기·지우기는 내려가고 목록과 내려받기만 남는다', async () => {
    const wrapper = await mountCard(THREE, true)

    expect(wrapper.find('.attach-dropzone').exists()).toBe(false)
    expect(wrapper.findAll('.attach-item-del')).toHaveLength(0)
    expect(wrapper.findAll('.attach-item')).toHaveLength(3)
    expect(wrapper.findAll('.attach-item-name')).toHaveLength(3)
    expect(wrapper.find('.attach-count-pill').text()).toBe('3개')
  })
})

describe('MainPanel 배치 (D0010 6-1)', () => {
  it('첨부 카드가 문서 미리보기 카드 바로 위에 온다', async () => {
    getRequest.mockResolvedValue({ data: { questions: [] } })
    const wrapper = await mountMainPanel({
      tabs: [{ id: DOC_ID, title: 'R', path: '', type: 'md' as const, typeCode: 'R' }],
    })

    const html = wrapper.html()
    const attachIdx = html.indexOf('attachment-card')
    const previewIdx = html.indexOf('md-preview-card')
    expect(attachIdx).toBeGreaterThanOrEqual(0)
    expect(previewIdx).toBeGreaterThanOrEqual(0)
    expect(attachIdx).toBeLessThan(previewIdx)
  })
})
