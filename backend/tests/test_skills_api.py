"""用户技能：CRUD + zip 导入导出往返（真实后端 + 真实 PG）。"""

from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile

import httpx
import pytest

from app.services.mcp_service import mcp_tool_wire_name
from tests.conftest import login_access_token

# 1x1 PNG：验证二进制资源走 base64 往返不失真
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _payload(key: str) -> dict:
    return {
        "key": key,
        "description": "集成测试用技能：解释慢查询并给出索引建议。",
        "instructions": "# 慢查询\n\n1. 先读 references/checks.md\n2. 再按 scripts/plan.sh 采集执行计划",
        "preapproved_tools": "",
        "risk": "safe",
        "group": "other",
        "tools": ["query_knowledge"],
        "inject_state": True,
        "references": [{"path": "checks.md", "text": "# 检查清单\n\n- 执行计划\n"}],
        "assets": [{"path": "logo.png", "base64": base64.b64encode(_PNG_BYTES).decode("ascii")}],
        "scripts": [{"path": "plan.sh", "text": "#!/bin/sh\necho explain\n"}],
    }


def _skill_zip(skill_md: str, extra: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        for path, content in (extra or {}).items():
            zf.writestr(path, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_skill_crud_and_zip_roundtrip(env: dict[str, str], api_base: str) -> None:
    key = f"it-skill-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post("/api/v1/skills", headers=headers, json=_payload(key))
        assert created.status_code == 200, created.text
        assert created.json()["source"] == "user"
        # 平台内新建的技能来源标记为 created
        assert created.json()["origin"] == "created"

        # 详情带资源正文，编辑回填不丢文件
        detail = await client.get(f"/api/v1/skills/{key}", headers=headers)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["tools"] == ["query_knowledge"]
        assert body["inject_state"] is True
        assert body["references"][0]["path"] == "checks.md"
        assert "执行计划" in body["references"][0]["text"]
        assert base64.b64decode(body["assets"][0]["base64"]) == _PNG_BYTES
        assert body["scripts"][0]["text"].startswith("#!/bin/sh")

        # 重名创建必须显式失败，不能静默覆盖
        dup = await client.post("/api/v1/skills", headers=headers, json=_payload(key))
        assert dup.status_code == 400, dup.text

        updated = dict(_payload(key))
        updated["instructions"] = "# 慢查询\n\n按时间线记录后再给索引建议。"
        patched = await client.patch(f"/api/v1/skills/{key}", headers=headers, json=updated)
        assert patched.status_code == 200, patched.text
        assert "按时间线记录" in patched.json()["instructions"]

        # 导出：ADK 目录结构 + 平台侧 metadata 原样往返
        exported = await client.get(f"/api/v1/skills/{key}/export", headers=headers)
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
            names = set(zf.namelist())
            assert {"SKILL.md", "references/checks.md", "assets/logo.png", "scripts/plan.sh"} <= names
            skill_md = zf.read("SKILL.md").decode("utf-8")
        assert f"name: {key}" in skill_md
        assert "risk: safe" in skill_md
        assert "group: other" in skill_md
        assert "adk_additional_tools" in skill_md

        # 导出的包能再解析（预览不写库）
        parsed = await client.post(
            "/api/v1/skills/parse",
            headers=headers,
            files={"file": ("skill.zip", exported.content, "application/zip")},
        )
        assert parsed.status_code == 200, parsed.text
        preview = parsed.json()
        assert preview["key"] == key
        assert preview["risk"] == "safe"
        assert preview["group"] == "other"
        assert [f["path"] for f in preview["scripts"]] == ["plan.sh"]

        deleted = await client.delete(f"/api/v1/skills/{key}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        gone = await client.get(f"/api/v1/skills/{key}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_skill_import_zip(env: dict[str, str], api_base: str) -> None:
    key = f"it-import-{uuid.uuid4().hex[:8]}"
    skill_md = f"""---
name: {key}
description: 从 zip 导入的技能
---

# 导入正文

读 references/guide.md。
"""
    raw = _skill_zip(skill_md, {"references/guide.md": "# 指南\n\n一步一步来。\n"})

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        # 包内没有 risk/group 时必须显式给出
        missing = await client.post(
            "/api/v1/skills/import",
            headers=headers,
            files={"file": ("skill.zip", raw, "application/zip")},
        )
        assert missing.status_code == 400, missing.text
        assert "风险等级" in missing.text

        imported = await client.post(
            "/api/v1/skills/import",
            headers=headers,
            files={"file": ("skill.zip", raw, "application/zip")},
            data={"risk": "safe", "group": "other"},
        )
        assert imported.status_code == 200, imported.text
        body = imported.json()
        assert body["key"] == key
        assert body["class"] == "safe"
        assert body["group"] == "other"
        assert body["references"][0]["path"] == "guide.md"
        # zip 导入的技能来源标记为 imported，与平台内新建区分
        assert body["origin"] == "imported"

        await client.delete(f"/api/v1/skills/{key}", headers=headers)


@pytest.mark.asyncio
async def test_skill_import_rejects_zip_slip(env: dict[str, str], api_base: str) -> None:
    key = f"it-slip-{uuid.uuid4().hex[:8]}"
    skill_md = f"---\nname: {key}\ndescription: 路径穿越样例\n---\n\n正文\n"
    raw = _skill_zip(skill_md, {"../escaped.txt": "should not land"})

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/skills/parse",
            headers=headers,
            files={"file": ("skill.zip", raw, "application/zip")},
        )
        assert resp.status_code == 400, resp.text


async def _pick_mcp_tool(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[str, str, dict] | None:
    """从真实 MCP 目录里挑一个可用工具；没有可用 Server 就让用例跳过。"""

    resp = await client.get("/api/v1/mcp/servers", headers=headers)
    assert resp.status_code == 200, resp.text
    for server in resp.json()["items"]:
        if not server["enabled"] or server["status"] != "ok":
            continue
        if server["tools_cache_json"] == "":
            continue
        tools = json.loads(server["tools_cache_json"])
        if not isinstance(tools, list) or len(tools) == 0:
            continue
        tool = tools[0]
        return server["name"], mcp_tool_wire_name(server["name"], tool["name"]), tool
    return None


@pytest.mark.asyncio
async def test_skill_declares_mcp_tool(env: dict[str, str], api_base: str) -> None:
    """技能可以声明 MCP 工具：目录可解析、参数来自 input_schema、助理自动并入白名单。"""

    key = f"it-mcp-skill-{uuid.uuid4().hex[:8]}"
    agent_name = f"mcp-agent-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        picked = await _pick_mcp_tool(client, headers)
        if picked is None:
            pytest.skip("没有可用（启用且探测成功）的 MCP Server")
        server_name, wire, tool = picked

        payload = _payload(key)
        payload["tools"] = [wire]
        created = await client.post("/api/v1/skills", headers=headers, json=payload)
        assert created.status_code == 200, created.text
        try:
            # 列表/详情都要标出 MCP 来源，否则前端只能显示一个裸名字
            summary = next(
                item
                for item in (await client.get("/api/v1/skills", headers=headers)).json()["items"]
                if item["key"] == key
            )
            assert summary["tools"] == [wire]
            assert summary["inventory_only"] is False
            assert summary["tool_refs"] == [
                {
                    "name": wire,
                    "kind": "mcp",
                    "server": server_name,
                    "available": True,
                    "description": tool["description"],
                }
            ]
            # 参数表由 MCP input_schema 转换，名字必须与 schema 属性一致
            assert [p["name"] for p in summary["params"]] == list(
                tool["input_schema"].get("properties", {})
            )

            # 目录里不存在的 MCP 工具必须显式拒绝，不能存进配置
            bad = dict(payload)
            bad["key"] = f"{key}-bad"
            bad["tools"] = [f"mcp__{server_name}__definitely_missing_tool"]
            rejected = await client.post("/api/v1/skills", headers=headers, json=bad)
            assert rejected.status_code == 400, rejected.text
            assert "MCP" in rejected.json()["detail"]

            # 助理勾选该技能：声明的 MCP 工具自动并入白名单
            agent_payload = {
                "name": agent_name,
                "description": "技能带头挂 MCP 工具的助理",
                "when_to_use": "",
                "system_prompt": "你是运维助理。",
                "critical_reminder": "",
                "allowed_tools": ["query_knowledge"],
                "disallowed_tools": [],
                "permission_mode": "",
                "model": "",
                "max_turns": 0,
                "output_format": "free",
                "examples": [],
                "knowledge_scope": {"paths": [], "doc_ids": []},
                "skills": [key],
            }
            agent = await client.post("/api/v1/agents/custom", headers=headers, json=agent_payload)
            assert agent.status_code == 200, agent.text
            assert wire in agent.json()["tools"]

            # 技能声明的工具不可屏蔽：显式 400
            blocked = await client.patch(
                f"/api/v1/agents/custom/{agent_name}",
                headers=headers,
                json={**agent_payload, "disallowed_tools": [wire]},
            )
            assert blocked.status_code == 400, blocked.text
            assert "由技能声明" in blocked.json()["detail"]
        finally:
            await client.delete(f"/api/v1/agents/custom/{agent_name}", headers=headers)
            await client.delete(f"/api/v1/skills/{key}", headers=headers)
