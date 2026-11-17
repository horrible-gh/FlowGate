// flowgate.v02test.0029 T0005 — shrinking a message image in the browser before it is uploaded (the server has no
// image library: it refuses an image whose long side is over MAX_IMAGE_DIMENSION, so the browser makes it fit).
//
// An image that already fits is sent exactly as it is (same bytes, so an animated GIF stays animated). A larger one is
// drawn onto a canvas at the fitting size: PNG stays PNG, everything else becomes WebP (a GIF keeps its first frame).
import { MAX_IMAGE_DIMENSION } from './messageImages'

export interface ImageSize { width: number; height: number }

export interface PreparedImage {
  blob: Blob
  filename: string
  /** True when the pixels were scaled down (the canvas ran); false = the original bytes. */
  resized: boolean
}

/** The decoded size of an image file; rejects when the browser cannot decode it. */
export async function measureImage(file: Blob): Promise<ImageSize> {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(file)
    try {
      return { width: bitmap.width, height: bitmap.height }
    } finally {
      bitmap.close?.()
    }
  }
  return loadElement(file, (img) => ({ width: img.naturalWidth, height: img.naturalHeight }))
}

/** `longSide` -> the size that fits MAX_IMAGE_DIMENSION, keeping the ratio (never below 1px). */
export function fittedSize(size: ImageSize, limit = MAX_IMAGE_DIMENSION): ImageSize {
  const long = Math.max(size.width, size.height)
  if (long <= limit) return { ...size }
  const scale = limit / long
  return { width: Math.max(1, Math.min(limit, Math.round(size.width * scale))), height: Math.max(1, Math.min(limit, Math.round(size.height * scale))) }
}

/** Draws `file` at `target` and encodes it: PNG for a PNG, WebP for every other format. */
export async function resizeImage(file: Blob, target: ImageSize): Promise<Blob> {
  const type = file.type === 'image/png' ? 'image/png' : 'image/webp'
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(file)
    try {
      return await encode(target, type, (ctx) => ctx.drawImage(bitmap, 0, 0, target.width, target.height))
    } finally {
      bitmap.close?.()
    }
  }
  return loadElement(file, (img) => encode(target, type, (ctx) => ctx.drawImage(img, 0, 0, target.width, target.height)))
}

async function encode(target: ImageSize, type: string, draw: (ctx: CanvasRenderingContext2D) => void): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = target.width
  canvas.height = target.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('canvas_unavailable')
  draw(ctx)
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('encode_failed'))), type, 0.92)
  })
}

function loadElement<T>(file: Blob, use: (img: HTMLImageElement) => T | Promise<T>): Promise<T> {
  const url = URL.createObjectURL(file)
  return new Promise<T>((resolve, reject) => {
    const img = new Image()
    img.onload = () => Promise.resolve(use(img)).then(resolve, reject).finally(() => URL.revokeObjectURL(url))
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('decode_failed')) }
    img.src = url
  })
}

const EXTENSION: Record<string, string> = { 'image/png': 'png', 'image/webp': 'webp', 'image/jpeg': 'jpg', 'image/gif': 'gif' }

function renamed(name: string, type: string): string {
  const stem = name.replace(/\.[^./\\]+$/, '') || 'image'
  return `${stem}.${EXTENSION[type] ?? 'bin'}`
}

export interface ImageDeps { measure: typeof measureImage; resize: typeof resizeImage }

/** The browser work `prepareImage` uses, read at call time: the seam a spec replaces (jsdom has no canvas). */
export const imageDeps: ImageDeps = { measure: measureImage, resize: resizeImage }

/** The upload form of one picked image: the original while its long side fits, else the scaled copy. */
export async function prepareImage(file: File, deps: ImageDeps = imageDeps): Promise<PreparedImage> {
  const size = await deps.measure(file)
  if (Math.max(size.width, size.height) <= MAX_IMAGE_DIMENSION) return { blob: file, filename: file.name || 'image', resized: false }
  const blob = await deps.resize(file, fittedSize(size))
  return { blob, filename: renamed(file.name || 'image', blob.type), resized: true }
}
