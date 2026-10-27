"""One-time, offline mapping to Ops Hub. Existing central credentials take priority."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def migrate(portal: Path, identity: Path):
    with sqlite3.connect(identity, timeout=10) as conn:
        conn.execute("ATTACH DATABASE ? AS portal", (str(portal),))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS portal.identity_links (identity_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id))")
        for uid, name, hashed, created in conn.execute("SELECT id,username,password_hash,created_at FROM portal.users ORDER BY id").fetchall():
            central = conn.execute("SELECT id FROM main.users WHERE username=?", (name,)).fetchone()
            if central is None:
                created_at = datetime.fromtimestamp(created, timezone.utc).replace(tzinfo=None).isoformat(' ')
                identity_id = conn.execute("INSERT INTO main.users(username,password_hash,auth_version,created_at) VALUES(?,?,0,?)",
                                           (name, hashed, created_at)).lastrowid
            else:
                identity_id = central[0]
            previous = conn.execute("SELECT identity_id FROM portal.identity_links WHERE user_id=?", (uid,)).fetchone()
            if previous is not None and previous[0] != identity_id:
                raise RuntimeError("Existing identity mapping conflicts; migration rolled back")
            conn.execute("INSERT OR IGNORE INTO portal.identity_links VALUES(?,?)", (identity_id, uid))
            linked = conn.execute("SELECT user_id FROM portal.identity_links WHERE identity_id=?", (identity_id,)).fetchone()
            if linked != (uid,):
                raise RuntimeError("Identity already belongs to another portal user")
        # Legacy portal sessions cease to authenticate at cutover.
        conn.execute("DELETE FROM portal.sessions")
        return conn.execute("SELECT count(*) FROM portal.identity_links").fetchone()[0]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('portal', type=Path)
    parser.add_argument('identity', type=Path)
    args = parser.parse_args()
    if not args.portal.is_file() or not args.identity.is_file():
        parser.error('Both existing databases are required; stop both services before migrating')
    print('Mapped portal accounts:', migrate(args.portal, args.identity))
