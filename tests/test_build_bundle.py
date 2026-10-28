"""build_bundle: the API deploy folder holds an allowlist of files, never a secret."""
import pytest

from tools import db
from tools.build_bundle import build


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    for f, text in {"tools/server.py": "x", "tools/__init__.py": "", "config/llm.yaml": "a: 1",
                    "web/index.html": "<html>", "web/data/site.json": "{}", "requirements.txt": "flask",
                    "data/trueprice.db": "big", ".env": "META_API_KEY=secret",
                    "tools/.env": "X=1", ".tmp/raw/page.html": "raw", "tools/__pycache__/a.pyc": "c"}.items():
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text(text)
    db.connect(r / "data" / "site.db").close()
    return r


def test_bundle_holds_code_config_site_and_slim_db_only(repo, tmp_path):
    out = build(repo, tmp_path / "bundle")
    got = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert "data/site.db" in got and "tools/server.py" in got and "web/data/site.json" in got
    assert "Procfile" in got and ".python-version" in got and "requirements.txt" in got
    assert not [f for f in got if ".env" in f or "trueprice.db" in f or ".tmp" in f or "__pycache__" in f]


def test_bundle_refuses_without_the_slim_db(repo, tmp_path):
    (repo / "data" / "site.db").unlink()
    with pytest.raises(FileNotFoundError, match="export_site_data"):
        build(repo, tmp_path / "bundle")


def test_bundle_db_starts_with_an_empty_spend_ledger(repo, tmp_path):
    # local test questions are recorded in data/site.db; they aren't the live site's spend
    (repo / "data" / "site.db").unlink()
    conn = db.connect(repo / "data" / "site.db")
    conn.execute("INSERT INTO llm_calls (provider, model, purpose, cache_key, cost_usd, ok, created_at) "
                 "VALUES ('meta', 'm', 'agent_live', 'k', 0.009, 1, '2026-10-28')")
    conn.commit()
    conn.close()
    out = build(repo, tmp_path / "bundle")
    shipped = db.connect(out / "data" / "site.db")
    assert shipped.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
