// structure.0048 B15 fixture (clean): a feature opens a document through openDocument only.
import { openDocument } from '../../openDocument'

export function useSample(docId: string) {
  return { open: () => openDocument(docId) }
}
