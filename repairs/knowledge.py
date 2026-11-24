"""工艺组检修手册：原因候选、适用前提与检查。

手册原文保留在 manual.tsv；这里把每条路径的适用前提写成可判定的规则。
评估只看直接证据（用户口述、技师复测、站点检查），转述不算确认；
同配置、同工况的观察才能互相对照，空杯与带水的触发不可相互否定。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Finding, Observation, SourceKind, polarity

LOADED = {"装杯", "清水"}      # 真实装杯载荷
UNLOADED = {"空转", "空杯"}    # 空载；空杯未响不否定带水复现，反之亦然
GEAR_FAMILIES = ("低档", "高档")  # 档位族；"高档尾段"归入"高档"


@dataclass(frozen=True)
class CheckSpec:
    """手册检查：需要哪些工位能力、为什么做。"""

    key: str
    route: str
    name: str
    requires: tuple[str, ...]
    purpose: str


@dataclass(frozen=True)
class PremiseGap:
    """前提缺口：缺什么观察、建议怎么补、能区分哪些路径。"""

    description: str
    suggestion: str
    requires: tuple[str, ...]
    purpose: str
    user_answerable: bool = True
    target_head: str | None = None
    target_gear: str | None = None


@dataclass
class RouteOutcome:
    route: str
    name: str
    manual_text: str
    state: str  # 成立 / 未证实 / 排除 / 未评估
    reasons: list[str] = field(default_factory=list)
    gaps: list[PremiseGap] = field(default_factory=list)
    checks: list[CheckSpec] = field(default_factory=list)
    evidence: list[Observation] = field(default_factory=list)
    partial: bool = False  # 已有部分证据，值得给出复测建议


CHECKS: dict[str, tuple[CheckSpec, ...]] = {
    "H1": (
        CheckSpec("H1.load", "H1", "真实装杯载荷配合检查", ("清水",),
                  "按真实装杯载荷查配合"),
    ),
    "H2": (
        CheckSpec("H2.follow", "H2", "套筒跟随检查", ("B头", "备机身"),
                  "需要对照机身与同类机头，做套筒跟随"),
    ),
    "H3": (
        CheckSpec("H3.bearing", "H3", "轴座检查", ("轴座检具",),
                  "症状随机身，须由有检具的精修工位检验"),
    ),
    "H4": (
        CheckSpec("H4.speed", "H4", "速度台测", ("速度台",),
                  "对照同配置另一个档位"),
    ),
}

CHECK_NAMES = [spec.name for specs in CHECKS.values() for spec in specs]
CHECK_ROUTE = {spec.name: route for route, specs in CHECKS.items() for spec in specs}


def _direct(observations: list[Observation]) -> list[Observation]:
    """转述不进证据。"""
    return [o for o in observations if o.source is not SourceKind.HEARSAY]


def _head(obs: Observation) -> str:
    return obs.config.head or "原机头"


def _body(obs: Observation) -> str:
    return obs.config.body or "原机身"


def _family(gear: str | None) -> str | None:
    return {"高档尾段": "高档"}.get(gear, gear)


def _ids(observations) -> str:
    return "、".join(o.id for o in observations)


def eval_h1(observations: list[Observation], row: dict) -> RouteOutcome:
    """装杯配合：装杯才发声且换机头仍出现。空杯与带水不可相互否定。"""
    direct = _direct(observations)
    by_head: dict[str, list[Observation]] = {}
    for o in direct:
        by_head.setdefault(_head(o), []).append(o)
    load_contrast = []
    for head, group in by_head.items():
        loaded = [o for o in group if o.positive and o.condition.load in LOADED]
        unloaded = [o for o in group
                    if o.finding is Finding.SILENT and o.condition.load in UNLOADED]
        if loaded and unloaded:
            load_contrast.append((head, loaded, unloaded))
    heads_sounding = {_head(o) for o in direct if o.positive}
    reasons, gaps = [], []
    premise_load = bool(load_contrast)
    if premise_load:
        for head, loaded, unloaded in load_contrast:
            reasons.append(f"{head}：有载发声（{_ids(loaded)}），空载排除（{_ids(unloaded)}）")
    else:
        gaps.append(PremiseGap(
            "缺少有载/空载的对照观察",
            "同一机头分别在装清水与空转下观察",
            ("清水",), "确认是否装杯才发声",
        ))
    premise_heads = len(heads_sounding) >= 2
    if premise_heads:
        reasons.append("换机头仍出现：" + "、".join(sorted(heads_sounding)))
    else:
        gaps.append(PremiseGap(
            "未确认换机头后症状仍出现",
            "换同类机头在原工况下观察",
            ("B头",), "确认症状是否随机头", user_answerable=False,
        ))
    state = "成立" if premise_load and premise_heads else "未证实"
    if state == "成立":
        reasons.append("空杯与带水的触发不可相互否定（手册原文），空载未响不推翻有载复现")
    evidence = [o for direct_group in
                ([g[1] + g[2] for g in load_contrast]) for o in direct_group]
    evidence += [o for o in direct if o.positive]
    return RouteOutcome(
        row["id"], row["route"], row["text"], state, reasons, gaps,
        list(CHECKS["H1"]), evidence, partial=bool(load_contrast),
    )


def eval_h2(observations: list[Observation], row: dict) -> RouteOutcome:
    """原头套筒：同一机身、不同机头在相同档位和载荷下有差别才适用。"""
    direct = _direct(observations)
    diffs = []
    for i, a in enumerate(direct):
        for b in direct[i + 1:]:
            if _head(a) == _head(b) or _body(a) != _body(b):
                continue
            if a.condition.gear is None or a.condition.gear != b.condition.gear:
                continue
            if a.condition.load is None or a.condition.load != b.condition.load:
                continue
            if polarity(a) * polarity(b) == -1:
                diffs.append((a, b))
    heads_seen = {_head(o) for o in direct}
    if diffs:
        reasons = [f"同工况下机头有差别：{a.id} 与 {b.id}" for a, b in diffs]
        return RouteOutcome(row["id"], row["route"], row["text"], "成立",
                            reasons, [], list(CHECKS["H2"]),
                            [o for pair in diffs for o in pair], partial=True)
    gaps = [PremiseGap(
        "未见同工况下不同机头的发声差别",
        "用同类 B 头与原机头在同档位同负载下对照发声",
        ("B头", "备机身"), "确认或排除原头套筒路径", user_answerable=False,
    )]
    reasons = ["现有记录中，同档位同负载下不同机头未出现一响一静的差别"]
    return RouteOutcome(row["id"], row["route"], row["text"], "未证实",
                        reasons, gaps, list(CHECKS["H2"]), [],
                        partial=len(heads_seen) >= 2)


def eval_h3(observations: list[Observation], row: dict) -> RouteOutcome:
    """机身轴座：同一机头跟随机身出现症状才走轴座检查。"""
    direct = _direct(observations)
    follows = []
    for i, a in enumerate(direct):
        for b in direct[i + 1:]:
            if _head(a) != _head(b) or _body(a) == _body(b):
                continue
            if a.condition.gear is None or b.condition.gear is None:
                continue
            if _family(a.condition.gear) != _family(b.condition.gear):
                continue
            if polarity(a) * polarity(b) == -1:
                follows.append((a, b))
    bodies_seen = {_body(o) for o in direct}
    if follows:
        reasons = [f"同一机头换机身即变：{a.id} 与 {b.id}" for a, b in follows]
        reasons.append("症状随机身，须由有检具的精修工位检验")
        return RouteOutcome(row["id"], row["route"], row["text"], "成立",
                            reasons, [], list(CHECKS["H3"]),
                            [o for pair in follows for o in pair], partial=True)
    gaps = [PremiseGap(
        "未做换机身对照",
        "同一机头换装备机身，在原工况下观察",
        ("备机身",), "确认症状是否随机身", user_answerable=False,
    )]
    return RouteOutcome(row["id"], row["route"], row["text"], "未证实",
                        ["未记录同一机头跨机身的一响一静对照"], gaps,
                        list(CHECKS["H3"]), [], partial=len(bodies_seen) >= 2)


def eval_h4(observations: list[Observation], row: dict) -> RouteOutcome:
    """档速专检：已确认仅发生于一个档位才用速度台。"""
    direct = _direct(observations)
    groups: dict[tuple[str, str], dict] = {}
    for o in direct:
        family = _family(o.condition.gear)
        if family not in GEAR_FAMILIES:
            continue  # 空档等试运状态不算症状档位
        key = (_head(o), _body(o))
        group = groups.setdefault(key, {"pos": set(), "neg": set(),
                                        "untested": set(), "obs": []})
        if o.positive:
            group["pos"].add(family)
        elif o.finding is Finding.SILENT:
            group["neg"].add(family)
        else:
            group["untested"].add(family)
        group["obs"].append(o)
    confirmed, gaps, evidence = [], [], []
    for (head, body), group in groups.items():
        if not group["pos"]:
            continue
        # 未排除也未试过的档位都是前提缺口（不管有没有"没试"的记录）
        unknown = set(GEAR_FAMILIES) - group["pos"] - group["neg"]
        if unknown:
            missing = "、".join(sorted(unknown))
            gaps.append(PremiseGap(
                f"{head}的{missing}未试",
                f"{head}装清水跑{missing}，看是否发声",
                ("清水",),
                "区分装杯配合（H1）与档速专检（H4）："
                "其他档位也响则非仅一档；仅一档响才上速度台",
                target_head=head, target_gear=sorted(unknown)[0],
            ))
        else:
            confirmed.append((head, body, group))
    if confirmed:
        reasons = [f"{head}（{body}）仅{'、'.join(sorted(g['pos']))}发声，"
                   f"其余档位已排除" for head, body, g in confirmed]
        evidence = [o for _, _, g in confirmed for o in g["obs"]]
        return RouteOutcome(row["id"], row["route"], row["text"], "成立",
                            reasons, gaps, list(CHECKS["H4"]), evidence,
                            partial=True)
    reasons = ["尚未确认症状仅发生于一个档位"]
    evidence = [o for _, g in groups.items() for o in g["obs"] if o.positive]
    return RouteOutcome(row["id"], row["route"], row["text"], "未证实",
                        reasons, gaps, list(CHECKS["H4"]), evidence,
                        partial=bool(gaps))


EVALUATORS = {"H1": eval_h1, "H2": eval_h2, "H3": eval_h3, "H4": eval_h4}
