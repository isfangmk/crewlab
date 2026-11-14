"""评测集、用例、运行记录与逐用例结果。

评测运行使用真实助理会话；用例断言只做确定性判断（关键词 / 工具 / 时延 /
token），不引入模型裁判，避免评测结果本身不可复现。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models_orm.base import AwareDateTime, Base


class EvalDataset(Base):
    """评测集：一组共享同一份助理配置的回归用例。"""

    __tablename__ = "eval_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    cases: Mapped[list[EvalCase]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list[EvalRun]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvalCase(Base):
    """单条用例：输入 + 确定性断言。"""

    __tablename__ = "eval_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON 数组；空数组 = 不校验该维度
    expected_keywords_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    forbidden_keywords_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    expected_tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    forbidden_tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 空 = 不限制
    max_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    dataset: Mapped[EvalDataset] = relationship(back_populates="cases")


class EvalRun(Base):
    """一次评测运行：发起时冻结用例与目标配置，跑完写汇总。"""

    __tablename__ = "eval_runs"

    run_uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 数据集改名/删除后仍能辨认历史运行
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # running | completed | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 发起时冻结的用例定义，避免运行过程中编辑用例导致结果不可解释
    cases_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    dataset: Mapped[EvalDataset] = relationship(back_populates="runs")
    results: Mapped[list[EvalCaseResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EvalCaseResult.created_at",
    )


class EvalCaseResult(Base):
    """逐用例结果：保留输入快照、输出、断言失败原因与观测关联。"""

    __tablename__ = "eval_case_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_uid: Mapped[str] = mapped_column(
        ForeignKey("eval_runs.run_uid", ondelete="CASCADE"), nullable=False, index=True
    )
    # 用例可能被删除；结果保留快照，case_id 只用于溯源
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    # passed | failed | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    failure_reasons_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    tool_names_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    run: Mapped[EvalRun] = relationship(back_populates="results")
