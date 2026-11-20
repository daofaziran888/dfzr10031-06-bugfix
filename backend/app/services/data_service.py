"""数据API发布业务规则：状态流转、字段校验与筛选口径都收在这里。

发布数据服务时可以挂接交付对账：用交付缺行对账单计算实际可交付编号集合，
并把发布当时的对账单作为对账依据冻结在服务记录上，后续重送不影响已发布依据。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.reconcile import ReconcileService
from app.store import store

MODULE = "data_service"
REQUIRED_FIELDS = ["服务编号", "服务名称", "输出数据集"]
STATUS_ORDER = ["草稿", "已配置", "已发布", "已停用"]
ACTION_RULES = {"保存配置": "已配置", "发布服务": "已发布", "停用服务": "已停用"}
# 带对账依据的发布：状态同样落到已发布，但要额外冻结交付缺行对账单
PUBLISH_WITH_BASIS = "发布服务并固化依据"
ACTION_RULES[PUBLISH_WITH_BASIS] = "已发布"
NEGATIVE_ACTIONS = ["停用服务"]


class DataServiceService:
    def __init__(self) -> None:
        self.reconcile = ReconcileService()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("服务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def publish_with_basis(self, entry_id: int, manifest_no: str) -> tuple[dict[str, Any] | None, str]:
        """发布服务并固化依据：可交付编号集合来自交付缺行对账单，依据即时冻结。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据服务 {entry_id} 不存在或已归档"
        if self.reconcile.get_manifest(manifest_no) is None:
            return None, f"签单 {manifest_no} 不存在，无法按交付对账单发布"
        statement = self.reconcile.build_statement(manifest_no)
        deliverable = self.reconcile.deliverable(manifest_no)
        basis = {
            "冻结时间": datetime.now().isoformat(timespec="seconds"),
            "签单编号": manifest_no,
            "可交付编号集合": list(deliverable["可交付编号集合"]),
            "缺行编号集合": list(deliverable["缺行编号集合"]),
            "对账单": statement,
        }
        entry["status"] = "已发布"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["交付签单编号"] = manifest_no
        entry["可交付编号集合"] = list(deliverable["可交付编号集合"])
        entry["对账依据"] = basis
        return entry, f"数据服务已发布，按签单 {manifest_no} 固化 {len(basis['可交付编号集合'])} 个可交付编号"

    def get_delivery_basis(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None or "对账依据" not in entry:
            return None
        return entry["对账依据"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据服务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据API发布可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"数据服务已{action}"
