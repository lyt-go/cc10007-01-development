"""聚类特征计算引擎：只依赖标准库，保证结果可追溯、可复现。

输入为新链路接收到的序列模式事件记录，输出分群特征、聚类标签、
分群覆盖人数、轮廓系数与高频行为路径，供聚类输入分群明细数据集使用。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

# 特征列固定顺序，保证每次快照的特征维度一致、可复核
FEATURE_COLUMNS = [
    "事件总数",
    "去重事件数",
    "最大连续步长",
    "搜索次数",
    "加购次数",
    "支付次数",
    "转化标志",
]

CLUSTER_LABELS_BY_RANK = ["高转化核心群", "加购犹豫群", "浅度浏览群"]
TARGET_CLUSTER_K = 3
MAX_ITERATIONS = 100


def _squared_distance(a: list[float], b: list[float]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def build_user_sequences(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按用户聚合事件记录，形成带顺序的行为序列与来源模式集合。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event["用户标识"])].append(event)

    users: dict[str, dict[str, Any]] = {}
    for user, rows in grouped.items():
        rows.sort(key=lambda row: (str(row["事件时间"]), int(row["事件序号"]), int(row["id"])))
        names = [str(row["事件名称"]) for row in rows]
        patterns = sorted({str(row["模式编号"]) for row in rows})
        users[user] = {
            "用户标识": user,
            "事件序列": names,
            "关联模式编号": patterns,
        }
    return dict(sorted(users.items()))


def extract_features(user_sequences: dict[str, dict[str, Any]]) -> dict[str, list[float]]:
    """把每个用户的事件序列换算成固定维度的分群特征向量。"""
    vectors: dict[str, list[float]] = {}
    for user, info in user_sequences.items():
        names = info["事件序列"]
        counter = Counter(names)
        vectors[user] = [
            float(len(names)),
            float(len(set(names))),
            float(len(names)),
            float(counter.get("搜索商品", 0)),
            float(counter.get("加购", 0)),
            float(counter.get("支付", 0)),
            1.0 if counter.get("支付", 0) > 0 else 0.0,
        ]
    return vectors


def _zscore_normalize(matrix: list[list[float]]) -> list[list[float]]:
    if not matrix:
        return []
    dim = len(matrix[0])
    means = [sum(row[j] for row in matrix) / len(matrix) for j in range(dim)]
    stds: list[float] = []
    for j in range(dim):
        variance = sum((row[j] - means[j]) ** 2 for row in matrix) / len(matrix)
        stds.append(math.sqrt(variance) or 1.0)  # 常量列不缩放，避免除零
    return [[(row[j] - means[j]) / stds[j] for j in range(dim)] for row in matrix]


def _nearest_centroid(point: list[float], centroids: list[list[float]]) -> int:
    best_idx, best_dist = 0, float("inf")
    for idx, centroid in enumerate(centroids):
        dist = _squared_distance(point, centroid)
        if dist < best_dist:  # 等距时保留编号更小的簇，保证确定性
            best_idx, best_dist = idx, dist
    return best_idx


def kmeans(matrix: list[list[float]], k: int = TARGET_CLUSTER_K) -> list[int]:
    """确定性 KMeans++：质心按最大最小距离挑选，平局取编号最小的样本。"""
    n = len(matrix)
    if n == 0:
        return []
    k = min(k, n)
    centroid_indexes = [0]
    while len(centroid_indexes) < k:
        nearest_sq = [
            min(_squared_distance(row, matrix[chosen]) for chosen in centroid_indexes)
            for row in matrix
        ]
        # 确定性 argmax：距离相同取索引最小
        next_idx = max(range(n), key=lambda idx: (nearest_sq[idx], -idx))
        centroid_indexes.append(next_idx)
    centroids = [list(matrix[idx]) for idx in centroid_indexes]

    assignments = [-1] * n
    for _ in range(MAX_ITERATIONS):
        new_assignments = [_nearest_centroid(row, centroids) for row in matrix]
        if new_assignments == assignments:
            break
        assignments = new_assignments
        for cluster_idx in range(k):
            members = [matrix[i] for i in range(n) if assignments[i] == cluster_idx]
            if not members:
                # 空簇：回退到离当前质心最远的样本，保持 k 个簇都有成员
                farthest = max(
                    range(n),
                    key=lambda i: (_squared_distance(matrix[i], centroids[cluster_idx]), -i),
                )
                centroids[cluster_idx] = list(matrix[farthest])
                continue
            dim = len(members[0])
            centroids[cluster_idx] = [
                sum(row[j] for row in members) / len(members) for j in range(dim)
            ]
    return assignments


def silhouette_scores(matrix: list[list[float]], assignments: list[int]) -> tuple[float, list[float]]:
    """标准轮廓系数：返回整体均值与逐样本得分（单簇时记 0）。"""
    n = len(matrix)
    if n < 2 or len(set(assignments)) < 2:
        return 0.0, [0.0] * n
    scores: list[float] = []
    for i in range(n):
        same = [j for j in range(n) if assignments[j] == assignments[i] and j != i]
        a = (
            sum(math.sqrt(_squared_distance(matrix[i], matrix[j])) for j in same) / len(same)
            if same else 0.0
        )
        other_clusters = sorted({c for c in assignments if c != assignments[i]})
        b_candidates: list[float] = []
        for cluster_idx in other_clusters:
            members = [j for j in range(n) if assignments[j] == cluster_idx]
            b_candidates.append(
                sum(math.sqrt(_squared_distance(matrix[i], matrix[j])) for j in members)
                / len(members)
            )
        b = min(b_candidates)
        denom = max(a, b)
        scores.append(0.0 if denom == 0 else (b - a) / denom)
    return sum(scores) / n, scores


def high_frequency_paths(
    user_sequences: dict[str, dict[str, Any]], members: list[str], top_n: int = 3
) -> list[dict[str, Any]]:
    """统计簇内高频完整行为路径，按频次降序、路径名升序保证稳定。"""
    counter: Counter[str] = Counter()
    for user in members:
        counter[" → ".join(user_sequences[user]["事件序列"])] += 1
    total = sum(counter.values()) or 1
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return [
        {"行为路径": path, "频次": count, "支持度": round(count / total, 4)}
        for path, count in ordered
    ]


def run_clustering(events: list[dict[str, Any]]) -> dict[str, Any]:
    """事件记录 → 分群明细行与汇总指标的完整计算入口。"""
    user_sequences = build_user_sequences(events)
    if not user_sequences:
        return {"rows": [], "metrics": {"覆盖人数": 0, "轮廓系数": 0.0, "聚类数": 0}}

    raw_vectors = extract_features(user_sequences)
    users = list(user_sequences.keys())
    raw_matrix = [raw_vectors[user] for user in users]
    matrix = _zscore_normalize(raw_matrix)
    assignments = kmeans(matrix)
    overall_silhouette, sample_scores = silhouette_scores(matrix, assignments)

    cluster_members: dict[int, list[str]] = defaultdict(list)
    for user, cluster_idx in zip(users, assignments):
        cluster_members[cluster_idx].append(user)

    # 按转化能力（支付、加购均值）排序簇，赋予稳定可读的业务标签
    def cluster_power(idx: int) -> tuple[float, float]:
        members = cluster_members[idx]
        pay = sum(raw_vectors[u][FEATURE_COLUMNS.index("支付次数")] for u in members) / len(members)
        cart = sum(raw_vectors[u][FEATURE_COLUMNS.index("加购次数")] for u in members) / len(members)
        return pay, cart

    ordered_clusters = sorted(cluster_members.keys(), key=lambda idx: cluster_power(idx), reverse=True)

    rows: list[dict[str, Any]] = []
    for rank, cluster_idx in enumerate(ordered_clusters):
        members = sorted(cluster_members[cluster_idx])
        member_scores = [sample_scores[users.index(u)] for u in members]
        patterns = sorted({p for u in members for p in user_sequences[u]["关联模式编号"]})
        paths = high_frequency_paths(user_sequences, members)
        rows.append({
            "聚类标签": CLUSTER_LABELS_BY_RANK[rank] if rank < len(CLUSTER_LABELS_BY_RANK) else f"分群{rank + 1}",
            "分群覆盖人数": len(members),
            "轮廓系数": round(sum(member_scores) / len(member_scores), 4),
            "高频行为路径": paths[0]["行为路径"] if paths else "",
            "高频路径明细": paths,
            "覆盖用户": members,
            "关联模式编号": patterns,
            "特征列": FEATURE_COLUMNS,
            "质心特征": [
                round(sum(raw_vectors[u][j] for u in members) / len(members), 4)
                for j in range(len(FEATURE_COLUMNS))
            ],
        })

    return {
        "rows": rows,
        "metrics": {
            "覆盖人数": len(users),
            "轮廓系数": round(overall_silhouette, 4),
            "聚类数": len(rows),
        },
    }
