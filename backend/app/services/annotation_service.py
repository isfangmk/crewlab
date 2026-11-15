"""人工标注：对评测结果给出 good / needs_work / bad 判定。

自动判分之外留人工口径：同一结果允许多人标注，一致性统计用于校准
标注标准；认领按「当前标注人尚未标注的结果」推进，避免重复劳动。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalAnnotation, EvalCaseResult
from app.services import eval_service

LABELS = ("good", "needs_work", "bad")
MAX_COMMENT_CHARS = 2000


def validate_annotation_fields(*, label: str, comment: str) -> dict[str, Any]:
    clean_label = label.strip()
    if clean_label not in LABELS:
        raise ValueError(f"label 只能是 {' / '.join(LABELS)}")
    clean_comment = comment.strip()
    if len(clean_comment) > MAX_COMMENT_CHARS:
        raise ValueError(f"备注最长 {MAX_COMMENT_CHARS} 个字符")
    return {"label": clean_label, "comment": clean_comment}


def annotation_to_wire(row: EvalAnnotation, result: EvalCaseResult | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "result_id": row.result_id,
        "annotator_user_id": row.annotator_user_id,
        "label": row.label,
        "comment": row.comment,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if result is not None:
        out["case_name"] = result.case_name
        out["run_uid"] = result.run_uid
        out["result_status"] = result.status
    return out


async def get_annotation(db: AsyncSession, annotation_id: str) -> EvalAnnotation:
    row = await db.get(EvalAnnotation, annotation_id)
    if row is None:
        raise LookupError(f"标注不存在: {annotation_id}")
    return row


async def _list_run_results(db: AsyncSession, run_uid: str) -> list[EvalCaseResult]:
    await eval_service.get_run(db, run_uid)
    return list(
        (
            await db.scalars(
                select(EvalCaseResult)
                .where(EvalCaseResult.run_uid == run_uid)
                .order_by(EvalCaseResult.created_at, EvalCaseResult.id)
            )
        ).all()
    )


async def submit_annotation(
    db: AsyncSession,
    *,
    result_id: str,
    annotator_user_id: int | None,
    label: str,
    comment: str,
) -> EvalAnnotation:
    """提交或覆盖本人对同一结果的标注（一人一结果一条）。"""

    fields = validate_annotation_fields(label=label, comment=comment)
    result = await db.get(EvalCaseResult, result_id)
    if result is None:
        raise LookupError(f"评测结果不存在: {result_id}")
    row = await db.scalar(
        select(EvalAnnotation).where(
            EvalAnnotation.result_id == result_id,
            EvalAnnotation.annotator_user_id == annotator_user_id,
        )
    )
    if row is None:
        row = EvalAnnotation(
            id=uuid4().hex,
            result_id=result_id,
            annotator_user_id=annotator_user_id,
        )
        db.add(row)
    row.label = fields["label"]
    row.comment = fields["comment"]
    await db.commit()
    await db.refresh(row)
    return row


async def update_annotation(
    db: AsyncSession,
    annotation_id: str,
    *,
    label: str,
    comment: str,
) -> EvalAnnotation:
    fields = validate_annotation_fields(label=label, comment=comment)
    row = await get_annotation(db, annotation_id)
    row.label = fields["label"]
    row.comment = fields["comment"]
    await db.commit()
    await db.refresh(row)
    return row


async def list_annotations(db: AsyncSession, run_uid: str) -> list[dict[str, Any]]:
    results = await _list_run_results(db, run_uid)
    if not results:
        return []
    result_map = {item.id: item for item in results}
    rows = list(
        (
            await db.scalars(
                select(EvalAnnotation)
                .where(EvalAnnotation.result_id.in_(list(result_map)))
                .order_by(EvalAnnotation.created_at, EvalAnnotation.id)
            )
        ).all()
    )
    return [annotation_to_wire(item, result_map.get(item.result_id)) for item in rows]


async def claim_next(
    db: AsyncSession, *, run_uid: str, annotator_user_id: int | None
) -> dict[str, Any] | None:
    """认领下一条待标注结果：跳过本人已标注的，按结果顺序推进。"""

    results = await _list_run_results(db, run_uid)
    if not results:
        return None
    done = set(
        (
            await db.scalars(
                select(EvalAnnotation.result_id).where(
                    EvalAnnotation.result_id.in_([item.id for item in results]),
                    EvalAnnotation.annotator_user_id == annotator_user_id,
                )
            )
        ).all()
    )
    counts = {
        row[0]: int(row[1])
        for row in (
            await db.execute(
                select(EvalAnnotation.result_id, func.count(EvalAnnotation.id))
                .where(EvalAnnotation.result_id.in_([item.id for item in results]))
                .group_by(EvalAnnotation.result_id)
            )
        ).all()
    }
    for result in results:
        if result.id in done:
            continue
        return {
            "result": eval_service.result_to_wire(result),
            "annotation_count": counts.get(result.id, 0),
        }
    return None


async def consistency(db: AsyncSession, run_uid: str) -> dict[str, Any]:
    """多人标注一致性：全部同标签的多标注结果占比。"""

    results = await _list_run_results(db, run_uid)
    result_ids = [item.id for item in results]
    rows = list(
        (
            await db.scalars(
                select(EvalAnnotation).where(
                    EvalAnnotation.result_id.in_(result_ids)
                ).order_by(EvalAnnotation.created_at, EvalAnnotation.id)
            )
        ).all()
    ) if result_ids else []
    by_result: dict[str, list[str]] = {}
    label_counts: dict[str, int] = {label: 0 for label in LABELS}
    for row in rows:
        by_result.setdefault(row.result_id, []).append(row.label)
        if row.label in label_counts:
            label_counts[row.label] += 1
    annotated = len(by_result)
    multi = {rid: labels for rid, labels in by_result.items() if len(labels) >= 2}
    agreed = sum(1 for labels in multi.values() if len(set(labels)) == 1)
    return {
        "results_total": len(results),
        "annotated_results": annotated,
        "multi_annotated": len(multi),
        "agreement_rate": (agreed / len(multi)) if multi else None,
        "label_counts": label_counts,
    }
