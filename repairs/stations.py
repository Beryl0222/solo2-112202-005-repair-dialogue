"""工位能力与交接登记。

派修按"检查需要的能力 + 工位本班实际设备状态"匹配，
不按问题描述里的词数匹配工位。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# availability 原文里的别名归一
_ALIASES = {"同类B头": "B头", "同类机头": "B头"}


def _norm(item: str) -> str:
    item = item.replace(" ", "")
    return _ALIASES.get(item, item)


def parse_availability(text: str) -> dict[str, str]:
    """把"空杯、清水，无同类备件"一类描述解析成 能力 -> 状态。

    状态：在 / 借出 / 调校中 / 无。
    """
    result: dict[str, str] = {}
    for segment in re.split(r"[，、；]", text):
        segment = segment.strip()
        if not segment:
            continue
        if segment.startswith("无"):
            result[_norm(segment[1:])] = "无"
        elif segment.endswith("借出"):
            result[_norm(segment[:-2])] = "借出"
        elif segment.endswith("调校中"):
            result[_norm(segment[:-3])] = "调校中"
        elif segment.endswith("在台"):
            result[_norm(segment[:-2])] = "在"
        elif segment.endswith("在"):
            result[_norm(segment[:-1])] = "在"
        else:
            result[_norm(segment)] = "在"
    return result


@dataclass
class Handoff:
    """已交接的工作：哪个案、什么状态、交接内容原文。"""

    case: str
    state: str  # 已登记 / 已签收未开工 / 已签收 / 等待接件
    item: str
    check: str | None = None  # 识别出的手册检查名


def parse_handoff(text: str, check_names: list[str] | None = None) -> Handoff | None:
    text = (text or "").strip()
    if not text:
        return None
    case, _, rest = text.partition(" ")
    state = "已登记"
    if "已签收" in rest:
        state = "已签收未开工" if "未开工" in rest else "已签收"
    elif "等待接件" in rest:
        state = "等待接件"
    check = None
    for name in check_names or []:
        if name in rest:
            check = name
            break
    return Handoff(case=case, state=state, item=rest, check=check)


@dataclass
class Station:
    id: str
    name: str
    availability: dict[str, str]
    handoff: Handoff | None = None

    @classmethod
    def from_row(cls, row: dict, check_names: list[str] | None = None) -> "Station":
        return cls(
            id=row["id"],
            name=row["station"],
            availability=parse_availability(row["availability"]),
            handoff=parse_handoff(row["handoff"], check_names),
        )

    def has(self, capability: str) -> bool:
        return self.availability.get(capability) == "在"

    def blocker(self, capability: str) -> str:
        status = self.availability.get(capability)
        return f"{self.name}：{capability}{status if status else '未登记'}"
