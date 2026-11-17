"""一次性修复：把计划步骤已画好但未落 artifacts 的产出物归位，并挂到汇总消息。

背景：headless 步骤里 run_python 的图表只进 sandbox_executions（session_id 正确），
旧版没有在步骤执行缝落 artifacts；汇总模型判断"图已生成、无需重画"时，汇总回合
也不产新图，导致最终汇总"说有图却没产物"。代码已在根因处修复（步骤即时落库 +
汇总复用步骤产物）；本脚本只修这条历史会话数据。

幂等：目标会话已有 plan-step- 前缀的 artifacts 则跳过。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"
SANDBOX_ID = "6bbdcbfb-9b25-455c-8c1a-bcfad947c77b"
SESSION_ID = "7933b772-88a7-4ef6-9da9-a86201541bf2"
SUMMARY_ID = "auto-summary-plan-190a5ea8-3b9f-4770-bf86-ee31317905bf"
STEP_MESSAGE_ID = "plan-step-1ba5f5c0-0000-0000-0000-000000000000"
MARKER = "__ONGRID_SANDBOX_RESULT__"
# 正文里推理段落结束、真实回答开始的标记
BODY_START = "## 项目工作流盘点结论"


def _parse_artifacts(stdout: str) -> list[dict]:
    for line in reversed(stdout.splitlines()):
        s = line.strip()
        if s.startswith(MARKER):
            payload = json.loads(s[len(MARKER):])
            arts = payload.get("artifacts", [])
            return arts if isinstance(arts, list) else []
    return []


def main() -> None:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        existing = db.execute(
            "SELECT count(*) FROM artifacts WHERE session_id=? AND message_id LIKE 'plan-step-%'",
            (SESSION_ID,),
        ).fetchone()[0]
        if existing:
            print(f"该会话已有 {existing} 条步骤产出物，跳过")
            return

        sandbox = db.execute(
            "SELECT stdout FROM sandbox_executions WHERE id=?", (SANDBOX_ID,)
        ).fetchone()
        if sandbox is None:
            raise SystemExit(f"未找到 sandbox 记录 {SANDBOX_ID}")
        artifacts = _parse_artifacts(sandbox["stdout"])
        if not artifacts:
            raise SystemExit("sandbox 未解析出产出物")
        print(f"解析出 {len(artifacts)} 条产出物")

        tool_call_id = f"repair-{SANDBOX_ID}"
        now = datetime.now(timezone.utc).isoformat()
        slim: list[dict] = []
        for seq, art in enumerate(artifacts):
            kind = str(art.get("kind", "text"))
            payload = art.get("payload")
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
            db.execute(
                "INSERT INTO artifacts (id, session_id, tool_call_id, kind, title, mime, "
                "payload_json, data_bytes, file_name, created_at, message_id, seq) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    SESSION_ID,
                    tool_call_id,
                    kind,
                    str(art.get("title", ""))[:256],
                    str(art.get("mime", "application/json"))[:128],
                    payload_json,
                    None,
                    "",
                    now,
                    STEP_MESSAGE_ID,
                    seq,
                ),
            )
            slim.append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": kind,
                    "title": str(art.get("title", "")),
                    "mime": str(art.get("mime", "application/json")),
                    "payload": payload,
                    "file_name": "",
                    "size": 0,
                }
            )

        # 汇总消息：补 tool_call_json（合成 run_python 卡）+ 清洗英文推理段
        tool_call = {
            "id": f"plan-py-{tool_call_id}",
            "name": "run_python",
            "status": "success",
            "arguments": {},
            "result": {"artifacts": slim},
        }
        summ = db.execute(
            "SELECT content FROM chat_messages WHERE id=?", (SUMMARY_ID,)
        ).fetchone()
        if summ is not None:
            content = summ["content"] or ""
            if BODY_START in content:
                header = content.split("\n\n", 1)[0]
                content = f"{header}\n\n{content[content.index(BODY_START):]}"
            db.execute(
                "UPDATE chat_messages SET content=?, tool_call_json=? WHERE id=?",
                (content, json.dumps(tool_call, ensure_ascii=False), SUMMARY_ID),
            )
            print("汇总消息已补内联产物并清洗推理段")
        db.commit()
        print(f"修复完成：{len(artifacts)} 条产出物已归位")
    finally:
        db.close()


if __name__ == "__main__":
    main()
