"""前置节点上下文渲染：把前驱节点的结论/要点/工具轨迹渲染成可引用文本行。

节点执行与最终汇总共用同一份证据视图，保证汇总看到的东西和节点一致。
按任务裁剪，避免前置上下文变成 token 黑洞。
"""

from __future__ import annotations

import json
from typing import Any


def compact(value: Any, limit: int = 800) -> str:
    """证据/工具值转单行文本，超限截断。"""

    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def render_prior_context(items: list[dict[str, Any]]) -> list[str]:
    """把前置节点的 conclusion/findings/tool_trace 渲染成可引用文本行。"""

    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        name = str(item.get("name") or f"节点{index}")
        lines.append(f"{index}. [{name}]")
        conclusion = str(item.get("conclusion") or "").strip()
        if conclusion:
            lines.append(f"   结论：{compact(conclusion, 1200)}")
        findings = item.get("findings")
        if isinstance(findings, list) and findings:
            lines.append("   要点与证据：")
            for finding in findings[:12]:
                if not isinstance(finding, dict):
                    continue
                title = compact(finding.get("title") or "", 120)
                detail = compact(finding.get("detail") or "", 500)
                evidence = finding.get("evidence")
                ev_text = ""
                if isinstance(evidence, list):
                    ev_text = "；".join(
                        compact(
                            f"call_id={ev.get('call_id', '')} "
                            f"excerpt={ev.get('excerpt', '')}",
                            200,
                        )
                        for ev in evidence[:3]
                        if isinstance(ev, dict)
                    )
                lines.append(f"     - {title}：{detail}" + (f"（{ev_text}）" if ev_text else ""))
        trace = item.get("tool_trace")
        if isinstance(trace, list) and trace:
            lines.append("   工具数据：")
            for call in trace[:16]:
                if not isinstance(call, dict):
                    continue
                tool_name = compact(call.get("name") or "", 80)
                args = compact(call.get("args"), 300)
                response = compact(call.get("response"), 1500)
                lines.append(f"     - {tool_name}(args={args}) -> {response}")
    return lines
