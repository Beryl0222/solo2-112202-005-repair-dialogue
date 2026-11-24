"""对照台资产：借出登记、唯一托付与实物配置消耗。

- 同一组对照件只能托给一个尚未完成的检修；
- 会拆改用户封装的检查（如装上 B 头会拆下用户封装的备用头），
  动手前必须列出随之不可复现的观察。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import find_head


@dataclass
class Asset:
    id: str
    name: str
    holder: str | None  # 持有案号；None 表示台架空闲
    retained_state: str  # 移动检具时会改变的物理配置（原文）
    loan_to: str

    @property
    def available(self) -> bool:
        return self.holder is None


class AssetPool:
    def __init__(self, assets: list[Asset]):
        self._assets = {a.name: a for a in assets}

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "AssetPool":
        assets = []
        for row in rows:
            loan_to = row["loan_to"].strip()
            holder = None if not loan_to or loan_to.startswith("台架") else loan_to.split()[0]
            assets.append(Asset(
                id=row["id"],
                name=row["asset"].replace(" ", ""),
                holder=holder,
                retained_state=row["retained_state"],
                loan_to=loan_to,
            ))
        return cls(assets)

    def get(self, name: str) -> Asset | None:
        return self._assets.get(name)

    def held_by(self, case: str) -> list[Asset]:
        return [a for a in self._assets.values() if a.holder == case]

    def book(self, name: str, case: str) -> Asset:
        """把对照件托给一案；已被其他未完成检修持有时拒绝。"""
        asset = self._assets.get(name)
        if asset is None:
            raise KeyError(f"对照台没有 {name}")
        if asset.holder is not None and asset.holder != case:
            raise RuntimeError(
                f"{name} 已借给 {asset.holder}（{asset.loan_to}），"
                f"同一组对照件只能托给一个尚未完成的检修"
            )
        asset.holder = case
        return asset

    def release(self, name: str, case: str) -> None:
        asset = self._assets.get(name)
        if asset is None or asset.holder != case:
            raise RuntimeError(f"{name} 不在 {case} 手上，无法归还")
        asset.holder = None

    def consumed_configs(self, name: str) -> list[str]:
        """移动该检具会拆掉的实物配置（从 retained_state 原文解析）。"""
        asset = self._assets.get(name)
        if asset is None:
            return []
        consumed = []
        for match in re.finditer(r"会拆下([^，。]+)", asset.retained_state):
            head = find_head(match.group(1))
            if head:
                consumed.append(head)
        return consumed
