"""一次性数据修正：把计划步骤 run_python 误挂到临时会话的产出物归位到真实会话。

背景：headless 执行时 tool_context.session 是 ADK 临时会话，旧版 run_python
直接取它的 id 当 session_id，导致产出物挂错会话、前端产出物面板看不到图。
代码已在根因处修复（headless 注入真实 session id）；本脚本只修历史遗留数据。

只改 source_ref 精确匹配的 2 条，目标会话由命令行传入，避免误伤。
用法：.venv/Scripts/python.exe scripts/fix_step_artifact_session.py <真实session_id>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"
BAD_SESSION = "b262f146-e13f-4d4b-a714-01845dd056d7"
REFS = (
    "绘制各工作流节点数与连线数对比柱状图",
    "绘制各工作流节点数与连线数对比柱状图（使用内置 chart() 助手）",
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: fix_step_artifact_session.py <真实session_id>")
    real_session = sys.argv[1]

    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT id, session_id FROM sandbox_executions "
            "WHERE session_id=? AND source_ref IN (?, ?)",
            (BAD_SESSION, *REFS),
        ).fetchall()
        print(f"命中 {len(rows)} 条待修正记录: {[r[0][:8] for r in rows]}")
        if not rows:
            return
        db.execute(
            "UPDATE sandbox_executions SET session_id=? "
            "WHERE session_id=? AND source_ref IN (?, ?)",
            (real_session, BAD_SESSION, *REFS),
        )
        db.commit()
        after = db.execute(
            "SELECT count(*) FROM sandbox_executions WHERE session_id=? AND source_ref IN (?, ?)",
            (real_session, *REFS),
        ).fetchone()[0]
        print(f"修正完成：真实会话下现有 {after} 条绘图产出物")
    finally:
        db.close()


if __name__ == "__main__":
    main()
