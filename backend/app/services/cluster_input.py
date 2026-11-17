"""聚类输入分群服务：把新链路事件记录加工为可追溯的聚类输入快照。

关键约束（与业务规则一一对应）：

1. 只接收「已确认」序列模式驱动的事件记录，挖掘中/已归档模式不进正式快照；
2. 快照先以「构建中」暂存，分群明细全部写完才原子切换为「已发布」；
   写入中断时保留「写入中断」记录用于复核，但半成品标签对聚类任务不可见；
3. 新版本发布后旧版本自动置「已替代」，聚类侧任意时刻只存在唯一可用结果；
4. 同一模式的确认回执与归档事件通过模式级锁串行处理，聚类侧永远不会
   同时看到两份半成品。
"""
from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime
from typing import Any

from app.services import cluster_engine
from app.services.sequence import SequenceService
from app.store import store

SNAPSHOT_MODULE = "cluster_input_snapshot"
DETAIL_MODULE = "cluster_input_detail"
EVENT_MODULE = "sequence_event"

SNAPSHOT_BUILDING = "构建中"
SNAPSHOT_PUBLISHED = "已发布"
SNAPSHOT_REPLACED = "已替代"
SNAPSHOT_INTERRUPTED = "写入中断"
VISIBLE_FROM_CLUSTER = SNAPSHOT_PUBLISHED

# 快照级锁：保证同一进程内同一时刻只有一个快照在构建/发布
_snapshot_lock = threading.RLock()
# 模式级锁：同一序列模式的确认回执、归档事件串行落地
_pattern_locks: dict[str, threading.RLock] = {}
_pattern_locks_guard = threading.Lock()


class SnapshotInterrupted(RuntimeError):
    """模拟写入中断：明细尚未写完即失败，半成品不允许发布。"""


def pattern_lock(pattern_no: str) -> threading.RLock:
    with _pattern_locks_guard:
        lock = _pattern_locks.get(pattern_no)
        if lock is None:
            lock = threading.RLock()
            _pattern_locks[pattern_no] = lock
        return lock


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


def _next_snapshot_no() -> str:
    return f"SNAP-{max((int(row.get('id', 0)) for row in store.rows(SNAPSHOT_MODULE)), default=0) + 1:04d}"


def _next_batch_no() -> str:
    today = datetime.now().strftime("%Y%m%d")
    same_day = [
        row for row in store.rows(SNAPSHOT_MODULE)
        if str(row.get("处理批次", "")).startswith(f"BATCH-{today}")
    ]
    return f"BATCH-{today}-{len(same_day) + 1:03d}"


def _next_version() -> int:
    return max((int(row.get("版本序号", 0)) for row in store.rows(SNAPSHOT_MODULE)), default=0) + 1


class ClusterInputService:
    """聚类输入分群明细数据集的构建、发布、复核与下发。"""

    # ---------- 新链路输入：序列模式事件记录 ----------

    def ingest_event(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """接收供新能力推送的序列模式事件记录，按模式编号挂到新链路输入表。"""
        required = ["模式编号", "用户标识", "事件名称", "事件序号"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        pattern_no = str(values["模式编号"]).strip()
        pattern = self._find_pattern(pattern_no)
        if pattern is None:
            return None, [f"序列模式 {pattern_no} 不存在"]
        rows = store.rows(EVENT_MODULE)
        entry = {"id": _next_id(EVENT_MODULE)}
        entry.update({field: values.get(field) for field in required})
        entry["事件编号"] = str(values.get("事件编号") or f"EVT-{entry['id']:04d}")
        entry["事件时间"] = str(values.get("事件时间") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        entry["记录版本"] = str(values.get("记录版本") or "v1")
        entry["接收链路"] = "供新能力-序列模式事件"
        # 事件先落库为「已接收」；只有模式处于已确认时才允许进入正式快照
        entry["status"] = "已接收"
        entry["pending"] = pattern.get("status") != "已确认"
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def list_events(
        self,
        *,
        pattern_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(EVENT_MODULE)
        if pattern_no:
            rows = [row for row in rows if pattern_no in str(row.get("模式编号", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ---------- 快照构建与原子发布 ----------

    def confirmed_patterns(self) -> list[dict[str, Any]]:
        """正式快照只认已确认模式；挖掘中与已归档模式一律排除。"""
        return [
            row for row in store.rows("sequence")
            if row.get("status") == "已确认"
        ]

    def generate_snapshot(
        self,
        *,
        batch_no: str | None = None,
        simulate_interrupt: bool = False,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """读取新链路事件序列并计算分群特征，生成一个聚类输入快照。

        全部明细写入成功才发布；simulate_interrupt=True 时模拟写入中断，
        快照与暂存明细都保留为半成品，但聚类侧读取不到它们。
        """
        with _snapshot_lock:
            patterns = self.confirmed_patterns()
            if not patterns:
                return None, "没有已确认的序列模式，无法生成聚类输入快照"
            pattern_nos = sorted(str(row["模式编号"]) for row in patterns)

            events = [
                row for row in store.rows(EVENT_MODULE)
                if str(row.get("模式编号")) in pattern_nos
            ]
            if not events:
                return None, "已确认模式下没有新链路事件记录，无法计算分群特征"

            snapshot = {
                "id": _next_id(SNAPSHOT_MODULE),
                "快照编号": _next_snapshot_no(),
                "处理批次": batch_no or _next_batch_no(),
                "处理状态": SNAPSHOT_BUILDING,
                "唯一可用": False,
                "版本序号": _next_version(),
                "来源模式编号": pattern_nos,
                "来源模式数": len(pattern_nos),
                "事件记录数": len(events),
                "分群数": 0,
                "覆盖人数": 0,
                "轮廓系数": 0.0,
                "高频行为路径": "",
                "创建时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "备注": remark or "",
                "status": SNAPSHOT_BUILDING,
                "pending": True,
                "abnormal": False,
            }
            store.rows(SNAPSHOT_MODULE).append(snapshot)

            if simulate_interrupt:
                # 模拟明细写到一半中断：留下暂存标签，但绝不发布
                store.rows(DETAIL_MODULE).append({
                    "id": _next_id(DETAIL_MODULE),
                    "快照编号": snapshot["快照编号"],
                    "处理批次": snapshot["处理批次"],
                    "聚类标签": "半成品标签",
                    "分群覆盖人数": 0,
                    "轮廓系数": 0.0,
                    "高频行为路径": "",
                    "关联模式编号": pattern_nos,
                    "覆盖用户": [],
                    "处理状态": "暂存",
                    "status": "暂存",
                    "pending": True,
                    "abnormal": True,
                })
                snapshot["处理状态"] = SNAPSHOT_INTERRUPTED
                snapshot["status"] = SNAPSHOT_INTERRUPTED
                snapshot["abnormal"] = True
                snapshot["备注"] = (remark or "") + " 写入中断：半成品标签未对外发布"
                raise SnapshotInterrupted(snapshot["快照编号"])

            result = cluster_engine.run_clustering(events)
            detail_rows: list[dict[str, Any]] = []
            for row in result["rows"]:
                detail_rows.append({
                    "id": _next_id(DETAIL_MODULE),
                    "快照编号": snapshot["快照编号"],
                    "处理批次": snapshot["处理批次"],
                    "聚类标签": row["聚类标签"],
                    "分群覆盖人数": row["分群覆盖人数"],
                    "轮廓系数": row["轮廓系数"],
                    "高频行为路径": row["高频行为路径"],
                    "高频路径明细": row["高频路径明细"],
                    "关联模式编号": row["关联模式编号"],
                    "覆盖用户": row["覆盖用户"],
                    "特征列": row["特征列"],
                    "质心特征": row["质心特征"],
                    "处理状态": SNAPSHOT_PUBLISHED,
                    "status": SNAPSHOT_PUBLISHED,
                    "pending": False,
                    "abnormal": False,
                })
            store.rows(DETAIL_MODULE).extend(detail_rows)

            # 明细全部写完后再原子切换：旧版本先降级，新版本再发布
            for old in store.rows(SNAPSHOT_MODULE):
                if old["id"] != snapshot["id"] and old.get("处理状态") == SNAPSHOT_PUBLISHED:
                    old["处理状态"] = SNAPSHOT_REPLACED
                    old["status"] = SNAPSHOT_REPLACED
                    old["唯一可用"] = False
                    old["pending"] = False
            for detail in store.rows(DETAIL_MODULE):
                if detail["快照编号"] != snapshot["快照编号"] and detail.get("处理状态") == SNAPSHOT_PUBLISHED:
                    detail["处理状态"] = SNAPSHOT_REPLACED
                    detail["status"] = SNAPSHOT_REPLACED
                    detail["pending"] = False

            top_paths = Counter(
                path["行为路径"]
                for row in result["rows"]
                for path in row["高频路径明细"]
            ).most_common(1)
            snapshot.update({
                "处理状态": SNAPSHOT_PUBLISHED,
                "status": SNAPSHOT_PUBLISHED,
                "唯一可用": True,
                "pending": False,
                "分群数": result["metrics"]["聚类数"],
                "覆盖人数": result["metrics"]["覆盖人数"],
                "轮廓系数": result["metrics"]["轮廓系数"],
                "高频行为路径": top_paths[0][0] if top_paths else "",
            })
            return snapshot, f"快照 {snapshot['快照编号']} 已发布，处理批次 {snapshot['处理批次']}"

    # ---------- 复核与查询 ----------

    def list_snapshots(
        self,
        *,
        batch_no: str | None = None,
        state: str | None = None,
        include_interrupted: bool = True,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(SNAPSHOT_MODULE)
        if not include_interrupted:
            rows = [row for row in rows if row.get("处理状态") != SNAPSHOT_INTERRUPTED]
        if batch_no:
            rows = [row for row in rows if batch_no in str(row.get("处理批次", ""))]
        if state:
            rows = [row for row in rows if row.get("处理状态") == state]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_snapshot(self, snapshot_no: str) -> dict[str, Any] | None:
        for row in store.rows(SNAPSHOT_MODULE):
            if row.get("快照编号") == snapshot_no:
                return row
        return None

    def list_details(
        self,
        *,
        snapshot_no: str | None = None,
        batch_no: str | None = None,
        cluster_visible_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = store.rows(DETAIL_MODULE)
        if cluster_visible_only:
            published = {
                row["快照编号"] for row in store.rows(SNAPSHOT_MODULE)
                if row.get("处理状态") == VISIBLE_FROM_CLUSTER
            }
            rows = [
                row for row in rows
                if row.get("处理状态") == VISIBLE_FROM_CLUSTER and row.get("快照编号") in published
            ]
        if snapshot_no:
            rows = [row for row in rows if row.get("快照编号") == snapshot_no]
        if batch_no:
            rows = [row for row in rows if row.get("处理批次") == batch_no]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def published_snapshot(self) -> dict[str, Any] | None:
        """聚类侧唯一入口：任意时刻最多返回一个已发布且唯一可用的快照。"""
        candidates = [
            row for row in store.rows(SNAPSHOT_MODULE)
            if row.get("处理状态") == VISIBLE_FROM_CLUSTER and row.get("唯一可用")
        ]
        if len(candidates) > 1:  # 防御性检查：发布逻辑保证不会走到这里
            raise RuntimeError("检测到多个可用聚类输入快照，发布链路异常")
        return candidates[0] if candidates else None

    def batch_review(self, batch_no: str) -> dict[str, Any] | None:
        """按处理批次复核：汇总批次头与分群明细，给出可核对的覆盖口径。"""
        snapshots = [
            row for row in store.rows(SNAPSHOT_MODULE)
            if row.get("处理批次") == batch_no
        ]
        if not snapshots:
            return None
        details = self.list_details(batch_no=batch_no)
        coverage = sum(int(row.get("分群覆盖人数", 0)) for row in details)
        silhouettes = [float(row.get("轮廓系数", 0.0)) for row in details]
        return {
            "处理批次": batch_no,
            "快照": sorted(snapshots, key=lambda row: int(row["id"])),
            "明细条数": len(details),
            "分群覆盖人数合计": coverage,
            "轮廓系数均值": round(sum(silhouettes) / len(silhouettes), 4) if silhouettes else 0.0,
            "明细": details,
            "可下发聚类": any(row.get("唯一可用") for row in snapshots),
        }

    # ---------- 聚类任务对接 ----------

    def receive_for_task(self, task_id: int) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """聚类分群待处理明细接收数据集：只接收唯一可用的已发布快照。"""
        task = store.find("cluster", task_id)
        if task is None:
            return None, f"聚类任务 {task_id} 不存在或已归档", None
        snapshot = self.published_snapshot()
        if snapshot is None:
            return None, "当前没有唯一可用的聚类输入分群明细数据集，提交已拒绝", None
        details = self.list_details(snapshot_no=snapshot["快照编号"], cluster_visible_only=True)
        if not details:
            return None, f"快照 {snapshot['快照编号']} 缺少完整分群明细，提交已拒绝", None
        return snapshot, f"已按处理批次 {snapshot['处理批次']} 接收分群明细", {"details": details}

    # ---------- 并发回执：确认回执 / 归档事件同时到达 ----------

    def handle_pattern_receipt(self, entry_id: int, receipt_type: str) -> tuple[dict[str, Any] | None, str]:
        """同一模式的两类回执串行处理，避免聚类侧同时读到两份半成品。"""
        pattern = store.find("sequence", entry_id)
        if pattern is None:
            return None, f"序列模式 {entry_id} 不存在或已归档"
        pattern_no = str(pattern["模式编号"])
        sequence_service = SequenceService()
        with pattern_lock(pattern_no):
            if receipt_type == "确认回执":
                action_name = "确认模式"
            elif receipt_type == "归档事件":
                action_name = "归档模式"
            else:
                return None, f"回执类型「{receipt_type}」不受支持"
            entry, message = sequence_service.run_action(entry_id, action_name)
            if entry is None:
                return None, message

            # 仍有已确认模式时才重建快照；全部归档则保留旧版本待人工复核
            if self.confirmed_patterns():
                try:
                    snapshot, snap_message = self.generate_snapshot(
                        remark=f"由模式 {pattern_no} 的{receipt_type}触发",
                    )
                except SnapshotInterrupted as exc:
                    return None, f"{receipt_type}已处理，但快照写入中断（{exc}），半成品未发布"
                return entry, f"{receipt_type}已处理（{message}）；{snap_message}"
            return entry, f"{receipt_type}已处理（{message}）；当前无已确认模式，未生成新快照"
    # ---------- 内部工具 ----------

    @staticmethod
    def _find_pattern(pattern_no: str) -> dict[str, Any] | None:
        for row in store.rows("sequence"):
            if row.get("模式编号") == pattern_no:
                return row
        return None
