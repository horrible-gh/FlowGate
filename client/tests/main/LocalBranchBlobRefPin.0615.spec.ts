// flowgate.default.0615 rev1 review finding — client/src/main/stores/explorer.ts:1105-1118,
// MdViewer.vue:204-207, TextViewer.vue:159-161: a local-branch file tab records its own
// gitCommit, but the viewers used to call fetchLocalBranchBlob with only (pid, branch, path)
// and let the store's mutable currentLocalBranchCommit decide the ref. Right after a browser
// restore the store's commit is empty (reads unpinned HEAD); after the explorer advances to a
// newer commit while an older tab stays open, a remount of that tab silently read the NEW
// commit instead of the one the tab/tree snapshot on screen implies. Both viewers must now
// forward props.gitCommit explicitly so the API call -- and the store's cache key -- pin to it.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'
import TextViewer from '@main/components/TextViewer.vue'

const { getRequest, apiGet } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  apiGet: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  default: { get: apiGet },
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  apiGet.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/git/branches/tree')) {
      return Promise.resolve({
        data: { data: { branch: 'test-branch', commit: 'pinned-commit-1', nodes: [] } },
      })
    }
    return Promise.resolve({
      data: { data: { branch: 'test-branch', path: 'a.md', commit: 'pinned-commit-1', content: 'content', binary: false } },
    })
  })
})

describe('MdViewer / TextViewer pin local-branch blob reads to their own gitCommit prop (0615 rev1)', () => {
  it('MdViewer forwards gitCommit as the explicit ref, not the store default', async () => {
    mount(MdViewer, {
      props: {
        path: 'a.md',
        projectId: 'proj',
        gitBranch: 'test-branch',
        gitCommit: 'pinned-commit-1',
      },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()

    const call = getRequest.mock.calls.find((c) => String(c[0]).includes('/git/branches/blob'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toContain('branch=test-branch')
    expect(String(call![0])).toContain('ref=pinned-commit-1')
  })

  it('MdViewer omits ref when the tab carries no gitCommit (falls back to store state, unchanged)', async () => {
    mount(MdViewer, {
      props: {
        path: 'a.md',
        projectId: 'proj',
        gitBranch: 'test-branch',
      },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()

    const call = getRequest.mock.calls.find((c) => String(c[0]).includes('/git/branches/blob'))
    expect(call).toBeTruthy()
    expect(String(call![0])).not.toContain('ref=')
  })

  it('TextViewer forwards gitCommit as the explicit ref, not the store default', async () => {
    mount(TextViewer, {
      props: {
        path: 'a.md',
        projectId: 'proj',
        gitBranch: 'test-branch',
        gitCommit: 'pinned-commit-1',
      },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()

    const call = getRequest.mock.calls.find((c) => String(c[0]).includes('/git/branches/blob'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toContain('branch=test-branch')
    expect(String(call![0])).toContain('ref=pinned-commit-1')
  })

  it('a stale tab (older gitCommit) reopened after the explorer advanced to a newer commit still reads its own pinned commit', async () => {
    // Simulates: tab A opened while the local branch was at commit-old; the explorer then
    // advanced the store's currentLocalBranchCommit to commit-new (e.g. re-selecting the same
    // branch after a create/delete elsewhere advanced its HEAD); tab A remounts (e.g. tab
    // switch) and must still ask for commit-old, never the store's newer commit.
    const { useExplorerStore } = await import('@main/stores/explorer')
    const store = useExplorerStore()
    // Advance the store's own idea of "current" commit for this branch away from the tab's.
    await store.fetchLocalBranchTree('proj', 'test-branch')
    getRequest.mockClear()

    mount(MdViewer, {
      props: {
        path: 'a.md',
        projectId: 'proj',
        gitBranch: 'test-branch',
        gitCommit: 'commit-old',
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const call = getRequest.mock.calls.find((c) => String(c[0]).includes('/git/branches/blob'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toContain('ref=commit-old')
    expect(String(call![0])).not.toContain('ref=pinned-commit-1')
  })
})
