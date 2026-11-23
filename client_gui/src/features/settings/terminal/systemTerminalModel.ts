// What 시스템 설정 › 웹 터미널 shows for GET /terminal/system (0078 · 0015-T B; 서버·화면 공통 약속 2).
import type { TerminalInstall, TerminalInstallStage, TerminalSystem } from '@/features/designer/flowTerminal/api'

export type TerminalSystemState = 'not_installed' | 'installing' | 'installed' | 'error'
export const INSTALL_STAGES: readonly TerminalInstallStage[] = ['download', 'verify', 'extract', 'test', 'apply']

export const isInstallRunning = (install: TerminalInstall | null | undefined): boolean =>
  !!install && (install.status === 'queued' || install.status === 'running')

/** 설치 중 while an installation runs; 설치됨 once installed (Linux/macOS from the start); 오류 when the last
 *  installation failed and nothing is installed; otherwise 설치 안 됨 (a cancelled installation too). */
export function terminalSystemState(system: TerminalSystem): TerminalSystemState {
  if (isInstallRunning(system.install)) return 'installing'
  if (system.installed) return 'installed'
  if (system.install?.status === 'failed') return 'error'
  return 'not_installed'
}

/** The index of the running stage in 받기 → 확인 → 풀기 → 시험 → 적용 (0 before the first one is named). */
export function stageIndex(install: TerminalInstall | null | undefined): number {
  const index = install?.stage ? INSTALL_STAGES.indexOf(install.stage) : -1
  return index >= 0 ? index : 0
}

/** The bar's width, 0 ~ 100. */
export function installPercent(install: TerminalInstall | null | undefined): number {
  const progress = Number(install?.progress ?? 0)
  if (!Number.isFinite(progress)) return 0
  return Math.max(0, Math.min(100, Math.round(progress * 100)))
}

/** Offline (the download failed): the screen adds the `setup-terminal --from-dir` line. */
export const isOfflineFailure = (install: TerminalInstall | null | undefined): boolean =>
  install?.status === 'failed' && install.error_code === 'terminal_download_failed'

export function formatSize(bytes: number | null | undefined): string {
  const value = Number(bytes ?? 0)
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}
