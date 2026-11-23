// v02test.0074 (시안 v2 · 6): a message's `[name](fg-attachment:<key>)` links as file chips.
//
// The body keeps the link as written; the server reports the current state of each linked file on the turn
// (`attachmentLinks`). This module turns one link into the chip's HTML — one implementation for the rendered AI message
// (the Markdown renderer's `link` hook) and for a person's plain text message — and holds what the chip's buttons do:
// ⬇ downloads the file straight from the Flow's attachment API (the attachment window is not opened), the name asks the
// container to show the file's row in the attachment window.
import { inject } from 'vue'
import { escapeHtml } from '../../core/markdown/markdownRenderer'
import { fetchFlowAttachment, fetchFlowAttachmentsZip } from './api'
import { ATTACHMENT_LINK_CONTEXT, type AttachmentLinkContext, type TurnAttachmentLink } from './types'

export const ATTACHMENT_SCHEME = 'fg-attachment:'

/** `unknown`: the server said nothing about this key (a message not stored yet): drawn plainly, its buttons still try. */
export type AttachmentChipStatus = TurnAttachmentLink['status'] | 'unknown'

/** One link as the chip shows it. `fileKey` is the file to download / go to now (null = no file). */
export interface AttachmentChip {
  key: string
  status: AttachmentChipStatus
  name: string
  size: number | null
  fileKey: string | null
}

export interface AttachmentChipLabels {
  deleted: string
  replaced: string
  /** Tooltip / accessible name of the file name button. */
  open: (name: string) => string
  /** Tooltip / accessible name of ⬇. */
  download: (name: string) => string
}

/** The key of an `fg-attachment:` address (also written `<fg-attachment:key>`), or null for any other address. */
export function attachmentKeyOf(href: string): string | null {
  const address = href.trim().replace(/^<(.*)>$/, '$1')
  if (!address.startsWith(ATTACHMENT_SCHEME)) return null
  const key = address.slice(ATTACHMENT_SCHEME.length)
  return /^[A-Za-z0-9_-]+$/.test(key) ? key : null
}

/** The chip of one link: the server's current name / size when it reported the key, else the link text as written. */
export function attachmentChipOf(key: string, text: string, links: readonly TurnAttachmentLink[] | undefined): AttachmentChip {
  const link = links?.find((entry) => entry.key === key)
  const written = text.trim() || key
  if (!link) return { key, status: 'unknown', name: written, size: null, fileKey: key }
  if (link.status === 'deleted') return { key, status: 'deleted', name: link.name ?? written, size: null, fileKey: null }
  return {
    key,
    status: link.status,
    name: link.name ?? written,
    size: link.size,
    fileKey: link.status === 'replaced' ? link.replacementKey : key,
  }
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const IMAGE_NAME = /\.(png|jpe?g|gif|webp)$/i
const TEXT_NAME = /\.(md|markdown|txt|log|json|ya?ml|csv|tsv|xml|ini|toml|conf|py|js|ts|vue|css|html?|sql|sh|bat|ps1)$/i

export const isImageName = (name: string): boolean => IMAGE_NAME.test(name)

/** Text files up to this size are previewed inline (the attachment read ceiling of the server, 1 MiB). */
export const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024
/** Images up to this size are previewed inline. */
export const IMAGE_PREVIEW_MAX_BYTES = 10 * 1024 * 1024

/** What the linked file list can show inline for a file: by its name and size only (nothing is fetched to decide). */
export function previewKindOf(name: string, size: number | null): 'image' | 'text' | 'none' {
  if (size == null) return 'none'
  if (IMAGE_NAME.test(name)) return size <= IMAGE_PREVIEW_MAX_BYTES ? 'image' : 'none'
  if (TEXT_NAME.test(name)) return size <= TEXT_PREVIEW_MAX_BYTES ? 'text' : 'none'
  return 'none'
}

/**
 * The chip's HTML (every value escaped). The buttons carry `data-fg-action` (`focus` / `download`); the message handles
 * their clicks by delegation, reading the file from the chip's `data-file-key` / `data-file-name`.
 * A deleted file: struck through, "삭제됨", no button at all.
 */
export function attachmentChipHtml(chip: AttachmentChip, labels: AttachmentChipLabels): string {
  const name = escapeHtml(chip.name)
  const icon = `<span class="tl-fchip-ico" aria-hidden="true">${isImageName(chip.name) ? '🖼' : '📄'}</span>`
  const open = `<span class="tl-fchip tl-fchip--${chip.status}" data-test="tl-file-chip" data-status="${chip.status}" data-attachment-key="${escapeHtml(chip.key)}"`
  if (!chip.fileKey) {
    return `${open}>${icon}<span class="tl-fchip-name" data-test="tl-file-chip-name">${name}</span><span class="tl-fchip-tag" data-test="tl-file-chip-tag">${escapeHtml(labels.deleted)}</span></span>`
  }
  const size = chip.size == null ? '' : `<span class="tl-fchip-size" data-test="tl-file-chip-size">${formatFileSize(chip.size)}</span>`
  const tag = chip.status === 'replaced' ? `<span class="tl-fchip-tag" data-test="tl-file-chip-tag">${escapeHtml(labels.replaced)}</span>` : ''
  const openLabel = escapeHtml(labels.open(chip.name))
  const downloadLabel = escapeHtml(labels.download(chip.name))
  return (
    `${open} data-file-key="${escapeHtml(chip.fileKey)}" data-file-name="${name}">${icon}` +
    `<button type="button" class="tl-fchip-name" data-test="tl-file-chip-name" data-fg-action="focus" title="${openLabel}">${name}</button>` +
    `${size}${tag}` +
    `<button type="button" class="tl-fchip-dl" data-test="tl-file-chip-download" data-fg-action="download" aria-label="${downloadLabel}" title="${downloadLabel}">⬇</button>` +
    `</span>`
  )
}

// A person's message is plain text (no Markdown): only the link form itself is recognised. `!` before it is the image
// form, which is never a file link (the server's rule too).
const PLAIN_LINK = /(!?)\[((?:\\.|[^\]\\\n])*)\]\(\s*(<fg-attachment:[A-Za-z0-9_-]+>|fg-attachment:[A-Za-z0-9_-]+)\s*\)/g

/**
 * A plain text message with its file links drawn as chips, or null when it has none (the caller then shows the text as
 * it always did). When the server listed the turn's links, only the keys it listed become chips (it leaves out a link
 * written inside code); the rest of the text is escaped and keeps its line breaks through the caller's `pre-wrap`.
 */
export function plainTextWithChips(text: string, links: readonly TurnAttachmentLink[] | undefined, labels: AttachmentChipLabels): string | null {
  if (!text.includes(ATTACHMENT_SCHEME)) return null
  let html = ''
  let last = 0
  let found = false
  for (const match of text.matchAll(PLAIN_LINK)) {
    const key = match[1] ? null : attachmentKeyOf(match[3])
    if (!key || (links && !links.some((entry) => entry.key === key))) continue
    const index = match.index ?? 0
    html += escapeHtml(text.slice(last, index))
    html += attachmentChipHtml(attachmentChipOf(key, match[2].replace(/\\(.)/g, '$1'), links), labels)
    last = index + match[0].length
    found = true
  }
  return found ? html + escapeHtml(text.slice(last)) : null
}

/** One file of the "linked files" list under an answer. */
export interface LinkedFile {
  key: string
  name: string
  size: number | null
  replaced: boolean
}

/** The files an answer links that exist now (a replaced link = the file there now), each once, in link order. */
export function linkedFilesOf(links: readonly TurnAttachmentLink[] | undefined): LinkedFile[] {
  const files: LinkedFile[] = []
  for (const link of links ?? []) {
    const key = link.status === 'available' ? link.key : link.status === 'replaced' ? link.replacementKey : null
    if (!key || files.some((file) => file.key === key)) continue
    files.push({ key, name: link.name ?? key, size: link.size, replaced: link.status === 'replaced' })
  }
  return files
}

/** A blob's text (UTF-8). FileReader where `Blob.text()` is missing. */
export function readBlobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsText(blob)
  })
}

/** Hands a blob to the browser as a download of that name. */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * What a chip / a linked file row does, bound to the panel's Flow. Each resolves false instead of throwing (no Flow,
 * the request failed, the file is not in the attachment window), so the screen only has to say so.
 */
export function useAttachmentLinkActions() {
  const context = inject<AttachmentLinkContext | null>(ATTACHMENT_LINK_CONTEXT, null)
  const flowId = (): string | null => context?.flowId() || null

  async function read(key: string): Promise<Blob | null> {
    const flow = flowId()
    if (!flow || !key) return null
    try {
      return await fetchFlowAttachment(flow, key)
    } catch {
      return null
    }
  }

  /** The original file, straight to the browser's downloads. */
  async function download(key: string, name: string): Promise<boolean> {
    const blob = await read(key)
    if (!blob) return false
    saveBlob(blob, name || key)
    return true
  }

  /** Several files as one ZIP (named like the attachment window's ZIP). */
  async function downloadZip(keys: readonly string[]): Promise<boolean> {
    const flow = flowId()
    if (!flow || !keys.length) return false
    try {
      saveBlob(await fetchFlowAttachmentsZip(flow, keys), `${flow}_attachments.zip`)
      return true
    } catch {
      return false
    }
  }

  /** Opens the Flow's attachment window on that file's row (the container's focusAttachment). */
  async function focus(key: string): Promise<boolean> {
    const flow = flowId()
    if (!context || !flow || !key) return false
    try {
      return (await context.focus(flow, key)) === true
    } catch {
      return false
    }
  }

  return { read, download, downloadZip, focus }
}
