import { PRODUCT_NAME } from '../productName'

export default {
  common: {
    reset: '元に戻す',
    toast: {
      ai_provider_in_use: "実行中のタスクで使用しているAIは削除できません。",
    },
  },
  auth: {
    login: {
      title: `${PRODUCT_NAME} ログイン`,
      password: 'パスワード',
      show_password: 'パスワードを表示',
      hide_password: 'パスワードを非表示',
      username_or_email: 'ユーザー名またはメール',
      username_or_email_placeholder: 'ユーザー名またはメールを入力',
      password_placeholder: 'パスワードを入力',
      os_login: 'OS ログイン',
      contact_admin: '管理者にお問い合わせください。',
      required: 'ユーザー名とパスワードを入力してください。',
      submit: 'ログイン',
      forgot_password: 'パスワードをお忘れですか？',
      redirecting: '移動中...',
      locked: {
        title: 'アカウントがロックされました',
        message: 'ログイン失敗が5回を超えたため、アカウントが一時的にロックされました。',
        countdown: 'ロック解除まで: {time}',
      },
      error: {
        invalid_credentials: 'ユーザー名またはパスワードが正しくありません。',
        attempt_count: 'ログイン試行 {count}/5回',
        attempt_count_warning: 'あと1回失敗すると15分間ロックされます。',
      },
    },
    totp: {
      title: '二要素認証 (OTP)',
      instruction: '認証アプリの6桁コードを入力してください。',
      code_label: '認証コード',
      submit: '確認',
      use_backup: 'バックアップコードを使用',
      back: '戻る',
      lost_hint: '認証アプリを紛失した場合は、管理者にTOTP初期化を依頼してください。',
      error: {
        invalid_code: '認証コードが正しくありません。',
        code_expired: 'セッションの有効期限が切れました。再度ログインしてください。',
      },
      backup: {
        title: 'バックアップコード入力',
        instruction: '登録時に発行されたバックアップコードを1つ入力してください。コードは一度のみ使用できます。',
        code_label: 'バックアップコード',
        submit: '確認',
        back_to_otp: 'OTP入力に戻る',
        error: {
          invalid: 'バックアップコードが正しくありません。',
          used: 'このバックアップコードはすでに使用されています。',
        },
      },
    },
    totp_setup: {
      title: '2段階認証の登録',
      instruction: '管理者のポリシーにより、2段階認証を登録しないとログインできません。',
      start: '登録を開始',
      secret_label: '認証アプリに入力するキー',
      uri_label: '認証アプリで開く (otpauth)',
      backup_title: 'バックアップコード — この一度だけ表示されます',
      code_label: '認証アプリの6桁コード',
      submit: '登録を完了',
      pending_hint: 'コードを確認すると登録が完了します。その前にこの画面を離れると何も保存されず、再度ログインすると新しいキーで登録をやり直せます。',
      failed: '2段階認証を登録できませんでした。',
    },
    invite: {
      title: '招待の承諾',
      instruction: '{username} ({email}) のパスワードを設定してください。',
      submit: 'パスワードを設定',
      back: 'ログインへ',
      invalid: '招待リンクが無効か期限切れです。管理者に再発行を依頼してください。',
      accepted: 'パスワードを設定しました。ログインしてください。',
    },
    password: {
      change: {
        title: 'パスワード変更が必要です',
        initial_message: '初回ログインです。安全な新しいパスワードを設定してください。',
        expired_message: 'パスワードの有効期限が切れました。新しいパスワードを設定してください。',
      },
      current_label: '現在のパスワード',
      new_label: '新しいパスワード',
      confirm_label: 'パスワード確認',
      policy_hint: '12文字以上、英大文字・英小文字・数字・記号のうち2種類以上',
      submit: '変更完了',
      error: {
        policy_violation: 'パスワードがポリシーを満たしていません。{policy}',
        mismatch: '新しいパスワードが一致しません。',
        current_incorrect: '現在のパスワードが正しくありません。',
      },
    },
  },
  error: {
    network: 'ネットワーク接続に失敗しました。',
    unauthorized: '認証されていないリクエストです。',
    forbidden: 'アクセス権限がありません。',
    not_found: '見つかりません。',
    server: 'サーバーエラーが発生しました。',
    error_group_r_exists: 'グループ内に既に作成されたドキュメントがあります。',
  },
  settings: {
    system: {
      ai: {
        title: 'AI設定',
      },
    },
    ai: {
      providers_title: 'プロバイダー一覧',
      providers_hint: '上にあるほど優先されます。一覧の順序が障害・使用量超過時のフォールバック順になります。順序はハンドルをドラッグするか、上・下ボタンで変更します。',
      empty: '登録されたプロバイダーがありません。',
      add_provider: 'プロバイダー追加',
      edit_provider: 'プロバイダー編集',
      default_badge: '既定選択',
      default_select_aria: '既定選択に指定',
      enabled: '有効',
      disabled_label: '無効',
      move_up: '上へ',
      move_down: '下へ',
      label_name: '名前',
      label_exec_type: '実行方式',
      label_kind: '種類',
      label_enabled: '有効',
      label_cli_command: 'CLI実行コマンド',
      magic_tool: '選択したCLIに合うコマンドを入力',
      magic_tool_error: 'コマンドを入力できませんでした。',
      magic_model_hint: '保存する前にコマンド内の model_name を実際のモデル名に置き換えてください。',
      skip_permissions_badge: '権限確認オフ',
      exec_mode_cli: 'CLI実行',
      exec_mode_api: 'API実行',
      view_command: 'コマンドを見る',
      command_title: '{name} · コマンド',
      skip_permissions_cmd_warn: 'この CLI は確認なしでファイルを読み書きし、コマンドを実行します。',
      delete_provider_title: 'プロバイダー削除',
      delete_provider_body: '「{name}」プロバイダーを削除します。',
      delete_provider_hint: '削除すると、フォールバック順から外れます。',
      label_api_base_url: 'API接続URL',
      label_api_model: 'APIモデル',
      label_api_key: 'APIキー',
      placeholder_name: '例: クロードCLI',
      placeholder_cli_command: '例: claude -p',
      placeholder_api_base_url: '空欄の場合は種類ごとの既定URLを使用します',
      placeholder_api_model: '例: claude-sonnet-5',
      placeholder_api_key: 'キーを入力してください',
      key_set_hint: 'キー登録済み (…{hint})',
      key_keep_hint: '空欄の場合は既存のキーを維持します。',
      key_clear: '既存キーを削除',
      key_cleared: '保存時にキーが削除されます。',
      key_none: 'キー未登録',
      key_unreadable: 'キーを読み取れません - 暗号化キーが変わりました。キーを再入力してください。',
      err_name_required: '名前を入力してください。',
      err_kind_required: '種類を選択してください。',
      err_cli_command_required: 'CLI実行コマンドを入力してください。',
      err_api_model_required: 'APIモデルを入力してください。',
      err_too_long: '{field}が長すぎます。最大{max}文字のところ、現在{len}文字です。',
      err_duplicate_name: '同じ名前のプロバイダーが既にあります。大文字小文字だけが異なる名前も同一とみなします。',
      err_too_many: 'プロバイダーは最大{max}件まで登録できます。',
      saveerr_title: '保存できませんでした。以下の項目を修正してから保存し直してください。',
      saveerr_row: '{row}番目「{name}」 · {field}: {reason}',
      saveerr_row_noname: '{row}番目の項目 · {field}: {reason}',
      saveerr_field: '{field}: {reason}',
      field: {
        name: '名前',
        exec_type: '実行方式',
        kind: '種類',
        cli_command: 'CLI実行コマンド',
        api_base_url: 'API接続URL',
        api_model: 'APIモデル',
        api_key: 'APIキー',
        providers: 'プロバイダー一覧',
        mode: '設定方式',
        default_provider_id: '既定プロバイダー',
        default_provider_index: '既定プロバイダー',
        target_agent_id: '対象 Agent',
      },
      reason: {
        required: '値を入力してください。',
        required_for_cli: 'CLI方式では必須です。',
        required_for_api: 'API方式では必須です。',
        too_long: '長すぎます。最大{max}文字まで入力できます。',
        duplicate_name: '他のプロバイダーと名前が重複しています。大文字小文字だけが異なる場合も重複です。',
        too_many: '件数が多すぎます。最大{max}件まで登録できます。',
        unknown_exec_type: 'サポートしていない実行方式です。',
        unknown_kind: '選択した実行方式ではサポートしていない種類です。',
        unknown_mode: 'サポートしていない設定方式です。',
        bad_default: '既定に指定したプロバイダーが一覧にありません。',
        required_for_remote_cli: 'リモート CLI では必須です。',
        invalid_target_agent: '選択した Agent が見つかりません。',
        target_agent_unavailable: '選択した Agent は利用できません。',
      },
      toast_invalid: '入力値が正しくありません。各項目を確認してください。',
      unsaved_badge: '未保存',
      unsaved_confirm: '保存していない変更があります。保存せずに移動しますか？',
      exec_type: {
        cli: 'CLI',
        api: 'API',
      },
      kind: {
        claude: 'クロード',
        copilot: 'コパイロット',
        codex: 'コーデックス',
        openai: 'OpenAI',
        custom: 'カスタム',
      },
    },
    users: {
      title: 'ユーザー管理',
      user_create_modal: {
        username_already_exists: 'このユーザー名は既に使用されています。',
      },
    },
    project: {
      ai: {
        title: 'AI設定',
      },
      git: {
        title: 'Gitリポジトリ連携',
        repo_url: 'リポジトリURL',
        provider: 'プロバイダ種別（任意）',
        secret: 'パスワード / トークン',
        base_branch: 'ベースブランチ',
        author_name: 'コミット Author 名（任意）',
        author_email: 'コミット Author メールアドレス（任意）',
      },
    },
  },
  main: {
    requirement: {
      create: {
        title: '要件定義作成',
        submit: '送信',
      },
    },
    design_handoff_dialog: {
      mention_batch: 'ワーカーさん、次のステップは設計作業です。次のタイプを一括で作成してください: {types}\n対象: {docRef}',
    },
    // 次のアクションが作業計画のときの専用提案ダイアログ。
    work_plan_create_dialog: {
      create_failed: '作業計画の作成中にエラーが発生しました。',
    },
    work_plan: {
      // ·F7 — ロック理由はサーバーの判定をそのまま表示する。
      step_no: '段階{n}',
      // 値が入ったステップが数量減少で消える前に確認する。
      // カード単位の移動と開始済みカードのロック。
      // サーバーが送る code+params から現在の UI locale で毎回再描画する。
      // 文言は work_plan_service._ERROR_COPY と対にする(プレースホルダ構文は同じ)。
      errors: {
        json_parse_failed: '作業計画の本文が JSON オブジェクトではありません。',
        wp_version_invalid: 'wp_version は整数でなければなりません。',
        wp_version_unsupported: 'wp_version={value} はこのサーバーでは読めません。',
        missing_field: '必須項目 {field} がありません。',
        type_invalid: '{field} の型が正しくありません。',
        unknown_field: '未知の項目 {field} です。実験用の項目は x_ で始めてください。',
        enum_not_allowed: '{field} に使用できない値です。',
        binding_not_allowed: 'binding は常に advisory でなければなりません。作業計画は実行を強制しません。',
        empty_selection: '{what} を一つ以上チェックしてください。',
        unknown_type_code: '{code} は数量を数えられるタイプではありません。',
        duplicate_type: 'タイプ {code} が二回出てきます。',
        quantities_key_mismatch: 'quantities のキーが counted_types と異なります。',
        unit_mismatch: '{code} の単位は {expected} です。',
        count_not_integer: '数量は整数でなければなりません。',
        count_out_of_range: '数量は {min} 以上 {max} 以下でなければなりません。',
        key_format_invalid: '論理キーは <タイプコード>#<回次> の書式です。受け取った値: {value}',
        duplicate_key: '論理キー {value} が二回出てきます。',
        steps_quantity_mismatch: 'steps が quantities から展開される段階リストと異なります。',
        steps_too_many: '段階は最大 {max} 個までです。',
        step_shape_mismatch: 'この段階の type・ordinal・pair_key・pair_role がキーと一致しません。',
        locked_flag_mismatch: 'ロック表示がタイプと異なります。',
        provider_not_allowed: 'テストレポート(TSR)はサーバーが自動組立するため提供者を指定できません。',
        note_not_allowed: 'テストレポート(TSR)はサーバーが自動組立するため一行メモを書けません。',
        origin_not_allowed: 'ロックされた段階の origin は system でなければなりません。',
        provider_not_candidate: '{value} はこの作業計画の提供者候補でも、このプロジェクトに登録された提供者でもありません。',
        provider_id_format_invalid: '提供者識別子の書式が正しくありません: {value}',
        duplicate_provider_candidate: '提供者 {value} が候補リストに二回あります。',
        provider_candidates_too_many: '提供者候補は最大 {max} 個までです。',
        note_too_long: '一行メモは {max} 文字までです。',
        note_has_control_char: '一行メモに改行・タブなどの制御文字は使えません。',
        provider_display_name_without_provider_id: 'provider_id が空なら provider_display_name も null でなければなりません。',
        review_count_invalid: 'レビュー回数は現在の許可リストにありません。',
        reviewer_not_allowed: 'レビューなし、またはサーバー組立段階にはレビュー担当を指定できません。',
        reviewer_provider_unavailable: 'レビュー担当 provider は現在このプロジェクトで有効ではありません。',
        reviewer_display_name_without_provider_id: 'reviewer_provider_id が空なら表示名も null でなければなりません。',
        pre_instruction_too_long: '事前指示本文は {max} 文字までです。',
        pre_instruction_control_char: '事前指示本文に許可されていない制御文字があります。',
        pre_instruction_not_allowed: '結果またはサーバー組立段階には事前指示を設定できません。',
        pre_instruction_attachment_digest_invalid: '添付 SHA-256 は小文字64桁の16進数でなければなりません。',
        pre_instruction_attachment_ai_forbidden: 'AI作成経路では添付参照を作成できません。',
        pre_instruction_attachment_doc_mismatch: '添付参照が現在の作業計画文書を指していません。',
        pre_instruction_attachment_reserved_name_required: '添付はサーバー生成の予約名でなければなりません。',
        pre_instruction_attachment_registry_missing: '添付の registry 行がありません。',
        pre_instruction_attachment_file_missing: '添付ファイルがありません。',
        pre_instruction_attachment_original_name_mismatch: '添付の元ファイル名が registry と異なります。',
        pre_instruction_attachment_digest_mismatch: '添付の内容ダイジェストが一致しません。',
        pre_instruction_attachment_outside_storage: '添付パスが保存領域の境界外です。',
        pair_not_adjacent: 'セットの指示段階の直後にはその結果段階が来なければならず、結果段階を単独で置くことはできません。',
        ordinal_order_invalid: '同じタイプ内では回次が出現順に 1 から続かなければなりません。',
        card_id_invalid: "card_id は {max} 文字以内の英数字・_#.:- の文字列で、'retired:' で始めることはできません。",
        card_id_duplicate: '異なるカードが同じ card_id({value})を持つことはできません。',
        card_id_pair_mismatch: 'セットの指示段階と結果段階は同じ card_id を持たなければなりません。',
        card_id_missing: '既存カードの card_id がありません。カードを移動・再採番しても card_id はそのまま送ってください。',
        card_id_unknown: '直前の本文にない card_id({value})です。新しいカードは card_id なしで送るとサーバーが付与します。',
        card_id_type_changed: 'card_id({value})のカードタイプは変更できません。',
        instruction_mode_started: '実行を開始したカード(card_id {value})は指示書省略の設定を変更できません。',
      },
      // `empty_selection` の `what_key` param を現在の UI locale に写す表。
      // キーは work_plan_service._EMPTY_SELECTION_WHAT と対にする。
      empty_selection_what: {
        counted_types: '数量を確認するタイプ',
        provider_candidates: '投入する提供者',
      },
    },
    tr2_body: {
      actions: {
        download: '反映案をダウンロード',
        cancel: 'キャンセル',
      },
      readiness: {
        code: {
          TR_HISTORY_RECOVERY_REQUIRED: 'Time Machine のソース/Git/台帳の復旧が完了するまで、削除や通常の変更は実行できません。',
          TR2_ACTIVE_SOURCE_EFFECT: '有効な TR2 のソース反映が残っているため、この文書は削除できません。先に Time Machine で反映を取り消してください。',
        },
      },
      gate: {
        passed: '成功',
      },
      errors: {
        load_failed: '反映案の読み込みに失敗: {detail}',
      },
    },
    // structured test specification (TS) and test report (TSR).
    test_document: {
      // one run entry point (action bar / Case row), its state, and the report body.
      field: {
        expected: '期待結果',
        actual: '実際の結果',
        evidence: 'Evidence',
      },
      verdict: {
        PASS: 'PASS',
        FAIL: 'FAIL',
        BLOCKED: 'BLOCKED',
        NOT_RUN: 'NOT_RUN',
      },
    },
    time_machine: {
      confirm: '差し戻す',
    },
    review_action_bar: {

      // 対象ワークスペースを占有している試行を実際の所有者種別で示す。
      // 短い案内 + 対象/動作を示すボタン。上の長い文言は[詳細]に残す。
      // 承認の応答が失われたあと、サーバーの Git 結果を確認している間の案内。
      // Git 後処理が待機/復旧状態に残った最終承認(ジョブが引き継いで処理する)。
      // TR 承認の通知。二つとも承認は成功したという文で始まる。
      ai_running_hint: 'このグループでAI実行が進行中です。終了するまで操作はロックされます。',
      ai_lease_orphaned_hint: '前回の実行が異常終了し、ロックが残っています。[ロック解除]を押してください。',
    },
    main_panel: {
      reject_mention_template: '## 差し戻し\n{docName} が差し戻されました。\n理由: {reason}',
    },
    next_action_modal: {
      mm_section_header: 'ユーザーメッセージ',
    },
    // QaHistoryDialog は質疑専用の全体表示に戻した(the earlier implementation 差し戻し
    // §3 「質疑は外せ」)。
    // ReviewHistoryDialog(差し戻し分)を統合した検収・差し戻し専用の全体表示ダイアログ。
    // 差し戻し: ①タイトルが「質疑・差し戻し」だったのは誤り(「検収・差し戻し」が正しい)
    // ②AI検収も差し戻しと同じく折りたためる必要がある ③質疑はこのダイアログから外した(自分の
    // ダイアログ qa_history に移した)。
    // [作業計画を適用]。文言は T 段階で決めると両設計書が揃って
    // 先送りした項目なので、画面に出る言葉はすべてここにまとめる。
    // 最終承認サイドバーの[変更内容を開く]が開く変更閲覧画面。
    git_errors: {
      network: 'サーバーに接続できません。接続を確認してから再試行してください。',
      generic: 'Git 操作に失敗しました。もう一度お試しください。',
      base_dirty: 'ベースのチェックアウトにコミットされていない変更があります。',
      base_untracked_conflict: 'ベースの未追跡ファイルがこの操作と競合しています。',
      dirty_worktree: '作業ツリーにコミットされていない変更があります（{n} ファイル）。',
      git_busy: '別の Git 操作が進行中です。',
      git_unavailable: 'サーバーで Git を利用できません。',
      review_not_found: 'マージレビューが見つかりません。',
      review_not_ready: 'マージレビューはこの操作を実行できる状態ではありません。',
      restoration_verification_failed: '復元したファイルを検証できませんでした。',
      forbidden: 'この Git 操作を実行する権限がありません。',
      invalid_state: '現在の状態ではこの Git 操作を実行できません。',
      invalid_request: 'Git リクエストが正しくありません。',
      workflow_revert_pending: 'Time Machine の巻き戻しで承認コミットの取り消しがまだ終わっていません。反映案(TR2)画面の[コミット取消を再試行]でコミット取り消しを先に完了してください。それまではこのグループを再び巻き戻したり次の段階へ進めたりできません。',
      // マージ開始後にブランチが動き、レビュー結果を反映できない場合。
      stale_source: 'マージ開始後にソースブランチが変わったため、現在のレビュー結果をそのまま反映できません。このマージを中止して、もう一度開始してください。',
      stale_target: 'マージ開始後に対象ブランチが変わったため、現在のレビュー結果をそのまま反映できません。このマージを中止して、もう一度開始してください。',
    },
    api_errors: {
      validation_failed: {
        kind_unsupported_kind: 'このCLI種別はサポートされていません。',
        model_name_invalid_model_name: 'モデル名が正しくありません。',
        generic: '入力内容を確認してください。',
      },
      ai_repeat_count_out_of_range: '繰り返し回数は{min}〜{max}の範囲で指定してください。',
    },
    git_finalize: {
      // proposal 1 — temporary artifacts excluded from the finalize commit.
      // 最終承認ダイアログのマージ先選択:
      // 「今回の承認が実際にどこへ入るか」だけを示す(ブランチ全体の管理は
      // git_branch_manager 側)。
      // グループの work base と異なる対象
      // 作業基準と異なる対象を選んだ場合だけ表示する確認チェック。
      // 競合解消 AI 実行が競合を解消できずに終わったとき。
      // 送信はサーバーに届いたが、この画面が結果の状態を認識できない場合。
      failed: 'Git反映処理に失敗しました',
    },
  },
  projects: {
    title: 'プロジェクト一覧',
    sub: 'プロジェクトを切り替えるか、新しいプロジェクトを作成してください。',
    new: '新しいプロジェクト',
    new_card: '新しいプロジェクトを作成',
    search_placeholder: 'プロジェクトを検索...',
    filter_all: 'すべて',
    filter_active: 'アクティブ',
    filter_archive: 'アーカイブ',
    current: '現在',
    archived: 'アーカイブ',
    open: '開く',
    open_readonly: '開く（読み取り専用）',
    archive: 'アーカイブ',
    restore: '復元',
    time_minutes: '{n}分前',
    time_hours: '{n}時間前',
    time_days: '{n}日前',
    modal_title: '新しいプロジェクト',
    form_name: 'プロジェクト名',
    form_name_placeholder: '例：MyProject',
    form_name_hint: '採番に使用します。英字・数字・ハイフン推奨',
    form_desc: '説明',
    form_desc_placeholder: 'プロジェクトの目的や簡単な説明...',
    optional: '任意',
    form_color: '識別カラー',
    form_structure: 'グループ構造',
    structure_3: '構造3 — Project/__ALL__/Group/Subgroup（推奨）',
    structure_4: '構造4 — Project/__ALL__/Group',
    structure_1: '構造1 — Project/Module/Group/Subgroup',
    structure_2: '構造2 — Project/Module/Group',
    form_storage: 'ストレージパス',
    storage_default: 'システムデフォルトを使用',
    storage_custom: 'カスタムパス',
    form_member: '初期メンバー',
    me: '自分',
    custom_path: 'カスタムパス',
    create_btn: 'プロジェクト作成',
    toast_name_required: 'プロジェクト名を入力してください。',
    toast_created: 'プロジェクト"{name}"が作成されました。',
    toast_archived: 'プロジェクトをアーカイブしました。',
    toast_restored: 'プロジェクトを復元しました。',
    toast_error: 'プロジェクトの作成中にエラーが発生しました。',
    toast_state_error: 'プロジェクトの状態を変更できませんでした。',
    label_display_name: '表示名',
    label_id: 'プロジェクトID',
    button_suggest_id: 'IDを提案',
    error_id_format: '英小文字、数字、ハイフン、アンダースコアのみ使用できます',
    toast_romanize_empty: 'ローマ字変換結果がありません。直接入力してください。',
  },
  modules: {
    label_display_name: '表示名',
    label_id: 'モジュールID',
    button_suggest_id: 'IDを提案',
    error_id_format: '英小文字、数字、ハイフン、アンダースコアのみ使用できます',
    toast_romanize_empty: 'ローマ字変換結果がありません。直接入力してください。',
  },
} as const

