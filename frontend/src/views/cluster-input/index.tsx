import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/cluster-input'

type Snapshot = Record<string, string | number | boolean | string[]>
type Detail = Record<string, string | number | unknown[]>
type Row = Record<string, string | number | null>

const snapshotColumns = [
  '快照编号',
  '处理批次',
  '处理状态',
  '唯一可用',
  '版本序号',
  '来源模式数',
  '事件记录数',
  '分群数',
  '覆盖人数',
  '轮廓系数',
  '高频行为路径',
]

const detailColumns = ['聚类标签', '分群覆盖人数', '轮廓系数', '高频行为路径', '关联模式编号']

export default function ClusterInputView() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [details, setDetails] = useState<Detail[]>([])
  const [events, setEvents] = useState<Row[]>([])
  const [review, setReview] = useState<Record<string, unknown> | null>(null)
  const [batchFilter, setBatchFilter] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [activeSnapshot, setActiveSnapshot] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const query = new URLSearchParams()
      if (batchFilter) query.set('batch_no', batchFilter)
      const response = await request(`${ENDPOINT}/snapshots?${query.toString()}`)
      if (!response.ok) throw new Error('聚类输入快照读取失败')
      const payload = await response.json()
      setSnapshots(payload.items ?? [])

      const published = await request(`${ENDPOINT}/snapshots/published`)
      const publishedPayload = await published.json()
      if (publishedPayload.ok) {
        setActiveSnapshot(publishedPayload.snapshot.快照编号)
        setDetails(publishedPayload.details ?? [])
      } else {
        setActiveSnapshot('')
        setDetails([])
      }

      const eventResponse = await request(`${ENDPOINT}/events?size=5`)
      const eventPayload = await eventResponse.json()
      setEvents(eventPayload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚类输入分群数据读取失败')
    }
  }, [batchFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  async function generate(simulateInterrupt: boolean) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/snapshots`, {
        method: 'POST',
        body: JSON.stringify({ values: simulateInterrupt ? { simulate_interrupt: true } : {} }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '快照生成失败')
      } else {
        setInfoMessage(payload.message ?? '快照已发布')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照生成失败')
    }
  }

  async function openReview(batchNo: string) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/batches/${encodeURIComponent(batchNo)}/review`)
      if (!response.ok) throw new Error('处理批次复核读取失败')
      setReview(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '批次复核失败')
    }
  }

  async function openSnapshot(snapshotNo: string) {
    setReview(null)
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/snapshots/${encodeURIComponent(snapshotNo)}`)
      if (!response.ok) throw new Error('快照详情读取失败')
      const payload = await response.json()
      setDetails(payload.details ?? [])
      setActiveSnapshot(snapshotNo)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照详情读取失败')
    }
  }

  const publishedCount = snapshots.filter((row) => row.处理状态 === '已发布').length
  const interruptedCount = snapshots.filter((row) => row.处理状态 === '写入中断').length

  return (
    <section className="page" data-module="cluster-input">
      <header className="page-head">
        <div>
          <h2>聚类输入分群明细数据集</h2>
          <p className="page-desc">
            从供新能力接收的序列模式事件记录读取事件序列并计算分群特征，生成可追溯的聚类输入快照；
            半成品标签不对外发布，任意时刻只有一个完整可用版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => void generate(false)}>
            生成聚类输入快照
          </button>
          <button className="btn" type="button" onClick={() => void generate(true)}>
            模拟写入中断
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出完整版本清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">快照版本总数</span>
          <strong className="stat-value">{snapshots.length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已发布（唯一可用）</span>
          <strong className="stat-value">{publishedCount}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">写入中断（半成品隔离）</span>
          <strong className="stat-value">{interruptedCount}</strong>
        </article>
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void reload()
        }}
      >
        <label className="filter-item">
          <span>处理批次</span>
          <input
            value={batchFilter}
            placeholder="按处理批次核对，如 BATCH-20261117-001"
            onChange={(event) => setBatchFilter(event.target.value)}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setBatchFilter('')}>重置条件</button>
      </form>

      {infoMessage && <p className="info-text">{infoMessage}</p>}
      {errorMessage && <p className="error-text">{errorMessage}</p>}

      <h3 className="section-title">快照版本（按处理批次复核）</h3>
      <table className="data-table">
        <thead>
          <tr>
            {snapshotColumns.map((column) => <th key={column}>{column}</th>)}
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {snapshots.map((row) => (
            <tr key={String(row.id)} className={row.处理状态 === '写入中断' ? 'row-abnormal' : ''}>
              {snapshotColumns.map((column) => (
                <td key={column}>
                  {column === '高频行为路径'
                    ? String(row[column] ?? '—').slice(0, 28)
                    : String(row[column] ?? '—')}
                </td>
              ))}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void openSnapshot(String(row.快照编号))}>
                  查看明细
                </button>
                <button className="link" type="button" onClick={() => void openReview(String(row.处理批次))}>
                  批次复核
                </button>
              </td>
            </tr>
          ))}
          {snapshots.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={snapshotColumns.length + 1}>暂无快照，点击「生成聚类输入快照」</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">
        分群明细{activeSnapshot ? `（快照 ${activeSnapshot}）` : '（暂无唯一可用快照）'}
      </h3>
      <table className="data-table">
        <thead>
          <tr>
            {detailColumns.map((column) => <th key={column}>{column}</th>)}
          </tr>
        </thead>
        <tbody>
          {details.map((row, index) => (
            <tr key={index}>
              {detailColumns.map((column) => (
                <td key={column}>
                  {Array.isArray(row[column]) ? (row[column] as unknown[]).join('、') : String(row[column] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
          {details.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={detailColumns.length}>
                聚类侧当前看不到任何半成品标签
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {review && (
        <div className="review-panel">
          <h3 className="section-title">批次复核：{String(review.处理批次)}</h3>
          <p>
            明细条数 {String(review.明细条数)} ｜ 分群覆盖人数合计 {String(review.分群覆盖人数合计)} ｜
            轮廓系数均值 {String(review.轮廓系数均值)} ｜ 可下发聚类 {String(review.可下发聚类)}
          </p>
        </div>
      )}

      <h3 className="section-title">新链路输入：序列模式事件记录（最近 5 条）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>事件编号</th><th>模式编号</th><th>用户标识</th><th>事件名称</th><th>事件序号</th><th>接收链路</th>
          </tr>
        </thead>
        <tbody>
          {events.map((row) => (
            <tr key={String(row.id)}>
              <td>{row.事件编号 ?? '—'}</td>
              <td>{row.模式编号 ?? '—'}</td>
              <td>{row.用户标识 ?? '—'}</td>
              <td>{row.事件名称 ?? '—'}</td>
              <td>{row.事件序号 ?? '—'}</td>
              <td>{row.接收链路 ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
