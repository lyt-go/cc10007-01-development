"""聚类分群业务规则：状态流转、字段校验、聚类输入快照接收口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.cluster_input import ClusterInputService
from app.store import store

MODULE = "cluster"
REQUIRED_FIELDS = ["任务编号", "聚类算法", "特征维度"]
STATUS_ORDER = ["草稿", "聚类计算中", "已完成", "已归档"]
ACTION_RULES = {"启动聚类": "聚类计算中", "确认完成": "已完成", "归档结果": "已归档"}
NEGATIVE_ACTIONS = []


class ClusterService:
    def __init__(self) -> None:
        self.input_service = ClusterInputService()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
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

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚类任务 {entry_id} 不存在或已归档"

        if action == "接收分群明细":
            return self._receive_input_detail(entry_id, entry)

        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于聚类分群可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        current = entry.get("status")
        if current in STATUS_ORDER and target in STATUS_ORDER:
            if STATUS_ORDER.index(target) < STATUS_ORDER.index(current):
                return None, f"聚类任务当前为「{current}」，不允许回退到「{target}」"
            if target == current:
                return None, f"聚类任务已是「{current}」，无需重复{action}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"聚类任务已{action}"

    def _receive_input_detail(
        self, entry_id: int, entry: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """待处理明细接收聚类输入分群数据集：只接受唯一可用的完整快照。"""
        if entry.get("status") not in ("草稿", "聚类计算中"):
            return None, f"聚类任务当前为「{entry.get('status')}」，不能再接收分群明细"
        snapshot, message, payload = self.input_service.receive_for_task(entry_id)
        if snapshot is None:
            # 拒绝时不写入任何半成品标签，任务保持原状态
            return None, message
        details = payload["details"]  # type: ignore[index]
        labels = "、".join(str(row["聚类标签"]) for row in details)
        coverage = sum(int(row["分群覆盖人数"]) for row in details)
        silhouette = round(
            sum(float(row["轮廓系数"]) for row in details) / len(details), 4
        )
        # 行为序列结论同步到聚类分群清单：标签、覆盖人数、轮廓系数落到任务行
        entry.update({
            "聚类标签": labels,
            "分群覆盖人数": coverage,
            "轮廓系数": silhouette,
            "关联快照编号": snapshot["快照编号"],
            "处理批次": snapshot["处理批次"],
            "高频行为路径": snapshot.get("高频行为路径", ""),
            "分群明细": details,
            "status": "聚类计算中",
            "pending": True,
            "abnormal": False,
        })
        return entry, f"聚类任务{message}"

    def task_details(self, entry_id: int) -> dict[str, Any] | None:
        """任务详情：同一快照下的聚类标签、分群覆盖人数、轮廓系数与批次核对信息。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        details = entry.get("分群明细") or []
        snapshot_no = entry.get("关联快照编号")
        review = None
        if entry.get("处理批次"):
            review = self.input_service.batch_review(entry["处理批次"])
        return {
            "任务": entry,
            "关联快照编号": snapshot_no,
            "处理批次": entry.get("处理批次"),
            "同快照分群明细": details,
            "批次复核": review,
        }
