import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/sequence'
const columns = [
  "模式编号",
  "用户分群",
  "事件序列",
  "最大步长",
  "最小支持度",
  "时间窗口天",
  "序列频次",
  "转化率"
]
const actions = [
  "发起挖掘",
  "确认模式",
  "归档模式"
]
const statuses = [
  "草稿",
  "挖掘中",
  "已确认",
  "已归档"
]

type Row = Record<string, string | number | null>

export default function SequenceView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}&size=200`)
      if (!response.ok) {
        throw new Error('序列模式列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '行为序列列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: "序列模式总数", value: total },
    { label: "已确认模式", value: rows.filter((row) => row.status === "已确认").length },
    { label: "挖掘中模式", value: rows.filter((row) => row.status === "挖掘中").length },
    { label: "已归档模式", value: rows.filter((row) => row.status === "已归档").length },
  ], [rows, total])

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
        setErrorMessage(payload.message ?? '行为序列动作未生效')
      } else {
        setInfoMessage(payload.message ?? '操作已生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '行为序列操作失败')
    }
  }

  // 新链路回执：确认回执 / 归档事件，后端按模式加锁串行处理并驱动聚类输入快照
  async function sendReceipt(receiptType: string, row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`/api/cluster-input/patterns/${row.id}/receipts`, {
        method: 'POST',
        body: JSON.stringify({ values: { receipt_type: receiptType } }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '回执处理失败')
      } else {
        setInfoMessage(payload.message ?? '回执已处理')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '回执处理失败')
    }
  }

  return (
    <section className="page" data-module="sequence">
      <header className="page-head">
        <div>
          <h2>行为序列管理</h2>
          <p className="page-desc">
            挖掘用户行为序列模式，识别高频行为路径和转化瓶颈；已确认模式通过新链路驱动聚类输入快照。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('序列模式登记入口尚未接入审批流')}>
            登记序列模式
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出行为序列清单
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
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{String(row.status ?? '—')}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void sendReceipt('确认回执', row)}>
                  确认回执
                </button>
                <button className="link" type="button" onClick={() => void sendReceipt('归档事件', row)}>
                  归档事件
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无行为序列数据，可先登记序列模式</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条行为序列记录</span>
      </footer>
    </section>
  )
}
