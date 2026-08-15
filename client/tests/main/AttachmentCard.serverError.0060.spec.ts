// flowgate.default.0060 TR0017 rev2 — 반려 "500 (Internal Server Error) / 파일 업로드 중 에러".
//
// 반려문이 화면에서 보여 준 것은 두 줄뿐이었다: 콘솔의 500 한 줄과, 어떤 실패든 똑같이 나오는
// 일반 문구. 서버가 저장에 실패한 5xx는 사용자가 다시 시도할 수 있는 상황과 다르고, 이제 서버가
// 그 실패를 트레이스백까지 로그에 남기므로 화면 문구도 그 사실을 말해야 한다.
//
// 여기서 못박는 것:
//   1. 5xx(코드 없이 오는 진짜 크래시 포함) → 서버 실패 전용 문구
//   2. P0011 봉투의 5xx 코드 세 가지 → 같은 문구
//   3. 4xx 판정(용량·잠금)은 원래 문구 그대로 — 5xx 처리에 휩쓸리지 않는다
//      (rev3: 확장자 4xx는 서버에서 사라졌다. UNSUPPORTED_EXTENSION 을 여기서 빼는 것이
//       "이제 어떤 확장자도 막지 않는다"의 클라이언트 쪽 못이다.)
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import AttachmentCard from '@main/components/AttachmentCard.vue'

const { getRequest, deleteRequest, postFormRequest, downloadBlobRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
  showToast: vi.fn(),
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
  useToast: () => ({ showToast }),
}))

const DOC_ID = 'flowgate.default.0060.0001-R'
const SERVER_MESSAGE = '서버가 파일을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요. 원인은 서버 로그에 기록되었습니다.'

function listResponse(items: unknown[]) {
  return { data: { data: { doc_id: DOC_ID, attachments: items, count: items.length } } }
}

function httpError(status: number, code?: string) {
  return {
    response: { status, data: code ? { error: { code, message: 'x', details: {} } } : 'Internal Server Error' },
  }
}

async function uploadAndReadToast(error: unknown): Promise<string> {
  getRequest.mockResolvedValue(listResponse([]))
  postFormRequest.mockRejectedValueOnce(error)
  const wrapper = mount(AttachmentCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
  await flushPromises()

  const input = wrapper.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [new File(['a'], 'a.txt')], configurable: true })
  await input.trigger('change')
  await flushPromises()

  const call = showToast.mock.calls.at(-1)
  return String(call?.[0] ?? '')
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postFormRequest.mockReset()
  showToast.mockReset()
})

describe('업로드 5xx — 서버 실패는 일반 실패와 구분해 알린다', () => {
  it('봉투 없는 진짜 500(반려문의 그 화면)도 서버 실패 문구로 나온다', async () => {
    expect(await uploadAndReadToast(httpError(500))).toBe(SERVER_MESSAGE)
  })

  it('ATTACHMENT_STORE_FAILED / METADATA_FAILED / OPERATION_FAILED 모두 같은 문구다', async () => {
    for (const code of ['ATTACHMENT_STORE_FAILED', 'ATTACHMENT_METADATA_FAILED', 'ATTACHMENT_OPERATION_FAILED']) {
      expect(await uploadAndReadToast(httpError(500, code))).toBe(SERVER_MESSAGE)
    }
  })

  it('4xx 판정은 원래 문구를 그대로 지킨다', async () => {
    expect(await uploadAndReadToast(httpError(413, 'ATTACHMENT_TOO_LARGE'))).toBe(
      '파일당 최대 20MB까지 올릴 수 있습니다.',
    )
    expect(await uploadAndReadToast(httpError(409, 'DOCUMENT_NOT_MUTABLE'))).toBe(
      '지금은 첨부를 바꿀 수 없습니다. 그룹이 폐기됐거나 AI가 실행 중입니다.',
    )
  })
})
