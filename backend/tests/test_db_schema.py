"""启动期补列：旧库缺列时幂等补齐，且保留既有数据。"""

from __future__ import annotations

import sqlite3

import pytest

from app.db import create_db_engine
from app.db_schema import ensure_schema


@pytest.mark.asyncio
async def test_ensure_schema_adds_missing_column_idempotently(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE user_agents (name TEXT PRIMARY KEY, description TEXT)")
    con.execute("INSERT INTO user_agents (name, description) VALUES ('legacy', '旧助理')")
    con.commit()
    con.close()

    engine = create_db_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        await ensure_schema(engine)
        # 幂等：列已存在时再次启动不报错
        await ensure_schema(engine)
    finally:
        await engine.dispose()

    con = sqlite3.connect(db_path)
    columns = [row[1] for row in con.execute("PRAGMA table_info(user_agents)")]
    row = con.execute(
        "SELECT name, description, sub_agents_json FROM user_agents"
    ).fetchone()
    con.close()
    assert "sub_agents_json" in columns
    assert row == ("legacy", "旧助理", None)
