/**
 * flowgate.default.0560 T0024 — GitConflictResolverDialog on the common dialog layer.
 *
 * NR0005 §11.1 called this instance "재설계에 가까운 이관 후보" and §13 made it its own sub-task.
 * D0008 §6 had already assigned it `conflict-large` and fixed the rule this spec exists to
 * prove: the large Conflict/Workflow dialogs hand Shell / Header / Footer to the common layer
 * and keep their BODY. So the two halves are asserted separately —
 *
 *   - the frame is the common one (§4-1, §4-6), and the footer is four semantic
 *     `DialogAction`s in the contract order with the disabled conditions the inline buttons
 *     carried (§4-4, §4-5);
 *   - the body is the same markup it was, still owned by this file — proven against the
 *     pre-migration template, committed verbatim as a fixture and compared block by block,
 *     byte for byte (§4-2) — and the Shift+↑/↓ chunk shortcut that used to hang off the
 *     deleted overlay still works (§4-3).
 *
 * The pre-existing behaviour cases live where they always did:
 * `tests/main/GitConflictResolverDialog.spec.ts` (chunk choices, folding, the submit gate),
 * `tests/main/DialogOutsideClickPreserved.0412.spec.ts` (backdrop), and the headless-Chrome
 * geometry is `tests/browser/conflict-resolver-deck.0481.mjs` /
 * `tests/browser/conflict-resolver-running.0481.mjs`.
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { parseConflictFile, type ConflictFileState } from '@main/composables/useConflictChunks'

const CLIENT_DIR = resolve(__dirname, '../..')
const COMPONENT = 'src/main/components/GitConflictResolverDialog.vue'

function source(relative: string): string {
  return readFileSync(resolve(CLIENT_DIR, relative), 'utf8')
}

const FILE_CONTENT = [
  ...Array.from({ length: 3 }, (_, i) => `head ${i + 1}`),
  '<<<<<<< HEAD',
  'ours one',
  '=======',
  'theirs one',
  '>>>>>>> main',
  'middle',
  '<<<<<<< HEAD',
  'ours two',
  '=======',
  'theirs two',
  '>>>>>>> main',
  'tail',
].join('\n')

function makeFile(path: string): ConflictFileState {
  const segments = parseConflictFile(FILE_CONTENT)
  if (!segments) throw new Error('fixture must parse')
  return { path, conflict_count: 2, directText: FILE_CONTENT, mode: 'chunk', segments, notice: '' }
}

function open(overrides: Record<string, unknown> = {}) {
  return mount(GitConflictResolverDialog, {
    props: {
      files: [makeFile('server/app/git_service.py')],
      branch: 'flowgate_default_0560',
      baseBranch: 'main',
      busy: false,
      loadStatus: 'ready' as const,
      errorMessage: '',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
      ...overrides,
    },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
    attachTo: document.body,
  })
}

function root(): DOMWrapper<HTMLElement> {
  return new DOMWrapper(document.body)
}

function surface(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.fg-dialog-surface')
}

function actionIds(): string[] {
  return [...document.querySelectorAll('.fg-dialog-footer [data-dialog-action-id]')]
    .map((el) => el.getAttribute('data-dialog-action-id') ?? '')
}

function actionRoles(): string[] {
  return [...document.querySelectorAll('.fg-dialog-footer [data-dialog-action-role]')]
    .map((el) => el.getAttribute('data-dialog-action-role') ?? '')
}

function action(id: string): HTMLButtonElement {
  const button = document.querySelector<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
  if (button == null) throw new Error(`footer action "${id}" is not rendered`)
  return button
}

beforeEach(() => {
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  resetDialogSystem()
})

/* ───────────────────────────── §4-1 — the shell is common ───────────────────────────── */

describe('T0024 §4-1 — the hand-built shell is gone', () => {
  it('the source declares conflict-large with a literal :open, and no overlay of its own', () => {
    const text = source(COMPONENT)

    expect(text).toContain('<DialogShell')
    expect(text).toContain('variant="conflict-large"')
    expect(text).toContain(':open="true"')

    // Class ATTRIBUTES only, matched as WHOLE class names — the file's comments are free to
    // name the markup they replaced, and `-` counts as a word boundary, so a `\b...\b` regex
    // would also match the body's `git-conflict-dialog-bd` and fail for the wrong reason.
    const declaredClasses = new Set(
      [...text.matchAll(/class="([^"{}]*)"/g)]
        .flatMap((m) => m[1].trim().split(/\s+/))
        .filter(Boolean),
    )
    for (const legacy of [
      'git-conflict-overlay',
      'git-conflict-dialog',
      'git-conflict-dialog-hd',
      'git-conflict-dialog-ft',
      'git-conflict-footer-actions',
      'git-dialog-close',
    ]) {
      expect(declaredClasses.has(legacy), `class="${legacy}" survived the migration`).toBe(false)
    }
    // …and the body's own classes did NOT go with them (D0008 §6).
    expect(declaredClasses.has('git-conflict-dialog-bd')).toBe(true)
    expect(declaredClasses.has('git-conflict-footer-context')).toBe(true)

    // `DialogShell`'s surface renders both of these; a second copy would be a duplicate role.
    expect(text).not.toMatch(/\brole="dialog"/)
    expect(text).not.toMatch(/\baria-modal=/)

    // §2.7 / §3: `closeOnBackdrop` is the variant default, never restated here.
    expect(text).not.toMatch(/:?close-on-backdrop=/)
  })

  it('mounting renders one common surface, and unmounting takes it away', async () => {
    const wrapper = open()
    await flushPromises()

    const el = surface()
    expect(el).not.toBeNull()
    expect(el!.getAttribute('data-dialog-variant')).toBe('conflict-large')
    expect(el!.classList.contains('git-conflict-resolver-dialog')).toBe(true)
    // `conflict-large` = sheet + xl (dialogVariantDefaults), taken as-is.
    expect(el!.classList.contains('fg-dialog-surface--sheet')).toBe(true)
    expect(el!.classList.contains('fg-dialog-surface--xl')).toBe(true)
    // exactly one dialog role on the screen
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1)

    wrapper.unmount()
    expect(surface()).toBeNull()
  })

  it('§2.2 — the header is DialogHeader, and the progress readout kept its slot', async () => {
    const wrapper = open()
    await flushPromises()

    const header = document.querySelector('.fg-dialog-header')!
    expect(header).not.toBeNull()
    expect(header.querySelector('.fg-dialog-header__title')!.textContent).toContain('flowgate_default_0560')
    expect(header.querySelector('.fg-dialog-header__subtitle')!.textContent!.trim().length).toBeGreaterThan(0)

    // Between the title block and the ✕, exactly where `margin-left: auto` used to put it.
    const progress = header.querySelector('.fg-dialog-header__actions > .git-conflict-progress')
    expect(progress).not.toBeNull()
    expect(progress!.textContent).toContain('0 / 2')
    expect(progress!.querySelector('.git-conflict-progress-fill')).not.toBeNull()

    // One close control, the header's own.
    expect(header.querySelectorAll('.fg-dialog-header__close')).toHaveLength(1)

    wrapper.unmount()
  })
})

/* ───── §4-2 (a) — the body is the PRE-migration body, compared against a stored baseline ───── */

/**
 * T0024 §2.3 asks for more than "the blocks are still on screen": the five body blocks have to
 * be markup-for-markup, class-for-class, condition-for-condition identical ACROSS the move
 * ("바이트 단위 diff"). A selector-existence test cannot show that — an attribute, an inner
 * element or a `v-if` could change under it and it would still pass.
 *
 * So the pre-migration `<template>` section is committed verbatim as a fixture, and ONE
 * extractor below runs over both sides: the fixture and today's component. Then the five
 * blocks are compared byte for byte.
 *
 * The fixture is exactly what
 *
 *     git show 8ff143f:client/src/main/components/GitConflictResolverDialog.vue | sed -n '1,280p'
 *
 * prints — lines 1-280 are that file's entire `<template>` at the commit this T started from —
 * and its sha256 is pinned below, so the baseline cannot be quietly re-cut from the migrated
 * file to make a real difference disappear.
 *
 * The ONE tolerated difference is leading indentation: the blocks moved two levels in (into
 * `DialogShell`'s `#default` slot and the §2.4 keydown wrapper), so each block is dedented by
 * its own common indent before comparing. Nothing else is normalized — attributes, class
 * names, conditions, comments, blank lines and trailing whitespace all have to match exactly.
 */
const PRE_MIGRATION_TEMPLATE = 'tests/main/fixtures/GitConflictResolverDialog.pre0560.template.txt'
const PRE_MIGRATION_TEMPLATE_SHA256 =
  'fba94ff74f4486fca0d9a8b754b3d1f57352c2eac1905ad52687caff191b3691'

interface TemplateNode {
  tag: string
  /** the open tag, verbatim (it may span lines) */
  open: string
  start: number
  end: number
  parent: TemplateNode | null
  children: TemplateNode[]
}

const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param',
  'source', 'track', 'wbr',
])

/** The `<template>` section only — `^</template>$` at column 0, since Vue's own
 *  `<template v-if>`/`<template #slot>` closers in this file are all indented. */
function templateSection(fileSource: string): string {
  const text = fileSource.replace(/\r\n/g, '\n')
  const close = /^<\/template>$/m.exec(text)
  if (close == null) throw new Error('the file has no <template> section')
  return text.slice(0, close.index + close[0].length + 1)
}

/**
 * A deliberately small element scanner: it knows comments, `{{ }}` interpolation, quoted
 * attribute values (this template really does carry `:disabled="codeFontRem <= 0.72"`, so a
 * naive `<`/`>` split mis-parses it), self-closing tags and void elements. That is enough to
 * get exact source ranges for the blocks we compare, without pulling in a parser whose own
 * normalisation could hide a difference.
 */
function parseTemplate(text: string): TemplateNode {
  const tree: TemplateNode = { tag: '#root', open: '', start: 0, end: text.length, parent: null, children: [] }
  const stack: TemplateNode[] = [tree]
  let i = 0
  while (i < text.length) {
    if (text[i] !== '<') {
      if (text.startsWith('{{', i)) {
        const close = text.indexOf('}}', i)
        i = close < 0 ? text.length : close + 2
        continue
      }
      i += 1
      continue
    }
    if (text.startsWith('<!--', i)) {
      const close = text.indexOf('-->', i)
      i = close < 0 ? text.length : close + 3
      continue
    }
    if (text.startsWith('</', i)) {
      const name = /^<\/([A-Za-z][-\w.]*)/.exec(text.slice(i, i + 64))?.[1]
      const gt = text.indexOf('>', i)
      if (name == null || gt < 0) { i += 1; continue }
      for (let k = stack.length - 1; k > 0; k -= 1) {
        if (stack[k].tag === name) {
          stack[k].end = gt + 1
          stack.length = k
          break
        }
      }
      i = gt + 1
      continue
    }
    const opening = /^<([A-Za-z][-\w.]*)/.exec(text.slice(i, i + 64))
    if (opening == null) { i += 1; continue }
    let j = i + opening[0].length
    let quote = ''
    while (j < text.length) {
      const c = text[j]
      if (quote !== '') {
        if (c === quote) quote = ''
      } else if (c === '"' || c === "'") {
        quote = c
      } else if (c === '>') {
        break
      }
      j += 1
    }
    const openEnd = j + 1
    const node: TemplateNode = {
      tag: opening[1],
      open: text.slice(i, openEnd),
      start: i,
      end: openEnd,
      parent: stack[stack.length - 1],
      children: [],
    }
    node.parent!.children.push(node)
    if (!text.slice(i, j).trimEnd().endsWith('/') && !VOID_TAGS.has(node.tag.toLowerCase())) {
      node.end = -1
      stack.push(node)
    }
    i = openEnd
  }
  const unclosed = stack.slice(1).map((n) => n.tag)
  if (unclosed.length > 0) throw new Error(`unclosed elements: ${unclosed.join(', ')}`)
  return tree
}

function descendants(node: TemplateNode): TemplateNode[] {
  return node.children.flatMap((child) => [child, ...descendants(child)])
}

function findOne(tree: TemplateNode, needle: string): TemplateNode {
  const hits = descendants(tree).filter((node) => node.open.includes(needle))
  if (hits.length !== 1) throw new Error(`expected 1 element whose open tag has ${needle}, found ${hits.length}`)
  return hits[0]
}

/** the open tag on one line, so a multi-line tag and a single-line one read the same */
function descriptor(node: TemplateNode): string {
  return node.open.replace(/\s+/g, ' ').replace(/ >$/, '>')
}

/** indentation is the only tolerance — see the section comment above */
function dedent(block: string): string {
  const lines = block.split('\n')
  const indents = lines
    .filter((line) => line.trim() !== '')
    .map((line) => line.length - line.trimStart().length)
  const cut = indents.length > 0 ? Math.min(...indents) : 0
  return lines.map((line) => (line.trim() === '' ? '' : line.slice(cut))).join('\n')
}

/** grow the range back to the start of its line, so the first line carries its indent too */
function fromLineStart(text: string, index: number): number {
  return text.lastIndexOf('\n', index) + 1
}

function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex')
}

/** the `v-if` / `v-else-if` / `v-else` chain around a block, outermost first — this is the
 *  "조건문" half of T0024 §2.3 and it is compared on its own */
function conditionChain(node: TemplateNode): string[] {
  const chain: string[] = []
  for (let cur: TemplateNode | null = node; cur != null && cur.tag !== '#root'; cur = cur.parent) {
    for (const m of descriptor(cur).matchAll(/(v-if|v-else-if|v-else)(="[^"]*")?/g)) {
      chain.unshift(`${cur.tag} ${m[0]}`)
    }
  }
  return chain
}

/** every non-conditional ancestor, outermost first: the FRAME the block hangs in. This is the
 *  one thing T0024 wanted changed (§2.1 shell, §2.4 wrapper), so it is asserted explicitly
 *  instead of being normalised away. */
function frameChain(node: TemplateNode): string[] {
  const frame: string[] = []
  for (let cur = node.parent; cur != null && cur.tag !== '#root'; cur = cur.parent) {
    if (!/v-if|v-else/.test(cur.open)) frame.unshift(descriptor(cur))
  }
  return frame
}

const BODY_BLOCKS = [
  // key                        anchor inside the block's open tag
  ['status-frames', 'v-if="loadStatus === \'loading\'"'],
  ['git-ai-assist-strip', 'class="git-ai-assist-strip"'],
  ['git-conflict-dialog-bd', 'class="git-conflict-dialog-bd"'],
  ['git-conflict-ai-strip', 'class="git-conflict-ai-strip"'],
  ['git-conflict-message-bar', 'class="git-conflict-message-bar"'],
] as const

type BodyBlockKey = (typeof BODY_BLOCKS)[number][0]

interface ExtractedBlock {
  text: string
  sha256: string
  lines: number
  conditions: string[]
  frame: string[]
}

/**
 * Pull the five blocks out of one `<template>`. `status-frames` is the loading / error / empty
 * trio: they are siblings and the comment between them is part of the markup, so the whole
 * contiguous span from the first to the last is taken as one block.
 */
function bodyBlocks(fileSource: string): Record<BodyBlockKey, ExtractedBlock> {
  const template = templateSection(fileSource)
  const tree = parseTemplate(template)
  const out = {} as Record<BodyBlockKey, ExtractedBlock>
  for (const [key, anchor] of BODY_BLOCKS) {
    const node = findOne(tree, anchor)
    const last = key === 'status-frames' ? findOne(tree, 'class="git-conflict-loading git-conflict-empty"') : node
    const text = dedent(template.slice(fromLineStart(template, node.start), last.end))
    const conditions = key === 'status-frames'
      ? [
        ...conditionChain(node),
        ...conditionChain(findOne(tree, 'class="git-conflict-loading git-conflict-load-error"')),
        ...conditionChain(last),
      ]
      : conditionChain(node)
    out[key] = { text, sha256: sha256(text), lines: text.split('\n').length, conditions, frame: frameChain(node) }
  }
  return out
}

/** what each baseline block weighs, so a silently empty extraction cannot pass as "identical" */
const BASELINE_LINES: Record<BodyBlockKey, number> = {
  'status-frames': 21,
  'git-ai-assist-strip': 9,
  'git-conflict-dialog-bd': 110,
  'git-conflict-ai-strip': 19,
  'git-conflict-message-bar': 11,
}

describe('T0024 §4-2 — the body blocks are byte-identical to the pre-migration baseline', () => {
  const baselineSource = source(PRE_MIGRATION_TEMPLATE)
  const currentSource = source(COMPONENT)

  it('the stored baseline really is the pre-migration file', () => {
    // pinned bytes: 20,286 of them, sha256 as printed by `git show 8ff143f:<component>`
    expect(sha256(baselineSource)).toBe(PRE_MIGRATION_TEMPLATE_SHA256)
    expect(baselineSource.startsWith('<template>\n')).toBe(true)
    expect(baselineSource.endsWith('</template>\n')).toBe(true)

    // …and it is the BEFORE side: it still carries the hand-built shell this T deleted,
    // which today's file no longer has (§4-1 asserts that half separately).
    for (const legacy of ['git-conflict-overlay', 'git-conflict-dialog-ft', 'git-dialog-close']) {
      expect(baselineSource).toContain(`class="${legacy}"`)
      expect(currentSource).not.toContain(`class="${legacy}"`)
    }
    expect(baselineSource).not.toContain('DialogShell')
  })

  it('all five blocks come out of both sides at their full size', () => {
    const baseline = bodyBlocks(baselineSource)
    const current = bodyBlocks(currentSource)

    for (const [key] of BODY_BLOCKS) {
      expect(baseline[key].lines, `${key} baseline`).toBe(BASELINE_LINES[key])
      expect(current[key].lines, `${key} current`).toBe(BASELINE_LINES[key])
      expect(baseline[key].text).toContain(key === 'status-frames' ? 'git-conflict-loading' : key)
    }
    // 170 lines of body markup is what this comparison is actually covering.
    const total = Object.values(BASELINE_LINES).reduce((a, b) => a + b, 0)
    expect(total).toBe(170)
  })

  it('§2.3 — every block matches the baseline byte for byte, indentation aside', () => {
    const baseline = bodyBlocks(baselineSource)
    const current = bodyBlocks(currentSource)

    for (const [key] of BODY_BLOCKS) {
      // the string compare is what prints the diff; the digest is the byte-level statement
      expect(current[key].text, `${key} drifted from the pre-migration markup`).toBe(baseline[key].text)
      expect(current[key].sha256).toBe(baseline[key].sha256)
    }
  })

  it('§2.3 — the conditions around every block are the originals, term for term', () => {
    const baseline = bodyBlocks(baselineSource)
    const current = bodyBlocks(currentSource)

    for (const [key] of BODY_BLOCKS) {
      expect(current[key].conditions, `${key} conditions`).toEqual(baseline[key].conditions)
    }
    // spelled out once, so a change to BOTH sides of the comparison is still visible here
    expect(current['status-frames'].conditions).toEqual([
      'div v-if="loadStatus === \'loading\'"',
      'div v-else-if="loadStatus === \'error\'"',
      'div v-else-if="!files.length"',
    ])
    expect(current['git-conflict-dialog-bd'].conditions).toEqual([
      'template v-if="loadStatus === \'ready\' && files.length"',
    ])
    expect(current['git-conflict-message-bar'].conditions).toEqual([
      'template v-if="loadStatus !== \'loading\'"',
      'div v-if="showAiActions"',
    ])
  })

  it('§2.1/§2.4 — the frame around the body is the only thing that changed', () => {
    const baseline = bodyBlocks(baselineSource)
    const current = bodyBlocks(currentSource)

    for (const [key] of BODY_BLOCKS) {
      expect(baseline[key].frame, `${key} baseline frame`).toEqual([
        '<template>',
        '<div class="git-conflict-overlay" @keydown="onResolverKeydown">',
        '<div class="git-conflict-dialog" role="dialog" aria-modal="true">',
      ])
      expect(current[key].frame, `${key} current frame`).toEqual([
        '<template>',
        '<DialogShell ref="shellRef" :open="true" variant="conflict-large"'
        + ' surface-class="git-conflict-resolver-dialog" @request-close="emit(\'close\')">',
        '<template #default>',
        '<div class="git-conflict-body" @keydown="onResolverKeydown">',
      ])
    }
  })

  it('nothing was added to the body wrapper beside the five blocks', () => {
    const template = templateSection(currentSource)
    const tree = parseTemplate(template)
    const blocks = new Set<TemplateNode>([
      ...BODY_BLOCKS.map(([, anchor]) => findOne(tree, anchor)),
      findOne(tree, 'class="git-conflict-loading git-conflict-load-error"'),
      findOne(tree, 'class="git-conflict-loading git-conflict-empty"'),
    ])
    const scaffolding: string[] = []
    const visit = (node: TemplateNode): void => {
      for (const child of node.children) {
        if (blocks.has(child)) continue
        scaffolding.push(descriptor(child))
        visit(child)
      }
    }
    visit(findOne(tree, 'class="git-conflict-body"'))

    // The wrapper holds the five blocks and the two `<template v-if>`s that were already
    // around them before the move — no extra element, and no extra condition.
    expect(scaffolding).toEqual([
      '<template v-if="loadStatus === \'ready\' && files.length">',
      '<template v-if="loadStatus !== \'loading\'">',
    ])
  })
})

/* ─────────────────────── §4-2 — the body did NOT become common ─────────────────────── */

describe('T0024 §4-2 (runtime) — D0008 §6: the body stays feature-owned', () => {
  it('every body block renders inside the common body, with its own classes', async () => {
    const wrapper = open()
    await flushPromises()

    const body = document.querySelector('.fg-dialog-body > .git-conflict-body')
    expect(body, 'the keydown wrapper is not the body slot content').not.toBeNull()

    for (const owned of [
      '.git-ai-assist-strip',
      '.git-conflict-dialog-bd',
      '.git-conflict-sidebar',
      '.git-conflict-workspace',
      '.git-conflict-navigator',
      '.git-code-size-controls',
      '.git-chunk-scroll',
      '.git-conflict-chunk',
      '.git-conflict-sides',
      '.git-conflict-message-bar',
    ]) {
      expect(body!.querySelector(owned), `${owned} left the body`).not.toBeNull()
    }

    // The common body never paints a conflict dialog's interior: `dialog.css` gives the
    // `sheet` body away as a bare flex column, which is why the blocks keep their layout.
    expect(document.querySelector('.fg-dialog-body .fg-dialog-message')).toBeNull()

    wrapper.unmount()
  })

  it('the direct-edit textarea is still the same in-place editor', async () => {
    const wrapper = open()
    await flushPromises()

    const [, directTab] = root().findAll('.git-conflict-mode-tabs button')
    await directTab.trigger('click')

    const editor = document.querySelector<HTMLTextAreaElement>('.git-conflict-direct-editor')
    expect(editor).not.toBeNull()
    expect(editor!.value).toContain('<<<<<<< HEAD')

    wrapper.unmount()
  })
})

/* ───────────────────── §4-3 — the Shift+↑/↓ shortcut survived the move ───────────────────── */

describe('T0024 §4-3 — Shift+↑/↓ still moves between chunks', () => {
  function activeChip(): string {
    return document.querySelector('.git-conflict-chip.active')?.textContent?.trim() ?? ''
  }

  function press(key: 'ArrowUp' | 'ArrowDown'): void {
    const target = document.querySelector('.git-conflict-body')
    expect(target, 'nothing carries the resolver keydown listener').not.toBeNull()
    target!.dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey: true, bubbles: true }))
  }

  it('the listener moved to the body wrapper and still wraps around both chunks', async () => {
    const wrapper = open()
    await flushPromises()
    expect(activeChip()).toBe('1')

    press('ArrowDown')
    await flushPromises()
    expect(activeChip()).toBe('2')

    press('ArrowDown')
    await flushPromises()
    expect(activeChip()).toBe('1')

    press('ArrowUp')
    await flushPromises()
    expect(activeChip()).toBe('2')

    wrapper.unmount()
  })

  it('an unmodified arrow key is still none of this dialog\'s business', async () => {
    const wrapper = open()
    await flushPromises()

    document.querySelector('.git-conflict-body')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await flushPromises()
    expect(activeChip()).toBe('1')

    wrapper.unmount()
  })
})

/* ──────────────── §4-4 / §4-5 — the footer is four semantic actions ──────────────── */

describe('T0024 §4-4 — the four buttons as DialogActions', () => {
  it('the painted order and the roles are the contract ones', async () => {
    const wrapper = open()
    await flushPromises()

    expect(actionIds()).toEqual(['copy-mention', 'ai-invoke', 'abort', 'submit'])
    // L0009 §1: `stop` is the "Type B 실행 중단" role, deliberately not `cancel`. There is no
    // cancel action at all — the way out is the header ✕ (and now ESC).
    expect(actionRoles()).toEqual(['aux', 'aux', 'stop', 'primary'])
    expect(actionRoles()).not.toContain('cancel')
    expect(action('abort').classList.contains('fg-dialog-btn--stop')).toBe(true)
    expect(action('submit').classList.contains('fg-dialog-btn--primary')).toBe(true)
    // tone is left at the default — none of the four is a destructive primary.
    for (const id of ['copy-mention', 'ai-invoke', 'abort', 'submit']) {
      expect(action(id).classList.contains('fg-dialog-btn--tone-default')).toBe(true)
    }

    wrapper.unmount()
  })

  it('each action emits exactly what the inline button emitted', async () => {
    const wrapper = open()
    await flushPromises()

    action('copy-mention').click()
    action('abort').click()
    await flushPromises()

    expect(wrapper.emitted('copy-mention')).toHaveLength(1)
    expect(wrapper.emitted('abort')).toHaveLength(1)

    wrapper.unmount()
  })

  it('§2.5 — the disabled conditions are the originals, term for term', async () => {
    const wrapper = open()
    await flushPromises()

    // nothing resolved yet: only the primary is held
    expect(action('copy-mention').disabled).toBe(false)
    expect(action('ai-invoke').disabled).toBe(false)
    expect(action('abort').disabled).toBe(false)
    expect(action('submit').disabled).toBe(true)

    // `busy` — the host is running something. Every one of the four carried `:disabled="busy"`,
    // including [중단]: `DialogFooter.busyDisables()` would leave a `stop` action live for the
    // FOOTER's own in-flight state, but this dialog's `busy` is a prop and is passed as the
    // action's explicit `disabled`, which composes first.
    await wrapper.setProps({ busy: true })
    for (const id of ['copy-mention', 'ai-invoke', 'abort', 'submit']) {
      expect(action(id).disabled, `${id} ignores busy`).toBe(true)
    }
    await wrapper.setProps({ busy: false })

    // [AI 호출]'s three extra terms, one at a time
    await wrapper.setProps({ providers: [] })
    expect(action('ai-invoke').disabled).toBe(true)
    await wrapper.setProps({ providers: [{ id: 'p1', name: 'Claude Sonnet 5' }] })
    expect(action('ai-invoke').disabled).toBe(false)

    await wrapper.setProps({ aiRunNotice: '해소하는 중입니다 · 0:12 경과' })
    expect(action('ai-invoke').disabled).toBe(true)
    await wrapper.setProps({ aiRunNotice: null })

    await wrapper.setProps({ aiRunPending: true })
    expect(action('ai-invoke').disabled).toBe(true)
    await wrapper.setProps({ aiRunPending: false })
    expect(action('ai-invoke').disabled).toBe(false)

    wrapper.unmount()
  })

  /**
   * §2.5 판단 사항. `[AI 호출]`의 hover tooltip(`:title="invokeBlockedReason"`)은 따라오지
   * 않는다: `DialogAction` 에 임의 속성 통로가 없고, `title` 필드를 계약에 더하는 것은 D 보정
   * 대상이지 이 T 의 범위가 아니다(§3). 같은 이유는 0481 T0010 rev5 가 버튼 위 띠에 완전한
   * 문장으로 이미 항상 표시하고 있으므로, 이것은 정보 손실이 아니라 중복 제거다.
   */
  it('§2.5 판단 — the blocked reason is the strip sentence, not a title attribute', async () => {
    const wrapper = open({ providers: [], selectedProvider: '' })
    await flushPromises()

    expect(action('ai-invoke').disabled).toBe(true)
    expect(action('ai-invoke').getAttribute('title')).toBeNull()

    const strip = document.querySelector('[data-test="conflict-ai-run"]')
    expect(strip, 'the reason has to be somewhere the operator can read it').not.toBeNull()
    expect(strip!.textContent!.trim().length).toBeGreaterThan(0)
    // the computed that fed it is gone too — `aiStrip` already derives the same sentences
    expect(source(COMPONENT)).not.toMatch(/const invokeBlockedReason/)
    // …and the contract was NOT widened to carry one.
    expect(source('src/main/components/dialogs/dialogTypes.ts')).not.toMatch(/^\s*title\?:/m)

    wrapper.unmount()
  })
})

/* ───────────── §4-5 / §4-6 — the context band, hideAiActions, and the exits ───────────── */

describe('T0024 §4-5 — the context band is DialogFooter\'s sibling', () => {
  it('the band sits directly above the button row and carries the whole left half', async () => {
    const wrapper = open()
    await flushPromises()

    const children = [...surface()!.children].map((el) => el.className)
    // header · body · context band · button row, in that order.
    expect(children[children.length - 2]).toContain('git-conflict-footer-context')
    expect(children[children.length - 1]).toContain('fg-dialog-footer')

    const band = surface()!.querySelector('.git-conflict-footer-context')!
    expect(band.querySelector('.git-conflict-guard')).not.toBeNull()
    expect(band.querySelector('.ft-divider')).not.toBeNull()
    expect(band.querySelector('.git-conflict-invoke-options .git-conflict-provider')).not.toBeNull()
    expect(band.querySelector('.git-conflict-invoke-options .git-conflict-auto-toggle input')).not.toBeNull()

    // and none of it leaked into the button row
    const footer = surface()!.querySelector('.fg-dialog-footer')!
    expect(footer.querySelector('select')).toBeNull()
    expect(footer.querySelector('input[type="checkbox"]')).toBeNull()

    wrapper.unmount()
  })

  it('§2.6 — hideAiActions leaves [중단] [해결 제출] and a guard-only band', async () => {
    const wrapper = open({ hideAiActions: true })
    await flushPromises()

    expect(actionIds()).toEqual(['abort', 'submit'])
    const band = surface()!.querySelector('.git-conflict-footer-context')!
    expect(band.querySelector('.git-conflict-guard')).not.toBeNull()
    expect(band.querySelector('.ft-divider')).toBeNull()
    expect(band.querySelector('.git-conflict-invoke-options')).toBeNull()
    expect(document.querySelector('.git-conflict-message-bar')).toBeNull()
    expect(document.querySelector('[data-test="conflict-ai-run"]')).toBeNull()

    wrapper.unmount()
  })

  it('the band and the row are both hidden while the list is loading, as before', async () => {
    const wrapper = open({ loadStatus: 'loading', files: [] })
    await flushPromises()

    expect(document.querySelector('.git-conflict-loading')).not.toBeNull()
    expect(document.querySelector('.git-conflict-footer-context')).toBeNull()
    expect(document.querySelector('.fg-dialog-footer')).toBeNull()

    wrapper.unmount()
  })
})

describe('T0024 §4-6 — the exits', () => {
  it('the backdrop still does not close it; the header ✕ and ESC do', async () => {
    const wrapper = open()
    await flushPromises()

    const overlay = document.querySelector<HTMLElement>('.fg-dialog-overlay')!
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()

    document.querySelector<HTMLButtonElement>('.fg-dialog-header__close')!.click()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)

    // ESC is what this dialog GAINS by joining the layer (`conflict-large`:
    // `closeOnEscape: true`). The dialog does not close itself either way — the host owns
    // the `v-if`, exactly as it owned the old ✕.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(2)
    expect(surface()).not.toBeNull()

    wrapper.unmount()
  })
})
