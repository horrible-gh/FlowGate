"""Explicit destructive-to-own-connections probe, restricted to the documented test DBs.

Run from server: python tests/integration/check_pool_recovery_0054.py --database flowgate_test1
Only sessions created by this probe are terminated; no application data is changed.
"""
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import os
os.environ.setdefault("USERNAME", "flowgate-test")  # command runner intentionally strips user env
import psycopg2
from sqloader.postgresql import PostgreSQLWrapper
from modules.flow_gate.db.resilience import install_checkout_validation

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True,choices=['flowgate_test1','flowgate_test2'])
    args=parser.parse_args()
    db=PostgreSQLWrapper(host='192.168.0.250',user=args.database,password=args.database,database=args.database,
        pool_min=20,pool_max=24,max_parallel_queries=20,max_idle=300,max_lifetime=1800,acquire_timeout=5)
    control=psycopg2.connect(host='192.168.0.250',user=args.database,password=args.database,dbname=args.database)
    control.autocommit=True
    try:
        install_checkout_validation(db)
        held=[db._checkout() for _ in range(20)]
        pids=[c.get_backend_pid() for c in held]
        for c in held: db._checkin(c)
        with control.cursor() as cur:
            for pid in pids:
                cur.execute('SELECT pg_terminate_backend(%s)',(pid,))
                assert cur.fetchone()[0]
        started=time.monotonic()
        assert db.fetch_one('SELECT 54 AS value')['value']==54
        elapsed=time.monotonic()-started
        with db.begin_transaction() as txn:
            txn.execute('SELECT 55 AS value')
            assert txn.fetchone()['value']==55
        print(f'PASS: terminated {len(pids)} own idle sessions; next read and transaction succeeded; recovery={elapsed:.3f}s')
    finally:
        db.close();control.close()
if __name__=='__main__': main()
