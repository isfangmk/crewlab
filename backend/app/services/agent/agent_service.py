"""用户自定义助理 CRUD，以及内置 default 解析。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.naming import adk_identifier, root_adk_name
from app.models.agent import UserAgent
from app.models.mcp import McpServer
from app.services.knowledge.knowledge_scope import (
    KnowledgeScope,
    empty_scope,
    parse_scope,
    scope_to_wire,
)
from app.services.platform import app_settings_service
from app.tools.builtin_names import all_builtin_tool_names

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

DEFAULT_AGENT_NAME = "default"
# 内置平台助手：代替用户检索并调用平台接口，不落库（与 default 同机制）
PLATFORM_AGENT_NAME = "platform-assistant"
# 平台助手的工具白名单；不含 mcp__ 项，故按契约不挂载任何 MCP
PLATFORM_TOOL_NAMES = ("search_platform_api", "call_platform_api", "ask_user")

# 子助理委托链最大层数：根助理 → 子 → 孙 → 曾孙，运行期装配同口径截断
MAX_DELEGATION_DEPTH = 3

# 允许的权限模式：
# "" = 不限（默认）
# "read-only" = 只读：该助理调用的 MCP 工具一律需要人工确认，不允许静默产生副作用
PERMISSION_MODES = ("", "read-only")

# 输出格式约束：文案由服务端按枚举生成，前端只能选，不能自由填写
OUTPUT_FORMATS = ("free", "json", "checklist")
_OUTPUT_FORMAT_INSTRUCTIONS = {
    "free": "",
    "json": "回答必须是一个 JSON 对象：不加 Markdown 代码块包裹，不附加任何解释文字。",
    "checklist": "回答必须是 Markdown 清单：每行一条可执行结论，必要时在条目末尾标注依据来源。",
}

MAX_EXAMPLES = 3
MAX_EXAMPLE_CHARS = 2000
MAX_DISPLAY_NAME_CHARS = 64


@dataclass(frozen=True)
class ResolvedAgent:
    """运行时解析后的助理配置。"""

    name: str
    description: str
    when_to_use: str
    system_prompt: str
    critical_reminder: str
    tools: list[str]
    disallowed_tools: list[str]
    permission_mode: str
    model: str
    max_turns: int
    source: str
    # 工具结果回喂模型的字符上限；None = 继承全局默认
    max_tool_result_chars: int | None = None
    # 上下文压缩触发 token 阈值；None = 继承全局默认，0 = 关闭
    context_compaction_threshold: int | None = None
    # 面向用户的显示名称；为空时前端回退到助理唯一标识
    display_name: str = ""
    output_format: str = "free"
    examples: tuple[dict[str, str], ...] = ()
    knowledge_scope: KnowledgeScope = empty_scope()
    # 勾选的技能 key；None = 继承全部技能（磁盘 + 用户），[] = 不装配任何技能
    skills: list[str] | None = None
    # 勾选的内置工具名；完整名单=全部装配，[]=不装配任何（默认取完整名单）
    builtin_tools: list[str] = field(default_factory=lambda: all_builtin_tool_names())
    # 子助理委托：委托链在保存时校验（存在性 / 防环）
    sub_agents: tuple[str, ...] = ()


def _parse_str_list(raw: str, field: str) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError(f"{field} 必须是 JSON 数组")
    out: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str) or item == "":
            raise TypeError(f"{field}[{i}] 必须是非空字符串")
        out.append(item)
    return out


def _dump_str_list(items: list[str], field: str) -> str:
    for i, item in enumerate(items):
        if not isinstance(item, str) or item == "":
            raise ValueError(f"{field}[{i}] 必须是非空字符串")
    return json.dumps(items, ensure_ascii=False)


def validate_output_format(value: str) -> None:
    """输出格式只能是已知枚举；未知值不能被静默当成自由回答。"""

    if value not in OUTPUT_FORMATS:
        raise ValueError(
            f"output_format 非法: {value!r}（允许：{', '.join(OUTPUT_FORMATS)}）"
        )


def parse_examples(raw: object) -> tuple[dict[str, str], ...]:
    """校验少样本示例：最多 3 条，输入输出都必填且有长度上限（超限报错不截断）。"""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TypeError("examples 必须是数组")
    if len(raw) > MAX_EXAMPLES:
        raise ValueError(f"examples 最多 {MAX_EXAMPLES} 条")
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"examples[{i}] 必须是对象")
        unknown = set(item.keys()) - {"input", "output"}
        if unknown:
            raise ValueError(f"examples[{i}] 含未知字段: {sorted(unknown)}")
        pair: dict[str, str] = {}
        for key in ("input", "output"):
            value = item.get(key)
            if not isinstance(value, str) or value.strip() == "":
                raise ValueError(f"examples[{i}].{key} 必须是非空字符串")
            if len(value) > MAX_EXAMPLE_CHARS:
                raise ValueError(
                    f"examples[{i}].{key} 超过 {MAX_EXAMPLE_CHARS} 字符"
                )
            pair[key] = value
        out.append(pair)
    return tuple(out)


def _dump_examples(examples: tuple[dict[str, str], ...]) -> str:
    return json.dumps(list(examples), ensure_ascii=False)


async def _current_time_section(db: AsyncSession) -> str:
    """当前时间上下文：每回合现算，智能体的时间判断锚定在当前时刻与时区上。"""

    try:
        label, tz = await app_settings_service.get_system_timezone(db)
    except ValueError:
        # 时区设置缺失或非法：退到默认 UTC+08:00，不让整段人设装配失败
        label = app_settings_service.DEFAULT_SYSTEM_TIMEZONE
        tz = app_settings_service.SYSTEM_TIMEZONES[label]
    now = datetime.now(tz)
    weekday = "星期" + "一二三四五六日"[now.weekday()]
    return (
        "## 当前时间\n"
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}（{label}，{weekday}）\n"
        "所有相对时间（今天/明天/几点等）都以该时间与用户所在时区解释。"
    )


async def compose_persona(db: AsyncSession, agent: ResolvedAgent) -> str:
    """运行时人设：当前时间 → 正文 → 必须遵守 → 输出格式 → 示例 → 知识范围。

    对话 / 审批恢复 / Playground / 子 Agent 四条路径都走这里，
    避免不同路径注入不同的人设片段。
    """

    validate_output_format(agent.output_format)
    parts: list[str] = [await _current_time_section(db)]
    if agent.system_prompt != "":
        parts.append(agent.system_prompt)
    if agent.critical_reminder != "":
        parts.append("## 必须遵守\n" + agent.critical_reminder)
    format_hint = _OUTPUT_FORMAT_INSTRUCTIONS[agent.output_format]
    if format_hint != "":
        parts.append("## 输出格式\n" + format_hint)
    if len(agent.examples) > 0:
        blocks: list[str] = ["## 示例"]
        for i, example in enumerate(agent.examples, start=1):
            blocks.append(
                f"### 示例 {i}\n用户：{example['input']}\n助理：{example['output']}"
            )
        parts.append("\n\n".join(blocks))
    scope = agent.knowledge_scope
    if not scope.is_empty:
        lines = ["## 知识范围"]
        if len(scope.paths) > 0:
            lines.append("只可检索以下目录（含子目录）：" + "、".join(scope.paths))
        if len(scope.doc_ids) > 0:
            lines.append("只可检索以下文档：" + "、".join(scope.doc_ids))
        lines.append(
            "调用 query_knowledge 时不得越过上述范围；范围外内容按未命中处理，"
            "如实说明没有检索到，不得臆测或改用其它来源冒充知识库结果。"
        )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def builtin_always_tool_count() -> int:
    """内置常驻工具数：所有普通助理都会装配，用于卡片展示。"""

    from app.tools.catalog import BUILTIN_TOOL_SPECS

    return sum(1 for spec in BUILTIN_TOOL_SPECS if spec["mount"] == "always")


def mcp_server_count(agent: ResolvedAgent, available_servers: set[str]) -> int:
    """助理实际挂载的 MCP Server 个数。

    默认助理挂全部可用 Server；其余助理按自身白名单，只数目录里仍存在的 Server。
    平台助手不挂 MCP。
    """

    if agent.name == PLATFORM_AGENT_NAME:
        return 0
    if agent.name == DEFAULT_AGENT_NAME:
        return len(available_servers)
    names = mcp_server_names_from_tools(agent.tools)
    return len(names & available_servers)


def agent_to_wire(
    agent: ResolvedAgent,
    *,
    mcp_servers: int = 0,
) -> dict:
    """把解析后的助理转成列表/详情响应字段。

    mcp_servers 是该助理实际挂载的 MCP Server 个数，由调用方按目录算好。
    """

    return {
        "name": agent.name,
        "display_name": agent.display_name or agent.name,
        "description": agent.description,
        "when_to_use": agent.when_to_use,
        "tools": agent.tools,
        "builtin_tool_count": len(agent.builtin_tools),
        "builtin_tools": list(agent.builtin_tools),
        "mcp_server_count": mcp_servers,
        "disallowed_tools": agent.disallowed_tools,
        "permission_mode": agent.permission_mode,
        "model": agent.model,
        "max_turns": agent.max_turns,
        "max_tool_result_chars": agent.max_tool_result_chars,
        "context_compaction_threshold": agent.context_compaction_threshold,
        "system_prompt": agent.system_prompt,
        "critical_reminder": agent.critical_reminder,
        "output_format": agent.output_format,
        "examples": [dict(item) for item in agent.examples],
        "knowledge_scope": scope_to_wire(agent.knowledge_scope),
        "skills": None if agent.skills is None else list(agent.skills),
        "sub_agents": list(agent.sub_agents),
        "source": agent.source,
    }


def builtin_default_agent() -> ResolvedAgent:
    """工作台内置默认助理（不落库）。"""

    return ResolvedAgent(
        name=DEFAULT_AGENT_NAME,
        display_name="默认助理",
        description="默认助理，适合通用问答与临时排查。",
        when_to_use="未指定专用助理时使用。",
        system_prompt="",
        critical_reminder="",
        tools=[],
        disallowed_tools=[],
        permission_mode="",
        model="",
        max_turns=0,
        source="builtin",
    )


# 能力边界：只说能做什么、约束是什么，不写"什么提问用哪个工具"的路由规则——
# 工具选择由模型按用户提问自行判断（项目硬规则）。
_PLATFORM_AGENT_PERSONA = (
    "你是 AI 工作台的平台助手，代替用户完成平台上的查询与管理工作。"
    "你可以检索平台提供的各项操作，按用户意图执行查询或变更；"
    "所有操作都以当前登录用户身份进行，权限与该用户在页面上直接操作完全相同，"
    "没有越权能力——用户无权访问的接口，你也访问不到。"
    "新增、修改、删除等变更操作需要用户确认后才会执行，查询操作直接执行。"
    "执行前先确认用户意图与关键参数（如目标资源的标识、要改成的取值）明确；"
    "信息不足时向用户澄清，不要臆造标识或填入占位值。"
    "操作完成后，用一句话说明做了什么、结果如何；失败时如实说明原因。"
)
_PLATFORM_AGENT_REMINDER = (
    "绝不使用超出当前用户权限的操作；关键参数不确定时先问用户，不要猜。"
    "变更操作的确认结果以用户为准，不要替用户决定。"
)


def builtin_platform_agent() -> ResolvedAgent:
    """内置平台助手（不落库）：挂通用内置工具 + 平台操作元工具，按用户身份调用平台接口。"""

    # 平台助手装配除 web_search（per_turn）与技能管理工具外的全部内置工具；
    # 技能管理工具不挂（平台助手只挂知识检索技能，不创建/安装技能）。
    platform_builtin_tools = [
        name
        for name in all_builtin_tool_names()
        if name not in ("create_skill", "install_skill_from_url", "install_skill_from_zip")
    ]
    return ResolvedAgent(
        name=PLATFORM_AGENT_NAME,
        display_name="平台助手",
        description="代替用户在平台上完成查询、新增、修改、删除等操作，权限与本人直接操作一致",
        when_to_use="需要在对话里完成平台上的管理操作时使用。",
        system_prompt=_PLATFORM_AGENT_PERSONA,
        critical_reminder=_PLATFORM_AGENT_REMINDER,
        tools=list(PLATFORM_TOOL_NAMES),
        disallowed_tools=[],
        permission_mode="",
        model="",
        max_turns=0,
        source="builtin",
        builtin_tools=platform_builtin_tools,
        # 只挂知识检索技能（其它内置技能若有，也不给平台助手）
        skills=["knowledge-search"],
    )


def _row_to_resolved(row: UserAgent) -> ResolvedAgent:
    return ResolvedAgent(
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        when_to_use=row.when_to_use,
        system_prompt=row.system_prompt,
        critical_reminder=row.critical_reminder,
        tools=_parse_str_list(row.allowed_tools_json, "allowed_tools_json"),
        disallowed_tools=_parse_str_list(row.disallowed_tools_json, "disallowed_tools_json"),
        permission_mode=row.permission_mode,
        model=row.model,
        max_turns=row.max_turns,
        max_tool_result_chars=row.max_tool_result_chars,
        context_compaction_threshold=row.context_compaction_threshold,
        source="user",
        output_format=row.output_format,
        examples=_parse_examples_json(row.examples_json),
        knowledge_scope=_parse_scope_json(row.knowledge_scope_json),
        skills=_parse_skills_json(row.skills_json),
        builtin_tools=_parse_builtin_tools_json(row.builtin_tools_json),
        sub_agents=(
            tuple(_parse_str_list(row.sub_agents_json, "sub_agents_json"))
            if row.sub_agents_json
            else ()
        ),
    )


def validate_agent_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ValueError("name 须为小写字母开头的标识（字母/数字/_/-，最长 64）")
    if name == DEFAULT_AGENT_NAME:
        raise ValueError("name 不能为保留名 default")
    if name == PLATFORM_AGENT_NAME:
        raise ValueError("name 不能为保留名 platform-assistant")


def validate_max_tool_result_chars(value: int | None) -> int | None:
    """助理级工具结果上限：None = 继承全局；否则须落在合法范围。"""
    if value is None:
        return None
    from app.services.platform.app_settings_service import (
        MAX_TOOL_RESULT_CHARS_MAX,
        MAX_TOOL_RESULT_CHARS_MIN,
    )

    if not (MAX_TOOL_RESULT_CHARS_MIN <= value <= MAX_TOOL_RESULT_CHARS_MAX):
        raise ValueError(
            f"max_tool_result_chars 范围 {MAX_TOOL_RESULT_CHARS_MIN}..{MAX_TOOL_RESULT_CHARS_MAX}"
        )
    return value


def validate_context_compaction_threshold(value: int | None) -> int | None:
    """助理级上下文压缩阈值：None = 继承全局，0 = 关闭；负数/超上限非法。"""
    if value is None:
        return None
    from app.services.platform.app_settings_service import (
        CONTEXT_COMPACTION_THRESHOLD_MAX,
        CONTEXT_COMPACTION_THRESHOLD_MIN,
    )

    if not (CONTEXT_COMPACTION_THRESHOLD_MIN <= value <= CONTEXT_COMPACTION_THRESHOLD_MAX):
        raise ValueError(
            f"context_compaction_threshold 范围 "
            f"{CONTEXT_COMPACTION_THRESHOLD_MIN}..{CONTEXT_COMPACTION_THRESHOLD_MAX}"
        )
    return value


def validate_display_name(display_name: str) -> str:
    """显示名称只用于展示：非空、限长，不能包含换行等控制字符。"""

    value = display_name.strip()
    if value == "":
        raise ValueError("display_name 不能为空")
    if len(value) > MAX_DISPLAY_NAME_CHARS:
        raise ValueError(f"display_name 最长 {MAX_DISPLAY_NAME_CHARS} 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("display_name 不能包含控制字符")
    return value


def validate_permission_mode(mode: str) -> None:
    """权限模式取值必须已知；未知值不能被静默当成「不限」。"""

    if mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode 非法: {mode!r}（允许："
            f"{', '.join(repr(m) for m in PERMISSION_MODES if m != '')} 或空串）"
        )


async def validate_sub_agents(
    db: AsyncSession,
    parent_name: str,
    sub_names: tuple[str, ...],
) -> None:
    """子助理委托校验：存在、不自引用、不重复、ADK 名不撞车、无环、深度不超限。

    新增一条委托边不只影响被编辑助理自己的链：它的祖先链也会变长，
    所以还要沿反向引用检查所有祖先的委托深度。
    """

    if len(set(sub_names)) != len(sub_names):
        duplicates = sorted({name for name in sub_names if sub_names.count(name) > 1})
        raise ValueError(f"子助理不能重复: {duplicates}")

    sanitized: dict[str, str] = {}
    for name in sub_names:
        if name == parent_name:
            raise ValueError(f"不能把助理委托给自己: {name}")
        try:
            await get_agent(db, name)
        except LookupError as exc:
            raise ValueError(f"子助理不存在: {name}") from exc
        cleaned = adk_identifier(name)
        if cleaned in sanitized:
            raise ValueError(
                f"子助理名称净化后撞名: {sanitized[cleaned]} 与 {name} 都是 {cleaned}"
            )
        sanitized[cleaned] = name
    root_ident = root_adk_name(parent_name)
    for cleaned, name in sanitized.items():
        if cleaned == root_ident:
            raise ValueError(f"子助理 {name} 的 ADK 名称与父助理冲突: {cleaned}")

    # 新增边只存在于 parent_name 之下；其余节点沿用已保存的委托列表
    proposed: dict[str, tuple[str, ...]] = {parent_name: tuple(sub_names)}
    agent_cache: dict[str, ResolvedAgent] = {}

    async def children_of(name: str) -> tuple[str, ...]:
        if name in proposed:
            return proposed[name]
        if name not in agent_cache:
            try:
                agent_cache[name] = await get_agent(db, name)
            except LookupError as exc:
                raise ValueError(f"子助理不存在: {name}") from exc
        return agent_cache[name].sub_agents

    memo: dict[str, int] = {}

    async def depth_of(node: str, path: set[str]) -> int:
        if node in path:
            raise ValueError(f"子助理委托成环: {node} 的委托链回到了自己")
        if node in memo:
            return memo[node]
        kids = await children_of(node)
        if len(kids) == 0:
            memo[node] = 0
            return 0
        value = 1 + max([await depth_of(kid, path | {node}) for kid in kids])
        memo[node] = value
        return value

    parent_depth = await depth_of(parent_name, set())
    if parent_depth > MAX_DELEGATION_DEPTH:
        raise ValueError(
            f"子助理委托链最多 {MAX_DELEGATION_DEPTH} 层，"
            f"{parent_name} 当前为 {parent_depth} 层"
        )

    # 反向引用：谁（递归）委托了 parent_name
    rows = (await db.scalars(select(UserAgent))).all()
    parents_by_child: dict[str, list[str]] = {}
    for row in rows:
        children = (
            _parse_str_list(row.sub_agents_json, "sub_agents_json")
            if row.sub_agents_json
            else []
        )
        for child in children:
            parents_by_child.setdefault(child, []).append(row.name)
    ancestors: set[str] = set()
    stack = list(parents_by_child.get(parent_name, []))
    while stack:
        cur = stack.pop()
        if cur in ancestors or cur == parent_name:
            continue
        ancestors.add(cur)
        stack.extend(parents_by_child.get(cur, []))
    for root in ancestors:
        depth = await depth_of(root, set())
        if depth > MAX_DELEGATION_DEPTH:
            raise ValueError(
                f"子助理委托链最多 {MAX_DELEGATION_DEPTH} 层，"
                f"{root} 将变为 {depth} 层"
            )


def agent_disallowed_tool_names(agent: ResolvedAgent) -> set[str]:
    """助理级禁用工具名集合（wire 名）。"""

    names: set[str] = set()
    for key in agent.disallowed_tools:
        if not isinstance(key, str) or key == "":
            raise ValueError("disallowed_tools 含无效工具名")
        names.add(key)
    return names


async def validate_skill_binding(
    db: AsyncSession,
    skills: list[str] | None,
    allowed_tools: list[str],
    disallowed_tools: list[str],
) -> tuple[list[str], list[str]]:
    """校验技能勾选：技能 key 必须存在。"""

    await validate_skill_keys(db, skills)
    return list(allowed_tools), list(disallowed_tools)


def validate_builtin_tool_names(builtin_tools: list[str]) -> None:
    """勾选的内置工具名必须在目录中；名单即装配范围，空数组表示不装配任何。"""

    from app.tools.catalog import BUILTIN_SELECTABLE_TOOL_NAMES

    unknown = [name for name in builtin_tools if name not in BUILTIN_SELECTABLE_TOOL_NAMES]
    if unknown:
        raise ValueError(f"内置工具不存在: {unknown}")


async def validate_skill_keys(db: AsyncSession, skills: list[str] | None) -> None:
    """勾选的技能 key 必须存在；None = 继承全部，无需校验。"""

    from app.services.skills.skills_service import list_skill_entries

    if skills is None:
        return
    by_key = {entry.skill.name for entry in await list_skill_entries(db)}
    missing = [key for key in skills if key not in by_key]
    if missing:
        raise ValueError(f"技能不存在: {missing}")


async def list_agents(db: AsyncSession) -> list[ResolvedAgent]:
    result = await db.scalars(select(UserAgent).order_by(UserAgent.name.asc()))
    rows = list(result.all())
    items = [builtin_default_agent(), builtin_platform_agent()]
    items.extend(_row_to_resolved(r) for r in rows)
    return items


async def get_agent(db: AsyncSession, name: str) -> ResolvedAgent:
    if name == DEFAULT_AGENT_NAME:
        return builtin_default_agent()
    if name == PLATFORM_AGENT_NAME:
        return builtin_platform_agent()
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    return _row_to_resolved(row)


def _parse_scope_json(raw: str) -> KnowledgeScope:
    return parse_scope(json.loads(raw))


def _parse_examples_json(raw: str) -> tuple[dict[str, str], ...]:
    return parse_examples(json.loads(raw))


def _parse_skills_json(raw: str | None) -> list[str] | None:
    """技能勾选：NULL = 继承全部；JSON 数组 = 只装配这些（可为空数组）。"""

    if raw is None:
        return None
    return _parse_str_list(raw, "skills_json")


def _dump_skills(skills: list[str] | None) -> str | None:
    if skills is None:
        return None
    return _dump_str_list(skills, "skills")


def _parse_builtin_tools_json(raw: str) -> list[str]:
    """内置工具勾选：JSON 数组，名单即装配范围（可为空数组）。"""

    return _parse_str_list(raw, "builtin_tools_json")


def _dump_builtin_tools(builtin_tools: list[str]) -> str:
    return _dump_str_list(builtin_tools, "builtin_tools")


async def create_user_agent(
    db: AsyncSession,
    *,
    name: str,
    display_name: str,
    description: str,
    when_to_use: str,
    system_prompt: str,
    critical_reminder: str,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    permission_mode: str,
    model: str,
    max_turns: int,
    max_tool_result_chars: int | None = None,
    context_compaction_threshold: int | None = None,
    output_format: str = "free",
    examples: tuple[dict[str, str], ...] = (),
    knowledge_scope: KnowledgeScope = empty_scope(),
    skills: list[str] | None = None,
    builtin_tools: list[str] = all_builtin_tool_names(),
    sub_agents: tuple[str, ...] = (),
) -> ResolvedAgent:
    validate_agent_name(name)
    clean_display_name = validate_display_name(display_name)
    await validate_sub_agents(db, name, sub_agents)
    if description == "":
        raise ValueError("description 不能为空")
    if system_prompt == "":
        raise ValueError("system_prompt 不能为空")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    max_tool_result_chars = validate_max_tool_result_chars(max_tool_result_chars)
    context_compaction_threshold = validate_context_compaction_threshold(
        context_compaction_threshold
    )
    validate_permission_mode(permission_mode)
    validate_output_format(output_format)
    scope = parse_scope(knowledge_scope)
    existing = await db.get(UserAgent, name)
    if existing is not None:
        raise ValueError(f"助理已存在: {name}")
    row = UserAgent(
        name=name,
        display_name=clean_display_name,
        description=description,
        when_to_use=when_to_use,
        system_prompt=system_prompt,
        critical_reminder=critical_reminder,
        allowed_tools_json=_dump_str_list(allowed_tools, "allowed_tools"),
        disallowed_tools_json=_dump_str_list(disallowed_tools, "disallowed_tools"),
        permission_mode=permission_mode,
        model=model,
        max_turns=max_turns,
        max_tool_result_chars=max_tool_result_chars,
        context_compaction_threshold=context_compaction_threshold,
        output_format=output_format,
        examples_json=_dump_examples(examples),
        knowledge_scope_json=json.dumps(scope_to_wire(scope), ensure_ascii=False),
        skills_json=_dump_skills(skills),
        builtin_tools_json=_dump_builtin_tools(builtin_tools),
        sub_agents_json=(
            _dump_str_list(list(sub_agents), "sub_agents") if sub_agents else None
        ),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建用户助理 name=%s tools=%s sub_agents=%s",
        name,
        len(allowed_tools),
        len(sub_agents),
    )
    return _row_to_resolved(row)


async def update_user_agent(
    db: AsyncSession,
    name: str,
    *,
    display_name: str,
    description: str,
    when_to_use: str,
    system_prompt: str,
    critical_reminder: str,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    permission_mode: str,
    model: str,
    max_turns: int,
    max_tool_result_chars: int | None = None,
    context_compaction_threshold: int | None = None,
    output_format: str = "free",
    examples: tuple[dict[str, str], ...] = (),
    knowledge_scope: KnowledgeScope = empty_scope(),
    skills: list[str] | None = None,
    builtin_tools: list[str] = all_builtin_tool_names(),
    sub_agents: tuple[str, ...] = (),
) -> ResolvedAgent:
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    clean_display_name = validate_display_name(display_name)
    await validate_sub_agents(db, name, sub_agents)
    if description == "":
        raise ValueError("description 不能为空")
    if system_prompt == "":
        raise ValueError("system_prompt 不能为空")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    max_tool_result_chars = validate_max_tool_result_chars(max_tool_result_chars)
    context_compaction_threshold = validate_context_compaction_threshold(
        context_compaction_threshold
    )
    validate_permission_mode(permission_mode)
    validate_output_format(output_format)
    scope = parse_scope(knowledge_scope)
    row.display_name = clean_display_name
    row.description = description
    row.when_to_use = when_to_use
    row.system_prompt = system_prompt
    row.critical_reminder = critical_reminder
    row.allowed_tools_json = _dump_str_list(allowed_tools, "allowed_tools")
    row.disallowed_tools_json = _dump_str_list(disallowed_tools, "disallowed_tools")
    row.permission_mode = permission_mode
    row.model = model
    row.max_turns = max_turns
    row.max_tool_result_chars = max_tool_result_chars
    row.context_compaction_threshold = context_compaction_threshold
    row.output_format = output_format
    row.examples_json = _dump_examples(examples)
    row.knowledge_scope_json = json.dumps(scope_to_wire(scope), ensure_ascii=False)
    row.skills_json = _dump_skills(skills)
    row.builtin_tools_json = _dump_builtin_tools(builtin_tools)
    row.sub_agents_json = (
        _dump_str_list(list(sub_agents), "sub_agents") if sub_agents else None
    )
    await db.commit()
    await db.refresh(row)
    logger.info("更新用户助理 name=%s", name)
    return _row_to_resolved(row)


async def resolve_draft_agent(
    db: AsyncSession,
    *,
    name: str,
    display_name: str,
    description: str,
    when_to_use: str,
    system_prompt: str,
    critical_reminder: str,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    permission_mode: str,
    model: str,
    max_turns: int,
    max_tool_result_chars: int | None = None,
    context_compaction_threshold: int | None = None,
    output_format: str = "free",
    examples: tuple[dict[str, str], ...] = (),
    knowledge_scope: KnowledgeScope = empty_scope(),
    skills: list[str] | None = None,
    builtin_tools: list[str] = all_builtin_tool_names(),
    sub_agents: tuple[str, ...] = (),
) -> ResolvedAgent:
    """把未保存的草稿解析成可运行的助理配置（不落库）。

    与 create_user_agent 走同一套校验口径，只是不插入 user_agents，
    供 Playground 试跑草稿时使用。
    """

    validate_agent_name(name)
    clean_display_name = validate_display_name(display_name)
    await validate_sub_agents(db, name, sub_agents)
    if description == "":
        raise ValueError("description 不能为空")
    if system_prompt == "":
        raise ValueError("system_prompt 不能为空")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    max_tool_result_chars = validate_max_tool_result_chars(max_tool_result_chars)
    context_compaction_threshold = validate_context_compaction_threshold(
        context_compaction_threshold
    )
    validate_permission_mode(permission_mode)
    validate_output_format(output_format)
    scope = parse_scope(knowledge_scope)
    return ResolvedAgent(
        name=name,
        display_name=clean_display_name,
        description=description,
        when_to_use=when_to_use,
        system_prompt=system_prompt,
        critical_reminder=critical_reminder,
        tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        permission_mode=permission_mode,
        model=model,
        max_turns=max_turns,
        max_tool_result_chars=max_tool_result_chars,
        source="user",
        output_format=output_format,
        examples=examples,
        knowledge_scope=scope,
        skills=skills,
        builtin_tools=builtin_tools,
        sub_agents=sub_agents,
    )


async def delete_user_agent(db: AsyncSession, name: str) -> None:
    if name == DEFAULT_AGENT_NAME:
        raise ValueError("不能删除内置助理 default")
    if name == PLATFORM_AGENT_NAME:
        raise ValueError("不能删除内置助理 platform-assistant")
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    # 仍被其他助理委托时禁止删除：否则父助理的委托链会在运行期断掉
    referrers: list[str] = []
    for other in (await db.scalars(select(UserAgent))).all():
        if other.name == name or not other.sub_agents_json:
            continue
        if name in _parse_str_list(other.sub_agents_json, "sub_agents_json"):
            referrers.append(other.name)
    if referrers:
        raise ValueError(f"以下助理仍在委托它，先解除委托再删除: {sorted(referrers)}")
    await db.delete(row)
    await db.commit()
    logger.info("删除用户助理 name=%s", name)


def mcp_server_names_from_tools(tools: list[str]) -> set[str]:
    """从 tools 白名单提取 MCP server 名；空白名单不挂任何 MCP。"""

    from app.services.mcp.mcp_service import mcp_server_name_from_wire_name

    names: set[str] = set()
    for key in tools:
        if not key.startswith("mcp__"):
            continue
        names.add(mcp_server_name_from_wire_name(key))
    return names


def effective_allowed_tools(agent: ResolvedAgent) -> list[str]:
    """MCP 工具白名单：助理勾选的 MCP 工具；空列表表示不挂任何 MCP。

    内置工具常驻全挂、不进白名单；白名单只决定哪些 MCP Server 进检索目录。
    """

    return list(agent.tools)


def filter_mcp_servers_by_tools(
    servers: list[McpServer],
    allowed_tools: list[str],
) -> list[McpServer]:
    """按工具白名单筛选可挂载的 MCP Server（按 mcp__<server>__* 的 server 段）。"""

    allowed_names = mcp_server_names_from_tools(allowed_tools)
    # wire 的 server 段就是 McpServer.name 原值，直接比对。
    return [s for s in servers if s.name in allowed_names]


def filter_mcp_servers_for_agent(
    servers: list[McpServer],
    agent: ResolvedAgent,
) -> list[McpServer]:
    """按助理 tools 白名单筛选可挂载的 MCP Server。"""

    filtered = filter_mcp_servers_by_tools(servers, agent.tools)
    logger.info(
        "按助理筛选 MCP agent=%s allowed_servers=%s matched=%s",
        agent.name,
        sorted(mcp_server_names_from_tools(agent.tools)),
        [s.name for s in filtered],
    )
    return filtered
