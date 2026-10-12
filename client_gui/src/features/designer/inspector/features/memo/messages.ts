// flowgate.v02.0053 T#9 — header ✎ 메모 strings, merged under `gui.inspector.memo`.
export default {
  ko: {
    label: '메모',
    write: '메모 쓰기',
    open: '메모 열기',
    // The memos could not be read: none is written until a read answers (a click reads again).
    unread: '메모를 읽지 못했습니다',
    // The new memo document's title (the card's name after it).
    title: '메모 — {title}',
    failure: {
      running: 'AI가 작업 중이라 지금은 메모를 쓸 수 없습니다',
      forbidden: '메모를 쓸 권한이 없습니다',
      network: '연결이 끊겨 메모를 쓰지 못했습니다',
      other: '메모를 쓰지 못했습니다',
    },
  },
  en: {
    label: 'Memo',
    write: 'Write a memo',
    open: 'Open the memo',
    unread: 'The memos could not be read',
    title: 'Memo — {title}',
    failure: {
      running: 'The AI is working, so a memo cannot be written now',
      forbidden: 'You do not have permission to write a memo',
      network: 'The connection dropped and the memo was not written',
      other: 'The memo could not be written',
    },
  },
  ja: {
    label: 'メモ',
    write: 'メモを書く',
    open: 'メモを開く',
    unread: 'メモを読み込めませんでした',
    title: 'メモ — {title}',
    failure: {
      running: 'AIが作業中のため、今はメモを書けません',
      forbidden: 'メモを書く権限がありません',
      network: '接続が切れたためメモを書けませんでした',
      other: 'メモを書けませんでした',
    },
  },
}
