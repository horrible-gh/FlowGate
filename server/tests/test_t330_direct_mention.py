#!/usr/bin/env python3
"""T330 validation ? direct mention function call (unit test).

Bypass the token router and call mention_service.build_mention() directly.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service


def test_case1_seq_head_pending():
    """Case 1: Sequence determined (pending) ? next_type='D'."""
    print("\n" + "=" * 80)
    print("Case 1: sequence determined (head status=pending) → next_type='D'")
    print("=" * 80)
    
    mention = mention_service.build_mention(
        project="proj-t330",
        module="server",
        group="0001",
        parent_type="R",
        parent_doc_number="R0001",
        parent_title="Case1: Head Pending",
        parent_doc_id="R0001",
        head_type="D",          # ? sequence determined
        head_status="pending",  # ? pending status
        scratch_dir="/tmp/scratch/case1",
        raw_token="fake_token_case1_aaaa_bbbb_cccc_dddd_eeee_ffff",
        api_base_url="http://localhost:8000/flow_gate/api/v1",
    )
    
    print("\nResponse message (27 lines):")
    for i, line in enumerate(mention.split("\n"), 1):
        print(f"  {i:2d}. {line}")
    
    # Verification 1: check next_type
    for line in mention.split("\n"):
        if line.startswith("next_type:"):
            actual = line.split("next_type:")[1].split("#")[0].strip()
            assert actual == "D", f"FAIL: expected 'D', actual '{actual}'"
            print(f"\n✓ next_type = 'D' (OK)")
            break
    else:
        raise AssertionError("next_type line missing")
    
    # Verification 2: check actual token
    assert "Bearer fake_token_case1_aaaa_bbbb_cccc_dddd_eeee_ffff" in mention, "raw_token mismatch"
    print("✓ Authorization contains the actual raw_token (OK)")
    
    print("\n✓ Case 1 passed")


def test_case2_no_sequence():
    """Case 2: Sequence unresolved (no seq) ? next_type='<??? ???>'."""
    print("\n" + "=" * 80)
    print("Case 2: sequence unresolved (no seq) → next_type='<sequence undetermined>'")
    print("=" * 80)
    
    mention = mention_service.build_mention(
        project="proj-t330",
        module="server",
        group="0002",
        parent_type="D",
        parent_doc_number="D0001",
        parent_title="Case2: No Sequence",
        parent_doc_id="D0001",
        head_type="",          # ? no sequence
        head_status="",        # ? no status
        scratch_dir="/tmp/scratch/case2",
        raw_token="fake_token_case2_aaaa_bbbb_cccc_dddd_eeee_ffff",
        api_base_url="http://localhost:8000/flow_gate/api/v1",
    )
    
    print("\nResponse message (27 lines):")
    for i, line in enumerate(mention.split("\n"), 1):
        print(f"  {i:2d}. {line}")
    
    # Verification 1: check next_type
    for line in mention.split("\n"):
        if line.startswith("next_type:"):
            actual = line.split("next_type:")[1].split("#")[0].strip()
            assert actual == "<Sequence undecided>", f"FAIL: expected '<Sequence undecided>', actual '{actual}'"
            print(f"\n✓ next_type = '<sequence undetermined>' (OK)")
            break
    else:
        raise AssertionError("next_type line missing")
    
    # Verification 2: check actual token
    assert "Bearer fake_token_case2_aaaa_bbbb_cccc_dddd_eeee_ffff" in mention, "raw_token mismatch"
    print("✓ Authorization contains the actual raw_token (OK)")
    
    print("\n✓ Case 2 passed")


def test_case3_seq_head_in_progress():
    """Case 3: Sequence in progress (head status=in_progress) ? next_type='<?? ?: D>'."""
    print("\n" + "=" * 80)
    print("Case 3: sequence in progress (head status=in_progress) → next_type='<in progress: D>'")
    print("=" * 80)
    
    mention = mention_service.build_mention(
        project="proj-t330",
        module="server",
        group="0003",
        parent_type="DS",
        parent_doc_number="DS0001",
        parent_title="Case3: Head In Progress",
        parent_doc_id="DS0001",
        head_type="D",             # ? sequence determined
        head_status="in_progress", # ? in_progress status
        scratch_dir="/tmp/scratch/case3",
        raw_token="fake_token_case3_aaaa_bbbb_cccc_dddd_eeee_ffff",
        api_base_url="http://localhost:8000/flow_gate/api/v1",
    )
    
    print("\nResponse message (27 lines):")
    for i, line in enumerate(mention.split("\n"), 1):
        print(f"  {i:2d}. {line}")
    
    # Verification 1: check next_type
    for line in mention.split("\n"):
        if line.startswith("next_type:"):
            actual = line.split("next_type:")[1].split("#")[0].strip()
            assert actual == "<In progress: D>", f"FAIL: expected '<In progress: D>', actual '{actual}'"
            print(f"\n✓ next_type = '<In progress: D>' (OK)")
            break
    else:
        raise AssertionError("next_type line missing")
    
    # Verification 2: check actual token
    assert "Bearer fake_token_case3_aaaa_bbbb_cccc_dddd_eeee_ffff" in mention, "raw_token mismatch"
    print("✓ Authorization contains the actual raw_token (OK)")
    
    print("\n✓ Case 3 passed")


if __name__ == "__main__":
    try:
        test_case1_seq_head_pending()
        test_case2_no_sequence()
        test_case3_seq_head_in_progress()
        
        print("\n" + "=" * 80)
        print("✓ All checks passed — T329 changes applied successfully")
        print("=" * 80)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Verification failed: {e}")
        sys.exit(1)
