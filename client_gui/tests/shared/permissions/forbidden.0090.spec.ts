import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The common refusal handling lives in shared/api.ts's response interceptor: a 403 raises the "no permission" box
// (shared/permissions/forbidden) once for every caller, a quiet probe or a call that words its own refusal
// does not, and a 401 still ends at the sign-in page. The adapter is replaced; the interceptor is the real one.

import api, { getRequest, postRequest } from '@shared/api'
import { dismissForbidden, isQuietForbiddenPath, onForbidden, useForbidden } from '@shared/permissions/forbidden'
import { onCapabilitiesInvalidated } from '@shared/permissions/capabilities'

let answer: (config: InternalAxiosRequestConfig) => { status: number; data: unknown }
const adapter: AxiosAdapter = async (config) => {
  const { status, data } = answer(config)
  const response = { status, data, headers: {}, statusText: '', config }
  if (status >= 400) throw new AxiosError(`HTTP ${status}`, 'ERR_BAD_REQUEST', config, null, response)
  return response
}
const originalAdapter = api.defaults.adapter

beforeEach(() => {
  api.defaults.adapter = adapter
  dismissForbidden()
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  api.defaults.adapter = originalAdapter
  dismissForbidden()
})

describe('403 → the common "no permission" box', () => {
  it('a refused read raises the box with the path (no query) and the server\'s reason', async () => {
    answer = () => ({ status: 403, data: { detail: 'project_access_denied' } })
    await expect(getRequest('/api/v1/projects/p2/groups', { limit: 5 })).rejects.toBeTruthy()
    const { event } = useForbidden()
    expect(event.value?.path).toBe('/api/v1/projects/p2/groups')
    expect(event.value?.detail).toBe('project_access_denied')
  })

  it('a refused write raises it too — a button with no words of its own is never silent', async () => {
    answer = () => ({ status: 403, data: { detail: { code: 'role_limit' } } })
    await expect(postRequest('/api/v1/documents', { a: 1 })).rejects.toBeTruthy()
    expect(useForbidden().event.value?.detail).toBe('role_limit')
  })

  it('the caller still gets the error to word its own refusal', async () => {
    answer = () => ({ status: 403, data: { detail: 'x' } })
    await expect(getRequest('/api/v1/x')).rejects.toMatchObject({ response: { status: 403 } })
  })

  it('a call that words its own refusal (fgSuppressForbidden) does not raise the box, but the listeners still hear it', async () => {
    answer = () => ({ status: 403, data: { detail: 'x' } })
    const heard = vi.fn()
    const off = onForbidden(heard)
    await expect(getRequest('/api/v1/x', {}, { fgSuppressForbidden: true } as never)).rejects.toBeTruthy()
    expect(useForbidden().event.value).toBeNull()
    expect(heard).toHaveBeenCalledTimes(1)
    off()
  })

  it('quiet probes (permission reads, sign-in, self-check line, chat image check) never raise the box', async () => {
    answer = () => ({ status: 403, data: { detail: 'x' } })
    for (const path of ['/api/v1/projects/p1/capabilities', '/rbac/projects/p1/my-permissions', '/auth/login', '/api/v1/flows/f1/nodes/n1/self-check', '/api/v1/flows/f1/images/i1/content']) {
      await expect(getRequest(path)).rejects.toBeTruthy()
    }
    expect(useForbidden().event.value).toBeNull()
    expect(isQuietForbiddenPath('/api/v1/documents/x')).toBe(false)
    expect(isQuietForbiddenPath('/flowgate/api/v1/projects/p1/capabilities?x=1')).toBe(true)
  })

  it('a 403 makes the screen ask for its permissions again — once per burst, not once per refusal', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      vi.setSystemTime(Date.now() + 60_000) // past the gap left by the earlier refusals of this file
      answer = () => ({ status: 403, data: {} })
      const heard = vi.fn()
      const off = onCapabilitiesInvalidated(heard)
      for (let i = 0; i < 3; i++) await expect(getRequest(`/api/v1/x${i}`)).rejects.toBeTruthy()
      expect(heard).toHaveBeenCalledTimes(1)
      vi.setSystemTime(Date.now() + 10_000)
      await expect(getRequest('/api/v1/later')).rejects.toBeTruthy()
      expect(heard).toHaveBeenCalledTimes(2)
      off()
    } finally {
      vi.useRealTimers()
    }
  })

  it('other errors (404, 500) never raise it', async () => {
    for (const status of [404, 409, 422]) {
      answer = () => ({ status, data: {} })
      await expect(postRequest('/api/v1/x', {})).rejects.toBeTruthy()
    }
    expect(useForbidden().event.value).toBeNull()
  })

  it('dismiss closes it', async () => {
    answer = () => ({ status: 403, data: {} })
    await expect(getRequest('/api/v1/x')).rejects.toBeTruthy()
    useForbidden().dismiss()
    expect(useForbidden().event.value).toBeNull()
  })
})

describe('401 (turned-off account, ended session) → the sign-in page, not the "no permission" box', () => {
  for (const detail of ['User account is inactive', 'session_revoked']) {
    it(`${detail}: no refresh token left → /login and no box`, async () => {
      const location = { pathname: '/app/designer', search: '', href: '', origin: 'http://localhost' }
      const saved = window.location
      delete (window as unknown as { location?: unknown }).location
      ;(window as unknown as { location: typeof location }).location = location
      try {
        answer = () => ({ status: 401, data: { detail } })
        await expect(getRequest('/api/v1/projects')).rejects.toBeTruthy()
        expect(location.href).toBe(`/login?next=${encodeURIComponent('/app/designer')}`)
        expect(useForbidden().event.value).toBeNull()
      } finally {
        ;(window as unknown as { location: Location }).location = saved
      }
    })
  }
})
