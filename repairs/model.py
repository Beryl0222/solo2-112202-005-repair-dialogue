"""返修接力的核心记录模型。

一条观察 = 谁说的（来源）+ 说哪个对象（配置）+ 什么工况（档位/负载）
+ 结论（发现）+ 原话与出处。空位一律表示"来件未记录"，
不等于"任意"，更不等于"没有"——"没试"就是未试，不能读成"没响"。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class SourceKind(enum.Enum):
    USER = "用户口述"
    TECH = "技师复测"
    CHECK = "站点检查"
    HEARSAY = "只听转述"


class Finding(enum.Enum):
    SOUNDING = "有声"
    SILENT = "明确排除"
    UNTESTED = "未试"
    INTERMITTENT = "偶发"


@dataclass(frozen=True)
class Config:
    """实物配置：机头 × 机身。空位表示来件未记录。"""

    head: str | None = None
    body: str | None = None

    def label(self) -> str:
        parts = [self.head or "机头未记录"]
        if self.body:
            parts.append(f"装在{self.body}")
        return "，".join(parts)


@dataclass(frozen=True)
class Condition:
    """工况：档位 × 负载。空位表示来件未记录。"""

    gear: str | None = None
    load: str | None = None

    def label(self) -> str:
        parts = [self.gear or "档位未记录"]
        if self.load:
            parts.append(self.load)
        return "，".join(parts)


@dataclass
class Observation:
    """一条观察记录。原话与出处始终保留；订正只追加新版本，不改写原记录。"""

    id: str
    case: str
    source: SourceKind
    config: Config
    condition: Condition
    finding: Finding
    text: str
    origin: str
    resolved_from: str | None = None  # "还是这样"指代解析到的观察 id
    note: str = ""
    corrects: str | None = None       # 本条订正了哪条观察
    corrected_by: str | None = None   # 本条被哪条订正取代

    @property
    def positive(self) -> bool:
        return self.finding in (Finding.SOUNDING, Finding.INTERMITTENT)

    def status_label(self) -> str:
        """支持的状态：明确排除、未试、只听转述、偶发、条件复现。"""
        if self.source is SourceKind.HEARSAY:
            return SourceKind.HEARSAY.value
        if self.finding is Finding.SOUNDING and (
            self.condition.gear or self.condition.load
        ):
            return "条件复现"
        return self.finding.value

    def describe(self) -> str:
        return (
            f"{self.config.label()}，{self.condition.label()}："
            f"{self.finding.value}（{self.source.value}，{self.origin}）"
        )


def polarity(obs: Observation) -> int:
    """阳性 +1，排除 -1，未试 0（未试不参与任何冲突判断）。"""
    if obs.positive:
        return 1
    if obs.finding is Finding.SILENT:
        return -1
    return 0


def config_matches(a: Config, b: Config) -> bool:
    """同配置：机头都有记录且一致，机身记录不冲突。"""
    if a.head is None or b.head is None or a.head != b.head:
        return False
    if a.body is not None and b.body is not None and a.body != b.body:
        return False
    return True


def condition_relation(a: Condition, b: Condition) -> str:
    """两条工况的关系：

    - "exact"：档位、负载双方都有记录且一致——可以据此判断能否推翻。
    - "partial"：记录到的槽位不矛盾，但至少一方缺槽位——找不到对应
      条件，冲突时只能保留差异。
    - "distinct"：双方记录到的槽位已经不同——本来就是两种工况，
      互不否定（否定某一档位/某一负载不取消另一工况的观察）。
    """
    slots_a = [a.gear, a.load]
    slots_b = [b.gear, b.load]
    for x, y in zip(slots_a, slots_b):
        if x is not None and y is not None and x != y:
            return "distinct"
    if all(x is not None and y is not None for x, y in zip(slots_a, slots_b)):
        return "exact"
    if a.gear is None and b.gear is None:
        return "distinct"  # 连档位都对不上，无法建立对应
    return "partial"
