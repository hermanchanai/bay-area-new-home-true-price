"""Assemble the live server's deploy folder from an allowlist (code, config, site, slim DB), never a secret.

    python -m tools.build_bundle            # -> .tmp/bundle/, then: railway up .tmp/bundle --path-as-root

`railway up` skips gitignored files, which would drop data/site.db, and --no-gitignore would upload .env. So
the deploy uploads this folder instead. The Meta key is a host environment variable, not a file. Run
tools.export_site_data first.
"""
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "bundle"
DIRS = ("tools", "config", "web", "migrations")
FILES = ("requirements.txt", "data/site.db")
# One process, so the per-IP limits and the answer cache are shared by every request.
PROCFILE = "web: gunicorn -w 1 --threads 4 --timeout 120 -b 0.0.0.0:$PORT 'tools.server:create_app()'\n"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*", ".DS_Store")


def build(root: Path = ROOT, out: Path = OUT) -> Path:
    if not (root / "data" / "site.db").exists():
        raise FileNotFoundError("data/site.db is missing: run python -m tools.export_site_data first")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for d in DIRS:
        if (root / d).exists():
            shutil.copytree(root / d, out / d, ignore=SKIP)
    for f in FILES:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / f, out / f)
    with sqlite3.connect(out / "data" / "site.db") as conn:     # local test questions aren't live-site spend
        conn.execute("DELETE FROM llm_calls")
    (out / "Procfile").write_text(PROCFILE)
    (out / ".python-version").write_text("3.12\n")
    leaked = [p for p in out.rglob("*") if p.name.startswith(".env")]
    if leaked:
        raise RuntimeError(f"secret-looking files in the bundle: {leaked}")
    return out


if __name__ == "__main__":
    out = build()
    n = sum(1 for p in out.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out.relative_to(ROOT)}: {n} files, {size / 1e6:.1f} MB")
