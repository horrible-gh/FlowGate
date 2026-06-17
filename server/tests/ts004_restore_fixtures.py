#!/usr/bin/env python3
"""TS004 test data regeneration script

Tasks:
1. server-ts004-9401 group (DS open, next=D)
2. server-ts004-9402 group (DC open, DS next=T)
"""

import sys
from datetime import datetime
from modules.flow_gate import db

def main():
    # Initialize the DB (create tables)
    db.init_db()
    
    print("=" * 60)
    print("TS004 test data regeneration")
    print("=" * 60)
    
    # ────────────────────────────────────────────────────────
    # DB-01: server-ts004-9401 group (DS open, next=D)
    # ────────────────────────────────────────────────────────
    
    print("\n[DB-01] Processing server-ts004-9401 group...")
    
    # Pre-check: verify whether the group already exists
    group_9401 = db.get_group("server-ts004-9401")
    if group_9401:
        print("✗ Group 'server-ts004-9401' already exists — skipping insert")
    else:
        # 1.1 Insert into the groups table
        try:
            db.insert_group(
                group_id="server-ts004-9401",
                project="server",
                module="ts004",
                title="TS004 DS path verification",
                priority=None
            )
            print("  ✓ groups: server-ts004-9401 inserted")
        except Exception as e:
            print(f"  ✗ groups insert failed: {e}")
            return 1
        
        # 1.2 R001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9401-R0001",
                doc_type="R",
                project="server",
                module="ts004",
                title="R001",
                group_id="server-ts004-9401",
                status="accepted"
            )
            db.insert_event(
                doc_id="server-ts004-9401-R0001",
                event_type="created",
                memo_file="R001_requirement.md"
            )
            print("  ✓ documents/events: server-ts004-9401-R0001 inserted")
        except Exception as e:
            print(f"  ✗ R001 insert failed: {e}")
            return 1
        
        # 1.3 Q001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9401-Q0001",
                doc_type="Q",
                project="server",
                module="ts004",
                title="Q001",
                target_id="server-ts004-9401-R0001",
                group_id="server-ts004-9401",
                status="accepted"
            )
            db.insert_event(
                doc_id="server-ts004-9401-Q0001",
                event_type="created",
                memo_file="Q001_question.md"
            )
            print("  ✓ documents/events: server-ts004-9401-Q0001 inserted")
        except Exception as e:
            print(f"  ✗ Q001 insert failed: {e}")
            return 1
        
        # 1.4 A001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9401-A0001",
                doc_type="A",
                project="server",
                module="ts004",
                title="A001",
                target_id="server-ts004-9401-Q0001",
                group_id="server-ts004-9401",
                status="accepted"
            )
            db.insert_event(
                doc_id="server-ts004-9401-A0001",
                event_type="created",
                memo_file="A001_answer.md"
            )
            print("  ✓ documents/events: server-ts004-9401-A0001 inserted")
        except Exception as e:
            print(f"  ✗ A001 insert failed: {e}")
            return 1
        
        # 1.5 AR001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9401-AR0001",
                doc_type="AR",
                project="server",
                module="ts004",
                title="AR001",
                target_id="server-ts004-9401-R0001",
                group_id="server-ts004-9401",
                status="accepted"
            )
            print("  ✓ documents: server-ts004-9401-AR0001 inserted")
        except Exception as e:
            print(f"  ✗ AR001 insert failed: {e}")
            return 1
        
        # 1.6 DS001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9401-DS0001",
                doc_type="DS",
                project="server",
                module="ts004",
                title="DS001",
                target_id="server-ts004-9401-AR0001",
                group_id="server-ts004-9401",
                status="open",
                next_action="D"
            )
            db.insert_event(
                doc_id="server-ts004-9401-DS0001",
                event_type="created",
                memo_file="ts004_t051_base_ds_open.md"
            )
            print("  ✓ documents/events: server-ts004-9401-DS0001 inserted")
        except Exception as e:
            print(f"  ✗ DS001 insert failed: {e}")
            return 1
    
    # ────────────────────────────────────────────────────────
    # DB-02: server-ts004-9402 group (DC open, DS next=T)
    # ────────────────────────────────────────────────────────
    
    print("\n[DB-02] Processing server-ts004-9402 group...")
    
    # Pre-check: verify whether the group already exists
    group_9402 = db.get_group("server-ts004-9402")
    if group_9402:
        print("✗ Group 'server-ts004-9402' already exists — skipping insert")
    else:
        # 2.1 Insert into the groups table
        try:
            db.insert_group(
                group_id="server-ts004-9402",
                project="server",
                module="ts004",
                title="TS004 DC T-candidate verification",
                priority=None
            )
            print("  ✓ groups: server-ts004-9402 inserted")
        except Exception as e:
            print(f"  ✗ groups insert failed: {e}")
            return 1
        
        # 2.2 DS002 document
        try:
            db.insert_document(
                doc_id="server-ts004-9402-DS0002",
                doc_type="DS",
                project="server",
                module="ts004",
                title="DS002",
                group_id="server-ts004-9402",
                status="accepted",
                next_action="T"
            )
            print("  ✓ documents: server-ts004-9402-DS0002 inserted")
        except Exception as e:
            print(f"  ✗ DS002 insert failed: {e}")
            return 1
        
        # 2.3 D001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9402-D0001",
                doc_type="D",
                project="server",
                module="ts004",
                title="D001",
                group_id="server-ts004-9402",
                status="accepted"
            )
            print("  ✓ documents: server-ts004-9402-D0001 inserted")
        except Exception as e:
            print(f"  ✗ D001 insert failed: {e}")
            return 1
        
        # 2.4 DC001 document
        try:
            db.insert_document(
                doc_id="server-ts004-9402-DC0001",
                doc_type="DC",
                project="server",
                module="ts004",
                title="DC001",
                target_id="server-ts004-9402-DS0002",
                group_id="server-ts004-9402",
                status="open"
            )
            db.insert_event(
                doc_id="server-ts004-9402-DC0001",
                event_type="created",
                memo_file="ts004_t051_base_dc_open.md"
            )
            print("  ✓ documents/events: server-ts004-9402-DC0001 inserted")
        except Exception as e:
            print(f"  ✗ DC001 insert failed: {e}")
            return 1
    
    # ────────────────────────────────────────────────────────
    # Check results (SELECT)
    # ────────────────────────────────────────────────────────
    
    print("\n" + "=" * 60)
    print("Verify INSERT results (SELECT)")
    print("=" * 60)
    
    # Verify DB-01
    print("\n[Verify] DB-01 group documents:")
    docs_9401 = db.get_documents_by_group_id("server-ts004-9401")
    if docs_9401:
        print(f"  → {len(docs_9401)} document(s) found:")
        for doc in docs_9401:
            print(f"    - {doc['doc_id']}: {doc['type']} ({doc['status']})")
    else:
        print("  → No documents found")
    
    # Verify DB-02
    print("\n[Verify] DB-02 group documents:")
    docs_9402 = db.get_documents_by_group_id("server-ts004-9402")
    if docs_9402:
        print(f"  → {len(docs_9402)} document(s) found:")
        for doc in docs_9402:
            print(f"    - {doc['doc_id']}: {doc['type']} ({doc['status']})")
    else:
        print("  → No documents found")
    
    print("\n" + "=" * 60)
    print("✓ TS004 test data regeneration complete")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
