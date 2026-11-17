# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型名称、特征变量 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 聚类输入分群 | `cluster_input` | 聚类输入快照/分群明细 | 快照编号、处理批次、聚类标签、分群覆盖人数、轮廓系数 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.tsx`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 聚类输入分群新链路

行为序列挖掘与聚类分群之间通过「聚类输入分群明细数据集」衔接，保证输入版本可追溯：

1. 供新能力推送的序列模式事件记录落入 `sequence_event`（接口
   `POST/GET /api/cluster-input/events`）；只有「已确认」序列模式的事件允许进入正式快照，
   挖掘中模式的事件只接收、不入快照。
2. `POST /api/cluster-input/snapshots` 按新链路读取事件序列，用标准库实现的确定性
   KMeans++、轮廓系数与高频路径统计（`app/services/cluster_engine.py`）计算分群特征，
   生成首次形成的聚类输入快照与分群明细。
3. 快照先以「构建中」暂存，全部明细写完才原子切换为「已发布」；写入中断
  （`simulate_interrupt=true`）保留「写入中断」半成品用于复核，但聚类侧不可见。
   新版本发布后旧版本自动置「已替代」，任意时刻只有一个「唯一可用」完整版本。
4. 聚类任务通过动作「接收分群明细」（`POST /api/cluster/{id}/actions`）只接收唯一可用
   快照；任务详情 `GET /api/cluster/{id}/details` 列出同一快照下的聚类标签、分群覆盖人数、
   轮廓系数与处理批次复核。CLUS-0001 作为回归样例。
5. 同一模式的确认回执与归档事件走 `POST /api/cluster-input/patterns/{id}/receipts`，
   服务内按模式加锁串行处理，聚类侧永远不会同时看到两份半成品。
6. 按处理批次复核：`GET /api/cluster-input/batches/{批次}/review`。

服务级回归脚本（不依赖第三方库）：

```bash
cd backend && .venv/bin/python -I tests/test_cluster_input_flow.py
```

领域：数据工程
子方向：数据分析与数据应用
