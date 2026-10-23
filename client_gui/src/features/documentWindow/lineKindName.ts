// flowgate.v02.0088 F14/F15 (D0004 §3.2): the kind name an open [작성] / [AI 호출] window shows. A line opened
// with the key of its kind shows it in the current language, so a language switch while the window stays open
// changes it; a line without a key (an older caller) or with a key the catalog does not have shows the name it
// came with. A translated name is never looked up backwards to guess its kind.

export interface LineKindSource {
  readonly name: string
  readonly nameKey?: string | null
}

export function lineKindName(
  source: LineKindSource,
  t: (key: string) => string,
  te: (key: string) => boolean,
): string {
  const key = source.nameKey
  return key && te(key) ? t(key) : source.name
}
