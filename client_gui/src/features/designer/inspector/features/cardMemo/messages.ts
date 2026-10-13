// flowgate.v02.0056 F2-2 — card memo strings, merged under `gui.inspector.cardMemo`.
// flowgate.v02.0056 (0017-CH) — no [AI가 참조] hint, no section head or empty line; `unsaved` is why the header
// ✎ 메모 cannot write on a card not stored yet. A memo opened to read has [편집] (`edit`) where the form has [저장].
export default {
  ko: {
    label: '메모',
    add: '메모 추가',
    unsaved: '저장된 카드에서 메모를 쓸 수 있습니다',
    retry: '다시 시도',
    ai_ref: 'AI가 참조',
    ai_ref_on: 'AI 참조',
    length: '{n}/{max}자',
    field: {
      title: '제목',
      body: '본문',
    },
    window: {
      new: '새 메모',
    },
    edit: '편집',
    delete: '삭제',
    delete_confirm: '이 메모를 삭제할까요?',
    save: '저장',
    cancel: '취소',
    close: '닫기',
    locked: {
      conflict: '담당이 겹친 플로우라 메모를 바꿀 수 없습니다',
      revision: '확정본 보기에서는 메모를 바꿀 수 없습니다',
    },
    failure: {
      field: {
        title: {
          required: '제목을 입력하세요.',
          not_a_text: '제목을 글자로 보낼 수 없습니다.',
          too_long: '제목은 200자까지 쓸 수 있습니다.',
          invalid_text: '제목은 줄바꿈이나 제어 문자 없이 한 줄로 씁니다.',
        },
        body: {
          not_a_text: '본문을 글자로 보낼 수 없습니다.',
          too_long: '본문은 20000자까지 쓸 수 있습니다.',
          no_change: '바뀐 내용이 없습니다.',
        },
        ai_ref: {
          not_a_bool: 'AI 참조 값을 보낼 수 없습니다.',
        },
      },
      invalid: '저장할 수 없는 내용입니다.',
      memo_not_found: '메모가 이미 없습니다.',
      flow_disposed: '폐기된 플로우라 메모를 바꿀 수 없습니다.',
      unavailable: '서버가 카드 메모를 지원하지 않습니다.',
      not_found: '카드나 플로우를 찾을 수 없습니다.',
      forbidden: '메모를 다룰 권한이 없습니다.',
      network: '서버에 연결하지 못했습니다.',
      other: '메모를 처리하지 못했습니다.',
    },
  },
  en: {
    label: 'Memos',
    add: 'Add a memo',
    unsaved: 'Memos are written on a stored card',
    retry: 'Retry',
    ai_ref: 'AI refers',
    ai_ref_on: 'AI refers',
    length: '{n}/{max} characters',
    field: {
      title: 'Title',
      body: 'Body',
    },
    window: {
      new: 'New memo',
    },
    edit: 'Edit',
    delete: 'Delete',
    delete_confirm: 'Delete this memo?',
    save: 'Save',
    cancel: 'Cancel',
    close: 'Close',
    locked: {
      conflict: 'The flow has conflicting owners; its memos cannot change',
      revision: 'Memos cannot change in a revision view',
    },
    failure: {
      field: {
        title: {
          required: 'Enter a title.',
          not_a_text: 'The title cannot be sent as text.',
          too_long: 'A title may have 200 characters at most.',
          invalid_text: 'A title is one line without line breaks or control characters.',
        },
        body: {
          not_a_text: 'The body cannot be sent as text.',
          too_long: 'A body may have 20000 characters at most.',
          no_change: 'Nothing changed.',
        },
        ai_ref: {
          not_a_bool: 'The AI refers value cannot be sent.',
        },
      },
      invalid: 'This content cannot be saved.',
      memo_not_found: 'The memo is gone.',
      flow_disposed: 'The flow was disposed; its memos cannot change.',
      unavailable: 'The server does not support card memos.',
      not_found: 'The card or the flow was not found.',
      forbidden: 'You may not handle memos.',
      network: 'Could not reach the server.',
      other: 'Could not handle the memo.',
    },
  },
  ja: {
    label: 'メモ',
    add: 'メモを追加',
    unsaved: '保存済みのカードでメモを書けます',
    retry: '再試行',
    ai_ref: 'AI が参照',
    ai_ref_on: 'AI 参照',
    length: '{n}/{max} 文字',
    field: {
      title: 'タイトル',
      body: '本文',
    },
    window: {
      new: '新しいメモ',
    },
    edit: '編集',
    delete: '削除',
    delete_confirm: 'このメモを削除しますか?',
    save: '保存',
    cancel: 'キャンセル',
    close: '閉じる',
    locked: {
      conflict: '担当が重なったフローのためメモを変更できません',
      revision: '確定版の表示ではメモを変更できません',
    },
    failure: {
      field: {
        title: {
          required: 'タイトルを入力してください。',
          not_a_text: 'タイトルを文字として送れません。',
          too_long: 'タイトルは 200 文字までです。',
          invalid_text: 'タイトルは改行や制御文字のない 1 行で書きます。',
        },
        body: {
          not_a_text: '本文を文字として送れません。',
          too_long: '本文は 20000 文字までです。',
          no_change: '変更がありません。',
        },
        ai_ref: {
          not_a_bool: 'AI 参照の値を送れません。',
        },
      },
      invalid: '保存できない内容です。',
      memo_not_found: 'メモはすでにありません。',
      flow_disposed: '破棄されたフローのためメモを変更できません。',
      unavailable: 'サーバーがカードメモに対応していません。',
      not_found: 'カードまたはフローが見つかりません。',
      forbidden: 'メモを扱う権限がありません。',
      network: 'サーバーに接続できませんでした。',
      other: 'メモを処理できませんでした。',
    },
  },
}
