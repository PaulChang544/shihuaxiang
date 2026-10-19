import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import SectionHead from '../components/SectionHead'
import CardColumns from '../components/CardColumns'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import { Badge } from '../ui/bits.jsx'
import { Modal } from '../ui/Overlay.jsx'
import { addJob, readJobs } from '../jobs.js'
import Select from '../ui/Select.jsx'
import { useToast } from '../ui/Toast.jsx'

const TYPES = ['节气时令', '手工 DIY', '茶话会', '志愿服务', '党团学习', '学习互助', '职业发展', '心理关怀', '技能工作坊', '文体活动', '文艺演出', '社会实践', '校庆活动', '其他']
const FIELDS = [
  ['name', '活动名称'], ['activity_type', '活动类型'], ['time', '时间'],
  ['form_points', '形式要点'], ['highlights', '亮点'], ['participation', '参与情况'],
]

export default function MaterialsPage() {
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)   // 粘贴正文 → AI 提取
  const [busy, setBusy] = useState(false)         // 截图上传中
  const [shotJob, setShotJob] = useState(() => readJobs().find((j) => j.kind === 'material_extract' && j.label === '截图素材入库') || null)
  const [editing, setEditing] = useState(null)
  const [confirming, setConfirming] = useState(null)   // 待删除素材（确认弹窗）
  const shotRef = useRef(null)
  const push = useToast()

  const load = () => api('/api/materials').then(setItems).catch((e) => push({ title: e.message, type: 'error' }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const extract = async () => {
    if (!url.trim()) {
      push({ title: '请先粘贴推文链接', type: 'warning' })
      return
    }
    if (!text.trim()) {
      push({ title: '请先粘贴推文正文', type: 'warning' })
      return
    }
    setLoading(true)
    try {
      const d = await api('/api/materials/extract', { method: 'POST', body: { text, url } })
      addJob({ job_id: d.job_id, kind: 'material_extract', ref_id: null, label: '素材入库' })
      push({ title: 'AI 正在提取素材', msg: '提取在后台进行，可以离开本页；完成后会通知你', type: 'info', duration: 5000 })
      setText(''); setUrl('')
    } catch (e) { push({ title: e.message, type: 'error' }) } finally { setLoading(false) }
  }

  /* 上传推文长截图：服务端切片转写后提取素材（可离开页面），链接必填与归档一致 */
  const onShotPick = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    if (!url.trim()) {
      push({ title: '请先粘贴推文链接', type: 'warning' })
      return
    }
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {})
    }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('url', url.trim())
      const d = await api('/api/materials/extract-image', { method: 'POST', body: fd })
      const entry = { job_id: d.job_id, kind: 'material_extract', ref_id: null, label: '截图素材入库' }
      addJob(entry)
      setShotJob(entry)
      push({ title: '已提交 AI 识别', msg: '识别和提取在后台进行，可以离开本页；完成后会通知你', type: 'info', duration: 5200 })
    } catch (e2) {
      push({ title: '上传失败', msg: e2.message, type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  /* 作业完成（App 级 watcher 广播）：素材类任务完成后刷新列表 */
  useEffect(() => {
    const onDone = (e) => {
      const d = e.detail
      if (d?.kind !== 'material_extract') return
      if (shotJob && d.job_id === shotJob.job_id) setShotJob(null)
      load()
    }
    window.addEventListener('shx-job-done', onDone)
    return () => window.removeEventListener('shx-job-done', onDone)
  }, [shotJob?.job_id])

  const openEdit = (item) => setEditing({ ...item })

  const saveEdit = async () => {
    if (!editing) return
    try {
      const body = {}
      FIELDS.forEach(([k]) => { body[k] = editing[k] })
      await api(`/api/materials/${editing.id}`, { method: 'PUT', body })
      push({ title: '已保存', type: 'success' }); setEditing(null); load()
    } catch (e) { push({ title: e.message, type: 'error' }) }
  }
  const remove = async (id) => {
    await api(`/api/materials/${id}`, { method: 'DELETE' })
    load()
  }
  const confirmRemove = () => {
    const target = confirming
    setConfirming(null)
    if (target) remove(target.id)
  }

  return (
    <div className="panel-stack" style={{ gap: 26 }}>
      <section>
        <SectionHead idx="01" cn="素材入库" en="Intake"
          tail="SCREENSHOT | PASTE → AI EXTRACT" />
        <div className="glass panel-stack" style={{ padding: '20px 22px' }}>
          <div className="field">
            <label className="field__label" htmlFor="material-shot">推文长截图 · SCREENSHOT</label>
            <div className="panel-row" style={{ gap: 10, flexWrap: 'wrap' }}>
              <input ref={shotRef} id="material-shot" type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={onShotPick} />
              <Button variant="primary" icon="camera" loading={busy} disabled={!!shotJob}
                onClick={() => shotRef.current?.click()}>
                {shotJob ? 'AI 正在识别截图…' : '上传推文长截图导入'}
              </Button>
              <span className="wb-card__muted">
                {shotJob ? '识别在后台进行，可以离开本页；完成后会自动通知，无需停留等待。'
                  : 'AI 自动转写标题与正文并提取素材；非推文截图会被拒收。'}
              </span>
            </div>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="material-url">推文链接 · SOURCE URL <small>必填，仅记录来源不抓取</small></label>
            <input id="material-url" className="input" placeholder="https://mp.weixin.qq.com/s/..." value={url}
              onChange={e => setUrl(e.target.value)} />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="material-text">推文正文 · ARTICLE BODY <small>可选：没有截图时直接粘贴正文</small></label>
            <textarea id="material-text" className="textarea" rows={6} placeholder="粘贴微信推文正文，AI 自动提取活动名称 / 类型 / 形式 / 亮点……"
              value={text} onChange={e => setText(e.target.value)} />
          </div>
          <div className="panel-row panel-row--between mat-foot">
            <Button variant="primary" icon="zap" loading={loading} onClick={extract}>AI 提取入库</Button>
          </div>
        </div>
      </section>

      <section>
        <SectionHead idx="02" cn="素材档案" en="Material Archive"
          tail={items.length ? `${items.length} 条往年活动` : 'EMPTY'} />
        {items.length === 0 ? (
          <div className="empty glass">
            <div className="empty__art"><Icon name="image" /></div>
            <div className="empty__title">素材库空</div>
            <div className="empty__text">入库的往年活动会在生成点子时自动作为参考素材，也在 Agent 对话的资料库里。</div>
          </div>
        ) : (
          <CardColumns
            items={items}
            columns={2}
            render={(it) => (
              <div key={it.id} className="glass entry-card">
                <div className="entry-card__head">
                  <span className="entry-card__title">{it.name}</span>
                  <div className="entry-actions">
                    <Button size="sm" icon="edit" onClick={() => openEdit(it)}>编辑</Button>
                    <Button size="sm" variant="danger" icon="trash" onClick={() => setConfirming(it)}>删除</Button>
                  </div>
                </div>
                <div className="row" style={{ marginTop: 10, gap: 8 }}>
                  <Badge tone="blue">{it.activity_type}</Badge>
                  <Badge>{it.time}</Badge>
                </div>
                <div className="entry-card__body">
                  <p>形式：{it.form_points}</p>
                  <p>亮点：{it.highlights}</p>
                  <p className="plan-meta" style={{ marginTop: 4 }}>参与 · {it.participation || '—'}</p>
                </div>
                {it.source_url && (
                  <p style={{ marginTop: 8 }}>
                    <a href={it.source_url} target="_blank" rel="noreferrer">推文链接 ↗</a>
                  </p>
                )}
              </div>
            )}
          />
        )}
      </section>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title="编辑素材"
        width={560}
        footer={(
          <>
            <Button onClick={() => setEditing(null)}>取消</Button>
            <Button variant="primary" onClick={saveEdit}>保存</Button>
          </>
        )}
      >
        {editing && (
          <div className="panel-stack" style={{ maxHeight: '62vh', overflowY: 'auto' }}>
            {FIELDS.map(([k, label]) => (
              <div className="field" key={k}>
                <label className="field__label" htmlFor={`material-edit-${k}`}>{label}</label>
                {k === 'activity_type'
                  ? (
                    <Select
                      id={`material-edit-${k}`}
                      options={TYPES}
                      value={editing[k] ?? ''}
                      onChange={v => setEditing(old => ({ ...old, [k]: v }))}
                    />
                  )
                  : (
                    <input id={`material-edit-${k}`} className="input" value={editing[k] ?? ''}
                      onChange={e => setEditing(v => ({ ...v, [k]: e.target.value }))} />
                  )}
              </div>
            ))}
          </div>
        )}
      </Modal>

      <Modal
        open={!!confirming}
        onClose={() => setConfirming(null)}
        title="删除素材"
        width={420}
        footer={(
          <>
            <Button onClick={() => setConfirming(null)}>取消</Button>
            <Button variant="danger" icon="trash" onClick={confirmRemove}>删除</Button>
          </>
        )}
      >
        <p>确定删除这条素材？</p>
        {confirming && <p className="console-hint" style={{ marginTop: 6 }}>{confirming.name}</p>}
      </Modal>
    </div>
  )
}
