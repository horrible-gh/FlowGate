import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useTabsStore } from '@main/stores/tabs'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
})

describe('useTabsStore.openTab metadata merge', () => {
  it('1. new tab carries full metadata', () => {
    const store = useTabsStore()
    store.openTab({
      id: 'x.x.0001.0005-D',
      title: '0005-D — Design',
      typeCode: 'D',
      path: '/abs/docs/0005-D.md',
      type: 'md',
    })
    expect(store.tabs).toHaveLength(1)
    expect(store.tabs[0]).toMatchObject({
      id: 'x.x.0001.0005-D',
      typeCode: 'D',
      path: '/abs/docs/0005-D.md',
    })
    expect(store.activeTabId).toBe('x.x.0001.0005-D')
  })

  it('2. existing tab without typeCode merges incoming typeCode and path', () => {
    const store = useTabsStore()
    store.tabs = [
      { id: 'doc-x', title: 'doc-x', path: '', type: 'md', typeCode: undefined },
    ]
    store.openTab({
      id: 'doc-x',
      title: 'doc-x — Design',
      typeCode: 'D',
      path: '/abs/docs/doc-x.md',
      type: 'md',
    })
    expect(store.tabs[0]).toMatchObject({
      id: 'doc-x',
      typeCode: 'D',
      path: '/abs/docs/doc-x.md',
    })
    expect(store.activeTabId).toBe('doc-x')
  })

  it('3. existing tab with typeCode is not downgraded when incoming omits typeCode', () => {
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'doc-x',
        title: 'doc-x',
        path: '/abs/docs/doc-x.md',
        type: 'md',
        typeCode: 'D',
      },
    ]
    store.openTab({
      id: 'doc-x',
      title: 'doc-x',
      path: '',
      type: 'md',
    })
    expect(store.tabs[0]).toMatchObject({
      typeCode: 'D',
      path: '/abs/docs/doc-x.md',
    })
  })

  it('4. existing unsupported tab transitions to concrete type and merges typeCode', () => {
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'doc-u',
        title: 'No MD',
        path: '',
        type: 'unsupported',
        typeCode: undefined,
      },
    ]
    store.openTab({
      id: 'doc-u',
      title: 'doc-u — Design',
      path: '/abs/docs/doc-u.md',
      type: 'md',
      typeCode: 'D',
      mdPath: '/abs/docs/doc-u.md',
    })
    expect(store.tabs[0]).toMatchObject({
      type: 'md',
      typeCode: 'D',
      path: '/abs/docs/doc-u.md',
      mdPath: '/abs/docs/doc-u.md',
    })
  })

  it('5. existing file tab is not converted to a document tab', () => {
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'file-x',
        title: 'readme.md',
        path: '/proj/readme.md',
        type: 'md',
        projectId: 'p',
        typeCode: undefined,
      },
    ]
    store.openTab({
      id: 'file-x',
      title: 'readme.md',
      path: '/proj/readme.md',
      type: 'md',
      typeCode: 'D',
    })
    expect(store.tabs[0]).toMatchObject({
      type: 'md',
      projectId: 'p',
      typeCode: undefined,
    })
  })

  it('merges mdPath when incoming provides a non-empty value', () => {
    const store = useTabsStore()
    store.tabs = [
      { id: 'doc-x', title: 'doc-x', path: '', type: 'md', typeCode: 'D' },
    ]
    store.openTab({
      id: 'doc-x',
      title: 'doc-x',
      path: '/abs/docs/doc-x.md',
      type: 'md',
      typeCode: 'D',
      mdPath: '/abs/docs/doc-x.md',
    })
    expect(store.tabs[0].mdPath).toBe('/abs/docs/doc-x.md')
  })

  it('increments the open request sequence when reopening the active tab', () => {
    const store = useTabsStore()
    const tab = {
      id: 'doc-x',
      title: 'doc-x',
      path: '/abs/docs/doc-x.md',
      type: 'md' as const,
    }

    store.openTab(tab)
    const firstRequest = store.openTabRequestSeq
    store.openTab(tab)

    expect(store.activeTabId).toBe('doc-x')
    expect(store.openTabRequestSeq).toBe(firstRequest + 1)
  })
})
