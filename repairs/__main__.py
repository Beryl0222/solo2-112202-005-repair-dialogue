"""演示入口：python3 -m repairs [案号]

读取业务资料包，输出每个案号的接力计划与用户可查状态。
"""
from __future__ import annotations

import sys

from pack_check import read_tables

from .assets import AssetPool
from .casefile import CaseFile, NextStepPlan
from .knowledge import CHECK_NAMES
from .stations import Station


def render_plan(case_file: CaseFile, plan: NextStepPlan) -> str:
    lines = [f"【{plan.case} 接力计划】{plan.decision}——{plan.summary}"]
    lines.append("路径评估（原因候选来自检修手册）：")
    for outcome in plan.routes:
        lines.append(f"  - {outcome.name}（{outcome.route}）：{outcome.state}")
        for reason in outcome.reasons:
            lines.append(f"      {reason}")
    if plan.do_now:
        lines.append("现在可做：")
        for check in plan.do_now:
            lines.append(f"  - [{check.kind}] {check.name} @ {check.station}"
                         f"——{check.purpose}")
    if plan.blocked:
        lines.append("被阻塞：")
        for check in plan.blocked:
            lines.append(f"  - [{check.kind}] {check.name}")
            for blocker in check.blockers:
                lines.append(f"      {blocker}")
    if plan.questions:
        lines.append("需要追问用户（仅限能改变下一步的）：")
        lines += [f"  - {q}" for q in plan.questions]
    else:
        lines.append("无需追问用户：缺的观察站点都能补，不占用用户一次回复。")
    if plan.notices:
        lines.append("交接通知：")
        lines += [f"  - {n.station} {n.action}：{n.reason}" for n in plan.notices]
    if plan.warnings:
        lines.append("动手前提醒：")
        lines += [f"  - {w}" for w in plan.warnings]
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    tables = read_tables()
    stations = [Station.from_row(r, CHECK_NAMES) for r in tables["stations.tsv"]]
    assets = AssetPool.from_rows(tables["loan_log.tsv"])
    cases = argv[1:] or sorted({r["case"] for r in tables["conversation.tsv"]})
    for case in cases:
        case_file = CaseFile.build(case, tables)
        plan = case_file.plan(stations, assets)
        print(render_plan(case_file, plan))
        print()
        print(case_file.status(plan).render())
        print("=" * 60)


if __name__ == "__main__":
    main(sys.argv)
