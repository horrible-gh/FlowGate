"""T044 TV/TVR backend flow smoke test.

Run from the server/ directory: `py _tv_smoke_test.py`.
Delete and reinitialize the existing DB, then verify the core scenarios in sequence.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

# Override the storage/test report paths with an isolated temporary directory.
_TMP = tempfile.mkdtemp(prefix="flowgate_tv_smoke_")
os.environ["FLOWGATE_STORAGE_DIR"] = os.path.join(_TMP, "storage")
os.environ["FLOWGATE_INBOX_DIR"] = os.path.join(_TMP, "storage", "inbox")
os.environ["FLOWGATE_PROCESSED_DIR"] = os.path.join(_TMP, "storage", "processed")
os.environ["FLOWGATE_ERROR_DIR"] = os.path.join(_TMP, "storage", "error")
os.environ["FLOWGATE_ACCEPT_DIR"] = os.path.join(_TMP, "storage", "accept")
os.environ["FLOWGATE_REJECT_DIR"] = os.path.join(_TMP, "storage", "reject")
os.environ["FLOWGATE_CANCELLED_DIR"] = os.path.join(_TMP, "storage", "cancelled")
os.environ["FLOWGATE_TEST_REPORTS_DIR"] = os.path.join(_TMP, "test_reports")
os.environ["FLOWGATE_TEST_REPORTS_ARCHIVE_DIR"] = os.path.join(_TMP, "test_reports_archive")
os.environ["FLOWGATE_DESIGN_REOPEN_DIR"] = os.path.join(_TMP, "design_reopen")

from modules.flow_gate import db, process_service  # noqa: E402

# Move the DB path to a temporary location as well
db.DB_PATH = os.path.join(_TMP, "flowgate_test.db")
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.INBOX_DIR = os.environ["FLOWGATE_INBOX_DIR"]
db.PROCESSED_DIR = os.environ["FLOWGATE_PROCESSED_DIR"]
db.ERROR_DIR = os.environ["FLOWGATE_ERROR_DIR"]
db.ACCEPT_DIR = os.environ["FLOWGATE_ACCEPT_DIR"]
db.REJECT_DIR = os.environ["FLOWGATE_REJECT_DIR"]
db.CANCELLED_DIR = os.environ["FLOWGATE_CANCELLED_DIR"]
db.TEST_REPORTS_DIR = os.environ["FLOWGATE_TEST_REPORTS_DIR"]
db.TEST_REPORTS_ARCHIVE_DIR = os.environ["FLOWGATE_TEST_REPORTS_ARCHIVE_DIR"]
db.DESIGN_REOPEN_DIR = os.environ["FLOWGATE_DESIGN_REOPEN_DIR"]
for d in (db.INBOX_DIR, db.PROCESSED_DIR, db.ERROR_DIR,
          db.ACCEPT_DIR, db.REJECT_DIR, db.CANCELLED_DIR,
          db.TEST_REPORTS_DIR, db.TEST_REPORTS_ARCHIVE_DIR,
          db.DESIGN_REOPEN_DIR):
    os.makedirs(d, exist_ok=True)

db.init_db()
db.add_allowed_project("FG", "core")
db.add_allowed_project("FG", "")


PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"[OK] {label}")
    else:
        FAILED.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} :: {detail}")


def _create_t_accepted(project="FG", module="core", group_id="FG-core-R001",
                       n: int | None = None) -> str:
    """Reserve an ID for one test T document and register it in accepted status."""
    seq = db.get_next_number(project, module, "T") if n is None else n
    t_doc_id = db.build_t_doc_id(project, module, seq)
    db.insert_document(
        doc_id=t_doc_id, doc_type="T", project=project, module=module,
        title=f"Test T {seq}",
        group_id=group_id,
        priority="medium",
        status="accepted",
    )
    return t_doc_id


# ── 1. Create TV → approve ─────────────────────────────────────────
t1 = _create_t_accepted()
res = process_service.create_tv(
    target_id=t1, tv_type="internal",     title="TV Smoke 1",
    project="FG", module="core",
    scenarios_json='[{"title":"Scenario A"},{"title":"Scenario B"}]',
    clear_db=True, clear_fs=True,
)
check("1-1. TV created successfully", res.get("status") == "success", str(res))
tv1 = res.get("tv_doc_id")
check("1-2. TV file created", bool(res.get("filename")) and os.path.exists(
    os.path.join(db.TEST_REPORTS_DIR, res.get("filename", ""))))

approve = process_service.approve_tv(tv1)
check("1-3. TV approved → Running", approve.get("tv_status") == "Running", str(approve))


# ── 2. Enter scenario results + aggregate ──────────────────────────
r1 = process_service.input_scenario_result(tv1, 1, "pass")
check("2-1. pass input", r1.get("status") == "success", str(r1))
check("2-2. progress 1/2", r1.get("progress") == "1/2", str(r1))
r2 = process_service.input_scenario_result(tv1, 2, "fail", note="Bug found")
check("2-3. fail input → Fail", r2.get("tv_status") == "Fail", str(r2))


# ── 3. Create TVR → reject → rerun_t ───────────────────────────────
tvr_res = process_service.create_tvr(tv1)
check("3-1. TVR created successfully", tvr_res.get("status") == "success", str(tvr_res))
tvr1 = tvr_res.get("tvr_doc_id")

rej = process_service.reject_tvr(tvr1, rejection_reason="Regression", followup="rerun_t")
check("3-2. TVR rejected rerun_t", rej.get("status") == "success", str(rej))
check("3-3. New T draft created", "rerun_t" in rej and rej["rerun_t"].get("new_t_doc_id"),
      str(rej))
new_t = rej["rerun_t"]["new_t_doc_id"]
new_t_doc = db.get_document_by_id(new_t)
check("3-4. New T status=draft",
      (new_t_doc or {}).get("status") == "draft",
      str(new_t_doc))
check("3-5. New T previous_t=t1",
      (new_t_doc or {}).get("previous_t") == t1, str(new_t_doc))
check("3-6. New T triggered_by=tvr1",
      (new_t_doc or {}).get("triggered_by") == tvr1, str(new_t_doc))
check("3-7. Original TV review_required",
      bool(db.get_document_by_id(tv1).get("review_required")),
      str(db.get_document_by_id(tv1)))
check("3-8. TV status Reject",
      (db.get_tv_status(tv1) or {}).get("tv_status") == "Reject")


# ── 4. Verify environment lock (no two Running items in the same project/module) ────
t2 = _create_t_accepted()
res2 = process_service.create_tv(
    target_id=t2, tv_type="internal",     title="TV Smoke 2",
    project="FG", module="core",
    scenarios_json='["Scenario X"]',
)
tv2 = res2.get("tv_doc_id")

t3 = _create_t_accepted()
res3 = process_service.create_tv(
    target_id=t3, tv_type="internal",     title="TV Smoke 3",
    project="FG", module="core",
    scenarios_json='["Scenario Y"]',
)
tv3 = res3.get("tv_doc_id")

ap2 = process_service.approve_tv(tv2)
check("4-1. Second TV approved", ap2.get("tv_status") == "Running", str(ap2))
ap3 = process_service.approve_tv(tv3)
check("4-2. Third TV approval blocked (env_busy)",
      ap3.get("error") == "env_busy", str(ap3))


# ── 5. design_reopen branch ────────────────────────────────────────
# tv2 → Fail → TVR → reject(design_reopen)
process_service.input_scenario_result(tv2, 1, "fail", note="Design defect")
tvr2_res = process_service.create_tvr(tv2)
tvr2 = tvr2_res.get("tvr_doc_id")
# Draft creation should proceed even if the same group has no DS/D document.
rej2 = process_service.reject_tvr(tvr2, rejection_reason="Design re-review",
                                  followup="design_reopen")
check("5-1. design_reopen success", rej2.get("status") == "success", str(rej2))
check("5-2. New DS draft created",
      rej2.get("design_reopen", {}).get("new_ds_doc_id"), str(rej2))
new_ds = rej2["design_reopen"]["new_ds_doc_id"]
ds_doc = db.get_document_by_id(new_ds)
check("5-3. New DS status=draft", (ds_doc or {}).get("status") == "draft",
      str(ds_doc))
check("5-4. New DS triggered_by=tvr2",
      (ds_doc or {}).get("triggered_by") == tvr2, str(ds_doc))
check("5-5. DS file created in DESIGN_REOPEN_DIR",
      os.path.exists(os.path.join(db.DESIGN_REOPEN_DIR,
                                  rej2["design_reopen"]["filename"])))


# ── 6. Clear failure path ───────────────────────────────────────────
# Prepare a new T/TV
t4 = _create_t_accepted()
res4 = process_service.create_tv(
    target_id=t4, tv_type="internal",     title="TV Clear Failure",
    project="FG", module="other",  # separate env
    scenarios_json='["Scenario"]',
    clear_db=True, clear_fs=True,
)
tv4 = res4.get("tv_doc_id")
db.add_allowed_project("FG", "other")
process_service.approve_tv(tv4)
process_service._clear_impl_override = {"db": "ok", "fs": "failed"}
run_res = process_service.run_tv(tv4, clear_before_run=True)
check("6-1. scenarios_started=False on clear failure",
      run_res.get("scenarios_started") is False, str(run_res))
check("6-2. clear result fs=failed",
      run_res.get("clear_result", {}).get("fs") == "failed", str(run_res))
check("6-3. TV status remains Running (no rollback)",
      (db.get_tv_status(tv4) or {}).get("tv_status") == "Running")
process_service._clear_impl_override = None
retry = process_service.retry_clear(tv4)
check("6-4. retry_clear success", retry.get("status") == "success", str(retry))


# ── 7. Convert hold → skip ─────────────────────────────────────────
t5 = _create_t_accepted(module="core")
# Force-close tv2 even though it is still Running to isolate the env
process_service.close_tv_force(tv2, reason="cleanup")
res5 = process_service.create_tv(
    target_id=t5, tv_type="internal", title="hold→skip",
    project="FG", module="core",
    scenarios_json='["sc1","sc2"]',
)
tv5 = res5.get("tv_doc_id")
process_service.approve_tv(tv5)
process_service.input_scenario_result(tv5, 1, "hold", note="Temporarily on hold")
h2s = process_service.hold_to_skip(tv5, 1, reason="environment constraints")
check("7-1. hold→skip conversion", h2s.get("status") == "success", str(h2s))
sc_state = [s for s in db.get_tv_scenarios(tv5) if s["scenario_idx"] == 1][0]
check("7-2. scenario result skip",
      (sc_state.get("result") or "").lower() == "skip", str(sc_state))


# ── 8. Approve TVR → TV Closed ─────────────────────────────────────
process_service.input_scenario_result(tv5, 2, "pass")
tvr5 = process_service.create_tvr(tv5).get("tvr_doc_id")
approve5 = process_service.approve_tvr(tvr5)
check("8-1. TVR approved successfully", approve5.get("status") == "success", str(approve5))
check("8-2. TV transitioned to Closed",
      (db.get_tv_status(tv5) or {}).get("tv_status") == "Closed")


# ── Aggregate results ───────────────────────────────────────────────
print()
print("=" * 60)
print(f"PASSED: {len(PASSED)}")
print(f"FAILED: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
print("=" * 60)

shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if not FAILED else 1)
