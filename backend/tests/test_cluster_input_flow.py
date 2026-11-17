"""聚类输入分群新链路的服务级回归脚本（不依赖 HTTP/第三方库）。

运行：cd backend && .venv/bin/python -I tests/test_cluster_input_flow.py
"""
from __future__ import annotations

import sys
import threading
import traceback

sys.path.insert(0, "/workspace/backend")

from app.seed import SEED_ROWS  # noqa: E402
from app.services.cluster_input import (  # noqa: E402
    SNAPSHOT_INTERRUPTED,
    SNAPSHOT_PUBLISHED,
    SNAPSHOT_REPLACED,
    ClusterInputService,
    SnapshotInterrupted,
)
from app.services.cluster import ClusterService  # noqa: E402
from app.store import Store  # noqa: E402
import app.store as store_module  # noqa: E402


def reset_store() -> None:
    store_module.store = Store()
    # 服务里直接引用了 app.store.store，需要同步替换
    import app.services.cluster_input as ci
    import app.services.cluster as cs
    import app.services.sequence as seq
    ci.store = store_module.store
    cs.store = store_module.store
    seq.store = store_module.store


failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    # ---------- 场景 1：首次形成聚类输入分群明细数据集 ----------
    reset_store()
    svc = ClusterInputService()
    snapshot, message = svc.generate_snapshot(batch_no="BATCH-REG-001")
    check("1.1 首次快照发布成功", snapshot is not None and snapshot["处理状态"] == SNAPSHOT_PUBLISHED, message)
    check("1.2 快照标记唯一可用", bool(snapshot["唯一可用"]))
    check("1.3 只含已确认模式（3 个，排除挖掘中 SEQU-0002）",
          snapshot["来源模式编号"] == ["SEQU-0003", "SEQU-0004", "SEQU-0005"],
          str(snapshot["来源模式编号"]))
    visible = svc.list_details(cluster_visible_only=True)
    check("1.4 聚类侧可见明细数等于分群数", len(visible) == snapshot["分群数"] == 3,
          f"visible={len(visible)} k={snapshot['分群数']}")
    coverage = sum(int(r["分群覆盖人数"]) for r in visible)
    check("1.5 分群覆盖人数合计等于快照覆盖人数", coverage == snapshot["覆盖人数"] == 19,
          f"coverage={coverage}")
    check("1.6 轮廓系数落在 [-1,1]", all(-1 <= float(r["轮廓系数"]) <= 1 for r in visible))
    check("1.7 每条明细带高频行为路径", all(r["高频行为路径"] for r in visible))
    review = svc.batch_review("BATCH-REG-001")
    check("1.8 批次复核覆盖口径一致",
          review["分群覆盖人数合计"] == 19 and review["可下发聚类"] is True,
          str(review["分群覆盖人数合计"]))

    # ---------- 场景 2：写入中断，半成品标签不得暴露 ----------
    try:
        svc.generate_snapshot(batch_no="BATCH-REG-002", simulate_interrupt=True)
        check("2.1 中断应抛出 SnapshotInterrupted", False)
    except SnapshotInterrupted:
        check("2.1 中断应抛出 SnapshotInterrupted", True)
    interrupted = [r for r in store_module.store.rows("cluster_input_snapshot")
                   if r["处理状态"] == SNAPSHOT_INTERRUPTED]
    check("2.2 中断快照保留用于复核", len(interrupted) == 1)
    published = svc.published_snapshot()
    check("2.3 中断后唯一可用仍是首个完整快照",
          published is not None and published["快照编号"] == snapshot["快照编号"])
    visible_after = svc.list_details(cluster_visible_only=True)
    check("2.4 半成品标签对聚类侧不可见",
          all(r["处理状态"] == SNAPSHOT_PUBLISHED and r["聚类标签"] != "半成品标签"
              for r in visible_after) and len(visible_after) == 3)
    half = [r for r in store_module.store.rows("cluster_input_detail") if r["处理状态"] == "暂存"]
    check("2.5 半成品明细以暂存态留痕", len(half) == 1 and half[0]["abnormal"] is True)

    # ---------- 场景 3：两个确认版本只能发布一个完整版本 ----------
    snap3, msg3 = svc.generate_snapshot(batch_no="BATCH-REG-003")
    check("3.1 第二个完整版本发布成功", snap3 is not None and snap3["版本序号"] >= 2, msg3)
    published_rows = [r for r in store_module.store.rows("cluster_input_snapshot")
                      if r["处理状态"] == SNAPSHOT_PUBLISHED]
    check("3.2 任意时刻只有一个已发布快照", len(published_rows) == 1
          and published_rows[0]["快照编号"] == snap3["快照编号"])
    old = svc.get_snapshot(snapshot["快照编号"])
    check("3.3 旧版本自动置为已替代", old["处理状态"] == SNAPSHOT_REPLACED and old["唯一可用"] is False)
    visible3 = svc.list_details(cluster_visible_only=True)
    check("3.4 聚类侧只能读到新版本明细",
          len(visible3) == 3 and all(r["快照编号"] == snap3["快照编号"] for r in visible3))
    replaced_details = [r for r in store_module.store.rows("cluster_input_detail")
                        if r["处理状态"] == SNAPSHOT_REPLACED]
    check("3.5 旧明细同步替代、不残留可用标签", len(replaced_details) == 3)

    # ---------- 场景 4：聚类待处理明细只接收唯一可用结果 ----------
    cluster_svc = ClusterService()
    task = store_module.store.find("cluster", 1)  # CLUS-0001 回归样例
    check("4.0 回归样例为 CLUS-0001", task["任务编号"] == "CLUS-0001")
    entry, msg = cluster_svc.run_action(1, "接收分群明细")
    check("4.1 CLUS-0001 接收成功", entry is not None, msg)
    check("4.2 结论同步清单：标签/覆盖/轮廓/批次落表",
          entry["关联快照编号"] == snap3["快照编号"]
          and entry["处理批次"] == "BATCH-REG-003"
          and entry["分群覆盖人数"] == 19
          and "高转化核心群" in entry["聚类标签"],
          str({k: entry.get(k) for k in ("关联快照编号", "处理批次", "分群覆盖人数", "聚类标签")}))
    details_payload = cluster_svc.task_details(1)
    check("4.3 任务详情列出同快照标签/覆盖人数/轮廓系数",
          len(details_payload["同快照分群明细"]) == 3
          and details_payload["批次复核"]["处理批次"] == "BATCH-REG-003")
    check("4.4 详情内覆盖人数之和与清单一致",
          sum(int(r["分群覆盖人数"]) for r in details_payload["同快照分群明细"]) == entry["分群覆盖人数"])

    # ---------- 场景 5：无可用快照时提交被拒，不写半成品 ----------
    reset_store()
    svc = ClusterInputService()
    cluster_svc = ClusterService()
    entry, msg = cluster_svc.run_action(1, "接收分群明细")
    check("5.1 无快照时提交被拒绝", entry is None and "唯一可用" in msg, msg)
    untouched = store_module.store.find("cluster", 1)
    check("5.2 拒绝后任务无半成品标签",
          untouched.get("聚类标签", "").startswith("聚类分群样例") and "分群明细" not in untouched)

    # ---------- 场景 6：确认回执 / 归档事件同时到达同一模式 ----------
    reset_store()
    svc = ClusterInputService()
    # 先发布基线快照（含 SEQU-0003/0004/0005）
    base, _ = svc.generate_snapshot(batch_no="BATCH-CONC-001")
    pattern5 = next(r for r in store_module.store.rows("sequence") if r["模式编号"] == "SEQU-0005")
    errors: list[str] = []

    def fire(receipt: str) -> None:
        try:
            local = ClusterInputService()
            local.handle_pattern_receipt(pattern5["id"], receipt)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{receipt}: {exc!r}")

    t1 = threading.Thread(target=fire, args=("归档事件",))
    t2 = threading.Thread(target=fire, args=("确认回执",))
    t1.start(); t2.start(); t1.join(); t2.join()
    check("6.1 并发回执无异常", not errors, str(errors))
    pubs = [r for r in store_module.store.rows("cluster_input_snapshot")
            if r["处理状态"] == SNAPSHOT_PUBLISHED]
    check("6.2 聚类侧始终只有一个可用快照", len(pubs) == 1, f"pubs={len(pubs)}")
    p5 = next(r for r in store_module.store.rows("sequence") if r["模式编号"] == "SEQU-0005")
    check("6.3 归档生效、重复确认被顺序规则拦下", p5["status"] == "已归档")
    latest = svc.published_snapshot()
    check("6.4 新快照按处理批次可核对且排除已归档模式",
          latest is not None and "SEQU-0005" not in latest["来源模式编号"]
          and "SEQU-0003" in latest["来源模式编号"],
          str(latest["来源模式编号"] if latest else None))
    check("6.5 新快照事件不含已归档/挖掘中模式",
          all(not (set(r["关联模式编号"]) & {"SEQU-0005", "SEQU-0002"})
              for r in svc.list_details(cluster_visible_only=True)))

    # ---------- 场景 7：确认挖掘中模式后其事件才进入正式快照 ----------
    entry, msg = svc.handle_pattern_receipt(2, "确认回执")  # SEQU-0002 挖掘中 → 已确认
    check("7.1 挖掘中模式确认成功", entry is not None and entry["status"] == "已确认", msg)
    snap7 = svc.published_snapshot()
    check("7.2 确认后新快照包含 SEQU-0002",
          "SEQU-0002" in snap7["来源模式编号"], str(snap7["来源模式编号"]))
    review7 = svc.batch_review(snap7["处理批次"])
    check("7.3 新快照可按批次核对", review7 is not None and review7["可下发聚类"] is True)

    print("\n==== 结果 ====")
    if failures:
        print(f"{len(failures)} 项失败：{failures}")
        return 1
    print("全部检查通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
