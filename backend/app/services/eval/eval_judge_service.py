"""LLM 评分（judge）：对评测结果按 rubric 打维度分。

确定性断言之外，用一个裁判模型对输出质量打分；判分不可复现，因此
留存 judge_model 与逐维度理由，解析失败显式落 parse_error，不静默吞。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalCaseResult, EvalJudgeConfig, EvalJudgeScore
from app.services.chat import chat_service
from app.services.platform import llm_config

logger = logging.getLogger(__name__)

# 单维度分数上限；裁判输出必须落在 [0, MAX_SCORE]
MAX_SCORE = 5.0


@dataclass(frozen=True)
class JudgeDimension:
    dimension: str
    score: float
    reason: str


def build_judge_prompt(*, rubric: str, case_input: str, output: str, tool_names: list[str]) -> list[dict]:
    """组装裁判消息：系统提示给评分口径，用户消息给被评内容。"""

    system = (
        "你是评测裁判。请对下面的助理回答质量打分。\n"
        "评分标准（rubric）：\n"
        f"{rubric if rubric.strip() else '按正确性、完整性、相关性打分。'}\n\n"
        f"每个维度给 0-{int(MAX_SCORE)} 的整数分，附一句中文理由。\n"
        "只输出一个 JSON 对象，不要 Markdown 代码块：\n"
        '{"dimensions": [{"dimension": "维度名", "score": 0, "reason": "理由"}]}'
    )
    tools = "、".join(tool_names) if tool_names else "无"
    user = (
        f"【用户输入】\n{case_input}\n\n"
        f"【助理回答】\n{output}\n\n"
        f"【调用的工具】\n{tools}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_judge_response(content: str) -> list[JudgeDimension]:
    """解析裁判返回的 JSON；结构不符直接抛错，由调用方落 parse_error。"""

    data = json.loads(content)
    if not isinstance(data, dict):
        raise TypeError("裁判返回必须是 JSON 对象")
    rows = data.get("dimensions")
    if not isinstance(rows, list) or len(rows) == 0:
        raise ValueError("裁判返回缺少 dimensions 数组")
    out: list[JudgeDimension] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"dimensions[{i}] 必须是对象")
        dimension = row.get("dimension")
        score = row.get("score")
        reason = row.get("reason", "")
        if not isinstance(dimension, str) or dimension == "":
            raise ValueError(f"dimensions[{i}].dimension 非法")
        if not isinstance(score, (int, float)):
            raise TypeError(f"dimensions[{i}].score 必须是数字")
        score_f = float(score)
        if score_f < 0 or score_f > MAX_SCORE:
            raise ValueError(f"dimensions[{i}].score 超出 0-{int(MAX_SCORE)}")
        out.append(JudgeDimension(dimension=dimension, score=score_f, reason=str(reason)))
    return out


def average_score(dims: list[JudgeDimension]) -> float:
    if not dims:
        return 0.0
    return sum(d.score for d in dims) / len(dims)


async def judge_result(
    db: AsyncSession,
    result: EvalCaseResult,
    config: EvalJudgeConfig,
    *,
    provider_id: str,
    model: str,
) -> list[EvalJudgeScore]:
    """对单条结果打分；返回待落库的 EvalJudgeScore 列表（不在此提交）。"""

    # 裁判模型优先用配置里的；留空则跟随评测目标模型
    use_provider = config.judge_provider or provider_id
    use_model = config.judge_model or model
    provider = await llm_config.resolve_model(db, use_provider, use_model)
    litellm_model = llm_config.compose_litellm_model(
        provider_id=provider.provider_id,
        model_prefix=provider.model_prefix,
        model=use_model,
    )
    extra = llm_config.provider_extra_body(provider)

    # 用工具入参/出参补强评分上下文；取不到就只用输出正文
    tool_context = ""
    if result.session_id:
        try:
            messages = await chat_service.list_messages(db, result.session_id)
            # 工具名 / 调用 id 存在 tool_call_json 里，消息行本体没有这两个字段
            tool_lines = []
            for m in messages:
                if m.role != "tool":
                    continue
                raw = json.loads(m.tool_call_json) if m.tool_call_json else {}
                meta = raw if isinstance(raw, dict) else {}
                label = str(meta.get("name") or meta.get("id") or meta.get("tool_call_id") or "")
                if not label and not m.content:
                    continue
                tool_lines.append(f"- {label or 'tool'}: {str(m.content)[:200]}")
            if tool_lines:
                tool_context = "\n【工具返回摘要】\n" + "\n".join(tool_lines[:20])
        except Exception:  # noqa: BLE001 - 评分上下文缺失不阻塞判分
            logger.warning("judge 取工具上下文失败 result_id=%s", result.id)

    messages = build_judge_prompt(
        rubric=config.rubric,
        case_input=result.input,
        output=result.output + tool_context,
        tool_names=json.loads(result.tool_names_json),
    )

    import litellm

    try:
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=messages,
            api_base=provider.base_url,
            api_key=provider.api_key,
            timeout=60,
            extra_body=extra if extra else None,
            response_format={"type": "json_object"},
        )
        # litellm 把流式 wrapper 并进了返回类型注解；这里是非流式响应，choices 实际存在
        choices = getattr(resp, "choices") or []
        content = choices[0].message.content or ""
        dims = parse_judge_response(content)
    except Exception as exc:  # noqa: BLE001 - 判分失败显式落 parse_error
        logger.exception("LLM 评分失败 result_id=%s", result.id)
        return [
            EvalJudgeScore(
                id=uuid4().hex,
                result_id=result.id,
                dimension="parse",
                score=0.0,
                reason=f"评分失败: {type(exc).__name__}: {exc}",
                judge_model="parse_error",
            )
        ]

    return [
        EvalJudgeScore(
            id=uuid4().hex,
            result_id=result.id,
            dimension=d.dimension,
            score=d.score,
            reason=d.reason,
            judge_model=f"{use_provider}/{use_model}",
        )
        for d in dims
    ]


def judge_config_to_wire(config: EvalJudgeConfig) -> dict[str, Any]:
    return {
        "dataset_id": config.dataset_id,
        "enabled": config.enabled,
        "rubric": config.rubric,
        "judge_provider": config.judge_provider,
        "judge_model": config.judge_model,
        "pass_score": config.pass_score,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def judge_score_to_wire(score: EvalJudgeScore) -> dict[str, Any]:
    return {
        "id": score.id,
        "result_id": score.result_id,
        "dimension": score.dimension,
        "score": score.score,
        "reason": score.reason,
        "judge_model": score.judge_model,
        "created_at": score.created_at.isoformat() if score.created_at else None,
    }


def validate_judge_fields(
    *,
    enabled: bool,
    rubric: str,
    judge_provider: str,
    judge_model: str,
    pass_score: float,
) -> dict[str, Any]:
    clean_rubric = rubric.strip()
    if len(clean_rubric) > 4000:
        raise ValueError("rubric 最长 4000 个字符")
    clean_provider = judge_provider.strip()
    clean_model = judge_model.strip()
    # 留空 = 跟随评测目标模型；给一半是配置歧义，直接失败
    if (clean_provider == "") != (clean_model == ""):
        raise ValueError("judge_provider 与 judge_model 必须同时提供或同时留空")
    if pass_score < 0 or pass_score > MAX_SCORE:
        raise ValueError(f"pass_score 必须在 0-{int(MAX_SCORE)} 之间")
    return {
        "enabled": bool(enabled),
        "rubric": clean_rubric,
        "judge_provider": clean_provider,
        "judge_model": clean_model,
        "pass_score": float(pass_score),
    }


async def get_judge_config(
    db: AsyncSession, dataset_id: int
) -> EvalJudgeConfig | None:
    return await db.scalar(
        select(EvalJudgeConfig).where(EvalJudgeConfig.dataset_id == dataset_id)
    )


async def save_judge_config(
    db: AsyncSession, dataset_id: int, *, fields: dict[str, Any]
) -> EvalJudgeConfig:
    """更新或首次创建评分配置（1:1 绑评测集）。"""

    row = await get_judge_config(db, dataset_id)
    if row is None:
        row = EvalJudgeConfig(dataset_id=dataset_id)
        db.add(row)
    row.enabled = fields["enabled"]
    row.rubric = fields["rubric"]
    row.judge_provider = fields["judge_provider"]
    row.judge_model = fields["judge_model"]
    row.pass_score = fields["pass_score"]
    await db.commit()
    await db.refresh(row)
    return row


async def list_judge_scores(
    db: AsyncSession, result_id: str
) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.scalars(
                select(EvalJudgeScore)
                .where(EvalJudgeScore.result_id == result_id)
                .order_by(EvalJudgeScore.created_at, EvalJudgeScore.id)
            )
        ).all()
    )
    return [judge_score_to_wire(item) for item in rows]
