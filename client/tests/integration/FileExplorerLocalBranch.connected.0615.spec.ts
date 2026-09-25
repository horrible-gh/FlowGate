// flowgate.default.0615 T0004 §7 / rev2 rejection finding 3 — "단순 mock component
// test만 추가하지 말고 branch catalog → explorer option → 실제 branch tree read 경계를
// 연결" for real: the two rev1 suites (server/tests/test_git_local_branch_tree_0615.py's
// TestClient-only connected regression, and FileExplorer.localBranch.0615.spec.ts's
// entirely-mocked @shared/api) never actually touch each other, so a drift in the
// server's response shape or the client's request routing would pass both silently.
//
// This spec closes that gap directly: it spawns the REAL server code
// (server/tests/_local_branch_live_server_0615.py -- the same git_routes.router this
// T added, wired up exactly like test_git_local_branch_tree_0615.py's own `full_repo`
// fixture) as a real subprocess bound to a real localhost port, then mounts the REAL
// (non-mocked) FileExplorer.vue and GitBranchManager.vue and lets @shared/api's axios
// instance make REAL HTTP requests against it. No @shared/api mock exists in this file.
//
// This is a deliberate, narrowly-scoped exception to this repo's rule that unit tests
// must never reach a real server (0394 T0004 / R0001 §5.1, enforced by
// tests/setup/blockNetwork.ts for every OTHER spec): it lives in its own
// tests/integration/ directory, runs under its own vitest.integration.config.ts (which
// does not load blockNetwork.ts), is excluded from the default vitest.config.ts run,
// and only runs via the separate `npm run test:integration` script -- so the ordinary
// `npm test` gate keeps the exact guarantee blockNetwork.ts was added for.
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import FileExplorer from '@main/components/FileExplorer.vue'
import GitBranchManager from '@main/components/GitBranchManager.vue'
import { useLayoutStore } from '@main/stores/layout'

// The confirm() modal itself (stack/teleport/ESC lifecycle) is a UI concern already
// covered by other specs (e.g. GitBranchManager.catalogChanged.0615.spec.ts uses the
// same stub). What THIS spec exists to prove is real HTTP shape/routing/selector
// wiring end to end, so the delete confirmation is stubbed to resolve immediately.
const { dialogConfirm } = vi.hoisted(() => ({ dialogConfirm: vi.fn(() => Promise.resolve(true)) }))
vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: dialogConfirm,
}))

const PORT = 18789 // must match vitest.integration.config.ts's LIVE_SERVER_PORT
const READY_URL = `http://127.0.0.1:${PORT}/flowgate/_live_server_ready`

let serverProcess: ChildProcess | null = null
let tmpRoot: string

async function waitForReady(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastError: unknown = null
  while (Date.now() < deadline) {
    try {
      const res = await fetch(READY_URL)
      if (res.ok) return
    } catch (e) {
      lastError = e
    }
    await new Promise((resolve) => { setTimeout(resolve, 200) })
  }
  throw new Error(`live server never became ready at ${READY_URL}: ${String(lastError)}`)
}

// A real HTTP round trip against the spawned server resolves over an actual OS
// socket -- a macrotask on Node's event loop -- not a microtask, so a single
// `await flushPromises()` after triggering a request is not guaranteed to have
// seen the response by the time an assertion runs (flushPromises only drains
// what is already queued at the moment it is called). Every assertion below that
// depends on a real request having completed polls for its condition instead of
// asserting immediately after one flush.
async function waitFor(check: () => boolean, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    await flushPromises()
    if (check()) return
    if (Date.now() >= deadline) {
      throw new Error(`waitFor: condition not met within ${timeoutMs}ms`)
    }
    await new Promise((resolve) => { setTimeout(resolve, 50) })
  }
}

beforeAll(async () => {
  tmpRoot = mkdtempSync(join(tmpdir(), 'fg-0615-connected-'))
  const pythonExe = process.env.FLOWGATE_TEST_PYTHON || 'python'
  const serverScript = join(__dirname, '..', '..', '..', 'server', 'tests', '_local_branch_live_server_0615.py')
  serverProcess = spawn(pythonExe, [serverScript, '--port', String(PORT), '--repo-root', tmpRoot], {
    stdio: 'pipe',
  })
  let stderr = ''
  serverProcess.stderr?.on('data', (chunk) => { stderr += String(chunk) })
  serverProcess.on('exit', (code) => {
    if (code !== null && code !== 0) {
      // eslint-disable-next-line no-console
      console.error(`live server exited early (code ${code}):\n${stderr}`)
    }
  })
  await waitForReady(20_000)
}, 30_000)

afterAll(() => {
  serverProcess?.kill()
  try { rmSync(tmpRoot, { recursive: true, force: true }) } catch { /* best-effort cleanup */ }
})

describe('File Explorer + Branch Manager against a REAL server (0615 T0004 rev2 connected regression)', () => {
  it('a branch created on the real server appears as a File Explorer option, selecting it reads that server\'s real tree/blob, and deleting it falls back to base', async () => {
    window.__accessToken__ = 'live-server-test-token'
    setActivePinia(createPinia())
    useLayoutStore().setFileExplorerCollapsed(false)

    const explorer = mount(FileExplorer, {
      props: { projectId: 'flowgate' },
      global: { plugins: [i18n] },
    })
    // Real GET /git/branches (only `main`, kind=base) + real GET /git/status
    // (no slots): no local-branch option yet, so the selector stays hidden.
    // Waited via the base tree actually rendering rather than a fixed flush count,
    // since both are real requests over a real socket.
    await waitFor(() => explorer.findAll('.tree-lbl').length > 0)
    expect(explorer.find('.fx-group-select').exists()).toBe(false)

    const manager = mount(GitBranchManager, {
      props: { projectId: 'flowgate' },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
    })
    // Wait for the manager's own real GET /git/branches to resolve and populate
    // the create-source select (createSource starts as '' until that catalog
    // load's syncSelections runs) -- submitting before that would POST an empty
    // source_branch.
    await waitFor(() => {
      const select = manager.find('[data-test="branch-zone-create"] select')
      return select.exists() && (select.element as HTMLSelectElement).value === 'main'
    })

    // Real POST /git/branches against the real server.
    await manager.get('[data-test="branch-zone-create"] input').setValue('test-branch-0615')
    await manager.get('[data-test="branch-zone-create"]').trigger('submit')

    // GitBranchManager's real fg:git_branches_changed dispatch drives File
    // Explorer's real (non-mocked) GET /git/branches, which now really lists the
    // branch just created with kind=local.
    await waitFor(() => {
      const select = explorer.find('.fx-group-select')
      return select.exists() && select.findAll('option').some((o) => o.text() === 'test-branch-0615')
    })
    let options = explorer.find('.fx-group-select').findAll('option').map((o) => o.text())
    expect(options).toContain('test-branch-0615')

    // Selecting it issues the real GET /git/branches/tree route this T added.
    await explorer.get('.fx-group-select').setValue('local:test-branch-0615')
    await waitFor(() => explorer.findAll('.tree-lbl').some((n) => n.text() === 'README.md'))

    expect(explorer.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(true)
    expect(explorer.findAll('.tree-lbl').map((n) => n.text())).toContain('README.md')

    // Real DELETE /git/branches/test-branch-0615.
    await manager.get('[data-test="delete-select"]').setValue('test-branch-0615')
    await manager.get('[data-test="delete-btn"]').trigger('click')

    // Falls back to base and drops the deleted branch's tree -- the real-server
    // equivalent of FileExplorer.localBranch.0615.spec.ts's mocked delete-fallback
    // regression, and the real GET /git/branches/tree for the deleted branch that
    // a stale selection would have hit now really answers 404 branch_not_found
    // (proven server-side by test_git_local_branch_tree_0615.py's own connected
    // regression step 7; here it is the reason the option and badge are both gone).
    await waitFor(() => {
      const select = explorer.find('.fx-group-select')
      return !select.exists() || !select.findAll('option').some((o) => o.text() === 'test-branch-0615')
    })
    const selectAfterDelete = explorer.find('.fx-group-select')
    options = selectAfterDelete.exists() ? selectAfterDelete.findAll('option').map((o) => o.text()) : []
    expect(options).not.toContain('test-branch-0615')
    expect(explorer.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
    // rev3 -- the selector option is now removed from `branchCatalog` synchronously,
    // the instant the delete event is handled (0615 rev3 fix for the ghost-option
    // rejection), independent of the real base-tree fetch this same delete-fallback
    // reload() also kicks off. Those two are separate async completions over the
    // same real socket: the option can (and here, does) disappear before the base
    // tree's own real GET resolves. Waiting on the option's removal above is no
    // longer a reliable proxy for "the tree has also settled to base" -- that needs
    // its own real-response wait rather than an immediate assertion.
    await waitFor(() => !explorer.findAll('.tree-lbl').some((n) => n.text() === 'README.md'))
    expect(explorer.findAll('.tree-lbl').map((n) => n.text())).not.toContain('README.md')

    explorer.unmount()
    manager.unmount()
  })
})
