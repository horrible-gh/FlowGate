import { ref } from 'vue'
import { isPreview, SettingsRequestError } from '../api'
import type { ChangeImpactRow, ChangeRequest } from '../types'

// v02test.0090 — the one way a settings screen changes who may do what: preview, show the effect,
// apply with the revision the preview gave. The screens never send an access change without it.
//
// - run() reads the preview. When nobody's access would change (and `confirmEmpty` is off) it applies
//   at once; otherwise it opens the impact dialog and waits for 적용 / 취소.
// - 적용 sends the revision of the preview the person has just looked at. If the server answers 409
//   revision_conflict (someone changed access in between) the preview is read again, the dialog shows
//   the new effect with a "다른 사람이 먼저 바꿨습니다" line, and nothing is applied until the person
//   confirms that new preview.
// - Any other refusal (last project administrator, no permission …) stays in the dialog as its own
//   message; 취소 closes without a write.

export interface ChangeSpec<T = unknown> {
  /** One line naming the change; the dialog title. */
  title: string
  request: ChangeRequest<T>
  /** Open the dialog even when no one's access changes (default: apply straight away). */
  confirmEmpty?: boolean
  /** The apply button's label (default 적용). */
  label?: string
}

export interface ChangeDialogState {
  title: string
  label: string | null
  revision: number
  impact: ChangeImpactRow[]
  /** The preview was read again because someone else changed access first. */
  refreshed: boolean
  error: string | null
  errorCode: string | null
}

function messageOf(err: unknown): { text: string; code: string | null } {
  if (err instanceof SettingsRequestError) return { text: err.message, code: err.code }
  return { text: err instanceof Error ? err.message : String(err), code: null }
}

export function useChangeFlow(onError: (message: string) => void = () => undefined) {
  const dialog = ref<ChangeDialogState | null>(null)
  const busy = ref(false)
  let spec: ChangeSpec<never> | null = null
  let finish: ((applied: { result: unknown } | null) => void) | null = null

  async function readPreview() {
    const answer = await spec!.request({ preview: true })
    if (!isPreview(answer)) throw new Error('preview_expected')
    return answer
  }

  async function apply(revision: number): Promise<{ result: unknown }> {
    const answer = await spec!.request({ revision })
    if (isPreview(answer)) throw new Error('apply_expected')
    return { result: answer.result }
  }

  /** Resolves with the applied `result`, or null when it was cancelled or failed (errors go to `onError`). */
  async function run<T>(next: ChangeSpec<T>): Promise<{ result: T } | null> {
    if (busy.value || dialog.value) return null
    spec = next as ChangeSpec<never>
    busy.value = true
    try {
      const preview = await readPreview()
      if (!preview.impact.length && !next.confirmEmpty) return (await apply(preview.revision)) as { result: T }
      dialog.value = { title: next.title, label: next.label ?? null, revision: preview.revision, impact: preview.impact, refreshed: false, error: null, errorCode: null }
      busy.value = false
      return await new Promise<{ result: T } | null>((resolve) => {
        finish = (applied) => resolve(applied as { result: T } | null)
      })
    } catch (err) {
      const { text, code } = messageOf(err)
      // A conflict on the immediate apply (nobody's access changes) reads the preview once more.
      if (code === 'revision_conflict' && !dialog.value) {
        try {
          const preview = await readPreview()
          dialog.value = { title: next.title, label: next.label ?? null, revision: preview.revision, impact: preview.impact, refreshed: true, error: null, errorCode: null }
          busy.value = false
          return await new Promise<{ result: T } | null>((resolve) => {
            finish = (applied) => resolve(applied as { result: T } | null)
          })
        } catch (again) {
          onError(messageOf(again).text)
          return null
        }
      }
      onError(text)
      return null
    } finally {
      busy.value = false
    }
  }

  async function confirm(): Promise<void> {
    const open = dialog.value
    if (!open || busy.value || !spec) return
    busy.value = true
    try {
      const applied = await apply(open.revision)
      dialog.value = null
      finish?.(applied)
      finish = null
    } catch (err) {
      const { text, code } = messageOf(err)
      if (code === 'revision_conflict') {
        try {
          const preview = await readPreview()
          dialog.value = { ...open, revision: preview.revision, impact: preview.impact, refreshed: true, error: null, errorCode: code }
        } catch (again) {
          dialog.value = { ...open, error: messageOf(again).text, errorCode: null }
        }
      } else {
        dialog.value = { ...open, error: text, errorCode: code }
      }
    } finally {
      busy.value = false
    }
  }

  function cancel(): void {
    if (busy.value) return
    dialog.value = null
    finish?.(null)
    finish = null
  }

  return { dialog, busy, run, confirm, cancel }
}
