// Shared modal instance id + open stack (T0006 §5/§9).
//
// Same reasoning as scrollLock.ts: a `let uidCounter` inside ModalShell's
// `<script setup>` re-runs per component instance, so it cannot mint ids that stay
// unique across two ModalShells open at once. `nextModalInstanceId()` hands out real
// cross-instance-unique ids from an actual module.
//
// The stack also lets nested ModalShells agree on which one is topmost, so only that
// one traps focus / handles Escape while the others stay open (but inert) underneath
// it (T0006 §5 nested modal lifecycle).
let idCounter = 0

export function nextModalInstanceId(): string {
  idCounter += 1
  return `ui-modal-${idCounter}`
}

const openStack: string[] = []

export function pushOpenModal(id: string): void {
  openStack.push(id)
}

export function popOpenModal(id: string): void {
  const index = openStack.lastIndexOf(id)
  if (index !== -1) openStack.splice(index, 1)
}

export function isTopmostModal(id: string): boolean {
  return openStack.length > 0 && openStack[openStack.length - 1] === id
}
