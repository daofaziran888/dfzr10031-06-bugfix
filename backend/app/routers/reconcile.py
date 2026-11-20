"""交付对账接口：登记签单与接入批次、查看对账单、复核差错、冻结对账依据。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.reconcile import ReconcileService

router = APIRouter(prefix="/api/reconcile", tags=["交付对账"])

service = ReconcileService()


@router.get("/manifests", response_model=PageResult[dict])
def list_manifests(keyword: str | None = None) -> PageResult[dict]:
    """签单列表：按签单编号或供应方关键字过滤。"""
    items = service.list_manifests(keyword=keyword)
    return PageResult(items=items, total=len(items), page=1, size=len(items) or 1)


@router.post("/manifests", response_model=ActionResult)
def create_manifest(payload: EntryPayload) -> ActionResult:
    """登记供应方签单：交付编号（签单编号）、应到字段与承诺行（行标识+承诺数量）。"""
    entry, problems = service.create_manifest(payload.values)
    if entry is None:
        return ActionResult(ok=False, message="签单登记失败：" + "、".join(problems))
    return ActionResult(ok=True, message="签单已登记，可开始接入批次对账", entry=entry)


@router.get("/manifests/{manifest_no}")
def get_manifest(manifest_no: str) -> dict[str, Any]:
    """签单明细：承诺行与当前对账汇总。"""
    entry = service.get_manifest(manifest_no)
    if entry is None:
        return {"ok": False, "message": f"签单 {manifest_no} 不存在"}
    return entry


@router.post("/manifests/{manifest_no}/batches", response_model=ActionResult)
def create_batch(manifest_no: str, payload: EntryPayload) -> ActionResult:
    """录入接入批次：交付编号、凭据号、实际字段与实际行；重复交付与更正行由后端识别。"""
    entry, problems = service.create_batch(manifest_no, payload.values)
    if entry is None:
        return ActionResult(ok=False, message="接入批次录入失败：" + "、".join(problems))
    return ActionResult(ok=True, message="接入批次已录入，对账单已刷新", entry=entry)


@router.get("/manifests/{manifest_no}/batches")
def list_batches(manifest_no: str) -> dict[str, Any]:
    """同一签单关联的分批凭据，全部可查询，重复交付会被标红。"""
    if service.get_manifest(manifest_no) is None:
        return {"ok": False, "message": f"签单 {manifest_no} 不存在", "items": []}
    items = service.list_batches(manifest_no)
    return {"items": items, "total": len(items)}


@router.get("/manifests/{manifest_no}/statement")
def get_statement(manifest_no: str) -> dict[str, Any]:
    """交付对账单：行级抵扣、差错清单（字段错位/整行丢失分开）、分批凭据。"""
    try:
        return service.build_statement(manifest_no)
    except KeyError:
        return {"ok": False, "message": f"签单 {manifest_no} 不存在"}


@router.get("/manifests/{manifest_no}/deliverable")
def get_deliverable(manifest_no: str) -> dict[str, Any]:
    """交付缺行对账单：数据服务实际可交付编号集合。"""
    if service.get_manifest(manifest_no) is None:
        return {"ok": False, "message": f"签单 {manifest_no} 不存在"}
    return service.deliverable(manifest_no)


@router.post("/manifests/{manifest_no}/reviews", response_model=ActionResult)
def add_review(manifest_no: str, payload: EntryPayload) -> ActionResult:
    """对字段错位、整行丢失等差错逐项复核，结论按复核键沉淀，重送后仍保留。"""
    record, message = service.add_review(manifest_no, payload.values)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    """单批次实际明细：实际字段、实际行与是否已冻结对账依据。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        return {"ok": False, "message": f"接入批次 {batch_id} 不存在"}
    return batch


@router.post("/batches/{batch_id}/freeze", response_model=ActionResult)
def freeze_basis(batch_id: int) -> ActionResult:
    """确认交付：冻结该批次当时的对账单作为对账依据，后续重送不再改写。"""
    snapshot, message = service.freeze_basis(batch_id)
    if snapshot is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=snapshot)
