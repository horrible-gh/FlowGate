/**
 * Official document ID format: <project>.<module>.<group>.<doc_number>-<doc_type>
 */

export function slashToNormalFormat(slashFormat: string): string {
  if (!slashFormat) return slashFormat
  const rawParts = slashFormat.split('/')
  const parts: string[] = []
  let i = 0
  while (i < rawParts.length) {
    if (rawParts[i] === 'branches' && i + 1 < rawParts.length) {
      i += 2
    } else {
      parts.push(rawParts[i])
      i++
    }
  }
  if (parts.length < 2) return slashFormat
  const lastPart = parts[parts.length - 1]
  if (lastPart.length <= 3 && /^[A-Z]+$/.test(lastPart)) {
    const beforeLast = parts.slice(0, -1).join('.')
    return `${beforeLast}-${lastPart}`
  }
  return parts.join('.')
}

export function normalizeDashFormat(dashFormat: string): string {
  if (!dashFormat) return dashFormat
  const lastDashIdx = dashFormat.lastIndexOf('-')
  if (lastDashIdx === -1) return dashFormat
  const beforeLast = dashFormat.substring(0, lastDashIdx).replace(/-/g, '.')
  const lastPart = dashFormat.substring(lastDashIdx + 1)
  return `${beforeLast}-${lastPart}`
}

/**
 * Converts an arbitrary raw document ID to the official format (<project>.<module>.<group>.<doc_number>-<doc_type>).
 *
 * Conversion rules:
 *   text.aaaa.0004.0001-R/DS → text.aaaa.0004.0001-R  (if base already has -TYPE, remove content after slash)
 *   text.aaaa.0004.0002/M   → text.aaaa.0004.0002-M   (slash → dash conversion)
 *   text.aaaa.0004.0001-R   → text.aaaa.0004.0001-R   (already in official format, no conversion)
 */
export function formatDocId(raw: string): string {
  if (!raw) return raw

  if (raw.includes('/')) {
    const slashIdx = raw.indexOf('/')
    const base = raw.substring(0, slashIdx)

    // If base already ends with -TYPECODE, content after slash is redundant → remove
    if (/-[A-Z]+$/.test(base)) {
      return base
    }

    // /TYPECODE pattern → convert to -TYPECODE (treat as type code if last segment is uppercase letters only)
    const parts = raw.split('/')
    const lastPart = parts[parts.length - 1]
    if (/^[A-Z]+$/.test(lastPart) && lastPart.length <= 4) {
      return `${parts.slice(0, -1).join('.')}-${lastPart}`
    }

    return parts.join('.')
  }

  // No slash → already in official format or another form
  return raw
}

export function qApiPath(qId: string): string {
  if (!qId) return qId
  if (qId.includes('/')) {
    return qId.split('/').map((part) => encodeURIComponent(part)).join('/')
  }
  return encodeURIComponent(qId)
}
