"""案卷：把会话、复测、手册、工位与对照件串成一条接力链。

- 初次登记与后续追问都保留原说法、对象与上下文；
- 复测与用户口述冲突时，按同配置、同工况判断能否推翻，
  找不到对应条件时保留差异；
- 下一步按工位可用能力与检查前置条件给出，不按问题词数匹配工位；
- 工位已签收不代表故障已确认。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import extract, knowledge
from .assets import AssetPool
from .knowledge import CHECK_ROUTE, EVALUATORS, RouteOutcome
from .model import (
    Finding,
    Observation,
    SourceKind,
    condition_relation,
    config_matches,
    polarity,
)
from .stations import Station


@dataclass
class Overturn:
    """同配置同工况下，专业复测推翻用户口述。"""

    overturned: Observation
    by: Observation
    reason: str


@dataclass
class Difference:
    """条件对应不上，保留差异，不判胜负。"""

    user_obs: Observation
    tech_obs: Observation
    reason: str


def reconcile(observations: list[Observation]) -> tuple[list[Overturn], list[Difference]]:
    """专业复测与用户口述的对账。

    同配置、同工况（exact）且结论相反 → 复测推翻口述；
    同配置但条件对应不上（partial）→ 保留差异；
    工况本来就不同（distinct）→ 互不否定，不产生任何关系。
    """
    live = [o for o in observations if o.corrected_by is None]
    users = [o for o in live if o.source is SourceKind.USER and polarity(o) != 0]
    pros = [o for o in live
            if o.source in (SourceKind.TECH, SourceKind.CHECK) and polarity(o) != 0]
    overturns, differences = [], []
    for user in users:
        for pro in pros:
            if polarity(user) * polarity(pro) != -1:
                continue
            if not config_matches(user.config, pro.config):
                continue
            relation = condition_relation(user.condition, pro.condition)
            if relation == "exact":
                overturns.append(Overturn(
                    user, pro, "同配置同工况，专业复测与用户口述冲突，以复测为准"))
            elif relation == "partial":
                differences.append(Difference(
                    user, pro,
                    "同机头但工况条件对应不上（有槽位未记录），保留差异，"
                    "需按用户实际工况复核"))
    return overturns, differences


@dataclass
class PlannedCheck:
    name: str
    kind: str  # 鉴别 / 检修
    route: str
    station: str | None
    purpose: str
    feasible: bool
    blockers: list[str] = field(default_factory=list)
    requires: tuple[str, ...] = ()


@dataclass
class HandoffNotice:
    station: str
    case: str
    action: str  # 暂停 / 继续 / 接件
    reason: str


@dataclass
class NextStepPlan:
    case: str
    decision: str  # 先问 / 先做鉴别检查 / 派修到X / 暂无可行下一步
    summary: str
    do_now: list[PlannedCheck] = field(default_factory=list)
    blocked: list[PlannedCheck] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notices: list[HandoffNotice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    routes: list[RouteOutcome] = field(default_factory=list)


@dataclass
class CaseStatus:
    """用户凭接件编号可查：能确认的结论、尚需验证的情形、下一受理工位。"""

    case: str
    confirmed: list[str]
    pending: list[str]
    next_station: str | None
    notes: list[str]

    def render(self) -> str:
        lines = [f"【{self.case} 接件状态】"]
        lines.append("目前能确认的结论：")
        lines += [f"  - {item}" for item in self.confirmed] or ["  （暂无）"]
        lines.append("尚需验证的情形：")
        lines += [f"  - {item}" for item in self.pending] or ["  （暂无）"]
        lines.append(f"下一受理工位：{self.next_station or '待定'}")
        for note in self.notes:
            lines.append(f"提示：{note}")
        return "\n".join(lines)


@dataclass
class Symptom:
    """一个并存症状：绑定在同一实物配置与档位上的阳性观察。"""

    key: tuple
    observations: list[Observation]

    def label(self) -> str:
        head, body, gear = self.key
        return f"{head}·{body}·{gear or '档位未记录'}"


class CaseFile:
    def __init__(self, case, turns, check_rows, manual_rows):
        self.case = case
        extraction = extract.extract_case(case, turns)
        self.observations = (
            extraction.observations
            + extract.observations_from_checks(case, check_rows)
        )
        self.asked = extraction.questions  # 前台已发出的追问（留痕）
        self.manual_rows = manual_rows
        self.completed_checks: dict[str, str] = {}  # 检查 key -> 证据说明
        self.overturns, self.differences = reconcile(self.observations)

    @classmethod
    def build(cls, case, tables) -> "CaseFile":
        turns = [r for r in tables["conversation.tsv"] if r["case"] == case]
        checks = [r for r in tables["prior_check.tsv"] if r["case"] == case]
        return cls(case, turns, checks, tables["manual.tsv"])

    @classmethod
    def from_observations(cls, case, observations, manual_rows) -> "CaseFile":
        """直接由结构化观察建案（用于登记台结构化录入与测试）。"""
        instance = cls.__new__(cls)
        instance.case = case
        instance.observations = list(observations)
        instance.asked = []
        instance.manual_rows = manual_rows
        instance.completed_checks = {}
        instance.overturns, instance.differences = reconcile(instance.observations)
        return instance

    def live_observations(self) -> list[Observation]:
        """最新订正版；被订正的原记录仍留档，只是不再生效。"""
        return [o for o in self.observations if o.corrected_by is None]

    def direct_observations(self) -> list[Observation]:
        """可进证据的观察：最新订正版且非转述。"""
        return [o for o in self.live_observations()
                if o.source is not SourceKind.HEARSAY]

    def routes(self) -> list[RouteOutcome]:
        outcomes = []
        for row in self.manual_rows:
            evaluator = EVALUATORS.get(row["id"])
            if evaluator is None:
                outcomes.append(RouteOutcome(
                    row["id"], row["route"], row["text"], "未评估",
                    ["手册路径暂无判定规则，保留原文"], [], []))
            else:
                outcomes.append(evaluator(self.direct_observations(), row))
        return outcomes

    def symptoms(self) -> list[Symptom]:
        """并存症状分组：同一实物配置同一档位上的阳性观察。"""
        groups: dict[tuple, list[Observation]] = {}
        for o in self.live_observations():
            if not o.positive or o.source is SourceKind.HEARSAY:
                continue
            key = (o.config.head or "原机头",
                   o.config.body or "原机身",
                   o.condition.gear)
            groups.setdefault(key, []).append(o)
        return [Symptom(key, obs) for key, obs in groups.items()]

    def complete_check(self, key: str, evidence_note: str) -> None:
        """登记检查完成；原证据留档，不因换担当而消失。"""
        self.completed_checks[key] = evidence_note

    def open_responsibilities(self) -> dict[str, list[str]]:
        """每个并存症状各自未完成的检修责任。"""
        outcomes = self.routes()
        responsibilities: dict[str, list[str]] = {}
        for symptom in self.symptoms():
            items = []
            for o in self.live_observations():
                if (o.finding is Finding.UNTESTED
                        and (o.config.head or "原机头") == symptom.key[0]):
                    items.append(f"补观察：{o.condition.label()}（{o.origin} 记录未试）")
            for outcome in outcomes:
                if outcome.state != "成立":
                    continue
                if not any(ev in symptom.observations for ev in outcome.evidence):
                    continue
                for check in outcome.checks:
                    if check.key in self.completed_checks:
                        items.append(f"{check.name}：已完成，证据留存"
                                     f"（{self.completed_checks[check.key]}）")
                    else:
                        items.append(f"{check.name}：未完成")
            responsibilities[symptom.label()] = items
        return responsibilities

    # ---------------------------------------------------------- 接力计划

    def plan(self, stations: list[Station], assets: AssetPool) -> NextStepPlan:
        outcomes = self.routes()
        do_now, blocked, notices, warnings, questions = [], [], [], [], []
        for outcome in outcomes:
            if outcome.state == "成立":
                for check in outcome.checks:
                    if check.key in self.completed_checks:
                        continue
                    placed = self._place(check.name, "检修", check.route,
                                         check.requires, check.purpose,
                                         stations, assets)
                    (do_now if placed.feasible else blocked).append(placed)
            elif outcome.state == "未证实" and outcome.partial:
                for gap in outcome.gaps:
                    placed = self._place(gap.suggestion, "鉴别", outcome.route,
                                         gap.requires, gap.purpose,
                                         stations, assets)
                    if placed.feasible:
                        do_now.append(placed)
                    elif gap.user_answerable and not self._already_recorded(gap):
                        questions.append(
                            f"请向用户确认：{gap.suggestion}"
                            f"（{gap.purpose}；站点当前做不了，答案会改变下一步）")
                    else:
                        blocked.append(placed)
        notices = self._handoff_notices(stations, outcomes, do_now)
        warnings = self._consumption_warnings(do_now + blocked, assets)
        warnings += self._idle_loan_notes(assets, do_now + blocked)
        decision, summary = self._decide(do_now, blocked, questions)
        return NextStepPlan(self.case, decision, summary, do_now, blocked,
                            questions, notices, warnings, outcomes)

    def _place(self, name, kind, route, requires, purpose,
               stations, assets) -> PlannedCheck:
        station = next(
            (st for st in stations if all(st.has(cap) for cap in requires)), None)
        blockers = []
        if station is None:
            for st in stations:
                for cap in requires:
                    if not st.has(cap):
                        blockers.append(st.blocker(cap))
        for cap in requires:
            asset = assets.get(cap)
            if asset is not None and asset.holder not in (None, self.case):
                blockers.append(
                    f"{cap}已借给 {asset.holder}（{asset.loan_to}），"
                    f"同一组对照件只能托给一个尚未完成的检修")
        asset_blocked = any("已借给" in b for b in blockers)
        return PlannedCheck(
            name=name, kind=kind, route=route,
            station=station.name if station else None,
            purpose=purpose,
            feasible=station is not None and not asset_blocked,
            blockers=blockers, requires=requires,
        )

    def _already_recorded(self, gap) -> bool:
        """缺口所指的对象与工况是否已有记录（含"未试"记录）。

        已记录就不再占用用户一次回复机会。
        """
        if gap.target_head is None and gap.target_gear is None:
            return False
        for o in self.live_observations():
            head = o.config.head or "原机头"
            if gap.target_head and head != gap.target_head:
                continue
            if gap.target_gear and o.condition.gear != gap.target_gear:
                continue
            return True
        return False

    def _handoff_notices(self, stations, outcomes, do_now) -> list[HandoffNotice]:
        notices = []
        by_route = {o.route: o for o in outcomes}
        for st in stations:
            handoff = st.handoff
            if handoff is None or handoff.case != self.case:
                continue
            if handoff.check:
                route = CHECK_ROUTE.get(handoff.check)
                outcome = by_route.get(route)
                if outcome is not None and outcome.state != "成立":
                    reason = "；".join(outcome.reasons) or "前提未证实"
                    notices.append(HandoffNotice(
                        st.name, self.case, "暂停",
                        f"{handoff.check}前提未成立（{outcome.state}：{reason}），"
                        f"请暂停，等待前台重新派工"))
                else:
                    notices.append(HandoffNotice(
                        st.name, self.case, "继续",
                        f"{handoff.check}前提仍成立，可按交接开工"))
            elif handoff.state == "等待接件":
                incoming = [c for c in do_now if c.station == st.name]
                if incoming:
                    notices.append(HandoffNotice(
                        st.name, self.case, "接件",
                        f"请接件：{incoming[0].name}（{incoming[0].purpose}）"))
            elif handoff.state == "已登记":
                notices.append(HandoffNotice(
                    st.name, self.case, "继续",
                    f"登记在案（{handoff.item}），按接力计划继续"))
        return notices

    def _consumption_warnings(self, planned, assets) -> list[str]:
        """会拆改用户封装的检查：动手前列出将不可复现的观察。"""
        warnings = []
        for check in planned:
            for cap in check.requires:
                for consumed in assets.consumed_configs(cap):
                    affected = [
                        o for o in self.live_observations()
                        if (o.config.head or "原机头") == consumed
                        and o.source is not SourceKind.HEARSAY
                        and (o.positive or o.finding is Finding.UNTESTED)
                    ]
                    if affected:
                        asset = assets.get(cap)
                        warnings.append(
                            f"{check.name}需装上{cap}"
                            f"（{asset.retained_state if asset else ''}）："
                            f"以下观察在配置拆改后将不可复现，应先完成并与"
                            f"真正的维修作业分开交接："
                            + "、".join(o.id for o in affected))
        return warnings

    def _idle_loan_notes(self, assets, planned) -> list[str]:
        """本案借用的对照件若已不被任何路径需要，提示可归还。"""
        notes = []
        needed = {cap for check in planned for cap in check.requires}
        for asset in assets.held_by(self.case):
            if asset.name not in needed:
                notes.append(
                    f"对照件：{asset.name}现由本案借用，现有证据下没有路径"
                    f"需要它做对照，可评估提前归还对照台")
        return notes

    def _decide(self, do_now, blocked, questions) -> tuple[str, str]:
        feasible = [c for c in do_now if c.feasible]
        if any(c.kind == "鉴别" for c in feasible):
            first = next(c for c in feasible if c.kind == "鉴别")
            return "先做鉴别检查", f"先在{first.station}做：{first.name}"
        if feasible:
            first = feasible[0]
            return f"派修到{first.station}", f"{first.station}接件：{first.name}"
        if questions:
            return "先问", questions[0]
        return "暂无可行下一步", "所有检查都被阻塞，等待条件解除"

    # ---------------------------------------------------------- 用户查询

    def status(self, plan: NextStepPlan | None = None) -> CaseStatus:
        confirmed, pending, notes = [], [], []
        overturned = {o.overturned.id: o for o in self.overturns}
        for o in self.live_observations():
            if o.source is SourceKind.HEARSAY:
                pending.append(f"{o.describe()}——只听转述，未证实")
            elif o.finding is Finding.UNTESTED:
                pending.append(
                    f"{o.config.label()}，{o.condition.label()}：未试"
                    f"（{o.origin} 原话“{o.text}”）")
            elif o.id in overturned:
                notes.append(
                    f"{o.origin} 的用户口述已被 {overturned[o.id].by.origin}"
                    f" 按同配置同工况的专业复测推翻")
            else:
                confirmed.append(o.describe())
        for diff in self.differences:
            pending.append(
                f"差异保留：{diff.user_obs.describe()} 与 "
                f"{diff.tech_obs.describe()}——{diff.reason}")
        next_station = None
        if plan is not None:
            feasible = [c for c in plan.do_now if c.feasible]
            if feasible:
                next_station = feasible[0].station
            placed = {c.name: c for c in plan.do_now + plan.blocked}
            for outcome in plan.routes:
                if outcome.state != "成立":
                    continue
                for check in outcome.checks:
                    if check.key in self.completed_checks:
                        continue
                    found = placed.get(check.name)
                    if found is None:
                        continue
                    if found.feasible:
                        pending.append(
                            f"{check.name}待做（{outcome.name}路径成立，"
                            f"排在{found.station}）")
                    else:
                        pending.append(
                            f"{check.name}未做（{outcome.name}路径成立，"
                            f"阻塞：{'；'.join(found.blockers)}）")
            for notice in plan.notices:
                if notice.action == "暂停":
                    notes.append(
                        f"{notice.station}已签收的检查前提未成立，已通知暂停——"
                        f"工位签收不代表故障已确认")
        return CaseStatus(self.case, confirmed, pending, next_station, notes)
