import { mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import GitFinalizeAxis from '@main/components/GitFinalizeAxis.vue'
import {
  actionNeedsCommitMessage,
  actionOfPosition,
  positionOfAction,
} from '@main/composables/finalizeAxis'

// The matrix the 0331 server publishes in state.action_axes.
const AXES = {
  scopes: ['merge', 'commit', 'none'],
  matrix: {
    merge: { push: 'merge', no_push: 'merge_only' },
    commit: { push: 'commit_push', no_push: 'commit_only' },
    none: { push: 'push', no_push: 'wait' },
  },
  commit_actions: ['merge', 'merge_only', 'commit_push', 'commit_only'],
}

// The shared i18n instance picks its locale from the browser/localStorage, so
// pin it here — these assertions are about the approved Korean wording.
beforeAll(() => {
  ;(i18n.global.locale as unknown as { value: string }).value = 'ko'
})

function mountAxis(action: string, extra: Record<string, unknown> = {}) {
  return mount(GitFinalizeAxis, {
    props: { modelValue: action, axes: AXES, name: 'spec', ...extra },
    global: { plugins: [i18n] },
  })
}

describe('GitFinalizeAxis — scope × push', () => {
  it('renders the approved scope order 머지 → 커밋 → 대기', () => {
    const w = mountAxis('merge')
    const labels = w.findAll('.gf-axis-opt span').map((n) => n.text())
    expect(labels).toEqual(['머지', '커밋', '대기'])
    w.unmount()
  })

  it('shows the current action as a scope pill plus the push checkbox', () => {
    const w = mountAxis('commit_only')
    const selected = w.findAll('.gf-axis-opt.sel').map((n) => n.text())
    expect(selected).toEqual(['커밋'])
    expect((w.find('.gf-axis-push-chk input').element as HTMLInputElement).checked).toBe(false)
    w.unmount()
  })

  it('emits the mapped action when the scope changes, keeping push', async () => {
    const w = mountAxis('merge') // merge scope, push on
    await w.find('.gf-axis-opt[data-scope="commit"] input').setValue(true)
    expect(w.emitted('update:modelValue')).toEqual([['commit_push']])
    w.unmount()
  })

  it('emits the mapped action when push is toggled, keeping scope', async () => {
    const w = mountAxis('commit_push')
    await w.find('.gf-axis-push-chk input').setValue(false)
    expect(w.emitted('update:modelValue')).toEqual([['commit_only']])
    w.unmount()
  })

  it('reaches every one of the six actions from the two axes', async () => {
    // Start each sweep from the 대기 scope so the click is always a real change:
    // 'push' is 대기+푸시, 'wait' is 대기 alone, and the two other scopes are one
    // click away in both push states.
    const reached: string[] = ['push', 'wait']
    for (const start of ['push', 'wait']) {
      for (const scope of ['merge', 'commit']) {
        const w = mountAxis(start)
        await w.find(`.gf-axis-opt[data-scope="${scope}"] input`).setValue(true)
        reached.push((w.emitted('update:modelValue')?.[0] as string[])[0])
        w.unmount()
      }
    }
    expect(reached).toHaveLength(6)
    expect(new Set(reached)).toEqual(
      new Set(['merge', 'merge_only', 'commit_push', 'commit_only', 'push', 'wait']),
    )
  })

  it('summarises the selection in one sentence per action', () => {
    for (const action of ['merge', 'merge_only', 'commit_push', 'commit_only', 'push', 'wait']) {
      const w = mountAxis(action)
      const text = w.find('.gf-axis-summary-text').text()
      // A missing key would render the key path itself.
      expect(text).not.toContain('git_finalize.axis_summary')
      expect(text.length).toBeGreaterThan(5)
      w.unmount()
    }
  })

  it('locks both axes while a finalize is running', async () => {
    const w = mountAxis('merge', { disabled: true })
    expect(
      w.findAll('.gf-axis-opt input, .gf-axis-push-chk input').every(
        (n) => (n.element as HTMLInputElement).disabled,
      ),
    ).toBe(true)
    await w.find('.gf-axis-push-chk input').trigger('change')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    w.unmount()
  })
})

describe('finalizeAxis mapping', () => {
  it('round-trips every action through position ↔ action', () => {
    for (const action of ['merge', 'merge_only', 'commit_push', 'commit_only', 'push', 'wait']) {
      const p = positionOfAction(AXES, action)
      expect(actionOfPosition(AXES, p.scope, p.push, 'merge')).toBe(action)
    }
  })

  it('falls back to the first scope with push for an unknown action', () => {
    expect(positionOfAction(AXES, 'stash')).toEqual({ scope: 'merge', push: true })
    expect(positionOfAction(null, 'merge')).toEqual({ scope: 'merge', push: true })
  })

  it('never blanks the selection when the scope is unknown', () => {
    expect(actionOfPosition(AXES, 'nope', true, 'merge_only')).toBe('merge_only')
  })

  // The 0331 contract split: `push` ships existing commits only, so it must not
  // ask for a commit subject the server will never use.
  it('asks for a commit subject for the four committing actions only', () => {
    expect(AXES.commit_actions.map((a) => actionNeedsCommitMessage(AXES, a))).toEqual([
      true,
      true,
      true,
      true,
    ])
    expect(actionNeedsCommitMessage(AXES, 'push')).toBe(false)
    expect(actionNeedsCommitMessage(AXES, 'wait')).toBe(false)
  })
})
