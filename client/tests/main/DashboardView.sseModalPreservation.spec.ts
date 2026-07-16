import { describe, expect, it } from 'vitest'
import dashboardSource from '@main/views/DashboardView.vue?raw'
import fileExplorerSource from '@main/components/FileExplorer.vue?raw'
import groupExplorerSource from '@main/components/GroupExplorer.vue?raw'

describe('Dashboard SSE explorer refresh', () => {
  it('refreshes explorer data without keying away open dialogs', () => {
    expect(dashboardSource).toContain(':refresh-token="explorerRefreshToken"')
    expect(dashboardSource).not.toMatch(/<FileExplorer[^>]*:key=/)
    expect(dashboardSource).not.toMatch(/<GroupExplorer[^>]*:key=/)

    expect(fileExplorerSource).toContain('refreshToken?: number')
    expect(fileExplorerSource).toContain('watch(() => props.refreshToken')

    expect(groupExplorerSource).toContain('refreshToken?: number')
    expect(groupExplorerSource).toContain('watch(() => props.refreshToken')

    // Keeping the explorer component is insufficient if reload() temporarily renders
    // the loading branch: that still unmounts GroupTreeNode and its open dialogs.
    expect(fileExplorerSource).toContain('const silent = nodes.value.length > 0')
    expect(fileExplorerSource).toContain('if (!silent) loading.value = true')
    expect(groupExplorerSource).toContain('const silent = nodes.value.length > 0')
    expect(groupExplorerSource).toContain('if (!silent) loading.value = true')
  })
})
