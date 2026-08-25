export function stripFrontmatter(md: string): string {
  if (!md) return md
  const m = md.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/)
  return m ? md.slice(m[0].length) : md
}
