import { describe, expect, it, vi } from 'vitest'

// F86 — the license fields of the voice catalog (contract 1: components[].license_url, notice_keys), the server's
// English unavailable reason shown through its key, and the single opener of the license window.
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: vi.fn(),
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

import { toVoiceCatalog } from '@/features/conversation/voice/api'
import { voiceReasonText } from '@/features/conversation/voice/messages'
import { LICENSE_PROJECT_TAB, closeLicenseWindow, licenseWindowState, openLicenseWindow, selectLicenseTab } from '@/features/licenses/licenseWindow'

describe('voice catalog license fields', () => {
  it('reads license_url per component and notice_keys per service', () => {
    const [service] = toVoiceCatalog({ services: [{
      id: 'whisper', name: 'whisper.cpp', license: 'MIT', models: [],
      components: [
        { filename: 'whisper-bin-x64.zip', component_type: 'engine', license: 'MIT', distribution: 'direct', url: 'https://u', license_url: 'https://l' },
        { filename: 'jfk.wav', component_type: 'sample', license: '', distribution: 'direct', url: 'https://s' },
      ],
      notice_keys: ['vcpp_runtime', 7, ''],
    }] })
    expect(service.components?.map((c) => c.licenseUrl)).toEqual(['https://l', null])
    expect(service.noticeKeys).toEqual(['vcpp_runtime'])
  })

  it('defaults to no notice keys when the server sends none', () => {
    const [service] = toVoiceCatalog({ services: [{ id: 'vosk', models: [], components: [{ filename: 'a.zip' }] }] })
    expect(service.noticeKeys).toEqual([])
    expect(service.components?.[0].licenseUrl).toBeNull()
  })
})

describe('voiceReasonText', () => {
  const t = (key: string) => `T:${key}`
  it('shows the Windows-only reason through its key once the key exists', () => {
    expect(voiceReasonText('This release requires Windows x64', t, () => true)).toBe('T:gui.voice.unavailable_windows')
    expect(voiceReasonText('This release requires Windows x64', t, () => false)).toBe('This release requires Windows x64')
    expect(voiceReasonText('other', t, () => true)).toBe('other')
    expect(voiceReasonText(null, t, () => true)).toBe('')
  })
})

describe('openLicenseWindow', () => {
  it('opens one window on the asked tab and raises it again on a second request', () => {
    closeLicenseWindow()
    openLicenseWindow('vosk')
    expect(licenseWindowState.open).toBe(true)
    expect(licenseWindowState.tab).toBe('vosk')
    const seq = licenseWindowState.seq
    openLicenseWindow()
    expect(licenseWindowState.tab).toBe(LICENSE_PROJECT_TAB)
    expect(licenseWindowState.seq).toBe(seq + 1)
    selectLicenseTab('whisper')
    expect(licenseWindowState.tab).toBe('whisper')
    closeLicenseWindow()
    expect(licenseWindowState.open).toBe(false)
  })
})
