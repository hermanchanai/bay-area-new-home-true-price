"""SQLite connection + migrations.

    python -m tools.db            # create / migrate data/trueprice.db and list tables
"""
import logging
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "trueprice.db"
MIGRATIONS = ROOT / "migrations"

log = logging.getLogger(__name__)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the DB with foreign keys on and every migration applied."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)  # pulls can run side by side; wait out each other's writes
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply migrations/NNN_*.sql in order, once each. Returns the names applied this call."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for sql_file in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        if sql_file.name in done:
            continue
        with conn:
            conn.executescript(sql_file.read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (sql_file.name,))
        applied.append(sql_file.name)
        log.info("applied %s", sql_file.name)
    return applied


def tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    c = connect()
    print(f"{DB_PATH}: {', '.join(tables(c))}")
