import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// flowgate.default.0560 T0014: migration contract for all ten native confirm call sites.
// The common queue's true/cancel semantics are exercised by DialogConfirmQueue.0560.spec.ts;
// these checks pin each feature's early-return boundary and its approved danger tone.
const read = (path: string) => readFileSync(join(process.cwd(), path), 'utf8')

const files = {
  sessions: read('src/settings/views/SecuritySessionsView.vue'),
  aiSettings: read('src/settings/views/project/AiProjectSettingsView.vue'),
  gitSettings: read('src/settings/views/project/GitSettingsView.vue'),
  miniplayer: read('src/main/components/AiInvokeMiniplayer.vue'),
  monitor: read('src/main/components/AiRunMonitorCard.vue'),
  continuous: read('src/main/components/ContinuousWorkDialog.vue'),
  gitStatus: read('src/main/components/GitStatusPanel.vue'),
  mainApp: read('src/main/App.vue'),
  settingsApp: read('src/settings/App.vue'),
}

describe('T0014 native confirm migration', () => {
  it('removes every production native dialog call from the seven migrated files', () => {
    const source = Object.values(files).join('\n')
    expect(source).not.toMatch(/window\.(?:confirm|alert|prompt)\s*\(/)
  })

  it('mounts the imperative confirm and alert hosts in both application roots', () => {
    for (const source of [files.mainApp, files.settingsApp]) {
      expect(source).toContain('<ConfirmDialog host />')
      expect(source).toContain('<AlertDialog host />')
    }
  })

  it('SecuritySessionsView stops before revoke-others on cancel with the §2 table danger tone', () => {
    expect(files.sessions).toContain("if(!await confirm({title:t('settings.security_sessions.confirm'),danger:true}))return;busy.value=true")
  })

  it('AiProjectSettingsView returns false on cancel and true after confirmation', () => {
    expect(files.aiSettings).toMatch(/onBeforeRouteLeave\(async \(\) => \{[\s\S]*await confirm\([\s\S]*if \(!ok\) return false;[\s\S]*return true;/)
  })

  it('GitSettingsView stops before disconnect on cancel with the general confirm tone', () => {
    expect(files.gitSettings).toContain("if (!await confirm({ title: t('settings.project.git.disconnect_confirm') })) return;")
    expect(files.gitSettings.indexOf('await confirm')).toBeLessThan(files.gitSettings.indexOf("await deleteRequest(`/api/v1/projects/${projectId.value}/git/config`)"))
  })

  it('AiInvokeMiniplayer release-paused stops before its busy/API path on cancel', () => {
    expect(files.miniplayer).toMatch(/doReleasePaused[\s\S]*if \(!await confirm\(\{ title: t\(confirmKey\) \}\)\) return\s*busy\.add/)
  })

  it('AiInvokeMiniplayer durable remove asks only for a non-resumable system stop', () => {
    expect(files.miniplayer).toMatch(/if \(isNonResumableSystemStop\(entry\)\) \{\s*const ok = await confirm[\s\S]*if \(!ok\) return\s*\}\s*busy\.add/)
  })

  it('AiRunMonitorCard durable remove preserves the same guarded early return', () => {
    expect(files.monitor).toMatch(/if \(isNonResumableSystemStop\(entry\)\) \{\s*const ok = await confirm[\s\S]*if \(!ok\) return\s*\}\s*busy\.add/)
  })

  it('ContinuousWorkDialog sequence-note revert waits for a general confirmation', () => {
    expect(files.continuous).toMatch(/async function revertSequenceNotes\(\)[\s\S]*await confirm\(\{ title:[^}]+\}\)[\s\S]*applySequenceNotePrefill/)
  })

  it('ContinuousWorkDialog preset revert waits for a general confirmation', () => {
    expect(files.continuous).toMatch(/async function revertPreset\(\)[\s\S]*await confirm\(\{ title:[^}]+\}\)[\s\S]*installPreset\(null\)/)
  })

  it('GitStatusPanel untracked removal is danger and stops before its request', () => {
    expect(files.gitStatus).toMatch(/doRemoveUntracked[\s\S]*await confirm\(\{[\s\S]*danger: true,[\s\S]*if \(!ok\) return[\s\S]*base-remove/)
  })

  it('GitStatusPanel unmerge is general and stops before its request', () => {
    expect(files.gitStatus).toMatch(/doUnmerge[\s\S]*await confirm\(\{ title:[\s\S]*if \(!ok\) return[\s\S]*git\/unmerge/)
  })

  it('uses danger at T0014 §2 table items 1 and 9', () => {
    const source = Object.values(files).join('\n')
    expect(source.match(/danger\s*:\s*true/g)).toHaveLength(2)
  })
})