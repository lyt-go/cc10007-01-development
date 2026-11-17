import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/cluster'
const columns = [
  "任务编号",
  "聚类算法",
  "特征维度",
  "目标聚类数",
  "距离度量",
  "迭代次数上限",
  "聚类标签",
  "轮廓系数",
  "分群覆盖人数",
  "关联快照编号",
  "处理批次"
]
const actions = [
  "接收分群明细",
  "启动聚类",
  "确认完成",
  "归档结果"
]
const statuses = [
  "草稿",
  "聚类计算中",
  "已完成",
  "已归档"
]
const stats = [
  {
    "label": "聚类任务总数",
    "value": 0
  },
  {
    "label": "已完成任务",
    "value": 0
  },
  {
    "label": "计算中任务",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type DetailRow = Record<string, string | number | unknown[]>
type TaskDetails = {
  关联快照编号: string | null
  处理批次: string | null
  同快照分群明细: DetailRow[]
  批次复核: { 分群覆盖人数合计: number; 轮廓系数均值: number; 可下发聚类: boolean } | null
}

export default function ClusterView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [taskDetails, setTaskDetails] = useState<TaskDetails | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('聚类任务列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚类分群列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '聚类分群动作未生效')
      } else {
        setInfoMessage(payload.message ?? '操作已生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚类分群操作失败')
    }
  }

  async function openDetails(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/details`)
      if (!response.ok) {
        throw new Error('任务详情读取失败')
      }
      setTaskDetails(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '任务详情读取失败')
    }
  }

  return (
    <section className="page" data-module="cluster">
      <header className="page-head">
        <div>
          <h2>聚类分群管理</h2>
          <p className="page-desc">
            待处理明细只接收唯一可用的聚类输入分群明细数据集；任务详情按同一快照列出聚类标签、
            分群覆盖人数和轮廓系数，并可按处理批次核对。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('聚类任务登记入口尚未接入审批流')}>
            登记聚类任务
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出聚类分群清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      {infoMessage && <p className="info-text">{infoMessage}</p>}
      {errorMessage && <p className="error-text">{errorMessage}</p>}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void openDetails(row)}>
                  任务详情
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无聚类分群数据，可先登记聚类任务</td>
            </tr>
          )}
        </tbody>
      </table>

      {taskDetails && (
        <div className="review-panel">
          <h3 className="section-title">
            任务详情{taskDetails.关联快照编号 ? `（快照 ${taskDetails.关联快照编号} / 批次 ${taskDetails.处理批次 ?? '—'}）` : '（尚未接收分群明细）'}
          </h3>
          {taskDetails.同快照分群明细.length > 0 ? (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>聚类标签</th><th>分群覆盖人数</th><th>轮廓系数</th><th>高频行为路径</th><th>关联模式编号</th>
                  </tr>
                </thead>
                <tbody>
                  {taskDetails.同快照分群明细.map((detail, index) => (
                    <tr key={index}>
                      <td>{String(detail.聚类标签 ?? '—')}</td>
                      <td>{String(detail.分群覆盖人数 ?? '—')}</td>
                      <td>{String(detail.轮廓系数 ?? '—')}</td>
                      <td>{String(detail.高频行为路径 ?? '—')}</td>
                      <td>{Array.isArray(detail.关联模式编号) ? detail.关联模式编号.join('、') : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {taskDetails.批次复核 && (
                <p className="info-text">
                  批次复核：覆盖人数合计 {taskDetails.批次复核.分群覆盖人数合计} ｜
                  轮廓系数均值 {taskDetails.批次复核.轮廓系数均值} ｜
                  可下发聚类 {taskDetails.批次复核.可下发聚类 ? '是' : '否'}
                </p>
              )}
            </>
          ) : (
            <p className="empty-state">该任务还没有接收聚类输入分群明细数据集</p>
          )}
          <button className="btn ghost" type="button" onClick={() => setTaskDetails(null)}>关闭详情</button>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条聚类分群记录</span>
      </footer>
    </section>
  )
}
