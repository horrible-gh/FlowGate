// display strings of the context menu's 2-B windows (rename, Flow Delete = dispose (DC),
// new folder / file, upload, folder download, delete, Review changes). Owned by this folder and used through a local
// vue-i18n scope that follows the app locale, as the menu's own strings are (contextMenu/messages.ts).

const ko = {
  close: '닫기', cancel: '취소', target: '대상', id: '식별자', location: '위치', root: '루트 폴더', flow: '플로우', group: '그룹',
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
      archivedTaken: '보관된 그룹 "{group}"이 쓰는 식별자입니다. 그 그룹의 식별자를 먼저 바꾸면 쓸 수 있습니다. (프로젝트 탐색기의 "보관 포함"에서 볼 수 있음)',
      busyDetailed: '실행 중인 작업이 있어 식별자를 바꿀 수 없습니다: {items}{more}',
      busyMore: ' 외 {n}건',
      titleSaved: '표시명은 저장되었습니다.',
      tooLarge: '문서가 {documents}건이라 식별자를 바꿀 수 없습니다(한도 {limit}건). 표시명은 바꿀 수 있습니다.',
      busyReasons: { flow_run_active: '플로우 실행 중', ai_lease_active: 'AI 사용 중', ai_run_active: 'AI 실행 중', ai_chain_paused: 'AI 작업 일시 중지', merge_session_open: '병합 세션 열림', test_run_active: '테스트 실행 중', self_check_active: '셀프체크 실행 중', git_job_active: 'Git 작업 중', chat_command_active: '채팅 명령 실행 중', agent_job_active: '에이전트 작업 중', flow_lock_timeout: '플로우 잠금 시간 초과' },

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
    // v02gui.0029 (0003-CH): the confirm's sentence, its two points, the input's placeholder and the reason chips.
    lead: '이 플로우를 목록에서 제거합니다.', placeholder: '왜 삭제하는지 적어 주세요. 기록에 남습니다.',
    points: { kept: '기록은 그대로 남습니다.', hidden: '플로우 목록에서는 더 이상 보이지 않습니다.' },
    chips: { duplicate: '중복', test: '테스트용', unused: '더 이상 안 씀', mistake: '잘못 만듦' },
  },
  archive: {
    title: '그룹 아카이브', submit: '아카이브', note: '기록은 보존하고 기본 목록에서 숨깁니다.',
    hint: '안의 플로우·문서·실행 기록·Git은 그대로 남습니다. "아카이브 포함"을 켜면 다시 보이고, 그룹 메뉴의 그룹 복원으로 되돌릴 수 있습니다.',
    server: { already: '이미 아카이브된 그룹입니다.' },
    // 0016-CH option B: the confirm's point list (the hint above, one line each).
    points: {
      kept: '안의 플로우·문서·실행 기록·Git은 그대로 남습니다.',
      visible: '"아카이브 포함"을 켜면 다시 보입니다.',
      restore: '그룹 메뉴의 그룹 복원으로 언제든 되돌릴 수 있습니다.',
    },
  },
  groupDelete: {
    title: '그룹 삭제', submit: '삭제', archiveInstead: '대신 아카이브',
    note: '빈 그룹입니다. 삭제하면 프로젝트 트리에서 사라집니다.',
    hint: '그룹만 지워지고, 이력이 지워지는 일은 없습니다.',
    blocked: 'Flow {n}개가 남아 있어 삭제할 수 없습니다. 먼저 아카이브하세요.',
    blockedArchived: 'Flow {n}개가 남아 있어 삭제할 수 없습니다.',
    points: { archiveAlt: '기록을 남기고 숨기기만 하려면 아카이브를 쓰세요.' },
  },
  create: {
    folderTitle: '새 폴더', fileTitle: '새 파일', name: '이름', submit: '만들기', where: '만들 위치',
    empty: '이름을 입력하세요.', chars: '이름에 / \\ : * ? " < > {\'|\'} 는 쓸 수 없습니다.', dots: '. 이나 .. 은 이름으로 쓸 수 없습니다.',
  },
  upload: {
    title: '업로드', where: '올릴 위치', pick: '파일 선택', none: '선택한 파일이 없습니다.', submit: '업로드', checking: '같은 이름의 파일을 확인하는 중…',
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
    fileTitle: '파일 삭제', folderTitle: '폴더 삭제', submit: '삭제', folderSubmit: '폴더 삭제',
    file: '이 파일 하나를 삭제합니다.', folderLead: '이 폴더를 삭제합니다.', contents: '안에 있는 모든 파일·폴더가 함께 지워집니다.',
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
  close: 'Close', cancel: 'Cancel', target: 'Target', id: 'Identifier', location: 'Location', root: 'Root folder', flow: 'Flow', group: 'Group',
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
      archivedTaken: 'Archived group "{group}" uses this identifier. Change that group’s identifier first to use it. (Find it in the Project Explorer with “Include archived”.)',
      busyDetailed: 'An active task prevents changing the identifier: {items}{more}',
      busyMore: ' and {n} more',
      titleSaved: 'The display name was saved.',
      tooLarge: 'The group has {documents} documents, so its identifier cannot change (limit {limit}). You can change the display name.',
      busyReasons: { flow_run_active: 'flow running', ai_lease_active: 'AI in use', ai_run_active: 'AI running', ai_chain_paused: 'AI work paused', merge_session_open: 'merge session open', test_run_active: 'test running', self_check_active: 'self-check running', git_job_active: 'Git job running', chat_command_active: 'chat command running', agent_job_active: 'agent job running', flow_lock_timeout: 'flow lock timed out' },

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
    lead: 'This flow is removed from the list.', placeholder: 'Why is it deleted? The reason is kept in the records.',
    points: { kept: 'Its records stay as they are.', hidden: 'It no longer shows in the flow list.' },
    chips: { duplicate: 'Duplicate', test: 'For testing', unused: 'No longer used', mistake: 'Made by mistake' },
  },
  archive: {
    title: 'Archive group', submit: 'Archive', note: 'Records are kept; the group is hidden from the default list.',
    hint: 'Its flows, documents, run history and Git stay as they are. Turn on "Include archived" to see it again, and use Restore group in its menu to bring it back.',
    server: { already: 'This group is already archived.' },
    points: {
      kept: 'Its flows, documents, run history and Git stay as they are.',
      visible: 'Turn on "Include archived" to see it again.',
      restore: 'Use Restore group in its menu to bring it back at any time.',
    },
  },
  groupDelete: {
    title: 'Delete group', submit: 'Delete', archiveInstead: 'Archive instead',
    note: 'This group is empty. Deleting it removes it from the project tree.',
    hint: 'Only the group goes; no history is erased.',
    blocked: '{n} flow(s) remain, so this group cannot be deleted. Archive it first.',
    blockedArchived: '{n} flow(s) remain, so this group cannot be deleted.',
    points: { archiveAlt: 'To keep the records and only hide it, use Archive instead.' },
  },
  create: {
    folderTitle: 'New folder', fileTitle: 'New file', name: 'Name', submit: 'Create', where: 'Created here',
    empty: 'Enter a name.', chars: 'A name cannot contain / \\ : * ? " < > {\'|\'}.', dots: '. and .. cannot be names.',
  },
  upload: {
    title: 'Upload', where: 'Uploaded here', pick: 'Choose files', none: 'No file chosen.', submit: 'Upload', checking: 'Checking for files with the same name…',
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
    fileTitle: 'Delete file', folderTitle: 'Delete folder', submit: 'Delete', folderSubmit: 'Delete folder',
    file: 'This one file is deleted.', folderLead: 'This folder is deleted.', contents: 'Every file and folder in it is deleted with it.',
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
  close: '閉じる', cancel: 'キャンセル', target: '対象', id: '識別子', location: '場所', root: 'ルートフォルダー', flow: 'フロー', group: 'グループ',
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
      archivedTaken: 'アーカイブ済みグループ「{group}」が使っている識別子です。先にそのグループの識別子を変更してください。(プロジェクトエクスプローラーの「アーカイブを含める」で表示できます)',
      busyDetailed: '実行中の作業があるため識別子を変更できません: {items}{more}',
      busyMore: '、ほか{n}件',
      titleSaved: '表示名は保存されました。',
      tooLarge: '文書が{documents}件あるため識別子を変更できません(上限{limit}件)。表示名は変更できます。',
      busyReasons: { flow_run_active: 'フロー実行中', ai_lease_active: 'AI使用中', ai_run_active: 'AI実行中', ai_chain_paused: 'AI作業一時停止中', merge_session_open: 'マージセッションが開いています', test_run_active: 'テスト実行中', self_check_active: 'セルフチェック実行中', git_job_active: 'Git作業中', chat_command_active: 'チャットコマンド実行中', agent_job_active: 'エージェント作業中', flow_lock_timeout: 'フローロックのタイムアウト' },
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
    lead: 'このフローを一覧から削除します。', placeholder: '削除する理由を書いてください。記録に残ります。',
    points: { kept: '記録はそのまま残ります。', hidden: 'フロー一覧には表示されなくなります。' },
    chips: { duplicate: '重複', test: 'テスト用', unused: 'もう使わない', mistake: '誤って作成' },
  },
  archive: {
    title: 'グループをアーカイブ', submit: 'アーカイブ', note: '記録は保持したまま、既定の一覧から非表示にします。',
    hint: '中のフロー・文書・実行履歴・Git はそのまま残ります。「アーカイブを含める」をオンにすると再び表示され、グループメニューの「グループを復元」で元に戻せます。',
    server: { already: 'すでにアーカイブ済みのグループです。' },
    points: {
      kept: '中のフロー・文書・実行履歴・Git はそのまま残ります。',
      visible: '「アーカイブを含める」をオンにすると再び表示されます。',
      restore: 'グループメニューの「グループを復元」でいつでも元に戻せます。',
    },
  },
  groupDelete: {
    title: 'グループを削除', submit: '削除', archiveInstead: '代わりにアーカイブ',
    note: '空のグループです。削除するとプロジェクトツリーから消えます。',
    hint: 'グループだけが削除され、履歴が消えることはありません。',
    blocked: 'フローが{n}件残っているため削除できません。先にアーカイブしてください。',
    blockedArchived: 'フローが{n}件残っているため削除できません。',
    points: { archiveAlt: '記録を残して非表示にするだけなら、アーカイブを使ってください。' },
  },
  create: {
    folderTitle: '新規フォルダー', fileTitle: '新規ファイル', name: '名前', submit: '作成', where: '作成先',
    empty: '名前を入力してください。', chars: '名前に / \\ : * ? " < > {\'|\'} は使えません。', dots: '. と .. は名前に使えません。',
  },
  upload: {
    title: 'アップロード', where: 'アップロード先', pick: 'ファイルを選択', none: '選択したファイルはありません。', submit: 'アップロード', checking: '同じ名前のファイルを確認中…',
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
    fileTitle: 'ファイルを削除', folderTitle: 'フォルダーを削除', submit: '削除', folderSubmit: 'フォルダーを削除',
    file: 'このファイル1つを削除します。', folderLead: 'このフォルダーを削除します。', contents: '中のすべてのファイル・フォルダーも一緒に削除されます。',
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
