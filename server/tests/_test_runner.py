import sys, os
sys.path.insert(0, '.')

# Clean slate - remove existing DB for clean test
from modules.flow_gate import db
try:
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
except PermissionError:
    # DB locked by another process, clean tables instead
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    for t in ['documents', 'events', 'allowed_projects']:
        try:
            conn.execute(f'DELETE FROM {t}')
        except:
            pass
    conn.commit()
    conn.close()
db.init_db()

# 0. Migration: verify that the documents.target_id column exists
import sqlite3
conn = sqlite3.connect(db.DB_PATH)
cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
conn.close()
print(f'0. documents columns include target_id: {"target_id" in cols}')
assert 'target_id' in cols

# 1. Linter: allowed empty -> should skip project validation
from modules.flow_gate import linter
h = {'type': 'N', 'project': 'unknown_proj', 'title': 'test', 'group_id': 'unknown_proj-__ALL__-0001', 'target_id': 'unknown_proj-__ALL__-0001-N0001'}
errors = linter.lint_header(h, set())
print(f'1. Linter empty allowed: errors={errors}')
assert errors == [], f'Expected no errors but got {errors}'

# 2. Linter: allowed non-empty -> should reject unknown project
errors2 = linter.lint_header(h, {'server'})
print(f'2. Linter with allowed: errors={errors2}')
assert len(errors2) == 1 and 'unknown_proj' in errors2[0]

# 3. Status change
db.add_allowed_project('server', '')
db.insert_document('server-__ALL__-0001-N0001', 'N', 'server', None, 'test doc')
from modules.flow_gate import service

res = service.change_status('server-__ALL__-0001-N0001', 'closed')
print(f'3a. Status open->closed: {res}')
assert res['success']

res2 = service.change_status('server-__ALL__-0001-N0001', 'rejected')
print(f'3b. Status closed->rejected (should fail): {res2}')
assert not res2['success']

doc = db.get_document_by_id('server-__ALL__-0001-N0001')
print(f'3c. Doc status after: {doc["status"]}')
assert doc['status'] == 'closed'

# 4. Events
events = db.get_events_by_doc_id('server-__ALL__-0001-N0001')
print(f'4a. Events for N001: {len(events)} events')
assert len(events) >= 1

recent = db.get_recent_events(5)
print(f'4b. Recent events: {len(recent)}')

# 5. Brief
brief = service.get_brief()
print(f'5. Brief: open_docs={len(brief["open_documents"])}, recent_events={len(brief["recent_events"])}')

# 6. NR/TR linter: verify that target_id is required
from modules.flow_gate import linter
h_nr_no_target = {'type': 'NR', 'project': 'server', 'title': 'test'}
errors_nr = linter.lint_header(h_nr_no_target, set())
print(f'6a. NR without target_id: errors={errors_nr}')
assert any('target_id' in e for e in errors_nr), f'Expected target_id error, got {errors_nr}'

h_nr_with_target = {'type': 'NR', 'project': 'server', 'title': 'test', 'target_id': 'server-__ALL__-0001-N0001', 'group_id': 'server-__ALL__-0001'}
errors_nr2 = linter.lint_header(h_nr_with_target, set())
print(f'6b. NR with target_id: errors={errors_nr2}')
assert errors_nr2 == [], f'Expected no errors but got {errors_nr2}'

# 7. NR Apply: verify that the target document transitions to closed
import os, tempfile, shutil

# The existing DB already has server-__ALL__-0001-N0001 as closed, so prepare a new document.
db.add_allowed_project('server', '')
db.insert_document('server-__ALL__-0001-N0002', 'N', 'server', None, 'target doc')
# Create an NR memo file in inbox
nr_content = "---\ntype: NR\nproject: server\ntitle: NR test\ntarget_id: server-__ALL__-0001-N0002\ngroup_id: server-__ALL__-0002\n---\nNR body\n"
nr_file = 'test_nr.md'
nr_path = os.path.join(db.INBOX_DIR, nr_file)
with open(nr_path, 'w', encoding='utf-8') as f:
    f.write(nr_content)

result_nr = service.apply_file(nr_file)
print(f'7a. NR apply result: {result_nr}')
assert result_nr['success'], f'NR apply failed: {result_nr}'

target_after = db.get_document_by_id('server-__ALL__-0001-N0002')
print(f'7b. target server-__ALL__-0001-N0002 status after NR apply: {target_after["status"]}')
assert target_after['status'] == 'closed', f'Expected closed, got {target_after["status"]}'

nr_doc = db.get_document_by_id(result_nr['doc_id'])
print(f'7c. NR doc status: {nr_doc["status"]} (should be open)')
assert nr_doc['status'] == 'open', f'Expected open, got {nr_doc["status"]}'

print(f'7d. NR doc target_id saved: {nr_doc.get("target_id")}')
assert nr_doc.get('target_id') == 'server-__ALL__-0001-N0002', f'Expected target_id server-__ALL__-0001-N0002, got {nr_doc.get("target_id")}'

linked = db.get_linked_result_documents('server-__ALL__-0001-N0002')
linked_ids = [d['doc_id'] for d in linked]
print(f'7e. linked docs for server-__ALL__-0001-N0002: {linked_ids}')
assert result_nr['doc_id'] in linked_ids, f'Expected {result_nr["doc_id"]} in linked docs {linked_ids}'

print()
print('=== ALL TESTS PASSED (including NR/TR) ===')

# 8. FastAPI import check
from config import settings
from routers.main import app
routes = [r.path for r in app.routes]
ctx = settings.CONTEXT
api_brief_route = f"{ctx}/api/v1/brief"
api_queue_route = f"{ctx}/api/v1/queue"
api_draft_route = f"{ctx}/api/v1/draft/{{doc_id:path}}"
api_detail_route = f"{ctx}/api/v1/detail/{{doc_id:path}}"
assert api_brief_route in routes
assert api_queue_route in routes
assert api_draft_route in routes
assert api_detail_route in routes

# 9. Action Queue categories
db.insert_document('server/N010', 'N', 'server', None, 'needs dispatch')
db.insert_document('server-__ALL__-0001-T0010', 'T', 'server', None, 'needs result')
db.insert_document('server-__ALL__-0001-NR0010', 'NR', 'server', None, 'needs review', target_id='server-__ALL__-0001-T0010')
db.update_document_status('server/N010', 'rejected')
db.insert_event('server/N010', 'conflict_detected', note='queue conflict sample')

import sqlite3
conn2 = sqlite3.connect(db.DB_PATH)
conn2.execute(
    "UPDATE documents SET updated_at = ? WHERE doc_id = ?",
    ('2000-01-01T00:00:00', 'server-__ALL__-0001-T0010')
)
conn2.commit()
conn2.close()

q = service.build_action_queue()
counts = {k: v['count'] for k, v in q['categories'].items()}
print(f'9. Queue counts: {counts}')
assert counts['needs_review'] >= 1
assert counts['rejected_followup'] >= 1
assert counts['conflict'] >= 1
assert counts['stale_open'] >= 1

# 10. Worker draft generation
db.insert_document('server/N020', 'N', 'server', None, 'draft target')
draft = service.build_worker_draft('server/N020')
print(f"10. Draft action for N020: {draft['suggested_action']['type']}")
assert draft['suggested_action']['type'] in ('dispatch', 'monitor')
assert 'worker_message_template' in draft and draft['worker_message_template']

# 11. Metadata lint/apply/update/queue sort
bad_meta = "---\ntype: N\nproject: server\npriority: p0\ndue_date: 2026/01/01\ntitle: bad meta\ngroup_id: server-__ALL__-0099\ntarget_id: server-__ALL__-0001-N0001\n---\nbody\n"
bad_header, bad_errors = linter.lint_file_content(bad_meta, db.get_allowed_projects())
print(f'11a. bad metadata errors: {bad_errors}')
assert any('priority' in e for e in bad_errors)
assert any('due_date' in e for e in bad_errors)

meta_content = "---\ntype: N\nproject: server\nowner: alice\npriority: high\ndue_date: 2026-01-01\ntitle: meta doc\ngroup_id: server-__ALL__-0003\ntarget_id: server-__ALL__-0001-N0001\n---\nbody\n"
meta_file = 'meta_doc.md'
with open(os.path.join(db.INBOX_DIR, meta_file), 'w', encoding='utf-8') as f:
    f.write(meta_content)
meta_res = service.apply_file(meta_file)
assert meta_res['success'], f'Metadata apply failed: {meta_res}'
meta_doc = db.get_document_by_id(meta_res['doc_id'])
print(f"11b. saved metadata: owner={meta_doc.get('owner')}, priority={meta_doc.get('priority')}, due_date={meta_doc.get('due_date')}")
assert meta_doc.get('owner') == 'alice'
assert meta_doc.get('priority') == 'high'
assert meta_doc.get('due_date') == '2026-01-01'

db.insert_document('server/N030', 'N', 'server', None, 'low prio doc', priority='low', due_date='2026-12-31')
db.insert_document('server/N031', 'N', 'server', None, 'urgent prio doc', priority='high', due_date='2026-01-02')
q2 = service.build_action_queue()
needs_dispatch_ids = [d['doc_id'] for d in q2['categories']['needs_dispatch']['items']]
print(f'11c. needs_dispatch order: {needs_dispatch_ids}')
assert 'server/N031' in needs_dispatch_ids and 'server/N030' in needs_dispatch_ids
assert needs_dispatch_ids.index('server/N031') < needs_dispatch_ids.index('server/N030')

upd = service.update_metadata('server/N030', 'bob', 'high', '2026-02-01')
assert upd['success'], f'Metadata update failed: {upd}'
upd_doc = db.get_document_by_id('server/N030')
print(f"11d. updated metadata N030: owner={upd_doc.get('owner')}, priority={upd_doc.get('priority')}, due_date={upd_doc.get('due_date')}")
assert upd_doc.get('owner') == 'bob'
assert upd_doc.get('priority') == 'high'
assert upd_doc.get('due_date') == '2026-02-01'

# 12. Workflow gaps detection
conn3 = sqlite3.connect(db.DB_PATH)
conn3.execute("UPDATE documents SET updated_at = ? WHERE doc_id = ?", ('2000-01-01T00:00:00', 'server/N030'))
conn3.execute("UPDATE documents SET created_at = ?, updated_at = ? WHERE doc_id = ?", ('2000-01-01T00:00:00', '2000-01-01T00:00:00', 'server/N031'))
conn3.execute("UPDATE documents SET created_at = ?, updated_at = ? WHERE doc_id = ?", ('2000-01-01T00:00:00', '2000-01-01T00:00:00', 'server-__ALL__-0001-T0010'))
conn3.execute("UPDATE documents SET created_at = ?, updated_at = ? WHERE doc_id = ?", ('2000-01-01T00:00:00', '2000-01-01T00:00:00', 'server-__ALL__-0001-NR0010'))
conn3.commit()
conn3.close()

gaps = service.detect_workflow_gaps()
gap_counts = gaps['counts']
print(f'12a. workflow gap counts: {gap_counts}')
assert gap_counts['stale_open'] >= 1
assert gap_counts['overdue'] >= 1
assert gap_counts['unassigned_important'] >= 1
assert gap_counts['missing_followup_from_n'] >= 1
assert gap_counts['missing_followup_from_t'] >= 1
assert gap_counts['review_stuck'] >= 1

brief2 = service.get_brief()
handover2 = service.get_handover()
print(f"12b. brief workflow_gaps total: {brief2['workflow_gaps']['total']}")
print(f"12c. handover workflow_gaps total: {handover2['workflow_gaps']['total']}")
assert 'workflow_gaps' in brief2
assert 'workflow_gaps' in handover2
assert brief2['workflow_gaps']['total'] >= 1
assert handover2['workflow_gaps']['total'] >= 1

# 13. Requeue/reprocess/memo detail/filter checks
err_content = "---\ntype: NR\nproject: server\ntitle: bad nr\n---\nbody\n"
err_file = 'bad_module.md'
with open(os.path.join(db.INBOX_DIR, err_file), 'w', encoding='utf-8') as f:
    f.write(err_content)
err_apply = service.apply_file(err_file)
print(f'13a. lint fail apply result: {err_apply}')
assert not err_apply['success']

error_view = service.get_reprocess_bucket_view('error')
error_names = [x['filename'] for x in error_view]
print(f'13b. error bucket files: {error_names}')
assert err_file in error_names

meta_detail = service.get_memo_detail('processed', meta_file)
print(f"13e. memo detail processed doc_id: {meta_detail.get('doc_id') if meta_detail else None}")
assert meta_detail is not None
assert meta_detail['header'] is not None

doc_detail = service.get_document_detail(meta_res['doc_id'])
print(f"13f. doc detail source filename: {doc_detail['source']['filename'] if doc_detail else None}")
assert doc_detail is not None
assert doc_detail['source']['filename'] == meta_file

fview = service.get_filtered_documents({'project': 'server', 'type': 'N', 'status': 'open', 'owner': 'alice', 'priority': 'high', 'q': 'meta'})
print(f"13g. filtered docs count: {fview['count']}")
assert fview['count'] >= 1

# 14. Quick views / stats / browser / helper / conflict compare / API envelope
quick_views = service.get_quick_filter_views()
quick_view_names = [v['name'] for v in quick_views]
print(f'14a. quick views: {quick_view_names}')
assert 'open' in quick_view_names
assert 'high_priority' in quick_view_names
assert 'owner_missing' in quick_view_names
assert 'n_without_t' in quick_view_names

stats = service.get_operational_stats()
print(f"14b. stats status counts: {stats['status_counts']}")
assert 'open' in stats['status_counts']
assert 'closed' in stats['status_counts']
assert 'rejected' in stats['status_counts']
assert 'conflict' in stats['status_counts']

processed_browser = service.get_file_browser_view('processed')
error_browser = service.get_file_browser_view('error')
conflict_browser = service.get_file_browser_view('conflict')
print(f"14c. file browser totals: processed={processed_browser['total']}, error={error_browser['total']}, conflict={conflict_browser['total']}")
assert processed_browser['bucket'] == 'processed'
assert error_browser['bucket'] == 'error'
assert conflict_browser['bucket'] == 'conflict'

helper = service.build_memo_template(
    doc_type='N',
    project='server',
    title='helper generated memo',
    body='hello',
)
print(f"14d. helper valid: {helper['valid']}")
assert '---' in helper['draft']

save_res = service.save_memo_template_to_inbox('helper_generated.md', helper['draft'])
print(f'14e. helper save result: {save_res}')
assert save_res['success']

conflict_candidates = service.get_reprocess_bucket_view('conflict')
if conflict_candidates:
    cmp_res = service.get_conflict_comparison(conflict_candidates[0]['filename'])
    print(f"14f. conflict compare exists: {cmp_res['exists']}")
    assert cmp_res['exists']

env_brief = service.envelope('brief', service.get_brief())
print(f"14g. envelope keys: {list(env_brief.keys())}")
assert env_brief['ok'] is True
assert env_brief['kind'] == 'brief'
assert 'data' in env_brief

print()
print('=== ALL TESTS PASSED (extended FlowGate E2E) ===')
