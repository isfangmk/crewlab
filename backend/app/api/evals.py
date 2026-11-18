"""评测集与评测运行 API。

读接口对已登录用户开放；写接口要求非只读角色。评测运行会消耗真实模型，
因此发起前把用例与目标配置冻结进运行记录，结果可按 trace 回溯。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services.eval import (
    annotation_service,
    eval_gate_service,
    eval_judge_service,
    eval_runner,
    eval_service,
    eval_share_service,
    redteam_library,
)
from app.services.observability.audit_service import write_audit
from app.services.observability.redaction import redact_value
from app.services.platform.auth_service import AuthUser
from app.services.platform.llm_config import resolve_model

router = APIRouter(tags=["evals"])


class DatasetCreateRequest(BaseModel):
    """新建评测集的请求体。"""

    name: str = Field(min_length=1, max_length=128, description="评测集名称，1~128 个字符")
    description: str = Field(default="", description="评测集说明，可留空")


class DatasetUpdateRequest(BaseModel):
    """更新评测集的请求体；字段留空表示不修改该项。"""

    name: str | None = Field(default=None, min_length=1, max_length=128, description="新的评测集名称，1~128 个字符；留空不修改")
    description: str | None = Field(default=None, description="新的评测集说明；留空不修改")
    # 门禁配置：基线运行 + 回归上限 + 通过率下限
    baseline_run_uid: str | None = Field(default=None, description="作为门禁基线的运行 uid；留空不启用基线对比")
    gate_max_regressions: int | None = Field(default=None, ge=0, description="门禁允许的回归用例数上限，需配合基线运行；留空不修改")
    gate_min_pass_rate: float | None = Field(default=None, ge=0, le=1, description="门禁通过率下限，0~1；留空不修改")


class CaseCreateRequest(BaseModel):
    """新增评测用例的请求体。"""

    name: str = Field(min_length=1, max_length=128, description="用例名称，1~128 个字符")
    input: str = Field(min_length=1, description="用例输入，即送给被测模型的提示词，至少 1 个字符")
    kind: str = Field(default="normal", description="用例类型：normal 常规回归，redteam 对抗（越狱/注入/滥用）")
    attack_type: str = Field(default="", description="攻击类型，最长 64 字符；对抗（redteam）用例必填，便于筛选统计")
    safety_keywords: list[str] = Field(default_factory=list, description="安全关键词断言列表，命中表示可能不安全")
    expected_keywords: list[str] = Field(default_factory=list, description="期望命中的关键词列表，输出须包含其中全部")
    forbidden_keywords: list[str] = Field(default_factory=list, description="禁止出现的关键词列表，输出命中任一即失败")
    expected_tools: list[str] = Field(default_factory=list, description="期望调用的工具名列表")
    forbidden_tools: list[str] = Field(default_factory=list, description="禁止调用的工具名列表")
    max_duration_ms: int | None = Field(default=None, ge=0, description="单次运行时长上限（毫秒），超出判失败；留空不限制")
    max_total_tokens: int | None = Field(default=None, ge=0, description="单次运行 token 消耗上限，超出判失败；留空不限制")
    enabled: bool = Field(default=True, description="是否启用该用例，停用后不参与评测运行")
    sort_order: int | None = Field(default=None, description="用例排序权重，数值越小越靠前；留空按默认顺序")


class CaseUpdateRequest(BaseModel):
    """更新评测用例的请求体；字段留空表示沿用当前值。"""

    name: str | None = Field(default=None, min_length=1, max_length=128, description="新的用例名称，1~128 个字符；留空沿用当前值")
    input: str | None = Field(default=None, min_length=1, description="新的用例输入（提示词），至少 1 个字符；留空沿用当前值")
    kind: str | None = Field(default=None, description="新的用例类型：normal 常规回归，redteam 对抗；留空沿用当前值")
    attack_type: str | None = Field(default=None, description="新的攻击类型，最长 64 字符；留空沿用当前值")
    safety_keywords: list[str] | None = Field(default=None, description="新的安全关键词断言列表；留空沿用当前值")
    expected_keywords: list[str] | None = Field(default=None, description="新的期望关键词列表；留空沿用当前值")
    forbidden_keywords: list[str] | None = Field(default=None, description="新的禁止关键词列表；留空沿用当前值")
    expected_tools: list[str] | None = Field(default=None, description="新的期望工具名列表；留空沿用当前值")
    forbidden_tools: list[str] | None = Field(default=None, description="新的禁止工具名列表；留空沿用当前值")
    max_duration_ms: int | None = Field(default=None, ge=0, description="新的时长上限（毫秒）；留空沿用当前值")
    max_total_tokens: int | None = Field(default=None, ge=0, description="新的 token 消耗上限；留空沿用当前值")
    enabled: bool | None = Field(default=None, description="是否启用；留空沿用当前值")
    sort_order: int | None = Field(default=None, description="新的排序权重；留空沿用当前值")


class RunCreateRequest(BaseModel):
    """发起评测运行的请求体。"""

    agent_id: str = Field(min_length=1, max_length=128, description="被测助理 id，1~128 个字符")
    # 都留空 = 助理模型或全局默认
    provider: str = Field(default="", description="覆盖的大模型提供方；留空则用助理配置或全局默认")
    model: str = Field(default="", description="覆盖的大模型名称；留空则用助理配置或全局默认")


class JudgeConfigRequest(BaseModel):
    """模型裁判（LLM-as-judge）评分配置。"""

    enabled: bool = Field(default=False, description="是否启用模型裁判评分")
    rubric: str = Field(default="", description="评分标准（提示词），描述如何给输出打分")
    judge_provider: str = Field(default="", description="裁判模型提供方；留空则用默认配置")
    judge_model: str = Field(default="", description="裁判模型名称；留空则用默认配置")
    pass_score: float = Field(default=3.0, ge=0, le=eval_judge_service.MAX_SCORE, description="通过分数线，0~5；达到该分判为通过")


class AnnotationCreateRequest(BaseModel):
    """提交一条评测结果标注的请求体。"""

    result_id: str = Field(min_length=1, max_length=32, description="被标注的评测结果 id")
    label: str = Field(min_length=1, max_length=32, description="标注标签，如 pass/fail 或自定义分类")
    comment: str = Field(default="", description="标注备注说明，可留空")


class AnnotationUpdateRequest(BaseModel):
    """更新一条评测标注的请求体。"""

    label: str = Field(min_length=1, max_length=32, description="新的标注标签")
    comment: str = Field(default="", description="新的标注备注说明，可留空")


def _dataset_detail(dataset: Any, cases: list[Any], runs: list[Any]) -> dict[str, Any]:
    return {
        **eval_service.dataset_to_wire(dataset),
        "cases": [eval_service.case_to_wire(item) for item in cases],
        "runs": [eval_service.run_to_wire(item) for item in runs],
    }


@router.get("/evals/datasets")
async def list_datasets(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出全部评测集。"""
    items = await eval_service.list_datasets(db)
    return {"items": items, "total": len(items)}


@router.post("/evals/datasets")
async def create_dataset(
    body: DatasetCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """新建一个评测集。"""
    try:
        row = await eval_service.create_dataset(
            db,
            name=body.name,
            description=body.description,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_create",
        resource_type="eval_dataset",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return eval_service.dataset_to_wire(row)


@router.get("/evals/datasets/{dataset_id}")
async def get_dataset_detail(
    dataset_id: int = Path(description="要查看的评测集 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """获取评测集详情，含其下全部用例与运行记录。"""
    try:
        dataset = await eval_service.get_dataset(db, dataset_id)
        cases = await eval_service.list_cases(db, dataset_id)
        runs = await eval_service.list_runs(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _dataset_detail(dataset, cases, runs)


@router.patch("/evals/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: int = Path(description="要更新的评测集 id"),
    body: DatasetUpdateRequest = Body(description="要更新的字段，留空项不修改"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """更新评测集信息及门禁配置。"""
    try:
        row = await eval_service.update_dataset(
            db,
            dataset_id,
            name=body.name,
            description=body.description,
            baseline_run_uid=body.baseline_run_uid,
            gate_max_regressions=body.gate_max_regressions,
            gate_min_pass_rate=body.gate_min_pass_rate,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_update",
        resource_type="eval_dataset",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return eval_service.dataset_to_wire(row)


@router.delete("/evals/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: int = Path(description="要删除的评测集 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """删除评测集（含其下用例与运行记录），不可恢复。"""
    try:
        row = await eval_service.get_dataset(db, dataset_id)
        name = row.name
        await eval_service.delete_dataset(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_delete",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"name": name},
    )
    return {"ok": True}


@router.post("/evals/datasets/{dataset_id}/cases")
async def create_case(
    dataset_id: int = Path(description="要新增用例的评测集 id"),
    body: CaseCreateRequest = Body(description="新用例的内容与断言"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """在评测集下新增一条评测用例。"""
    try:
        fields = eval_service.validate_case_fields(
            name=body.name,
            input_text=body.input,
            kind=body.kind,
            attack_type=body.attack_type,
            safety_keywords=body.safety_keywords,
            expected_keywords=body.expected_keywords,
            forbidden_keywords=body.forbidden_keywords,
            expected_tools=body.expected_tools,
            forbidden_tools=body.forbidden_tools,
            max_duration_ms=body.max_duration_ms,
            max_total_tokens=body.max_total_tokens,
        )
        row = await eval_service.create_case(
            db,
            dataset_id,
            fields=fields,
            enabled=body.enabled,
            sort_order=body.sort_order,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_create",
        resource_type="eval_case",
        resource_id=str(row.id),
        detail={"dataset_id": dataset_id, "name": row.name},
    )
    return eval_service.case_to_wire(row)


@router.patch("/evals/cases/{case_id}")
async def update_case(
    case_id: int = Path(description="要更新的评测用例 id"),
    body: CaseUpdateRequest = Body(description="要更新的字段，留空项沿用当前值"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """部分更新评测用例；请求未带的字段沿用当前值，再整体校验。"""
    try:
        existing = await eval_service.get_case(db, case_id)
        current = eval_service.case_to_wire(existing)
        # 部分更新：请求里没带的字段沿用当前值，再整体走一次校验
        fields = eval_service.validate_case_fields(
            name=body.name if body.name is not None else current["name"],
            input_text=body.input if body.input is not None else current["input"],
            kind=body.kind if body.kind is not None else current["kind"],
            attack_type=(
                body.attack_type
                if body.attack_type is not None
                else current["attack_type"]
            ),
            safety_keywords=(
                body.safety_keywords
                if body.safety_keywords is not None
                else current["safety_keywords"]
            ),
            expected_keywords=(
                body.expected_keywords
                if body.expected_keywords is not None
                else current["expected_keywords"]
            ),
            forbidden_keywords=(
                body.forbidden_keywords
                if body.forbidden_keywords is not None
                else current["forbidden_keywords"]
            ),
            expected_tools=(
                body.expected_tools
                if body.expected_tools is not None
                else current["expected_tools"]
            ),
            forbidden_tools=(
                body.forbidden_tools
                if body.forbidden_tools is not None
                else current["forbidden_tools"]
            ),
            max_duration_ms=(
                body.max_duration_ms
                if "max_duration_ms" in body.model_fields_set
                else current["max_duration_ms"]
            ),
            max_total_tokens=(
                body.max_total_tokens
                if "max_total_tokens" in body.model_fields_set
                else current["max_total_tokens"]
            ),
        )
        row = await eval_service.update_case(
            db,
            case_id,
            fields=fields,
            enabled=body.enabled,
            sort_order=body.sort_order,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_update",
        resource_type="eval_case",
        resource_id=str(row.id),
        detail={"dataset_id": row.dataset_id, "name": row.name},
    )
    return eval_service.case_to_wire(row)


@router.delete("/evals/cases/{case_id}")
async def delete_case(
    case_id: int = Path(description="要删除的评测用例 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """删除一条评测用例，不可恢复。"""
    try:
        row = await eval_service.get_case(db, case_id)
        dataset_id = row.dataset_id
        name = row.name
        await eval_service.delete_case(db, case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_delete",
        resource_type="eval_case",
        resource_id=str(case_id),
        detail={"dataset_id": dataset_id, "name": name},
    )
    return {"ok": True}


@router.post("/evals/datasets/{dataset_id}/runs")
async def start_run(
    dataset_id: int = Path(description="要发起运行的评测集 id"),
    body: RunCreateRequest = Body(description="被测助理与模型覆盖配置"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """对评测集发起一次运行：冻结用例与目标配置后异步执行，返回运行记录。"""
    try:
        dataset = await eval_service.get_dataset(db, dataset_id)
        agent_id, provider, model = await eval_service.resolve_eval_target(
            db,
            agent_id=body.agent_id,
            provider=body.provider,
            model=body.model,
        )
        run = await eval_service.create_run(
            db,
            dataset=dataset,
            agent_id=agent_id,
            provider=provider,
            model=model,
            created_by=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    eval_runner.start_run(run.run_uid)
    await write_audit(
        actor_user_id=user.id,
        action="eval.run",
        resource_type="eval_run",
        resource_id=run.run_uid,
        detail={
            "dataset_id": run.dataset_id,
            "dataset_name": run.dataset_name,
            "agent_id": run.agent_id,
            "provider": run.provider,
            "model": run.model,
            "total_cases": run.total_cases,
        },
    )
    return eval_service.run_to_wire(run)


@router.get("/evals/runs/{run_uid}")
async def get_run(
    run_uid: str = Path(description="要查看的运行 uid"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """获取运行详情，含用例快照与逐条评测结果。"""
    try:
        return await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/evals/runs/{run_uid}/compare")
async def compare_run(
    run_uid: str = Path(description="要对比的目标运行 uid"),
    baseline: str = Query(min_length=1, description="作为基线对比的运行 uid，至少 1 个字符"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """将目标运行与基线运行对比，给出通过率/回归等差异。"""
    try:
        return await eval_service.compare_runs(db, run_uid, baseline)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evals/datasets/{dataset_id}/judge-config")
async def get_judge_config(
    dataset_id: int = Path(description="要查询裁判配置的评测集 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """读取评测集的模型裁判配置；未配置时返回关闭状态的默认值。"""
    try:
        await eval_service.get_dataset(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    config = await eval_judge_service.get_judge_config(db, dataset_id)
    if config is None:
        # 未配置 = 关闭状态的默认口径，前端直接可编辑
        return {
            "dataset_id": dataset_id,
            "enabled": False,
            "rubric": "",
            "judge_provider": "",
            "judge_model": "",
            "pass_score": 3.0,
            "updated_at": None,
        }
    return eval_judge_service.judge_config_to_wire(config)


@router.put("/evals/datasets/{dataset_id}/judge-config")
async def save_judge_config(
    dataset_id: int = Path(description="要保存裁判配置的评测集 id"),
    body: JudgeConfigRequest = Body(description="裁判评分配置"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """保存评测集的模型裁判配置；指定裁判模型时先校验其可解析。"""
    try:
        await eval_service.get_dataset(db, dataset_id)
        fields = eval_judge_service.validate_judge_fields(
            enabled=body.enabled,
            rubric=body.rubric,
            judge_provider=body.judge_provider,
            judge_model=body.judge_model,
            pass_score=body.pass_score,
        )
        # 指定了裁判模型就先校验可解析，避免运行期才暴雷
        if fields["judge_provider"] != "":
            await resolve_model(db, fields["judge_provider"], fields["judge_model"])
        row = await eval_judge_service.save_judge_config(db, dataset_id, fields=fields)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.judge_config_update",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"enabled": row.enabled, "pass_score": row.pass_score},
    )
    return eval_judge_service.judge_config_to_wire(row)


@router.get("/evals/runs/{run_uid}/results/{result_id}/judge-scores")
async def list_judge_scores(
    run_uid: str = Path(description="评测运行 uid"),
    result_id: str = Path(description="要查看评分的评测结果 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出某条评测结果的全部模型裁判评分明细。"""
    try:
        detail = await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result_id not in {item["id"] for item in detail["results"]}:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    items = await eval_judge_service.list_judge_scores(db, result_id)
    return {"items": items, "total": len(items)}


@router.post("/evals/annotations")
async def create_annotation(
    body: AnnotationCreateRequest = Body(description="要提交的标注内容"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """对一条评测结果提交标注（同一结果每人仅一条，重复提交会更新）。"""
    try:
        row = await annotation_service.submit_annotation(
            db,
            result_id=body.result_id,
            annotator_user_id=user.id,
            label=body.label,
            comment=body.comment,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.annotation",
        resource_type="eval_annotation",
        resource_id=row.id,
        detail={"result_id": row.result_id, "label": row.label},
    )
    return annotation_service.annotation_to_wire(row)


@router.post("/evals/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: str = Path(description="要更新的标注 id"),
    body: AnnotationUpdateRequest = Body(description="新的标签与备注"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按 id 更新一条标注的标签与备注。"""
    try:
        row = await annotation_service.update_annotation(
            db, annotation_id, label=body.label, comment=body.comment
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.annotation",
        resource_type="eval_annotation",
        resource_id=row.id,
        detail={"result_id": row.result_id, "label": row.label},
    )
    return annotation_service.annotation_to_wire(row)


@router.get("/evals/runs/{run_uid}/annotations")
async def list_annotations(
    run_uid: str = Path(description="评测运行 uid"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出运行的全部标注及标注一致性统计。"""
    try:
        items = await annotation_service.list_annotations(db, run_uid)
        stats = await annotation_service.consistency(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": items, "total": len(items), "consistency": stats}


@router.get("/evals/annotations/queue")
async def claim_next_annotation(
    run_uid: str = Query(min_length=1, description="要领取标注任务的运行 uid，至少 1 个字符"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """领取下一条待标注结果；队列已清空时返回空载荷而非错误。"""
    try:
        item = await annotation_service.claim_next(
            db, run_uid=run_uid, annotator_user_id=user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item is None:
        # 队列清空是正常状态，返回空载荷而不是错误
        return {"result": None, "annotation_count": 0}
    return item


@router.post("/evals/runs/{run_uid}/gate")
async def evaluate_gate(
    run_uid: str = Path(description="要评估门禁的运行 uid"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """按评测集门禁配置评估运行是否通过（回归数、通过率等）。"""
    try:
        result = await eval_gate_service.evaluate_gate(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.gate",
        resource_type="eval_run",
        resource_id=run_uid,
    )
    return result


@router.get("/evals/runs/{run_uid}/export")
async def export_run(
    run_uid: str = Path(description="要导出的运行 uid"),
    format: str = Query(default="json", pattern="^(json|csv)$", description="导出格式：json 结构化文件，csv 表格（Excel 可直接打开）"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> Response:
    """导出运行结果为文件；内容按统一规则脱敏后返回。"""
    try:
        detail = await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.export",
        resource_type="eval_run",
        resource_id=run_uid,
        detail={"format": format},
    )
    # 导出内容按统一脱敏规则处理，避免把密钥类字段带出系统
    run_meta = {key: value for key, value in detail.items() if key != "results"}
    results = [redact_value(item) for item in detail["results"]]
    if format == "json":
        payload = redact_value({**run_meta, "results": results})
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="eval-{run_uid}.json"'
            },
        )
    columns = [
        "case_name",
        "status",
        "input",
        "output",
        "failure_reasons",
        "tool_names",
        "duration_ms",
        "total_tokens",
        "trace_id",
        "session_id",
        "created_at",
    ]
    buffer = io.StringIO()
    # utf-8-sig 让 Excel 直接双击打开不乱码
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for item in results:
        row = []
        for column in columns:
            value = item.get(column)
            if isinstance(value, list):
                value = " / ".join(str(part) for part in value)
            row.append(value if value is not None else "")
        writer.writerow(row)
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eval-{run_uid}.csv"'},
    )


@router.post("/evals/datasets/{dataset_id}/redteam/seed")
async def seed_redteam_cases(
    dataset_id: int = Path(description="要植入对抗用例的评测集 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """从红队用例库批量植入对抗用例到评测集，返回新建数量与用例列表。"""
    try:
        created = await redteam_library.seed_redteam_cases(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.redteam_seed",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"created": len(created)},
    )
    return {"created": len(created), "items": [eval_service.case_to_wire(row) for row in created]}


class ShareCreateRequest(BaseModel):
    """创建运行只读分享的请求体。"""

    expires_in_hours: int = Field(default=24, ge=1, le=720, description="分享有效期（小时），范围 1~720（30 天）")


@router.post("/evals/runs/{run_uid}/share")
async def create_run_share(
    run_uid: str = Path(description="要分享的运行 uid"),
    body: ShareCreateRequest = Body(description="分享有效期"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """创建运行只读分享；明文 token 只此一次返回，库里存哈希。"""

    try:
        await eval_service.get_run(db, run_uid)
        raw, row = await eval_share_service.create_eval_share(
            db,
            run_uid=run_uid,
            created_by=user.id,
            expires_in_hours=body.expires_in_hours,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.share",
        resource_type="eval_run",
        resource_id=run_uid,
        detail={"expires_in_hours": body.expires_in_hours},
    )
    return {
        "token": raw,
        "expires_at": row.expires_at.isoformat(),
        "run_uid": run_uid,
    }


@router.get("/evals/shares/{token}")
async def read_run_share(
    token: str = Path(description="分享链接中的明文 token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """只读分享：无需登录；载荷整体脱敏后返回，含内嵌评分与门禁结论。"""

    try:
        share = await eval_share_service.resolve_eval_share(db, token)
        detail = await eval_service.get_run_detail(db, share.run_uid)
        dataset = await eval_service.get_dataset(db, detail["dataset_id"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # judge 分内嵌到结果行，公开页一次拿全，不必逐条再调接口
    results: list[dict] = []
    for item in detail["results"]:
        scores = await eval_judge_service.list_judge_scores(db, item["id"])
        results.append({**item, "judge_scores": scores})
    # 门禁 / 标注一致性是可选块：没配基线或没标注就不给
    try:
        gate = await eval_gate_service.evaluate_gate(db, share.run_uid)
    except (LookupError, ValueError):
        gate = None
    stats = await annotation_service.consistency(db, share.run_uid)
    payload = {
        "expires_at": share.expires_at.isoformat(),
        "dataset": eval_service.dataset_to_wire(dataset),
        "run": {
            key: value
            for key, value in detail.items()
            if key not in ("results", "cases_snapshot")
        },
        "cases_snapshot": detail.get("cases_snapshot", []),
        "results": results,
        "gate": gate,
        "consistency": stats,
    }
    return redact_value(payload)
