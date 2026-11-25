import { PRODUCT_NAME } from '../productName'

export default {
  common: {
    reset: '되돌리기',
    toast: {
      ai_provider_in_use: "진행 중인 작업에서 사용 중인 AI는 삭제할 수 없습니다.",
    },
  },
  auth: {
    login: {
      title: `${PRODUCT_NAME} 로그인`,
      password: '비밀번호',
      show_password: '비밀번호 표시',
      hide_password: '비밀번호 숨기기',
      username_or_email: '사용자명 또는 이메일',
      username_or_email_placeholder: '사용자명 또는 이메일 입력',
      password_placeholder: '비밀번호 입력',
      os_login: 'OS 로그인',
      contact_admin: '관리자에게 문의하세요.',
      required: '사용자명과 비밀번호를 입력해 주세요.',
      submit: '로그인',
      forgot_password: '비밀번호를 잊으셨나요?',
      redirecting: '이동 중...',
      locked: {
        title: '계정이 잠금되었습니다',
        message: '로그인 시도가 5회를 초과하여 계정이 일시적으로 잠금되었습니다.',
        countdown: '잠금 해제까지: {time}',
      },
      error: {
        invalid_credentials: '사용자명 또는 비밀번호가 올바르지 않습니다.',
        attempt_count: '로그인 시도 {count}/5회',
        attempt_count_warning: '1회 더 실패하면 15분간 잠깁니다.',
      },
    },
    totp: {
      title: '2단계 인증 (OTP)',
      instruction: '인증 앱의 6자리 코드를 입력하세요.',
      code_label: '인증 코드',
      submit: '확인',
      use_backup: '백업 코드 사용',
      back: '뒤로',
      lost_hint: '인증 앱을 분실한 경우 관리자에게 TOTP 초기화를 요청하세요.',
      error: {
        invalid_code: '인증 코드가 올바르지 않습니다.',
        code_expired: '세션이 만료되었습니다. 다시 로그인해 주세요.',
      },
      backup: {
        title: '백업 코드 입력',
        instruction: '등록 시 발급된 백업 코드 1개를 입력하세요. 코드는 1회용입니다.',
        code_label: '백업 코드',
        submit: '확인',
        back_to_otp: 'OTP 입력으로 돌아가기',
        error: {
          invalid: '백업 코드가 올바르지 않습니다.',
          used: '이미 사용된 백업 코드입니다.',
        },
      },
    },
    totp_setup: {
      title: '2단계 인증 등록',
      instruction: '관리자 정책에 따라 2단계 인증을 등록해야 로그인할 수 있습니다.',
      start: '등록 시작',
      secret_label: '인증 앱에 입력할 키',
      uri_label: '인증 앱으로 열기 (otpauth)',
      backup_title: '백업 코드 — 지금 한 번만 표시됩니다',
      code_label: '인증 앱의 6자리 코드',
      submit: '등록 완료',
      pending_hint: '코드를 확인해야 등록이 끝납니다. 그 전에 이 화면을 벗어나면 아무것도 저장되지 않으며, 다시 로그인하면 새 키로 등록을 시작합니다.',
      failed: '2단계 인증을 등록하지 못했습니다.',
    },
    invite: {
      title: '초대 수락',
      instruction: '{username} ({email}) 계정의 비밀번호를 설정하세요.',
      submit: '비밀번호 설정',
      back: '로그인으로',
      invalid: '초대 링크가 유효하지 않거나 만료되었습니다. 관리자에게 다시 요청하세요.',
      accepted: '비밀번호가 설정되었습니다. 로그인하세요.',
    },
    password: {
      change: {
        title: '비밀번호 변경 필수',
        initial_message: '첫 로그인입니다. 안전한 새 비밀번호를 설정해 주세요.',
        expired_message: '비밀번호가 만료되었습니다. 새 비밀번호를 설정해 주세요.',
      },
      current_label: '현재 비밀번호',
      new_label: '새 비밀번호',
      confirm_label: '비밀번호 확인',
      policy_hint: '12자 이상 + 대문자, 소문자, 숫자/특수문자 중 2종 이상',
      submit: '변경 완료',
      error: {
        policy_violation: '비밀번호가 정책을 만족하지 않습니다. {policy}',
        mismatch: '새 비밀번호가 일치하지 않습니다.',
        current_incorrect: '현재 비밀번호가 올바르지 않습니다.',
      },
    },
  },
  error: {
    network: '네트워크 연결에 실패했습니다.',
    unauthorized: '인증되지 않은 요청입니다.',
    forbidden: '접근 권한이 없습니다.',
    not_found: '찾을 수 없습니다.',
    server: '서버 오류가 발생했습니다.',
    error_group_r_exists: '그룹 내 생성된 문서입니다.',
  },
  settings: {
    system: {
      ai: {
        title: 'AI 설정',
      },
    },
    ai: {
      providers_title: '프로바이더 목록',
      providers_hint: '위에 있을수록 우선합니다. 목록 순서가 장애·사용량 초과 시의 대체(fallback) 순서가 됩니다. 순서는 손잡이를 끌어 옮기거나 위·아래 버튼으로 바꿉니다.',
      empty: '등록된 프로바이더가 없습니다.',
      add_provider: '프로바이더 추가',
      edit_provider: '프로바이더 편집',
      default_badge: '기본 선택',
      default_select_aria: '기본 선택 지정',
      enabled: '활성',
      disabled_label: '비활성',
      move_up: '위로',
      move_down: '아래로',
      label_name: '이름',
      label_exec_type: '실행 방식',
      label_kind: '종류',
      label_enabled: '활성',
      label_cli_command: 'CLI 실행 커맨드',
      magic_tool: '선택한 CLI에 맞는 커맨드 채우기',
      magic_tool_error: '커맨드를 채우지 못했습니다.',
      magic_model_hint: '저장하기 전에 커맨드의 model_name을 실제 모델명으로 바꾸세요.',
      skip_permissions_badge: '권한 확인 생략됨',
      exec_mode_cli: 'CLI 실행',
      exec_mode_api: 'API 실행',
      view_command: '커맨드 보기',
      command_title: '{name} · 커맨드',
      skip_permissions_cmd_warn: '이 CLI는 확인 없이 파일을 읽고 쓰고 명령을 실행합니다.',
      delete_provider_title: '프로바이더 삭제',
      delete_provider_body: '“{name}” 프로바이더를 삭제합니다.',
      delete_provider_hint: '삭제하면 대체(fallback) 순서에서 빠집니다.',
      label_api_base_url: 'API 접속 URL',
      label_api_model: 'API 모델',
      label_api_key: 'API 키',
      placeholder_name: '예: 클로드 CLI',
      placeholder_cli_command: '예: claude -p',
      placeholder_api_base_url: '비워두면 종류별 기본 URL을 사용합니다',
      placeholder_api_model: '예: claude-sonnet-5',
      placeholder_api_key: '키를 입력하세요',
      key_set_hint: '키 등록됨 (…{hint})',
      key_keep_hint: '비워두면 기존 키를 유지합니다.',
      key_clear: '기존 키 삭제',
      key_cleared: '저장 시 키가 삭제됩니다.',
      key_none: '키 미등록',
      key_unreadable: '키를 읽을 수 없음 - 암호화 키가 바뀌었습니다. 키를 다시 입력하세요.',
      err_name_required: '이름을 입력하세요.',
      err_kind_required: '종류를 선택하세요.',
      err_cli_command_required: 'CLI 실행 커맨드를 입력하세요.',
      err_api_model_required: 'API 모델을 입력하세요.',
      err_too_long: '{field}이(가) 너무 깁니다. 최대 {max}자인데 현재 {len}자입니다.',
      err_duplicate_name: '이미 같은 이름의 프로바이더가 있습니다. 대소문자만 다른 이름도 같은 이름으로 봅니다.',
      err_too_many: '프로바이더는 최대 {max}개까지 등록할 수 있습니다.',
      saveerr_title: '저장하지 못했습니다. 아래 항목을 고친 뒤 다시 저장하세요.',
      saveerr_row: '{row}번째 “{name}” · {field}: {reason}',
      saveerr_row_noname: '{row}번째 항목 · {field}: {reason}',
      saveerr_field: '{field}: {reason}',
      field: {
        name: '이름',
        exec_type: '실행 방식',
        kind: '종류',
        cli_command: 'CLI 실행 커맨드',
        api_base_url: 'API 접속 URL',
        api_model: 'API 모델',
        api_key: 'API 키',
        providers: '프로바이더 목록',
        mode: '설정 방식',
        default_provider_id: '기본 프로바이더',
        default_provider_index: '기본 프로바이더',
        target_agent_id: '대상 Agent',
      },
      reason: {
        required: '값을 입력하세요.',
        required_for_cli: 'CLI 방식에서는 반드시 입력해야 합니다.',
        required_for_api: 'API 방식에서는 반드시 입력해야 합니다.',
        too_long: '너무 깁니다. 최대 {max}자까지 입력할 수 있습니다.',
        duplicate_name: '이름이 다른 프로바이더와 중복됩니다. 대소문자만 다른 경우도 중복입니다.',
        too_many: '개수가 너무 많습니다. 최대 {max}개까지 등록할 수 있습니다.',
        unknown_exec_type: '지원하지 않는 실행 방식입니다.',
        unknown_kind: '선택한 실행 방식에서 지원하지 않는 종류입니다.',
        unknown_mode: '지원하지 않는 설정 방식입니다.',
        bad_default: '기본으로 지정한 프로바이더가 목록에 없습니다.',
        required_for_remote_cli: '원격 CLI에서는 반드시 고르세요.',
        invalid_target_agent: '선택한 Agent를 찾을 수 없습니다.',
        target_agent_unavailable: '선택한 Agent를 쓸 수 없습니다.',
      },
      toast_invalid: '입력값이 올바르지 않습니다. 각 항목을 확인하세요.',
      unsaved_badge: '저장 안 됨',
      unsaved_confirm: '저장하지 않은 변경이 있습니다. 저장하지 않고 이동하시겠습니까?',
      exec_type: {
        cli: 'CLI',
        api: 'API',
      },
      kind: {
        claude: '클로드',
        copilot: '코파일럿',
        codex: '코덱스',
        openai: 'OpenAI',
        custom: '커스텀',
      },
    },
    users: {
      title: '사용자 관리',
      user_create_modal: {
        username_already_exists: '이미 사용 중인 사용자 이름입니다.',
      },
    },
    project: {
      ai: {
        title: 'AI 설정',
      },
      git: {
        title: 'Git 리포지터리 연동',
        repo_url: '리포지터리 주소',
        provider: '제공자 종류 (선택)',
        secret: '비밀번호 / 토큰',
        base_branch: '본 브랜치',
        author_name: '커밋 Author 이름 (선택)',
        author_email: '커밋 Author 이메일 (선택)',
      },
    },
  },
  main: {
    requirement: {
      create: {
        title: '요건정의 작성',
        submit: '제출',
      },
    },
    design_handoff_dialog: {
      mention_batch: '워커님, 다음 단계는 설계 작업입니다. 다음 타입을 일괄 생성해 주세요: {types}\n대상: {docRef}',
    },
    // flowgate.default.0405 P0004 — 다음 액션이 작업계획일 때 여는 전용 제안 다이얼로그.
    work_plan_create_dialog: {
      create_failed: '작업계획 생성 중 오류가 발생했습니다.',
    },
    work_plan: {
      // 0403 NR0004 F5·F7 — 잠금 사유는 서버 판정을 그대로 옮긴다.
      step_no: '단계 {n}',
      // D0007 §5.5 — 값 있는 단계가 수량 감소로 사라지기 전 확인.
      // 0649 T#3 (NR0003 §5.3 / O7) — 카드 단위 이동과 시작된 카드 잠금.
      // T0007 §1.2 — 서버가 보내는 code+params를 현재 UI locale로 매 렌더마다 다시 그린다.
      // 문구는 work_plan_service._ERROR_COPY와 짝을 맞춘다(자리표시자 문법이 같다).
      errors: {
        json_parse_failed: '작업계획 본문이 JSON 객체가 아닙니다.',
        wp_version_invalid: 'wp_version 은 정수여야 합니다.',
        wp_version_unsupported: 'wp_version={value} 는 이 서버가 읽을 수 없습니다.',
        missing_field: '필수 항목 {field} 가 없습니다.',
        type_invalid: '{field} 의 형이 올바르지 않습니다.',
        unknown_field: '모르는 항목 {field} 입니다. 실험용 항목은 x_ 로 시작해야 보존됩니다.',
        enum_not_allowed: '{field} 에 쓸 수 없는 값입니다.',
        binding_not_allowed: 'binding 은 항상 advisory 여야 합니다. 작업계획은 실행을 강제하지 않습니다.',
        empty_selection: '{what} 을 하나 이상 체크해 주세요.',
        unknown_type_code: '{code} 는 수량을 셀 수 있는 타입이 아닙니다.',
        duplicate_type: '타입 {code} 가 두 번 나옵니다.',
        quantities_key_mismatch: 'quantities 의 키가 counted_types 와 다릅니다.',
        unit_mismatch: '{code} 의 단위는 {expected} 입니다.',
        count_not_integer: '수량은 정수여야 합니다.',
        count_out_of_range: '수량은 {min} 이상 {max} 이하여야 합니다.',
        key_format_invalid: '논리 키는 <타입코드>#<회차> 서식이어야 합니다. 받은 값: {value}',
        duplicate_key: '논리 키 {value} 가 두 번 나옵니다.',
        steps_quantity_mismatch: 'steps 가 quantities 에서 펼쳐지는 단계 목록과 다릅니다.',
        steps_too_many: '단계는 최대 {max} 개까지입니다.',
        step_shape_mismatch: '이 단계의 type·ordinal·pair_key·pair_role 이 논리 키와 맞지 않습니다.',
        locked_flag_mismatch: '잠금 표시가 타입과 다릅니다.',
        provider_not_allowed: '테스트레포트(TSR)는 서버가 자동 조립하므로 공급자를 지정할 수 없습니다.',
        note_not_allowed: '테스트레포트(TSR)는 서버가 자동 조립하므로 한줄 멘트를 적을 수 없습니다.',
        origin_not_allowed: '잠긴 단계의 origin 은 system 이어야 합니다.',
        provider_not_candidate: '{value} 는 이 작업계획의 공급자 후보도 아니고 이 프로젝트에 등록된 공급자도 아닙니다.',
        provider_id_format_invalid: '공급자 식별자 서식이 올바르지 않습니다: {value}',
        duplicate_provider_candidate: '공급자 {value} 가 후보 목록에 두 번 있습니다.',
        provider_candidates_too_many: '공급자 후보는 최대 {max} 개까지입니다.',
        note_too_long: '한줄 멘트는 {max} 자까지입니다.',
        note_has_control_char: '한줄 멘트에 개행·탭 같은 제어문자를 쓸 수 없습니다.',
        provider_display_name_without_provider_id: 'provider_id 가 비어 있으면 provider_display_name 도 null 이어야 합니다.',
        review_count_invalid: '검수 횟수가 현재 허용 목록에 없습니다.',
        reviewer_not_allowed: '검수 안 함 또는 서버 조립 단계에는 검수 담당을 지정할 수 없습니다.',
        reviewer_provider_unavailable: '검수 담당 provider가 현재 프로젝트에서 활성 상태가 아닙니다.',
        reviewer_display_name_without_provider_id: 'reviewer_provider_id가 비어 있으면 표시명도 null이어야 합니다.',
        pre_instruction_too_long: '사전지시 본문은 {max}자까지입니다.',
        pre_instruction_control_char: '사전지시 본문에 허용되지 않는 제어문자가 있습니다.',
        pre_instruction_not_allowed: '결과 또는 서버 조립 단계에는 사전지시를 넣을 수 없습니다.',
        pre_instruction_attachment_digest_invalid: '첨부 SHA-256은 소문자 64자리 16진수여야 합니다.',
        pre_instruction_attachment_ai_forbidden: 'AI 작성 경로에서는 첨부 참조를 만들 수 없습니다.',
        pre_instruction_attachment_doc_mismatch: '첨부 참조가 현재 작업계획 문서를 가리키지 않습니다.',
        pre_instruction_attachment_reserved_name_required: '첨부는 서버가 만든 예약 이름이어야 합니다.',
        pre_instruction_attachment_registry_missing: '첨부 registry 행이 없습니다.',
        pre_instruction_attachment_file_missing: '첨부 파일이 없습니다.',
        pre_instruction_attachment_original_name_mismatch: '첨부 원래 파일명이 registry와 다릅니다.',
        pre_instruction_attachment_digest_mismatch: '첨부 내용 지문이 다릅니다.',
        pre_instruction_attachment_outside_storage: '첨부 경로가 저장소 경계를 벗어납니다.',
        pair_not_adjacent: '세트의 지시 단계 바로 다음에는 그 결과 단계가 와야 하며, 결과 단계는 단독으로 둘 수 없습니다.',
        ordinal_order_invalid: '같은 타입 안에서는 회차가 등장 순서대로 1부터 이어져야 합니다.',
        card_id_invalid: "card_id 는 {max}자 이내의 영문·숫자·_#.:- 문자열이어야 하며 'retired:' 로 시작할 수 없습니다.",
        card_id_duplicate: '서로 다른 카드가 같은 card_id({value})를 가질 수 없습니다.',
        card_id_pair_mismatch: '세트의 지시 단계와 결과 단계는 같은 card_id 를 가져야 합니다.',
        card_id_missing: '기존 카드의 card_id 가 빠졌습니다. 카드를 옮기거나 다시 번호를 매겨도 card_id 는 그대로 보내야 합니다.',
        card_id_unknown: '직전 본문에 없는 card_id({value})입니다. 새 카드는 card_id 없이 보내면 서버가 부여합니다.',
        card_id_type_changed: 'card_id({value})의 카드 타입을 바꿀 수 없습니다.',
        instruction_mode_started: '이미 실행을 시작한 카드(card_id {value})는 지시서 생략 설정을 바꿀 수 없습니다.',
      },
      // T0007 rev1 §1 — `empty_selection`의 `what_key` param을 현재 UI locale로 옮기는 표.
      // work_plan_service._EMPTY_SELECTION_WHAT과 키를 맞춘다.
      empty_selection_what: {
        counted_types: '수량을 확인할 타입',
        provider_candidates: '투입할 공급자',
      },
    },
    tr2_body: {
      actions: {
        download: '반영안 다운로드',
        cancel: '취소',
      },
      readiness: {
        code: {
          TR_HISTORY_RECOVERY_REQUIRED: 'Time Machine 소스/Git/원장 복구가 끝나지 않아 삭제 또는 일반 변경을 진행할 수 없습니다.',
          TR2_ACTIVE_SOURCE_EFFECT: '현재 소스에 활성 TR2 반영 효과가 남아 있어 문서를 삭제할 수 없습니다. 먼저 Time Machine으로 해당 반영을 취소하세요.',
        },
      },
      gate: {
        passed: '통과',
      },
      errors: {
        load_failed: '반영안 불러오기 실패: {detail}',
      },
    },
    // flowgate.default.0549 T0008: structured test specification (TS) and test report (TSR).
    test_document: {
      // 0684 T#3: one run entry point (action bar / Case row), its state, and the report body.
      field: {
        expected: '기대 결과',
        actual: '실제 결과',
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
      confirm: '되돌리기',
    },
    review_action_bar: {

      ai_running_hint: '이 그룹에서 AI 실행이 진행 중입니다. 끝날 때까지 동작이 잠깁니다.',
      ai_lease_orphaned_hint: '이전 실행이 비정상 종료되어 잠금이 남아 있습니다. [잠금 해제]를 눌러 주세요.',
    },
    main_panel: {
      reject_mention_template: '## 반려\n{docName} 가 반려되었습니다.\n사유: {reason}',
    },
    next_action_modal: {
      mm_section_header: '사용자 메세지',
    },
    // 0311 T0004: QaHistoryDialog 는 질의 전체보기 전용으로 되돌아왔다(TR0005 rev6 반려
    // §3 — "질의는 빼라").
    // ReviewHistoryDialog(반려분)이 병합된 검수·반려 전용 전체보기 다이얼로그.
    // TR0005 rev6 반려: ①제목이 "질의·반려"였던 것이 잘못이었다("검수·반려"가 맞다)
    // ②AI 검수도 반려처럼 접을 수 있어야 한다 ③질의는 이 다이얼로그에서 뺐다(자기
    // 다이얼로그인 qa_history 로 옮겼다).
    // 0399 D0010/L0011 — [작업계획 적용]. 문구를 T 단계에서 정하기로 두 설계서가 함께
    // 미뤄 둔 항목이라, 화면에 나가는 말은 전부 여기 한자리에 모아 둔다.
    // 0325 TR0007 rev1 — 최종 승인 사이드바의 [변경사항 열기] 가 여는 변경사항 열람 화면.
    git_errors: {
      network: '서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도하세요.',
      generic: 'Git 작업에 실패했습니다. 다시 시도하세요.',
      base_dirty: '기준 체크아웃에 커밋되지 않은 변경이 있습니다.',
      base_untracked_conflict: '기준 체크아웃의 추적되지 않은 파일이 이 작업과 충돌합니다.',
      dirty_worktree: '작업 트리에 커밋되지 않은 변경이 있습니다({n}개 파일).',
      git_busy: '다른 Git 작업이 진행 중입니다.',
      git_unavailable: '서버에서 Git을 사용할 수 없습니다.',
      review_not_found: '병합 검토를 찾을 수 없습니다.',
      review_not_ready: '병합 검토가 이 작업을 수행할 준비가 되지 않았습니다.',
      restoration_verification_failed: '복원된 파일을 확인하지 못했습니다.',
      forbidden: '이 Git 작업을 수행할 권한이 없습니다.',
      invalid_state: '현재 상태에서는 이 Git 작업을 수행할 수 없습니다.',
      invalid_request: 'Git 요청이 올바르지 않습니다.',
      workflow_revert_pending: 'Time Machine 되돌리기의 승인 커밋 취소가 아직 끝나지 않았습니다. 반영안(TR2) 화면의 [커밋 취소 다시 시도]로 커밋 취소를 먼저 마치세요. 그 전에는 이 그룹을 다시 되돌리거나 다음 단계로 진행할 수 없습니다.',
      // 0685 T0006 §5 — 병합 시작 뒤 브랜치가 움직여 검토 결과를 반영할 수 없는 경우.
      stale_source: '병합을 시작한 뒤 원본 브랜치가 바뀌어 지금의 검토 결과를 그대로 반영할 수 없습니다. 이 병합을 중단하고 다시 시작하세요.',
      stale_target: '병합을 시작한 뒤 대상 브랜치가 바뀌어 지금의 검토 결과를 그대로 반영할 수 없습니다. 이 병합을 중단하고 다시 시작하세요.',
    },
    // flowgate.default.0578 T0010 §2.2/작업6 — extractApiErrorMessage와 비-Git 오류
    // sink가 공유하는 등록 code 문구. GROUP_AI_RUN_LOCKED는 새 키를 만들지 않고
    // review_action_bar의 기존 키를 그대로 참조한다.
    api_errors: {
      validation_failed: {
        kind_unsupported_kind: '지원하지 않는 CLI 종류입니다.',
        model_name_invalid_model_name: '모델 이름이 올바르지 않습니다.',
        generic: '입력값을 확인해 주세요.',
      },
      ai_repeat_count_out_of_range: '반복 횟수는 {min}에서 {max} 사이여야 합니다.',
    },
    git_finalize: {
      // 0382 NR0003 제안 1 — 마무리 커밋에서 뺀 임시 산출물.
      // flowgate.default.0594 T0016 §3.3/§5 — 최종승인 대화상자의 merge target
      // 선택기: "이번 승인이 실제 어디로 들어가는지"만 보여준다(전체 브랜치
      // 관리는 git_branch_manager 쪽 몫).
      // flowgate.default.0665 T0004 — 그룹 work base와 다른 대상 경고
      // 0685 T0006 §4 — 작업 기준과 다른 대상을 고른 경우에만 표시되는 확인 체크.
      // 0481 T0010 rev3 — 이 다이얼로그는 이제 자기 실행이 도는 동안에도 닫히지 않는다.
      // 진행 상황을 보러 기본 AI 실행 화면으로 나갈 이유를 없애는 줄.
      // 0481 T0010 #2 — 빈 목록도 막다른 길이 아니다: 다시 읽거나, AI를 부르거나, 병합을 중단할 수 있다.
      // 0481 T0010 rev5 (반려 #1·#4) — [AI 호출]이 지금 어디까지 갔는지, 혹은 왜 아직
      // 누를 수 없는지를 다이얼로그 안에서 말하는 줄들. rev4 까지 이 자리는 버튼의 title
      // 속성 하나뿐이어서, 못 누르는 버튼과 눌러도 아무 일이 없는 버튼이 구분되지 않았다.
      // 0674 T0004 §2-1 — 충돌 해소 AI 실행이 충돌을 풀지 못하고 끝났을 때(실패·부분·유실).
      // 0481 T0010 rev2 — 제출은 서버에 닿았는데 화면이 그 결과 상태를 모르는 경우.
      // 화면이 서버보다 낡으면(배포 빌드가 실패해 이전 번들이 계속 서빙되면) 반드시
      // 이 자리로 온다. 그때 아무 말도 하지 않으면 사용자에게는 "눌러도 반응이 없다"가 된다.
      failed: 'Git 반영 처리에 실패했습니다',
    },
  },
  projects: {
    title: '프로젝트 목록',
    sub: '프로젝트를 전환하거나 새 프로젝트를 생성하세요.',
    new: '새 프로젝트',
    new_card: '새 프로젝트 만들기',
    search_placeholder: '프로젝트 검색...',
    filter_all: '전체',
    filter_active: '활성',
    filter_archive: '아카이브',
    current: '현재',
    archived: '아카이브',
    open: '열기',
    open_readonly: '열기 (읽기 전용)',
    archive: '아카이브',
    restore: '복원',
    time_minutes: '{n}분 전',
    time_hours: '{n}시간 전',
    time_days: '{n}일 전',
    modal_title: '새 프로젝트',
    form_name: '프로젝트 이름',
    form_name_placeholder: '예: MyProject',
    form_name_hint: '채번에 사용됩니다. 영문·숫자·하이픈 권장',
    form_desc: '설명',
    form_desc_placeholder: '프로젝트 목적이나 간략한 설명...',
    optional: '선택',
    form_color: '식별 색상',
    form_structure: '그룹 구조',
    structure_3: '구조 3 — 프로젝트/__ALL__/그룹/서브그룹 (권장)',
    structure_4: '구조 4 — 프로젝트/__ALL__/그룹',
    structure_1: '구조 1 — 프로젝트/모듈/그룹/서브그룹',
    structure_2: '구조 2 — 프로젝트/모듈/그룹',
    form_storage: '스토리지 경로',
    storage_default: '시스템 기본값 사용',
    storage_custom: '직접 지정',
    form_member: '최초 멤버',
    me: '나',
    custom_path: '커스텀 경로',
    create_btn: '프로젝트 생성',
    toast_name_required: '프로젝트 이름을 입력해 주세요.',
    toast_created: '프로젝트 "{name}" 가 생성되었습니다.',
    toast_archived: '프로젝트가 아카이브되었습니다.',
    toast_restored: '프로젝트가 활성화되었습니다.',
    toast_error: '프로젝트 생성 중 오류가 발생했습니다.',
    toast_state_error: '프로젝트 상태를 변경하지 못했습니다.',
    label_display_name: '표시명',
    label_id: '프로젝트 ID',
    button_suggest_id: 'ID 추천',
    error_id_format: '영문 소문자, 숫자, 하이픈, 언더바만 사용',
    toast_romanize_empty: '음역 결과가 없습니다. 직접 입력하세요.',
  },
  modules: {
    label_display_name: '표시명',
    label_id: '모듈 ID',
    button_suggest_id: 'ID 추천',
    error_id_format: '영문 소문자, 숫자, 하이픈, 언더바만 사용',
    toast_romanize_empty: '음역 결과가 없습니다. 직접 입력하세요.',
  },
} as const

