"""聚类输入分群接口：新链路事件接收、快照构建发布、批次复核与聚类下发。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cluster_input import (
    SnapshotInterrupted,
    SNAPSHOT_INTERRUPTED,
    ClusterInputService,
)

router = APIRouter(prefix="/api/cluster-input", tags=["聚类输入分群"])

service = ClusterInputService()

EVENT_FIELDS = ["事件编号", "模式编号", "用户标识", "事件名称", "事件序号", "事件时间", "记录版本", "接收链路"]
SNAPSHOT_FIELDS = [
    "快照编号", "处理批次", "处理状态", "唯一可用", "版本序号", "来源模式数",
    "事件记录数", "分群数", "覆盖人数", "轮廓系数", "高频行为路径", "创建时间",
]
DETAIL_FIELDS = ["快照编号", "处理批次", "聚类标签", "分群覆盖人数", "轮廓系数", "高频行为路径", "关联模式编号"]


@router.get("/events", response_model=PageResult[dict])
def list_events(
    pattern_no: str | None = Query(default=None, description="按模式编号过滤事件记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取新链路接收到的序列模式事件记录。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = service.list_events(pattern_no=pattern_no, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/events", response_model=ActionResult)
def ingest_event(payload: EntryPayload) -> ActionResult:
    """接收供新能力推送的一条序列模式事件记录。"""
    entry, missing = service.ingest_event(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"事件接收失败：{'、'.join(missing)}")
    return ActionResult(ok=True, message="序列模式事件记录已按新链路接收", entry=entry)


@router.post("/snapshots", response_model=ActionResult)
def generate_snapshot(payload: EntryPayload) -> ActionResult:
    """读取已确认模式的事件序列并计算分群特征，原子发布一个完整快照。"""
    values = payload.values or {}
    simulate_interrupt = bool(values.get("simulate_interrupt") or values.get("模拟中断"))
    batch_no = str(values.get("处理批次") or "").strip() or None
    try:
        snapshot, message = service.generate_snapshot(
            batch_no=batch_no,
            simulate_interrupt=simulate_interrupt,
            remark=payload.remark,
        )
    except SnapshotInterrupted as exc:
        return ActionResult(ok=False, message=f"快照写入中断：{exc}，半成品标签未暴露给聚类任务")
    if snapshot is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=snapshot)


@router.get("/snapshots", response_model=PageResult[dict])
def list_snapshots(
    batch_no: str | None = Query(default=None, description="按处理批次核对"),
    state: str | None = Query(default=None, description="构建中、已发布、已替代、写入中断"),
    include_interrupted: bool = Query(default=True, description="复核视图是否包含写入中断的半成品"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """快照清单：复核时可按处理批次过滤；默认展示含中断在内的全部版本。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_snapshots(
        batch_no=batch_no,
        state=state,
        include_interrupted=include_interrupted,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/snapshots/published")
def get_published_snapshot() -> dict[str, Any]:
    """聚类侧读取入口：只返回唯一可用的已发布快照及其分群明细。"""
    snapshot = service.published_snapshot()
    if snapshot is None:
        return {"ok": False, "message": "当前没有唯一可用的聚类输入分群明细数据集", "snapshot": None, "details": []}
    details = service.list_details(snapshot_no=snapshot["快照编号"], cluster_visible_only=True)
    return {"ok": True, "snapshot": snapshot, "details": details}


@router.get("/snapshots/{snapshot_no}")
def get_snapshot(snapshot_no: str) -> dict[str, Any]:
    """快照详情：头信息 + 分群明细；写入中断的快照只给复核视图，不进聚类下发。"""
    snapshot = service.get_snapshot(snapshot_no)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"快照 {snapshot_no} 不存在")
    details = service.list_details(snapshot_no=snapshot_no)
    return {
        "snapshot": snapshot,
        "details": details,
        "cluster_visible": snapshot.get("处理状态") != SNAPSHOT_INTERRUPTED and snapshot.get("唯一可用", False),
    }


@router.get("/details")
def list_details(
    snapshot_no: str | None = Query(default=None),
    batch_no: str | None = Query(default=None),
    cluster_visible_only: bool = Query(default=False, description="聚类任务读取时只看已发布完整明细"),
) -> dict[str, Any]:
    """分群明细数据集：可按快照/批次过滤；cluster_visible_only 排除一切半成品。"""
    rows = service.list_details(
        snapshot_no=snapshot_no,
        batch_no=batch_no,
        cluster_visible_only=cluster_visible_only,
    )
    return {"total": len(rows), "items": rows}


@router.get("/batches/{batch_no}/review")
def review_batch(batch_no: str) -> dict[str, Any]:
    """按处理批次复核快照与分群覆盖口径。"""
    review = service.batch_review(batch_no)
    if review is None:
        raise HTTPException(status_code=404, detail=f"处理批次 {batch_no} 不存在")
    return review


@router.post("/patterns/{entry_id}/receipts", response_model=ActionResult)
def handle_pattern_receipt(entry_id: int, payload: EntryPayload) -> ActionResult:
    """同一模式的确认回执/归档事件入口：串行处理，聚类侧不会同时读到两份半成品。"""
    receipt_type = str(payload.values.get("receipt_type") or payload.values.get("回执类型") or "").strip()
    entry, message = service.handle_pattern_receipt(entry_id, receipt_type)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_snapshots() -> dict[str, Any]:
    """导出聚类输入分群数据集清单（仅完整已发布版本，不含半成品）。"""
    items, total = service.list_snapshots(include_interrupted=False, page=1, size=10000)
    return {"module": "cluster_input", "total": total, "items": items}
