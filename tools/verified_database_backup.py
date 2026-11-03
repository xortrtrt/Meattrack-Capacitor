"""Private local pg_dump backup, actual restore/reset rehearsal, and state binding.

The archive and manifest live outside the repository. Never print database rows,
connection strings or child-process output containing private information.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.config import database_dsn
from tools.transaction_reset import require_loopback, reset_transactions


def _private(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Keep private database backups outside the repository.")
    return path


def _file_hash(path):
    h = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def database_state(conn):
    """Compare all public table contents and identity sequences without exporting rows."""
    h = sha256()
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            h.update(table.encode())
            cur.execute(sql.SQL("SELECT md5(to_jsonb(t)::text) FROM public.{} t ORDER BY 1").format(sql.Identifier(table)))
            for row in cur:
                h.update(row[0].encode())
        cur.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        sequences = [r[0] for r in cur.fetchall()]
        for sequence in sequences:
            cur.execute(sql.SQL("SELECT last_value,is_called FROM public.{}").format(sql.Identifier(sequence)))
            h.update(json.dumps([sequence, *cur.fetchone()]).encode())
    return h.hexdigest()


def _identity(conn):
    params = conn.get_dsn_parameters()
    return sha256(json.dumps({key: params.get(key) for key in ('host', 'port', 'dbname', 'user')}, sort_keys=True).encode()).hexdigest()


def validate_backup(path):
    path = _private(path)
    manifest = path.with_suffix(path.suffix + '.restore.json')
    if not path.is_file() or not manifest.is_file():
        raise ValueError("A private archive and verified restore manifest are required.")
    with path.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ValueError("Expected a custom-format pg_dump archive.")
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if record.get('version') != 1 or record.get('restored') is not True or record.get('reset_rehearsed') is not True:
        raise ValueError("Backup must pass actual restore and reset rehearsal before use.")
    if not record.get('source_identity') or not record.get('state') or _file_hash(path) != record.get('archive_sha256'):
        raise ValueError("Backup archive or verified restore manifest does not match.")
    return record


def validate_backup_for_connection(path, conn):
    require_loopback(conn)
    record = validate_backup(path)
    if record['source_identity'] != _identity(conn) or record['state'] != database_state(conn):
        raise ValueError("The database changed since the verified backup. Stop writers and create a fresh verified backup.")


def _docker(args, *, stdout=None, stdin=None):
    process = subprocess.run(['docker', 'compose', 'exec', '-T', 'db', *args], cwd=ROOT,
                             stdin=stdin, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode:
        raise RuntimeError("Local PostgreSQL backup/restore command failed; private output was not printed.")


def create_verified_backup(path):
    path = _private(path)
    if path.exists():
        raise ValueError("Use a new private archive filename; backups are never overwritten.")
    path.parent.mkdir(parents=True, exist_ok=True)
    clone_name = 'meattrack_restore_' + uuid4().hex
    with psycopg2.connect(database_dsn()) as source:
        require_loopback(source)
        params = source.get_dsn_parameters()
        before = database_state(source)
        source.commit()
        with path.open('xb') as stream:
            _docker(['pg_dump', '-U', params['user'], '-d', params['dbname'], '--schema=public', '-Fc'], stdout=stream)
        if database_state(source) != before:
            raise RuntimeError("Source changed during backup; stop writers before retrying with a new archive.")
        source.commit()
        admin = psycopg2.connect(database_dsn())
        admin.autocommit = True
        created = False
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(clone_name)))
                created = True
            with path.open('rb') as stream:
                _docker(['pg_restore', '-U', params['user'], '-d', clone_name, '--single-transaction',
                         '--exit-on-error', '--clean', '--if-exists', '--no-owner', '--no-acl'], stdin=stream)
            with psycopg2.connect(database_dsn(), dbname=clone_name) as clone:
                if database_state(clone) != before:
                    raise RuntimeError("Restored database content/sequence verification failed.")
                clone.commit()
                rehearsal = reset_transactions(clone, apply=True)
                if not rehearsal['committed']:
                    raise RuntimeError("Reset rehearsal failed.")
            if database_state(source) != before:
                raise RuntimeError("Source changed during rehearsal; no verified manifest was created.")
            record = dict(version=1, restored=True, reset_rehearsed=True,
                          archive_sha256=_file_hash(path), state=before, source_identity=_identity(source))
            path.with_suffix(path.suffix + '.restore.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
            return dict(restored=True, reset_rehearsed=True, archive_sha256=record['archive_sha256'])
        finally:
            if created:
                # Exact generated local clone only; never the source database.
                if not clone_name.startswith('meattrack_restore_') or clone_name == params['dbname']:
                    raise RuntimeError("Unexpected restore database identity; cleanup stopped.")
                with admin.cursor() as cur:
                    cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(clone_name)))
            admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--maintenance-confirmed', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(create_verified_backup(args.output), indent=2))


if __name__ == '__main__':
    main()
