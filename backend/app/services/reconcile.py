"""交付对账业务规则。

领域对象：
- 签单（manifest）：供应方签单的交付编号、应到字段与承诺行（行标识 + 承诺数量）。
- 接入批次（batch）：接入侧实际收到的分批凭据，含实际字段、实际行，支持交付/重送/更正。
- 复核记录（review）：针对字段错位、整行丢失、重复到货、超额行等差错的人工复核结论。
- 对账依据快照（basis）：批次确认时冻结的对账单，后续重送不再改写历史依据。

核心口径（所有规则只在本模块计算，路由层不做业务判断）：
1. 同一行标识两次到货只抵扣一次承诺数量：首次到货形成抵扣，之后的原行重送只记重复到货。
2. 更正行取代同一行标识的当前版本用于字段校验与可用量计算，但已抵扣数量不回补、不增加。
3. 字段错位（应到字段缺失/多余）与整行丢失（承诺行标识从未到货）分别立差错、分别复核。
4. 同一交付编号重送两次只计一次应收：应收行数只由签单承诺行决定，重复交付标红不增行。
5. 超额行（行标识不在签单里）不计入任何可用量。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

MANIFEST_REQUIRED = ["签单编号", "供应方", "数据集编号", "应到字段", "承诺行"]
BATCH_REQUIRED = ["凭据号", "交付编号", "实际字段", "实际行"]
BATCH_KINDS = ["交付", "重送", "更正"]
ROW_KINDS = ["原行", "更正行"]

# 差错类型
DISP_FIELD = "字段错位"
DISP_MISSING = "整行丢失"
DISP_DUP = "重复到货"
DISP_EXTRA = "超额行"
DISP_DELIVERY = "重复交付"
DISCREPANCY_TYPES = [DISP_FIELD, DISP_MISSING, DISP_DUP, DISP_EXTRA, DISP_DELIVERY]

REVIEW_CONCLUSIONS = ["确认差错", "驳回差错"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ReconcileRepository:
    """交付对账专用内存仓库：结构是嵌套的，不塞进通用扁平 store。"""

    def __init__(self) -> None:
        self.manifests: list[dict[str, Any]] = []
        self.batches: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self._seq = 0

    def next_id(self) -> int:
        self._seq += 1
        return self._seq

    def find_manifest(self, manifest_no: str) -> dict[str, Any] | None:
        for manifest in self.manifests:
            if manifest["签单编号"] == manifest_no:
                return manifest
        return None

    def find_batch(self, credential_no: str) -> dict[str, Any] | None:
        for batch in self.batches:
            if batch["凭据号"] == credential_no:
                return batch
        return None

    def batches_of(self, manifest_no: str) -> list[dict[str, Any]]:
        return [b for b in self.batches if b["签单编号"] == manifest_no]

    def reset(self, manifests: list[dict[str, Any]], batches: list[dict[str, Any]],
              reviews: list[dict[str, Any]]) -> None:
        self.manifests = manifests
        self.batches = batches
        self.reviews = reviews
        self._seq = max(
            [m["id"] for m in manifests] + [b["id"] for b in batches] + [0]
        )


repository = ReconcileRepository()


def review_key(credential_no: str, discrepancy_type: str, row_id: str) -> str:
    """复核记录的稳定键：差错项每次重算，但人工结论按键沉淀。"""
    return f"{credential_no}|{discrepancy_type}|{row_id}"


class ReconcileService:
    # ------------------------------------------------------------------ 签单
    def list_manifests(self, keyword: str | None = None) -> list[dict[str, Any]]:
        items = [self._manifest_summary(m) for m in repository.manifests]
        if keyword:
            items = [m for m in items if keyword in str(m.get("签单编号", ""))
                     or keyword in str(m.get("供应方", ""))]
        return items

    def get_manifest(self, manifest_no: str) -> dict[str, Any] | None:
        manifest = repository.find_manifest(manifest_no)
        if manifest is None:
            return None
        detail = deepcopy(manifest)
        detail["对账汇总"] = self.build_statement(manifest_no)["汇总"]
        return detail

    def create_manifest(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in MANIFEST_REQUIRED if not values.get(f)]
        if missing:
            return None, missing
        fields = [str(f).strip() for f in values["应到字段"] if str(f).strip()]
        lines_raw = values["承诺行"]
        if not isinstance(lines_raw, list) or not lines_raw:
            return None, ["承诺行"]
        lines: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for line in lines_raw:
            row_id = str(line.get("行标识") or "").strip()
            if not row_id:
                return None, ["承诺行.行标识"]
            if row_id in seen_ids:
                return None, [f"承诺行.行标识重复:{row_id}"]
            seen_ids.add(row_id)
            lines.append({"行标识": row_id, "承诺数量": max(0, _to_int(line.get("承诺数量"), 0))})
        if not fields:
            return None, ["应到字段"]
        manifest_no = str(values["签单编号"]).strip()
        if repository.find_manifest(manifest_no):
            return None, [f"签单编号已存在:{manifest_no}"]
        manifest = {
            "id": repository.next_id(),
            "签单编号": manifest_no,
            "供应方": str(values["供应方"]).strip(),
            "数据集编号": str(values["数据集编号"]).strip(),
            "应到字段": fields,
            "承诺行": lines,
            "签单时间": _now(),
            "状态": "对账中",
        }
        repository.manifests.append(manifest)
        return deepcopy(manifest), []

    # ------------------------------------------------------------------ 批次
    def create_batch(self, manifest_no: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        manifest = repository.find_manifest(manifest_no)
        if manifest is None:
            return None, [f"签单不存在:{manifest_no}"]
        missing = [f for f in BATCH_REQUIRED if f not in values or values.get(f) in (None, "", [])]
        if missing:
            return None, missing
        credential_no = str(values["凭据号"]).strip()
        if repository.find_batch(credential_no):
            return None, [f"凭据号已存在:{credential_no}"]
        kind = str(values.get("批次类型") or "交付").strip()
        if kind not in BATCH_KINDS:
            return None, [f"批次类型取值:{ '/'.join(BATCH_KINDS) }"]
        actual_fields = [str(f).strip() for f in values["实际字段"] if str(f).strip()]
        rows_raw = values["实际行"]
        if not isinstance(rows_raw, list) or not rows_raw:
            return None, ["实际行"]
        actual_rows: list[dict[str, Any]] = []
        for row in rows_raw:
            row_id = str(row.get("行标识") or "").strip()
            if not row_id:
                return None, ["实际行.行标识"]
            row_kind = str(row.get("行类型") or "原行").strip()
            if row_kind not in ROW_KINDS:
                return None, [f"行类型取值:{ '/'.join(ROW_KINDS) }"]
            actual_rows.append({
                "行标识": row_id,
                "实际数量": max(0, _to_int(row.get("实际数量"), 0)),
                "行类型": row_kind,
            })
        delivery_no = str(values["交付编号"]).strip()
        repeated_delivery = any(b["交付编号"] == delivery_no for b in repository.batches_of(manifest_no))
        batch = {
            "id": repository.next_id(),
            "签单编号": manifest_no,
            "凭据号": credential_no,
            "交付编号": delivery_no,
            "批次类型": kind,
            "实际字段": actual_fields,
            "实际行": actual_rows,
            "接收时间": _now(),
            "重复交付": repeated_delivery,
            "对账依据快照": None,
        }
        repository.batches.append(batch)
        return deepcopy(batch), []

    def list_batches(self, manifest_no: str) -> list[dict[str, Any]]:
        return [self._batch_summary(b) for b in repository.batches_of(manifest_no)]

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        for batch in repository.batches:
            if batch["id"] == batch_id:
                return deepcopy(batch)
        return None

    def freeze_basis(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        batch = next((b for b in repository.batches if b["id"] == batch_id), None)
        if batch is None:
            return None, f"接入批次 {batch_id} 不存在"
        manifest = repository.find_manifest(batch["签单编号"])
        if manifest is None:
            return None, "批次关联的签单不存在"
        statement = self.build_statement(manifest["签单编号"])
        snapshot = {
            "冻结时间": _now(),
            "冻结凭据号": batch["凭据号"],
            "签单快照": deepcopy(manifest),
            "分批凭据": [self._batch_summary(b) for b in repository.batches_of(manifest["签单编号"])],
            "对账单": statement,
        }
        batch["对账依据快照"] = snapshot
        return deepcopy(snapshot), f"批次 {batch['凭据号']} 的对账依据已冻结"

    # ------------------------------------------------------------------ 复核
    def add_review(self, manifest_no: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        manifest = repository.find_manifest(manifest_no)
        if manifest is None:
            return None, f"签单 {manifest_no} 不存在"
        key = str(values.get("复核键") or "").strip()
        conclusion = str(values.get("结论") or "").strip()
        if not key:
            return None, "缺少复核键"
        if conclusion not in REVIEW_CONCLUSIONS:
            return None, f"复核结论只能是：{'、'.join(REVIEW_CONCLUSIONS)}"
        active_keys = {item["复核键"] for item in self.build_statement(manifest_no)["差错清单"]}
        if key not in active_keys:
            return None, "复核键不在当前对账单的差错清单里，无法复核"
        record = {
            "id": repository.next_id(),
            "签单编号": manifest_no,
            "复核键": key,
            "结论": conclusion,
            "备注": str(values.get("备注") or "").strip(),
            "复核时间": _now(),
        }
        repository.reviews.append(record)
        return deepcopy(record), "复核结论已记录"

    # -------------------------------------------------------------- 对账引擎
    def build_statement(self, manifest_no: str) -> dict[str, Any]:
        manifest = repository.find_manifest(manifest_no)
        if manifest is None:
            raise KeyError(manifest_no)
        promised_fields = list(manifest["应到字段"])
        promised = {line["行标识"]: int(line["承诺数量"]) for line in manifest["承诺行"]}

        # row_id -> 首次到货（抵扣依据，永不被后续重送改写）
        accepted: dict[str, dict[str, Any]] = {}
        # 所有出现过的行标识：字段错位的到货也算到货，整行丢失只看行标识是否出现
        arrived: set[str] = set()
        # row_id -> 最近一次到货（重送原行会刷新当前版本，但不产生新抵扣）
        latest: dict[str, dict[str, Any]] = {}
        # row_id -> 最近一次更正行（优先级最高，决定当前字段与可用量）
        corrected: dict[str, dict[str, Any]] = {}
        discrepancies: list[dict[str, Any]] = []
        seen_deliveries: set[str] = set()

        for batch in repository.batches_of(manifest_no):
            credential = batch["凭据号"]
            if batch["交付编号"] in seen_deliveries:
                discrepancies.append(self._disp(
                    DISP_DELIVERY, "—", credential,
                    f"交付编号 {batch['交付编号']} 已到过货，重复交付只计一次应收行数",
                ))
            seen_deliveries.add(batch["交付编号"])

            for row in batch["实际行"]:
                row_id = row["行标识"]
                qty = int(row["实际数量"])
                if row_id not in promised:
                    discrepancies.append(self._disp(
                        DISP_EXTRA, row_id, credential,
                        f"行标识不在签单承诺中，实际 {qty} 不计入可用量",
                    ))
                    continue
                arrived.add(row_id)
                # 每次到货都按应到字段核一遍：字段错位按凭据分别立账、分别复核
                field_note = self._field_note(promised_fields, batch["实际字段"])
                if field_note:
                    discrepancies.append(self._disp(
                        DISP_FIELD, row_id, credential,
                        f"{'更正版' if row['行类型'] == '更正行' else '到货'}字段错位，{field_note}",
                    ))
                if row_id not in accepted:
                    # 首次到货：形成唯一一次承诺抵扣
                    accepted[row_id] = {
                        "凭据号": credential,
                        "实际数量": qty,
                        "行类型": row["行类型"],
                        "实际字段": list(batch["实际字段"]),
                    }
                else:
                    # 同一行标识第二次及以后到货：原行只记重复到货，抵扣量保持首单
                    if row["行类型"] == "更正行":
                        corrected[row_id] = {
                            "凭据号": credential,
                            "实际数量": qty,
                            "实际字段": list(batch["实际字段"]),
                        }
                    else:
                        discrepancies.append(self._disp(
                            DISP_DUP, row_id, credential,
                            f"该行标识已于凭据 {accepted[row_id]['凭据号']} 抵扣过承诺数量，重送原行不再抵扣",
                        ))
                latest[row_id] = {
                    "凭据号": credential,
                    "实际数量": qty,
                    "行类型": row["行类型"],
                    "实际字段": list(batch["实际字段"]),
                }

        # 行级对账：抵扣量冻结、可用量随更正版本变化
        row_lines: list[dict[str, Any]] = []
        for row_id, promised_qty in promised.items():
            first = accepted.get(row_id)
            correction = corrected.get(row_id)
            # 当前版本优先级：最近更正行 > 最近一次到货 > 首单
            effective = correction or latest.get(row_id) or first
            deducted = min(first["实际数量"], promised_qty) if first else 0
            if effective is None:
                effective_fields: list[str] = []
                effective_qty = 0
                effective_credential = "—"
            else:
                effective_fields = list(effective["实际字段"])
                effective_qty = int(effective["实际数量"])
                effective_credential = effective["凭据号"]
            missing_fields = [f for f in promised_fields if f not in effective_fields] if effective else list(promised_fields)
            extra_fields = [f for f in effective_fields if f not in promised_fields] if effective else []
            field_ok = bool(effective) and not missing_fields and not extra_fields
            usable_qty = min(effective_qty, promised_qty) if field_ok else 0
            if not effective:
                row_status = "整行丢失"
            elif correction:
                row_status = "可交付（更正版）" if field_ok else "字段错位（已更正仍错位）"
            elif not field_ok:
                row_status = "字段错位"
            else:
                row_status = "可交付"
            row_lines.append({
                "行标识": row_id,
                "承诺数量": promised_qty,
                "已抵扣数量": deducted,
                "当前实际可用数量": usable_qty,
                "抵扣差额": promised_qty - deducted,
                "可用缺口": promised_qty - usable_qty,
                "字段状态": "字段一致" if field_ok else "字段错位",
                "行状态": row_status,
                "首次凭据号": first["凭据号"] if first else "—",
                "当前凭据号": effective_credential,
                "缺失字段": missing_fields,
                "多余字段": extra_fields,
            })

        for row_id in promised:
            if row_id not in arrived:
                discrepancies.append(self._disp(
                    DISP_MISSING, row_id, "签单",
                    "全部接入批次中均未收到该行标识，承诺数量尚未抵扣",
                ))

        review_map = {r["复核键"]: r for r in repository.reviews if r["签单编号"] == manifest_no}
        for item in discrepancies:
            record = review_map.get(item["复核键"])
            item["复核状态"] = record["结论"] if record else "待复核"
            item["复核时间"] = record["复核时间"] if record else None

        type_counter = {t: 0 for t in DISCREPANCY_TYPES}
        for item in discrepancies:
            type_counter[item["差错类型"]] += 1

        missing_ids = [line["行标识"] for line in row_lines if line["行状态"] == "整行丢失"]
        summary = {
            "应收行数": len(promised),
            "已抵扣行数": len(arrived),
            "缺行数": len(missing_ids),
            "承诺总量": sum(promised.values()),
            "已抵扣总量": sum(line["已抵扣数量"] for line in row_lines),
            "当前可用总量": sum(line["当前实际可用数量"] for line in row_lines),
            "凭据数": len(repository.batches_of(manifest_no)),
            "去重交付数": len(seen_deliveries),
            "差错计数": type_counter,
            "缺行编号集合": missing_ids,
            "可交付编号集合": [rid for rid in promised if rid not in set(missing_ids)],
        }
        return {
            "签单编号": manifest_no,
            "生成时间": _now(),
            "汇总": summary,
            "行对账": row_lines,
            "差错清单": discrepancies,
            "分批凭据": [self._batch_summary(b) for b in repository.batches_of(manifest_no)],
        }

    def deliverable(self, manifest_no: str) -> dict[str, Any]:
        """交付缺行对账单 -> 数据服务实际可交付编号集合。"""
        statement = self.build_statement(manifest_no)
        return {
            "签单编号": manifest_no,
            "生成时间": statement["生成时间"],
            "可交付编号集合": statement["汇总"]["可交付编号集合"],
            "缺行编号集合": statement["汇总"]["缺行编号集合"],
            "差错计数": statement["汇总"]["差错计数"],
        }

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _field_note(promised_fields: list[str], actual_fields: list[str]) -> str:
        missing = [f for f in promised_fields if f not in actual_fields]
        extra = [f for f in actual_fields if f not in promised_fields]
        parts: list[str] = []
        if missing:
            parts.append(f"缺失字段：{'、'.join(missing)}")
        if extra:
            parts.append(f"多余字段：{'、'.join(extra)}")
        return "，".join(parts)

    @staticmethod
    def _disp(discrepancy_type: str, row_id: str, credential_no: str, note: str) -> dict[str, Any]:
        return {
            "差错类型": discrepancy_type,
            "行标识": row_id,
            "凭据号": credential_no,
            "说明": note,
            "复核键": review_key(credential_no, discrepancy_type, row_id),
            "复核状态": "待复核",
            "复核时间": None,
        }

    @staticmethod
    def _batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": batch["id"],
            "签单编号": batch["签单编号"],
            "凭据号": batch["凭据号"],
            "交付编号": batch["交付编号"],
            "批次类型": batch["批次类型"],
            "实际字段": list(batch["实际字段"]),
            "实际行数": len(batch["实际行"]),
            "接收时间": batch["接收时间"],
            "重复交付": batch["重复交付"],
            "已冻结依据": batch["对账依据快照"] is not None,
        }

    @staticmethod
    def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": manifest["id"],
            "签单编号": manifest["签单编号"],
            "供应方": manifest["供应方"],
            "数据集编号": manifest["数据集编号"],
            "应到字段": list(manifest["应到字段"]),
            "承诺行数": len(manifest["承诺行"]),
            "承诺总量": sum(int(line["承诺数量"]) for line in manifest["承诺行"]),
            "签单时间": manifest["签单时间"],
            "状态": manifest["状态"],
        }


def build_seed() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """示例场景：一份分两次交付后收到错位重送的签单，一份更正+超额行的签单。"""
    manifests: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    seq = 0

    def nid() -> int:
        nonlocal seq
        seq += 1
        return seq

    m1 = {
        "id": nid(),
        "签单编号": "MANI-0001",
        "供应方": "华信数据服务商",
        "数据集编号": "DATA-0001",
        "应到字段": ["客户编号", "手机号", "地区", "授信额度"],
        "承诺行": [
            {"行标识": "R-01", "承诺数量": 100},
            {"行标识": "R-02", "承诺数量": 100},
            {"行标识": "R-03", "承诺数量": 100},
            {"行标识": "R-04", "承诺数量": 100},
            {"行标识": "R-05", "承诺数量": 100},
        ],
        "签单时间": "2026-11-10T09:00:00",
        "状态": "对账中",
    }
    manifests.append(m1)
    b1 = {
        "id": nid(), "签单编号": "MANI-0001", "凭据号": "CRED-1001",
        "交付编号": "DLV-2001", "批次类型": "交付", "重复交付": False, "对账依据快照": None,
        "实际字段": ["客户编号", "手机号", "地区", "授信额度"],
        "实际行": [
            {"行标识": "R-01", "实际数量": 100, "行类型": "原行"},
            {"行标识": "R-02", "实际数量": 100, "行类型": "原行"},
        ],
        "接收时间": "2026-11-11T10:00:00",
    }
    b2 = {
        "id": nid(), "签单编号": "MANI-0001", "凭据号": "CRED-1002",
        "交付编号": "DLV-2002", "批次类型": "交付", "重复交付": False, "对账依据快照": None,
        "实际字段": ["客户编号", "手机号", "地区", "授信额度"],
        "实际行": [
            {"行标识": "R-03", "实际数量": 100, "行类型": "原行"},
            {"行标识": "R-04", "实际数量": 80, "行类型": "原行"},
        ],
        "接收时间": "2026-11-12T10:00:00",
    }
    # 错位重送：字段名整体错位且多一列，含 R-01 原行重送与缺行 R-05 的补发
    b3 = {
        "id": nid(), "签单编号": "MANI-0001", "凭据号": "CRED-1003",
        "交付编号": "DLV-2003", "批次类型": "重送", "重复交付": False, "对账依据快照": None,
        "实际字段": ["客户编号", "手机", "区域", "授信额度", "备注"],
        "实际行": [
            {"行标识": "R-01", "实际数量": 100, "行类型": "原行"},
            {"行标识": "R-05", "实际数量": 100, "行类型": "原行"},
        ],
        "接收时间": "2026-11-13T10:00:00",
    }
    batches.extend([b1, b2, b3])

    m2 = {
        "id": nid(),
        "签单编号": "MANI-0002",
        "供应方": "北辰信息供应商",
        "数据集编号": "DATA-0002",
        "应到字段": ["订单号", "收货人", "地址", "金额"],
        "承诺行": [
            {"行标识": "O-01", "承诺数量": 50},
            {"行标识": "O-02", "承诺数量": 50},
            {"行标识": "O-03", "承诺数量": 50},
        ],
        "签单时间": "2026-11-14T09:00:00",
        "状态": "对账中",
    }
    manifests.append(m2)
    c1 = {
        "id": nid(), "签单编号": "MANI-0002", "凭据号": "CRED-2001",
        "交付编号": "DLV-3001", "批次类型": "交付", "重复交付": False, "对账依据快照": None,
        "实际字段": ["订单号", "收货人", "地址"],
        "实际行": [
            {"行标识": "O-01", "实际数量": 50, "行类型": "原行"},
            {"行标识": "O-02", "实际数量": 50, "行类型": "原行"},
            {"行标识": "O-03", "实际数量": 50, "行类型": "原行"},
        ],
        "接收时间": "2026-11-15T10:00:00",
    }
    # 更正版修好了 O-01 的字段，但另一列多出两行 X-01/X-02
    c2 = {
        "id": nid(), "签单编号": "MANI-0002", "凭据号": "CRED-2002",
        "交付编号": "DLV-3002", "批次类型": "更正", "重复交付": False, "对账依据快照": None,
        "实际字段": ["订单号", "收货人", "地址", "金额"],
        "实际行": [
            {"行标识": "O-01", "实际数量": 50, "行类型": "更正行"},
            {"行标识": "X-01", "实际数量": 10, "行类型": "原行"},
            {"行标识": "X-02", "实际数量": 10, "行类型": "原行"},
        ],
        "接收时间": "2026-11-16T10:00:00",
    }
    # 同一批里原行与更正行同时重送
    c3 = {
        "id": nid(), "签单编号": "MANI-0002", "凭据号": "CRED-2003",
        "交付编号": "DLV-3003", "批次类型": "重送", "重复交付": False, "对账依据快照": None,
        "实际字段": ["订单号", "收货人", "地址", "金额"],
        "实际行": [
            {"行标识": "O-02", "实际数量": 50, "行类型": "原行"},
            {"行标识": "O-02", "实际数量": 50, "行类型": "更正行"},
        ],
        "接收时间": "2026-11-17T10:00:00",
    }
    # 同一交付编号 DLV-3002 再送一次：应收行数不增加
    c4 = {
        "id": nid(), "签单编号": "MANI-0002", "凭据号": "CRED-2004",
        "交付编号": "DLV-3002", "批次类型": "重送", "重复交付": True, "对账依据快照": None,
        "实际字段": ["订单号", "收货人", "地址", "金额"],
        "实际行": [
            {"行标识": "O-03", "实际数量": 50, "行类型": "原行"},
        ],
        "接收时间": "2026-11-18T10:00:00",
    }
    batches.extend([c1, c2, c3, c4])
    return manifests, batches, []


def seed_reconcile() -> None:
    repository.reset(*build_seed())
