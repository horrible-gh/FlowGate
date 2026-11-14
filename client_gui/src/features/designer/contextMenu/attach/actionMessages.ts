// flowgate.v02.0060 T#2 — display strings of the context menu's 2-B windows (rename, Flow 삭제 = dispose (DC),
// new folder / file, upload, folder download, delete, 수정 확인). Owned by this folder and used through a local
// vue-i18n scope that follows the app locale, as the menu's own strings are (contextMenu/messages.ts).

const ko = {
  close: '닫기', cancel: '취소', target: '대상', id: '식별자', location: '위치', root: '루트 폴더', flow: '플로우',
  stale: '메뉴를 연 뒤 대상이 바뀌었거나 사라져 실행하지 않습니다.',
  rename: {
    groupTitle: '그룹 이름 변경', flowTitle: '플로우 이름 변경', name: '새 이름', submit: '이름 변경',
    note: '표시 이름만 바뀝니다. 식별자는 그대로입니다.', required: '이름을 입력하세요.',
    groupName: '이름', nameLong: '이름은 100자 이하여야 합니다.', ident: '식별자',
    identProblem: {
      empty: '식별자를 입력하세요.', upper: '식별자는 소문자만 쓸 수 있습니다.',
      chars: '식별자에는 영문 소문자·숫자·-·_만 쓸 수 있습니다.', long: '식별자는 40자 이하여야 합니다.',
      reserved: '예약된 식별자라 쓸 수 없습니다.',
    },
    server: {
      taken: '"{group}" 그룹이 쓰는 식별자입니다.',
      reserved: '이 그룹의 식별자는 바꿀 수 없습니다.',
      busy: '이 그룹에서 실행 중인 작업이 있습니다({group}). 끝난 뒤 다시 시도하세요.',
      branchExists: '새 식별자와 같은 이름의 Git 브랜치나 작업 폴더가 이미 있습니다({group}).',
      pathExists: '새 식별자와 같은 이름의 문서 폴더가 이미 있습니다.',
      gitFailed: 'Git 브랜치 이름을 바꾸지 못했습니다. 아무것도 바뀌지 않았습니다.',
      notFound: '그룹을 찾을 수 없습니다(이미 지워졌거나 옮겨졌습니다).',
    },
  },
  dispose: {
    title: '플로우 삭제', submit: '삭제', reason: '삭제 사유', reasonRequired: '사유를 입력해야 삭제할 수 있습니다.',
    note: '목록에서 제거됩니다. 기록은 유지됩니다.',
  },
  create: {
    folderTitle: '새 폴더', fileTitle: '새 파일', name: '이름', submit: '만들기',
    empty: '이름을 입력하세요.', chars: '이름에 / \\ : * ? " < > {\'|\'} 는 쓸 수 없습니다.', dots: '. 이나 .. 은 이름으로 쓸 수 없습니다.',
  },
  upload: {
    title: '업로드', pick: '파일 선택', none: '선택한 파일이 없습니다.', submit: '업로드', checking: '같은 이름의 파일을 확인하는 중…',
    overwriteTitle: '이미 있는 것으로 확인된 파일', noKnown: '탐색기에 보이는 파일 중 같은 이름은 없습니다.',
    overwriteNote: '이 폴더에 같은 이름의 파일이 있으면 덮어씁니다. 탐색기에 보이지 않는 파일(.gitignore로 제외된 파일, 읽기 권한이 없는 파일 등)도 덮어쓸 수 있습니다.',
    overwrite: '같은 이름의 파일이 있으면 덮어써도 됩니다', checkFailed: '이 폴더의 기존 파일을 확인하지 못했습니다. 선택한 파일은 모두 덮어쓸 수 있는 것으로 봅니다.',
    done: '업로드 결과', uploaded: '올린 파일 {n}개', skipped: '건너뛴 파일 {n}개', skippedRow: '{path} — {reason}',
  },
  download: {
    title: '폴더 다운로드', submit: '다운로드', zip: '폴더를 ZIP 파일 하나로 받습니다.', empty: '빈 폴더는 ZIP에 들어가지 않습니다.',
    failed: '다운로드하지 못했습니다', done: '다운로드했습니다: {name}',
  },
  remove: {
    title: '삭제', submit: '삭제', file: '이 파일 하나를 삭제합니다.', folder: '이 폴더와 그 안의 모든 파일·폴더를 삭제합니다.',
    irreversible: '이 작업은 되돌릴 수 없습니다.',
  },
  diff: {
    title: '수정 확인', base: '비교 기준', statusLabel: '상태', baseValue: '프로젝트 Base {base}', loading: '읽는 중…', same: '차이 없음',
    binary: '이진 파일이라 비교하지 않습니다.', tooLarge: '파일이 커서 비교하지 않습니다.', retry: '다시 읽기',
    counts: '추가 {add}줄 · 삭제 {del}줄', gap: '같은 줄 {n}개', noEol: '파일 끝 개행 없음', crlf: 'CRLF',
    partial: '표시할 줄이 많아 {shown} / {total}행까지만 보입니다.', more: '더 보기',
    status: { M: '수정', A: '추가', D: '삭제' },
  },
  refusal: {
    disposed: '이미 폐기된 플로우입니다.', not_found: '대상이 없습니다(이미 지워졌거나 옮겨졌습니다).', forbidden: '권한이 없습니다.',
    locked: '플로우가 작업 중이라 지금은 바꿀 수 없습니다.', managed: '관리 경로라 여기서 바꿀 수 없습니다.',
    exists: '같은 이름이 이미 있습니다.', invalid: '서버가 요청을 거절했습니다(경로·이름·종류 확인).',
    worktree: '플로우 워크트리를 쓸 수 없습니다.', too_large: '파일이 너무 큽니다(파일당 100MB, 전체 500MB).',
    network: '서버에 연결하지 못했습니다.', failed: '처리하지 못했습니다.',
    base_unavailable: '프로젝트 Base를 확인할 수 없어 비교하지 않았습니다.', base_mismatch: '서버가 다른 기준으로 답해 비교 결과를 보여주지 않습니다.',
  },
}

type Messages = typeof ko

const en: Messages = {
  close: 'Close', cancel: 'Cancel', target: 'Target', id: 'Identifier', location: 'Location', root: 'Root folder', flow: 'Flow',
  stale: 'The target changed or is gone since the menu opened; nothing runs.',
  rename: {
    groupTitle: 'Rename group', flowTitle: 'Rename flow', name: 'New name', submit: 'Rename',
    note: 'Only the display name changes. The identifier stays.', required: 'Enter a name.',
    groupName: 'Name', nameLong: 'Names are 100 characters or fewer.', ident: 'Identifier',
    identProblem: {
      empty: 'Enter an identifier.', upper: 'Identifiers are lowercase only.',
      chars: 'Use only lowercase letters, digits, - and _.', long: 'Identifiers are 40 characters or fewer.',
      reserved: 'This identifier is reserved.',
    },
    server: {
      taken: 'The group "{group}" uses this identifier.',
      reserved: "This group's identifier cannot change.",
      busy: 'Work is running in this group ({group}). Try again when it ends.',
      branchExists: 'A Git branch or worktree with the new name already exists ({group}).',
      pathExists: 'A document folder with the new name already exists.',
      gitFailed: 'The Git branch could not be renamed. Nothing was changed.',
      notFound: 'The group is not there (deleted or moved).',
    },
  },
  dispose: {
    title: 'Delete flow', submit: 'Delete', reason: 'Reason', reasonRequired: 'A reason is required to delete.',
    note: 'Removed from the list. Records are kept.',
  },
  create: {
    folderTitle: 'New folder', fileTitle: 'New file', name: 'Name', submit: 'Create',
    empty: 'Enter a name.', chars: 'A name cannot contain / \\ : * ? " < > {\'|\'}.', dots: '. and .. cannot be names.',
  },
  upload: {
    title: 'Upload', pick: 'Choose files', none: 'No file chosen.', submit: 'Upload', checking: 'Checking for files with the same name…',
    overwriteTitle: 'Files known to exist', noKnown: 'No file shown in the explorer has the same name.',
    overwriteNote: 'A file with the same name in this folder is overwritten, even one the explorer does not show (ignored by .gitignore, not readable, and so on).',
    overwrite: 'Files with the same name may be overwritten', checkFailed: 'Could not read the files in this folder. Any chosen file may overwrite one.',
    done: 'Upload result', uploaded: '{n} uploaded', skipped: '{n} skipped', skippedRow: '{path} — {reason}',
  },
  download: {
    title: 'Download folder', submit: 'Download', zip: 'The folder is downloaded as one ZIP file.', empty: 'Empty folders are not included in the ZIP.',
    failed: 'Download failed', done: 'Downloaded: {name}',
  },
  remove: {
    title: 'Delete', submit: 'Delete', file: 'This one file is deleted.', folder: 'This folder and every file and folder in it are deleted.',
    irreversible: 'This cannot be undone.',
  },
  diff: {
    title: 'View changes', base: 'Compared with', statusLabel: 'Status', baseValue: 'Project Base {base}', loading: 'Reading…', same: 'No difference',
    binary: 'Binary file: not compared.', tooLarge: 'File too large: not compared.', retry: 'Read again',
    counts: '{add} added · {del} removed', gap: '{n} unchanged lines', noEol: 'No newline at end of file', crlf: 'CRLF',
    partial: 'Too many lines: showing {shown} of {total} rows.', more: 'Show more',
    status: { M: 'Modified', A: 'Added', D: 'Deleted' },
  },
  refusal: {
    disposed: 'The flow is already disposed.', not_found: 'The target is not there (deleted or moved).', forbidden: 'Permission denied.',
    locked: 'The flow is busy; it cannot change now.', managed: 'A managed path cannot change here.',
    exists: 'The name already exists.', invalid: 'The server refused the request (check path, name, type).',
    worktree: 'The flow worktree is unavailable.', too_large: 'Too large (100 MB per file, 500 MB in total).',
    network: 'Could not reach the server.', failed: 'Could not be done.',
    base_unavailable: 'The project Base is unknown; nothing was compared.', base_mismatch: 'The server answered for another base; the result is not shown.',
  },
}

const ja: Messages = {
  close: '閉じる', cancel: 'キャンセル', target: '対象', id: '識別子', location: '場所', root: 'ルートフォルダー', flow: 'フロー',
  stale: 'メニューを開いた後に対象が変わったか、なくなったため実行しません。',
  rename: {
    groupTitle: 'グループ名を変更', flowTitle: 'フロー名を変更', name: '新しい名前', submit: '名前を変更',
    note: '表示名だけが変わります。識別子はそのままです。', required: '名前を入力してください。',
    groupName: '名前', nameLong: '名前は100文字以下にしてください。', ident: '識別子',
    identProblem: {
      empty: '識別子を入力してください。', upper: '識別子は小文字のみ使えます。',
      chars: '識別子には英小文字・数字・-・_のみ使えます。', long: '識別子は40文字以下にしてください。',
      reserved: '予約済みの識別子のため使えません。',
    },
    server: {
      taken: 'グループ「{group}」が使っている識別子です。',
      reserved: 'このグループの識別子は変更できません。',
      busy: 'このグループで実行中の作業があります({group})。終わってから再度お試しください。',
      branchExists: '新しい識別子と同じ名前のGitブランチまたは作業フォルダーが既にあります({group})。',
      pathExists: '新しい識別子と同じ名前の文書フォルダーが既にあります。',
      gitFailed: 'Gitブランチの名前を変更できませんでした。何も変更されていません。',
      notFound: 'グループが見つかりません(削除または移動されました)。',
    },
  },
  dispose: {
    title: 'フローを削除', submit: '削除', reason: '削除理由', reasonRequired: '理由を入力すると削除できます。',
    note: '一覧から削除されます。記録は保持されます。',
  },
  create: {
    folderTitle: '新規フォルダー', fileTitle: '新規ファイル', name: '名前', submit: '作成',
    empty: '名前を入力してください。', chars: '名前に / \\ : * ? " < > {\'|\'} は使えません。', dots: '. と .. は名前に使えません。',
  },
  upload: {
    title: 'アップロード', pick: 'ファイルを選択', none: '選択したファイルはありません。', submit: 'アップロード', checking: '同じ名前のファイルを確認中…',
    overwriteTitle: 'あることを確認したファイル', noKnown: 'エクスプローラーに表示されるファイルに同じ名前はありません。',
    overwriteNote: 'このフォルダーに同じ名前のファイルがあれば上書きします。エクスプローラーに表示されないファイル(.gitignoreで除外されたファイル、読み取り権限のないファイルなど)も上書きされることがあります。',
    overwrite: '同じ名前のファイルがあれば上書きしてかまいません', checkFailed: 'このフォルダーの既存ファイルを確認できませんでした。選択したファイルはすべて上書きする可能性があります。',
    done: 'アップロード結果', uploaded: 'アップロード {n}件', skipped: 'スキップ {n}件', skippedRow: '{path} — {reason}',
  },
  download: {
    title: 'フォルダーをダウンロード', submit: 'ダウンロード', zip: 'フォルダーを1つのZIPファイルで受け取ります。', empty: '空のフォルダーはZIPに含まれません。',
    failed: 'ダウンロードできませんでした', done: 'ダウンロードしました: {name}',
  },
  remove: {
    title: '削除', submit: '削除', file: 'このファイル1つを削除します。', folder: 'このフォルダーと中のすべてのファイル・フォルダーを削除します。',
    irreversible: 'この操作は元に戻せません。',
  },
  diff: {
    title: '変更を確認', base: '比較基準', statusLabel: '状態', baseValue: 'プロジェクトBase {base}', loading: '読み込み中…', same: '差分なし',
    binary: 'バイナリファイルのため比較しません。', tooLarge: 'ファイルが大きいため比較しません。', retry: '再読み込み',
    counts: '追加 {add}行 · 削除 {del}行', gap: '同じ行 {n}行', noEol: 'ファイル末尾に改行なし', crlf: 'CRLF',
    partial: '表示する行が多いため {shown} / {total}行までを表示しています。', more: 'さらに表示',
    status: { M: '変更', A: '追加', D: '削除' },
  },
  refusal: {
    disposed: 'すでに破棄されたフローです。', not_found: '対象がありません(削除または移動されました)。', forbidden: '権限がありません。',
    locked: 'フローが作業中のため今は変更できません。', managed: '管理パスのためここでは変更できません。',
    exists: '同じ名前がすでにあります。', invalid: 'サーバーが要求を拒否しました(パス・名前・種類を確認)。',
    worktree: 'フローのワークツリーを使えません。', too_large: 'ファイルが大きすぎます(1ファイル100MB、合計500MB)。',
    network: 'サーバーに接続できませんでした。', failed: '処理できませんでした。',
    base_unavailable: 'プロジェクトBaseを確認できないため比較しませんでした。', base_mismatch: 'サーバーが別の基準で応答したため結果を表示しません。',
  },
}

export const menuActionMessages = { ko, en, ja }
