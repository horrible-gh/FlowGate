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



class ProbeProxy:
    """Drop only this probe's TCP connections; never alter the shared test DB.

    This validates the application's pool through a real network outage. It does
    not interrupt an independently deployed web server or prove UI recovery.
    """

    def __init__(self):
        import socket
        import threading
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.blocked = threading.Event()
        self.applied = threading.Event()
        self.stopped = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        import select
        import socket
        peers = {}

        def discard(sock):
            other = peers.pop(sock, None)
            for item in (sock, other):
                if item is not None:
                    peers.pop(item, None)
                    try:
                        item.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    item.close()

        try:
            while not self.stopped.is_set():
                if self.blocked.is_set():
                    for sock in list(peers):
                        discard(sock)
                    self.applied.set()
                else:
                    self.applied.clear()
                ready, _, _ = select.select([self.listener, *peers], [], [], 0.05)
                for sock in ready:
                    if sock is self.listener:
                        client, _ = self.listener.accept()
                        if self.blocked.is_set():
                            client.close()
                            continue
                        try:
                            upstream = socket.create_connection(('192.168.0.250', 5432), timeout=3)
                        except OSError:
                            client.close()
                            continue
                        client.settimeout(2)
                        upstream.settimeout(2)
                        peers[client] = upstream
                        peers[upstream] = client
                    elif sock in peers:
                        try:
                            data = sock.recv(65536)
                            if data:
                                peers[sock].sendall(data)
                            else:
                                discard(sock)
                        except OSError:
                            discard(sock)
        except Exception as exc:
            self.failure = type(exc).__name__
        finally:
            for sock in list(peers):
                discard(sock)
            self.listener.close()

    def outage(self, blocked):
        if blocked:
            self.blocked.set()
        else:
            self.blocked.clear()
        deadline = time.monotonic() + 5
        while self.applied.is_set() != blocked:
            if self.failure or not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Probe proxy failed to apply outage state')
            time.sleep(0.01)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Probe proxy did not stop')


def check_sustained_outage(database, seconds):
    import json
    proxy = ProbeProxy()
    db = None
    try:
        db = PostgreSQLWrapper(host='127.0.0.1', port=proxy.port,
            user=database, password=database, database=database,
            pool_min=2, pool_max=4, max_parallel_queries=2,
            max_idle=300, max_lifetime=1800, acquire_timeout=2)
        install_checkout_validation(db)
        assert db.fetch_one('SELECT 54 AS value')['value'] == 54
        proxy.outage(True)
        started = time.monotonic()
        failures = 0
        last_failure = None
        while time.monotonic() - started < seconds:
            try:
                db.fetch_one('SELECT 54 AS value')
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                failures += 1
                last_failure = time.monotonic() - started
            else:
                raise AssertionError('DB query succeeded during network outage')
            time.sleep(min(0.25, max(0, seconds - (time.monotonic() - started))))
        assert failures > 0
        proxy.outage(False)
        restored = time.monotonic()
        assert db.fetch_one('SELECT 54 AS value')['value'] == 54
        read_seconds = time.monotonic() - restored
        with db.begin_transaction() as txn:
            txn.execute('SELECT 55 AS value')
            assert txn.fetchone()['value'] == 55
        transaction_seconds = time.monotonic() - restored
        assert not proxy.failure, proxy.failure
        assert transaction_seconds <= 30, 'Pool recovery exceeded 30 seconds'
        print('PASS: sustained probe-only DB outage; ' + json.dumps({
            'database': database, 'requested_outage_seconds': seconds,
            'actual_outage_seconds': restored - started,
            'failed_reads': failures, 'last_failed_read_seconds': last_failure,
            'read_after_restore_seconds': read_seconds,
            'transaction_after_restore_seconds': transaction_seconds,
            'scope': 'probe_pool_only', 'browser_measured': False,
        }), flush=True)
    finally:
        try:
            if db is not None:
                db.close()
        finally:
            proxy.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True,choices=['flowgate_test1','flowgate_test2'])
    parser.add_argument('--outage-seconds', type=int, choices=[0, 30, 60], default=0,
        help='Sustained probe-only TCP outage; zero retains the idle-session test')
    args=parser.parse_args()
    if args.outage_seconds:
        check_sustained_outage(args.database, args.outage_seconds)
        return
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
