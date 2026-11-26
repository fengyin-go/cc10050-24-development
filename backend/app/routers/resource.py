"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for
from app.services.parse_evidence import ParseEvidenceConflict
from app.store import store

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/metric_reconcile_report/{report_id}", response_model=dict)
def get_report(report_id: int) -> dict[str, Any]:
    """读取一条差额复核报告，并给出可复算的差额说明。"""
    service = service_for("collect_metrics")
    if service is None:
        raise HTTPException(status_code=404, detail="采集量指标模块未注册")
    report = store.find("metric_reconcile_report", report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"复核报告 {report_id} 不存在")
    return {**report, "差额复核": service.explain_diff(report_id)}


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        # 领域服务可直接给出一句原因；基础服务返回的是字段名清单。
        message = missing[0] if len(missing) == 1 and len(missing[0]) > 12 \
            else f"缺少必填字段：{'、'.join(missing)}"
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)


# ---- 证据交付专用接口：申请 / 补报 / 核对 / 下载 / 复核 ----
# 这些路径必须先于 /{module}/{entry_id} 注册同前缀的固定段，否则会被
# {entry_id} 的动态匹配抢先，导致 int 转换报 422。

def _evidence_service():
    service = service_for("parse_evidence")
    if service is None:
        raise HTTPException(status_code=404, detail="证据交付模块未注册")
    return service


@router.post("/parse_evidence/{entry_id}/supplement", response_model=dict)
def supplement_evidence(entry_id: int, payload: ActionPayload) -> dict[str, Any]:
    """补报样本明细：按指纹幂等，重复指纹不新增明细。"""
    service = _evidence_service()
    values = payload.values or {}
    result = service.supplement(
        entry_id,
        fingerprint=str(values.get("样本指纹", "") or ""),
        result=str(values.get("结果", "命中")),
        source=str(values.get("来源", "补报")),
        allow_after_download=bool(values.get("允许下载后补报")),
    )
    entry = result.pop("entry", None)
    result["summary"] = service.summary_of(entry) if entry else None
    return result


@router.get("/parse_evidence/{entry_id}/reconcile", response_model=dict)
def reconcile_evidence(entry_id: int) -> dict[str, Any]:
    """交付前分母核对：申请分母、实际明细与包内总量。"""
    service = _evidence_service()
    return service.reconcile(entry_id)


@router.post("/parse_evidence/{entry_id}/confirm", response_model=dict)
def confirm_evidence(entry_id: int, payload: ActionPayload) -> dict[str, Any]:
    """当班人确认继续导出，补报并入验收分母。"""
    service = _evidence_service()
    values = payload.values or {}
    try:
        result = service.confirm_export(
            entry_id, accept_supplemented=not values.get("拒绝补报"))
    except ParseEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["summary"] = service.summary_of(result.pop("entry")) if result.get("entry") else None
    return result


@router.get("/parse_evidence/{entry_id}/package")
def download_evidence(entry_id: int) -> StreamingResponse:
    """下载证据包 CSV；每次下载冻结一个交付批次。"""
    service = _evidence_service()
    try:
        filename, content = service.render_csv(entry_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/parse_evidence/{entry_id}/verify", response_model=dict)
def verify_evidence(entry_id: int, payload: ActionPayload) -> dict[str, Any]:
    """按实际下载文件复核证据；缺省用最近一次交付批次的冻结快照。"""
    service = _evidence_service()
    values = payload.values or {}
    batch = str(values.get("交付批次", "") or "")
    rows = values.get("明细")
    if rows is None and values.get("文件内容"):
        rows = service.parse_uploaded_rows(str(values["文件内容"]))
    return service.verify_download(entry_id, batch=batch, rows=rows)


@router.post("/parse_audit/{entry_id}/request-evidence", response_model=dict)
def request_evidence(entry_id: int) -> dict[str, Any]:
    """从解析质量审计窗口申请证据导出。"""
    service = service_for("parse_audit")
    if service is None:
        raise HTTPException(status_code=404, detail="解析质量审计模块未注册")
    return service.request_evidence(entry_id)


@router.post("/parse_evidence/{entry_id}/link", response_model=dict)
def link_evidence_review(entry_id: int, payload: ActionPayload) -> dict[str, Any]:
    """把实际下载文件对应的证据登记到采集量指标，返回指标记录 ID。"""
    service = _evidence_service()
    values = payload.values or {}
    batch = str(values.get("交付批次", "") or "")
    rows = values.get("明细")
    if rows is None and values.get("文件内容"):
        rows = service.parse_uploaded_rows(str(values["文件内容"]))
    result = service.link_metric_review(entry_id, batch=batch, rows=rows)
    result.pop("entry", None)
    return result


@router.post("/collect_metrics/{metric_id}/evidence-review", response_model=dict)
def evidence_review(metric_id: int, payload: ActionPayload) -> dict[str, Any]:
    """采集量指标按已下载的证据批次复核并写差额复核报告。"""
    service = service_for("collect_metrics")
    if service is None:
        raise HTTPException(status_code=404, detail="采集量指标模块未注册")
    values = payload.values or {}
    if values.get("证据交付单") and values.get("证据批次"):
        linked = _evidence_service().link_metric_review(
            int(values["证据交付单"]), batch=str(values["证据批次"]),
            rows=values.get("明细") or (
                _evidence_service().parse_uploaded_rows(str(values["文件内容"]))
                if values.get("文件内容") else None),
        )
        if not linked.get("ok"):
            return {"ok": False, "message": linked.get("message", "关联复核失败"),
                    "verify": linked.get("verify")}
    return service.apply_evidence_review(metric_id)

