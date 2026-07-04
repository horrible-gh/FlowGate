from enum import Enum

class EventType(str, Enum):
    FILE_EXPLORER_REFRESH                 = "file_explorer_refresh"
    DOCUMENT_EXPLORER_REFRESH             = "document_explorer_refresh"
    GROUP_VIEW_REFRESH                    = "group_view_refresh"
    NOTIFICATION_NEW_ACTION_CANDIDATE     = "notification_new_action_candidate"
    EDIT_MARKER_ADDED                     = "edit_marker_added"
    QNA_Q_REGISTERED                      = "qna_q_registered"   # D022 §3-3 Phase 3 added
    DOC_REVIEW_STATUS_CHANGED             = "doc_review_status_changed"  # M026 §8-1 Phase 5 added
    AI_REVIEW_ARRIVED                     = "ai_review_arrived"  # inbox action:review push — notify reviewers a verdict landed
    TEST_RUN_STARTED                      = "test_run_started"
    TEST_STAGE_FINISHED                   = "test_stage_finished"
    TEST_CASE_FINISHED                    = "test_case_finished"
    TEST_RUN_FINISHED                     = "test_run_finished"
