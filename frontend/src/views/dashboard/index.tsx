import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dashboard'
const columns = [
  "视图编号",
  "视图名称",
  "指标组合",
  "图表布局",
  "数据刷新秒",
  "对比周期",
  "告警线配置",
  "订阅接收组"
]
const actions = [
  "提交视图评审",
  "发布视图",
  "下线视图"
]
const statuses = [
  "草稿",
  "视图评审中",
  "已发布",
  "已下线"
]

type Row = Record<string, string | number | null>
type DetailRow = Record<string, string | number | unknown[]>
type PublishedPayload = {
  ok: boolean
  message?: string
  snapshot: Record<string, string | number | string[]> | null
  details: DetailRow[]
}

export default function DashboardView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [published, setPublished] = useState<PublishedPayload | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const query = new URLSearchParams(filters).toString()
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('看板视图列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)

      // 看板结果：只展示聚类侧唯一可用的完整快照，半成品不进看板
      const resultResponse = await request('/api/cluster-input/snapshots/published')
      if (resultResponse.ok) {
        setPublished(await resultResponse.json())
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '运营看板列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('运营看板动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '运营看板操作失败')
    }
  }

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>运营看板管理</h2>
          <p className="page-desc">配置运营数据看板视图，组合多维度指标和实时告警线；看板结果同步聚类输入分群结论。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('看板视图登记入口尚未接入审批流')}>
            登记看板视图
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出运营看板清单
          </button>
          <button className="btn ghost" type="button" onClick={() => void reload()}>
            刷新视图
          </button>
        </div>
      </header>

      <h3 className="section-title">看板结果：聚类输入分群（唯一可用完整版本）</h3>
      <div className="result-panel">
        {published?.ok && published.snapshot ? (
          <>
            <div className="result-grid">
              <div><span className="stat-label">快照编号</span><strong>{String(published.snapshot.快照编号)}</strong></div>
              <div><span className="stat-label">处理批次</span><strong>{String(published.snapshot.处理批次)}</strong></div>
              <div><span className="stat-label">分群数</span><strong>{String(published.snapshot.分群数)}</strong></div>
              <div><span className="stat-label">分群覆盖人数</span><strong>{String(published.snapshot.覆盖人数)}</strong></div>
              <div><span className="stat-label">轮廓系数</span><strong>{String(published.snapshot.轮廓系数)}</strong></div>
              <div><span className="stat-label">来源模式数</span><strong>{String(published.snapshot.来源模式数)}</strong></div>
            </div>
            <table className="data-table" style={{ marginTop: 10 }}>
              <thead>
                <tr><th>聚类标签</th><th>分群覆盖人数</th><th>轮廓系数</th><th>高频行为路径</th></tr>
              </thead>
              <tbody>
                {published.details.map((detail, index) => (
                  <tr key={index}>
                    <td>{String(detail.聚类标签 ?? '—')}</td>
                    <td>{String(detail.分群覆盖人数 ?? '—')}</td>
                    <td>{String(detail.轮廓系数 ?? '—')}</td>
                    <td>{String(detail.高频行为路径 ?? '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="empty-state">当前没有唯一可用的聚类输入分群明细数据集，看板暂不展示半成品结果</p>
        )}
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无运营看板数据，可先登记看板视图</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条运营看板记录</span>
      </footer>
    </section>
  )
}
