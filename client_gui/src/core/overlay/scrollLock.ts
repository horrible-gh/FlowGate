// Shared ref-counted body scroll lock (T0006 §5/§9).
//
// More than one ModalShell can be open at once, and the lock must only lift once the
// last one closes, regardless of open/close order. A plain top-level `let` inside
// `<script setup>` is NOT module state: SFC `<script setup>` compiles into the
// component's setup() closure, so it re-runs per instance. Real cross-instance state
// has to live in an actual module, same as zIndex.ts/focusReturn.ts.
let lockCount = 0
let previousBodyOverflow = ''

export function lockBodyScroll(): void {
  if (lockCount === 0) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  lockCount += 1
}

export function unlockBodyScroll(): void {
  lockCount = Math.max(0, lockCount - 1)
  if (lockCount === 0) {
    document.body.style.overflow = previousBodyOverflow
  }
}
