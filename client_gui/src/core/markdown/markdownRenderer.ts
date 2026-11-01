// flowgate.v02.0113 T#5 — the GFM renderer of the document viewer (features/documentWindow/MdViewer.vue: GitHub-Flavored
// Markdown, a code block in its copy wrapper) as one shared factory, so the run result details render the AI messages
// with the same renderer. `safe` is for text no person wrote in this screen (an AI's message): raw HTML in it is shown as
// text and a link or image whose address is not http(s) / mailto / a page anchor is shown as its text only.
import { Marked, type Token, type Tokens } from 'marked'

export interface MarkdownRendererOptions {
  /** The code block copy button's label, read at each parse (it follows the current language). */
  readonly copyLabel: () => string
  /** Escape raw HTML and drop unsafe link / image addresses. */
  readonly safe?: boolean
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

const SAFE_HREF = /^(https?:|mailto:|#)/i

export function createMarkdownRenderer(options: MarkdownRendererOptions): (source: string) => string {
  const renderer = new Marked({ gfm: true })
  renderer.use({
    renderer: {
      code({ text, lang }: { text: string; lang?: string }): string {
        const escaped = text
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
        const langClass = lang ? ` class="language-${options.safe ? escapeHtml(lang) : lang}"` : ''
        const label = options.copyLabel()
        return `<div class="code-block-wrapper"><button class="code-copy-btn" type="button" aria-label="${label}" title="${label}">⧉</button><pre><code${langClass}>${escaped}</code></pre></div>`
      },
    },
  })
  if (options.safe) {
    renderer.use({
      renderer: {
        html({ text }: { text: string }): string {
          return escapeHtml(text)
        },
        link(this: { parser: { parseInline(tokens: Token[]): string } }, { href, title, tokens }: Tokens.Link): string {
          const inner = this.parser.parseInline(tokens)
          if (!SAFE_HREF.test(href.trim())) return inner
          const titled = title ? ` title="${escapeHtml(title)}"` : ''
          return `<a href="${escapeHtml(href.trim())}"${titled} target="_blank" rel="noopener noreferrer">${inner}</a>`
        },
        image({ text }: Tokens.Image): string {
          return escapeHtml(text)
        },
      },
    })
  }
  return (source: string) => renderer.parse(source || '') as string
}
