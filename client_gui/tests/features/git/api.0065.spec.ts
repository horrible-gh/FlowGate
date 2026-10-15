import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0065 T#2 — features/git/api.ts: the existing project Git settings contracts the
// `Git 연결` window uses (v0.1 GitSettingsView), their request bodies and what the client keeps.
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest }))

import {
  GitRequestError,
  fetchGitConfig,
  fetchGitProvision,
  gitErrorOf,
  provisionGit,
  saveGitConfig,
  testGitConnection,
} from '@/features/git/api'
import type { GitConfigSave } from '@/features/git/types'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
})

const TOKEN = 'tok-0065-api'
const axiosError = (status: number, data: unknown) => Object.assign(new Error('x'), { isAxiosError: true, response: { status, data } })

const input = (overrides: Partial<GitConfigSave> = {}): GitConfigSave => ({
  repoUrl: 'https://github.com/org/repo.git',
  provider: 'github',
  username: 'dev',
  secret: TOKEN,
  baseBranch: 'v0.2',
  ...overrides,
})

const RAW_CONFIG = {
  project_id: 'p',
  repo_url: 'https://github.com/org/repo.git',
  provider: 'github',
  username: 'dev',
  secret_masked: 'ghp_****abcd',
  has_secret: true,
  base_branch: 'v0.2',
  default_finalize_action: 'wait',
  enabled: false,
  translate_url: null,
  author_name: null,
  author_email: null,
  tr_scope_stage: 'observe',
}

describe('GET git/config', () => {
  it('maps the settings and keeps no token, not even masked', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, configured: true, config: RAW_CONFIG } })
    const view = await fetchGitConfig('p 1')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p%201/git/config')
    expect(view).toEqual({
      configured: true,
      repoUrl: 'https://github.com/org/repo.git',
      provider: 'github',
      username: 'dev',
      baseBranch: 'v0.2',
      enabled: false,
      hasSecret: true,
    })
    expect(JSON.stringify(view)).not.toContain('****')
  })

  it('no settings: configured false with defaults', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, configured: false, config: null } })
    expect(await fetchGitConfig('p')).toEqual({
      configured: false,
      repoUrl: '',
      provider: 'generic',
      username: null,
      baseBranch: 'main',
      enabled: false,
      hasSecret: false,
    })
  })
})

describe('PUT git/config', () => {
  it('sends the window fields with enabled: true and nothing else', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, configured: true, config: { ...RAW_CONFIG, enabled: true } } })
    const view = await saveGitConfig('p', input())
    expect(putRequest).toHaveBeenCalledTimes(1)
    const [path, body] = putRequest.mock.calls[0]
    expect(path).toBe('/api/v1/projects/p/git/config')
    expect(body).toEqual({
      repo_url: 'https://github.com/org/repo.git',
      provider: 'github',
      username: 'dev',
      secret: TOKEN,
      base_branch: 'v0.2',
      enabled: true,
    })
    // Fields the settings screen owns stay unsent, so the server keeps them (exclude_unset).
    for (const key of ['default_finalize_action', 'translate_url', 'author_name', 'author_email', 'tr_scope_stage']) {
      expect(Object.keys(body), key).not.toContain(key)
    }
    expect(view.enabled).toBe(true)
    expect(JSON.stringify(view)).not.toContain(TOKEN)
  })

  it('no token typed → secret null (keep the stored one), never ""; blank username → null', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, configured: true, config: RAW_CONFIG } })
    await saveGitConfig('p', input({ secret: null, username: null }))
    await saveGitConfig('p', input({ secret: '', username: '' }))
    for (const [, body] of putRequest.mock.calls) {
      expect(body.secret).toBeNull()
      expect(body.username).toBeNull()
    }
  })

  it('a refused save throws the normalized code only', async () => {
    putRequest.mockRejectedValue(axiosError(422, { ok: false, error: { code: 'invalid_request', message: `bad ${TOKEN}` } }))
    const error = await saveGitConfig('p', input()).catch((e) => e)
    expect(error).toBeInstanceOf(GitRequestError)
    expect(gitErrorOf(error)).toMatchObject({ code: 'invalid_request', status: 422 })
    expect(error.message).not.toContain(TOKEN)
  })
})

describe('POST git/test-connection', () => {
  it('tests the window values before saving; a blank token goes out as null', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, result: { reachable: true, authenticated: true, remote_default_branch: 'main', base_branch_exists: true, elapsed_ms: 12 } },
    })
    expect(await testGitConnection('p', input())).toEqual({
      reachable: true,
      authenticated: true,
      failureCode: null,
      remoteDefaultBranch: 'main',
    })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/test-connection', {
      repo_url: 'https://github.com/org/repo.git',
      username: 'dev',
      secret: TOKEN,
      base_branch: 'v0.2',
      provider: 'github',
    })
    await testGitConnection('p', input({ secret: '' }))
    expect(postRequest.mock.calls[1][1].secret).toBeNull()
  })

  it('keeps the failure code, drops the server text (it can hold the URL)', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        result: {
          reachable: true,
          authenticated: false,
          remote_default_branch: null,
          failure: { code: 'auth_failed', message: `fatal: Authentication failed for 'https://dev:${TOKEN}@github.com/'` },
        },
      },
    })
    const result = await testGitConnection('p', input())
    expect(result).toEqual({ reachable: true, authenticated: false, failureCode: 'auth_failed', remoteDefaultBranch: null })
    expect(JSON.stringify(result)).not.toContain(TOKEN)
  })

  it('an HTTP 200 { ok: false } is a request failure', async () => {
    postRequest.mockResolvedValue({ status: 200, data: { ok: false, error: { code: 'forbidden', message: 'x' } } })
    expect(gitErrorOf(await testGitConnection('p', input()).catch((e) => e))).toMatchObject({ code: 'forbidden' })
  })
})

describe('git/provision', () => {
  const view = { configured: true, enabled: true, base_branch: 'v0.2', base_path_state: 'empty', base_checkout_exists: false, adopt_snapshot: null, last_attempt: { result: 'failed', reason: 'x', trigger: 'manual', at: 't' } }

  it('POST: result.status failed is an HTTP 200 answer, not an error', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'failed', mode: 'clone', reason: 'fatal: repository not found', provision: view } } })
    const result = await provisionGit('p')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/provision', {})
    expect(result).toEqual({
      status: 'failed',
      mode: 'clone',
      busy: false,
      provision: { configured: true, enabled: true, baseCheckoutExists: false, lastAttemptResult: 'failed' },
    })
    // The server reason text is not kept.
    expect(JSON.stringify(result)).not.toContain('repository not found')
  })

  it('POST: reason git_busy is told apart; ok passes through', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'failed', mode: 'none', reason: 'git_busy', provision: view } } })
    expect((await provisionGit('p')).busy).toBe(true)
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'ok', mode: 'none', reason: null, provision: null } } })
    expect(await provisionGit('p')).toEqual({ status: 'ok', mode: 'none', busy: false, provision: null })
  })

  it('POST: a refused request (409 not_enabled) throws', async () => {
    postRequest.mockRejectedValue(axiosError(409, { ok: false, error: { code: 'not_enabled', message: 'x' } }))
    expect(gitErrorOf(await provisionGit('p').catch((e) => e))).toMatchObject({ code: 'not_enabled', status: 409 })
  })

  it('GET: the Base checkout state', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, provision: view } })
    expect(await fetchGitProvision('p')).toEqual({ configured: true, enabled: true, baseCheckoutExists: false, lastAttemptResult: 'failed' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/provision')
  })
})
