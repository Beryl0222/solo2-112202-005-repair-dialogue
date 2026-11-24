"""把会话原文转写为结构化观察，保守处理未知槽位。

规则要点：
- "没试/没跑"是未试，不是没响；
- "还是这样"按最近的阳性症状理解，记下指代来源，对象换成新提到的部件；
- "邻居说"一类只记为转述，不进复测证据；
- "更正"不改写原记录，追加一条订正版并互相链接；
- 没提到的槽位留空（未记录），原话始终保留在 text 里。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Condition, Config, Finding, Observation, SourceKind

_HEAD_WORDS = [
    ("用户原机头", "原机头"),
    ("原机头", "原机头"),
    ("原头", "原机头"),
    ("用户备用头", "备用头"),
    ("备用头", "备用头"),
    ("B 头", "B头"),
    ("B头", "B头"),
    ("同一头", "原机头"),  # 换机身后说的"同一头"就是用户自己的机头
]
_BODY_WORDS = [
    ("另一台机身", "另一台机身"),
    ("换机身", "另一台机身"),
    ("备机身", "备机身"),
]
_GEAR_WORDS = ["高档尾段", "低档", "高档", "空档"]  # 长的先匹配
_LOAD_WORDS = [
    ("空转", "空转"),
    ("空杯", "空杯"),
    ("装杯", "装杯"),
    ("放水", "清水"),
    ("清水", "清水"),
    ("带水", "清水"),
]
_HEARSAY_MARKERS = ("邻居说", "听说", "他说")
_ANAPHORA = ("还是这样", "也一样")
_CORRECTION = "更正"
# “只有 X 响”时，用户同时排除了哪些档位
_EXCLUSIVE_COMPLEMENT = {"高档尾段": ("低档",), "高档": ("低档",), "低档": ("高档",)}

_CTX = object()  # emit 的槽位默认值：取上下文


def find_head(text: str) -> str | None:
    for surface, canon in _HEAD_WORDS:
        if surface in text:
            return canon
    return None


def find_body(text: str) -> str | None:
    for surface, canon in _BODY_WORDS:
        if surface in text:
            return canon
    return None


def find_gear(text: str) -> str | None:
    for gear in _GEAR_WORDS:
        if gear in text:
            return gear
    return None


def find_load(text: str) -> str | None:
    for surface, canon in _LOAD_WORDS:
        if surface in text:
            return canon
    return None


def detect_finding(text: str) -> Finding | None:
    """按保守顺序识别结论词；识别不了就返回 None，不猜。"""
    if "没试" in text or "没跑" in text or "未试" in text:
        return Finding.UNTESTED
    if "不稳定" in text or "隔一会又响" in text:
        return Finding.INTERMITTENT
    if any(w in text for w in ("没响", "不再响", "未响", "没声音", "安静")):
        return Finding.SILENT
    if "有声" in text or "响" in text:
        return Finding.SOUNDING
    return None


@dataclass
class QuestionRecord:
    """前台追问留痕：原话、出处一并保留。"""

    id: str
    case: str
    text: str
    origin: str


@dataclass
class Extraction:
    observations: list[Observation] = field(default_factory=list)
    questions: list[QuestionRecord] = field(default_factory=list)


@dataclass
class _Draft:
    """跨分句的上下文：最近提到的对象与工况。"""

    head: str | None = None
    body: str | None = None
    gear: str | None = None
    load: str | None = None


def extract_case(case: str, turns: list[dict]) -> Extraction:
    """按 turn 顺序转写一个案号的会话。turns 为 conversation.tsv 的行。"""
    result = Extraction()
    ctx = _Draft()
    last_positive: Observation | None = None

    for turn in sorted(turns, key=lambda r: int(r["turn"])):
        origin = turn["id"]
        role = turn["role"]
        text = turn["text"]
        counter = 0

        def emit(*, source, finding, clause, note="", resolved_from=None,
                 head=_CTX, body=_CTX, gear=_CTX, load=_CTX) -> Observation:
            nonlocal counter, last_positive
            counter += 1
            obs = Observation(
                id=f"{origin}#{counter}",
                case=case,
                source=source,
                config=Config(
                    head=ctx.head if head is _CTX else head,
                    body=ctx.body if body is _CTX else body,
                ),
                condition=Condition(
                    gear=ctx.gear if gear is _CTX else gear,
                    load=ctx.load if load is _CTX else load,
                ),
                finding=finding,
                text=clause,
                origin=origin,
                resolved_from=resolved_from,
                note=note,
            )
            result.observations.append(obs)
            if obs.positive and source is not SourceKind.HEARSAY:
                last_positive = obs
            return obs

        if role == "前台":
            result.questions.append(
                QuestionRecord(id=f"{origin}#q", case=case, text=text, origin=origin)
            )
            head = find_head(text)  # 追问提到的对象进入上下文
            if head:
                ctx.head = head
            continue

        source = SourceKind.USER if role == "用户" else SourceKind.TECH
        for clause in re.split(r"[。，；？]", text):
            clause = clause.strip()
            if not clause:
                continue
            if _CORRECTION in clause:
                _apply_correction(clause, result.observations, source, origin, emit)
                continue
            if any(m in clause for m in _HEARSAY_MARKERS):
                emit(
                    source=SourceKind.HEARSAY,
                    finding=detect_finding(clause) or Finding.UNTESTED,
                    clause=clause,
                    head=None, body=None,
                    gear=find_gear(clause), load=find_load(clause),
                    note="只听转述，未经复测，不进证据",
                )
                continue
            head = find_head(clause)
            if head:
                ctx.head = head
            body = find_body(clause)
            if body:
                ctx.body = body
            gear = find_gear(clause)
            if gear:
                ctx.gear = gear
            load = find_load(clause)
            if load:
                ctx.load = load

            if any(a in clause for a in _ANAPHORA):
                if last_positive is not None:
                    emit(
                        source=source,
                        finding=last_positive.finding,
                        clause=clause,
                        resolved_from=last_positive.id,
                        gear=last_positive.condition.gear,
                        load=last_positive.condition.load,
                        note=f"“还是这样”按最近症状（{last_positive.id}）理解，"
                        f"对象换成{ctx.head or '未记录'}",
                    )
                continue
            if "只有" in clause and detect_finding(clause) is Finding.SOUNDING:
                emit(source=source, finding=Finding.SOUNDING, clause=clause)
                for other in _EXCLUSIVE_COMPLEMENT.get(ctx.gear or "", ()):
                    emit(
                        source=source,
                        finding=Finding.SILENT,
                        clause=clause,
                        gear=other,
                        note=f"用户称“只有{ctx.gear}响”，排除其他档位",
                    )
                continue
            finding = detect_finding(clause)
            if finding is not None:
                if finding is Finding.UNTESTED and find_load(clause) is None:
                    emit(source=source, finding=finding, clause=clause, load=None)
                else:
                    emit(source=source, finding=finding, clause=clause)
                continue
            if "也" in clause and gear:
                emit(source=source, finding=Finding.SOUNDING, clause=clause,
                     note="承接前文讨论的症状")
                continue
            if load and "试" in clause:
                emit(source=source, finding=Finding.SOUNDING, clause=clause,
                     note="症状在该负载下试出")
                continue
            # 无结论的分句：只更新上下文，不产生观察
    return result


def _apply_correction(clause, observations, source, origin, emit) -> None:
    """订正某条观察的对象或含义：找到最近一条工况槽位吻合的观察，

    追加一条订正版；原记录保留，用 corrects/corrected_by 互相链接。
    """
    load = find_load(clause)
    gear = find_gear(clause)
    head = find_head(clause)
    finding = detect_finding(clause)
    target = None
    for obs in reversed(observations):
        if obs.corrected_by is not None:
            continue
        if load and obs.condition.load != load:
            continue
        if gear and obs.condition.gear != gear:
            continue
        target = obs
        break
    if target is None:
        return
    revised = emit(
        source=source,
        finding=finding or target.finding,
        clause=clause,
        head=head or target.config.head,
        body=target.config.body,
        gear=target.condition.gear,
        load=target.condition.load,
        note=f"订正 {target.id} 的{'对象' if head else '含义'}",
    )
    revised.corrects = target.id
    target.corrected_by = revised.id


def observations_from_checks(case: str, rows: list[dict]) -> list[Observation]:
    """prior_check.tsv 的复测记录转观察；config 保留原记录的组件和负载。"""
    result = []
    for row in rows:
        if row["case"] != case:
            continue
        config_text, result_text = row["config"], row["result"]
        finding = detect_finding(result_text)
        note = f"记录于 {row['recorded_at']}"
        if finding is None:
            finding = Finding.UNTESTED
            note = f"检查未出结果：{result_text}；{note}"
        load = find_load(result_text) or find_load(config_text)
        if finding is Finding.UNTESTED and find_load(result_text) is None:
            load = find_load(result_text)  # 未执行的检查不绑负载
        result.append(
            Observation(
                id=f"{row['id']}#1",
                case=case,
                source=SourceKind.CHECK,
                config=Config(
                    head=find_head(result_text) or find_head(config_text),
                    body=find_body(result_text) or find_body(config_text),
                ),
                condition=Condition(
                    gear=find_gear(result_text) or find_gear(config_text),
                    load=load,
                ),
                finding=finding,
                text=f"{row['check']}：{result_text}",
                origin=row["id"],
                note=note,
            )
        )
    return result
