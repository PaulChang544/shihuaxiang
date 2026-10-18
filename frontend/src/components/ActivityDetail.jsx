import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, BASE as apiBase, xhrSend } from '../api'
import { idbGet, idbPut, idbDel } from '../idb'
import { compressImage } from '../imageCompress'
import { humanName } from '../names.js'
import { addJob, readJobs } from '../jobs.js'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import Select from '../ui/Select.jsx'
import { Modal } from '../ui/Overlay.jsx'
import { Badge } from '../ui/bits.jsx'
import { SliderNav, useSliderNav } from '../ui/SliderTabs.jsx'
import { useToast } from '../ui/Toast.jsx'
import { IconButton } from '../ui/Button.jsx'
import IS_MOBILE from '../isMobile.js'

const STAGE_TABS = [
  { key: 'dispatch', label: '派发' },
  { key: 'planning', label: '策划' },
  { key: 'procurement', label: '采购报销' },
  { key: 'execution', label: '举办' },
  { key: 'coverage', label: '影像稿件' },
  { key: 'publish', label: '推文发布' },
];
const STAGE_TO_TAB = { dispatch: 0, planning: 1, procurement: 2, execution: 3, coverage: 4, publish: 5, done: 5 };
const STAGE_LABEL = { dispatch: '派发', planning: '策划中', procurement: '采购报销', execution: '举办', coverage: '影像稿件', publish: '推文发布', done: '已归档' };

const fmtMoney = (n) => `¥${Number(n || 0).toFixed(2).replace(/\.00$/, '')}`
const fmtSize = (n) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.ceil((n || 0) / 1024)}KB`)

const todayISO = () => new Date(`${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}-${String(new Date().getDate()).padStart(2, '0')}`)
const daysLeftOf = (date) => {
  if (!date) return null
  const left = Math.round((new Date(`${date}T00:00:00`) - todayISO()) / 86400000)
  return Number.isNaN(left) ? null : left
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

/* 通用：带草稿的文本字段（act 变化时同步一次） */
function useDraft(value) {
  const [draft, setDraft] = useState(value)
  const ref = useRef(value)
  useLayoutEffect(() => {
    if (value !== ref.current) {
      ref.current = value
      setDraft(value ?? '')
    }
  }, [value])
  return [draft, setDraft, () => setDraft(ref.current ?? '')]
}

function Panel({ title, en, next, onNext, children }) {
  return (
    <div>
      <div className="wb-panel__head">
        <div>
          <div className="wb-panel__title">{title}</div>
          <div className="cond-label">{en}</div>
        </div>
        {!IS_MOBILE && next && (
          <Button variant="outline" iconEnd="arrowRight" onClick={onNext}>完成，进入「{next}」</Button>
        )}
      </div>
      <div className="glass panel-stack" style={{ padding: '20px 22px', marginTop: 14 }}>
        {children}
        {IS_MOBILE && next && (
          <Button block variant="primary" iconEnd="arrowRight" onClick={onNext}>完成，进入「{next}」</Button>
        )}
      </div>
    </div>
  )
}

/* ① 活动派发：负责人（可多名）+ 负责人本机确认接单 */
function DispatchPanel({ act, patch, me, members, onConfirm, confirmAdvance }) {
  const [note, setNote, reset] = useDraft(act.dispatch_note)
  const [date, setDate] = useDraft(act.event_date)
  const leadIds = act.lead_ids || []
  const leads = leadIds.map((id) => members.find((m) => m.id === id)).filter(Boolean)
  const mine = me?.id != null && leadIds.includes(me.id)
  const waitList = act.dispatch_status !== 'confirmed'
  const push = useToast()

  const addLead = (id) => {
    if (!id) return
    if (leadIds.includes(Number(id))) return
    patch({ lead_ids: [...leadIds, Number(id)] }, '已添加负责人')
  }
  const removeLead = (id) => {
    const named = members.find((m) => m.id === id)
    patch({ lead_ids: leadIds.filter((x) => x !== id) }, `已移除 ${named?.name || '负责人'}`)
  }
  const advance = () => {
    if (!leadIds.length) {
      push({ title: '请先指派负责人', type: 'warning' })
      return
    }
    const fields = { dispatch_note: note }
    if (date && date !== act.event_date) fields.event_date = date
    const lines = []
    if (fields.event_date) lines.push(`举办日期设为 ${fields.event_date}（相关 DDL 随之重算）`)
    if (note.trim()) lines.push(`保存派发备注（${note.trim().length} 字）`)
    confirmAdvance({ to: 'planning', fields, lines })
  }

  return (
    <Panel title="活动派发" en="DISPATCH & CONFIRM" next="策划中" onNext={advance}>
      <div className="field">
        <label className="field__label">负责人 <small>可多名</small></label>
        <div className="wb-leadchips">
          {leads.length ? leads.map((m) => (
            <span key={m.id} className="wb-leadchip">
              {m.name}
              <button aria-label={`移除 ${m.name}`} onClick={() => removeLead(m.id)}><Icon name="x" size={11} /></button>
            </span>
          )) : <span className="wb-card__muted">尚未指派，先在下方添加负责人</span>}
        </div>
        <Select
          options={members.filter((m) => m.role !== 'super' && !leadIds.includes(m.id)).map((m) => ({ value: m.id, label: m.name + (m.role_note ? `（${m.role_note}）` : '') }))}
          value=""
          placeholder="添加负责人"
          onChange={addLead}
        />
        {act.lead_name && <div className="field__hint">名单：{act.lead_name}</div>}
      </div>
      {leadIds.length > 0 && (
        <div className="field">
          <label className="field__label">接单状态</label>
          <div style={{ height: 40, display: 'flex', alignItems: 'center' }}>
            {act.dispatch_status === 'confirmed'
              ? <Badge tone="sage">已接单 · {act.confirmed_at}</Badge>
              : <Badge tone="rose">待 {act.lead_name} 确认</Badge>}
          </div>
        </div>
      )}
      {waitList && leadIds.length > 0 && (
        mine ? (
          <div className="alert" style={{ '--tone': 'var(--rose)', '--tone-deep': 'var(--rose-deep)' }}>
            <span className="alert__icon"><Icon name="flag" size={16} /></span>
            <div className="alert__body">
              <div className="alert__text">本次活动已派给你。确认前请核对时间与安排。</div>
              <div className="row" style={{ marginTop: 8 }}>
                <Button size="sm" variant="primary" icon="check" onClick={onConfirm}>确认接单</Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="alert" style={{ '--tone': 'var(--sand)', '--tone-deep': 'var(--sand-deep)' }}>
            <span className="alert__icon"><Icon name="info" size={16} /></span>
            <div className="alert__body">
              <div className="alert__text">
                已派给 {act.lead_name}。负责人需在「切换身份」处绑定本人后确认接单。
                当前身份：{me ? humanName(me.name) : '未选择'}。
              </div>
            </div>
          </div>
        )
      )}
      <div className="field">
        <label className="field__label" htmlFor="act-date">举办日期 <small>策划敲定前可调整，相关截止时间随之重算</small></label>
        <input id="act-date" className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <div className="panel-row" style={{ marginTop: 8 }}>
          <Button size="sm" onClick={async () => {
            if (!date) {
              push({ title: '请先选择举办日期', type: 'warning' })
              return
            }
            await patch({ event_date: date }, '举办日期已更新，相关截止时间已重新推算')
          }}>保存日期</Button>
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="act-note">派发备注</label>
        <textarea id="act-note" className="textarea" rows={2} placeholder="补充说明：重点、注意事项、截止时间等"
          value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="panel-row" style={{ marginTop: 8 }}>
          <Button size="sm" onClick={async () => { await patch({ dispatch_note: note }, '备注已保存') }}>保存备注</Button>
          <Button size="sm" variant="ghost" onClick={reset}>还原</Button>
        </div>
      </div>
    </Panel>
  )
}

/* ② 活动策划：文本备注 + 招募推文链接与策划/招募 DDL */
function PlanningPanel({ act, patch, confirmAdvance }) {
  const [note, setNote, reset] = useDraft(act.plan_note)
  const [platDdl, setPlanDdl] = useDraft(act.plan_ddl)
  const [recUrl, setRecUrl] = useDraft(act.recruitment_url)
  const [recDdl, setRecDdl] = useDraft(act.recruitment_ddl)
  const idea = act.idea_id
  const push = useToast()
  const saveAll = () => patch(
    { plan_note: note, plan_ddl: platDdl, recruitment_url: recUrl.trim(), recruitment_ddl: recDdl },
    '策划信息已保存',
  )
  const advance = () => {
    if (!note.trim()) {
      push({ title: '请先填写方案要点', type: 'warning' })
      return
    }
    const lines = [
      `保存方案要点（${note.trim().length} 字）`,
      `策划 DDL ${platDdl || '未定'}`,
      `招募推文 DDL ${recDdl || '未定'}`,
    ]
    if (recUrl.trim()) lines.push('登记招募推文链接')
    confirmAdvance({ to: 'procurement', fields: { plan_note: note, plan_ddl: platDdl, recruitment_url: recUrl.trim(), recruitment_ddl: recDdl }, lines })
  }
  return (
    <Panel title="活动策划" en="PLANNING" next="采购报销" onNext={advance}>
      {idea && act.idea_name && (
        <div className="panel-row" style={{ gap: 10 }}>
          <Badge tone="lilac">灵感点子 · {act.idea_name}</Badge>
          {act.idea_highlight && <span className="wb-card__meta"><Icon name="zap" size={12} /> {act.idea_highlight}</span>}
        </div>
      )}
      <div className="field">
        <label className="field__label" htmlFor="act-plan">策划备注 · 方案要点</label>
        <textarea id="act-plan" className="textarea" rows={7}
          placeholder={'把方案要点写在这里：当天流程、分工、物料清单、预期效果……\n（布置到人：比如 签到—张三、物料—组织策划部）'}
          value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className={(IS_MOBILE ? 'wb-ddl-grid-col' : 'wb-ddl-grid')}>
        <div className="field">
          <label className="field__label" htmlFor="act-plan-ddl">策划 DDL</label>
          <input id="act-plan-ddl" className="input" type="date" value={platDdl}
            onChange={(e) => setPlanDdl(e.target.value)} />
        </div>
        <div className="field">
          <label className="field__label" htmlFor="act-rec-ddl">招募推文 DDL</label>
          <input id="act-rec-ddl" className="input" type="date" value={recDdl}
            onChange={(e) => setRecDdl(e.target.value)} />
          <div className="field__hint">惯例：举办日前 7 天</div>
        </div>
        <div className="field">
          <label className="field__label" htmlFor="act-rec-url">招募推文链接</label>
          <input id="act-rec-url" className="input" placeholder="已发布的招募推文 https://…"
            value={recUrl} onChange={(e) => setRecUrl(e.target.value)} />
        </div>
      </div>
      <div className="panel-row" style={{ marginTop: 8 }}>
        <Button size="sm" variant="primary" onClick={saveAll}>保存策划信息</Button>
        <Button size="sm" variant="ghost" onClick={reset}>还原</Button>
      </div>
    </Panel>
  )
}

/* ③ 材料购买和报销：条目表 + 票据 + 采购 DDL */
function ProcurementPanel({ act, activityId, patch, reload, confirmAdvance, me }) {
  const [form, setForm] = useState({ item: '', amount: '', note: '' })
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState(null)
  const [procDdl, setProcDdl] = useDraft(act.procurement_ddl)
  const push = useToast()
  const pending = act.expenses.length === 0 || act.expenses.some((e) => !e.reimbursed)
  const advance = () => {
    const lines = []
    if (procDdl !== act.procurement_ddl) {
      lines.push(`保存采购 DDL ${procDdl || '未定'}`)
    }
    let note = ''
    if (pending) {
      note = act.expenses.length === 0
        ? '采购清单还没有条目——可先跳过，报销在后续阶段补记。'
        : '仍有未报销的条目——本次推进视为跳过采购报销。'
      lines.push('跳过采购报销')
    } else {
      lines.push('采购报销已全部结清')
    }
    const fields = procDdl !== act.procurement_ddl ? { procurement_ddl: procDdl } : {}
    confirmAdvance({ to: 'execution', fields, lines, note })
  }
  const total = act.expenses.reduce((s, e) => s + Number(e.amount || 0), 0)
  const reimbursed = act.expenses.filter((e) => e.reimbursed).reduce((s, e) => s + Number(e.amount || 0), 0)

  const add = async () => {
    if (!form.item.trim()) return
    setAdding(true)
    try {
      await api(`/api/activities/${activityId}/expenses`, {
        method: 'POST',
        body: { item: form.item.trim(), amount: Number(form.amount) || 0, note: form.note },
      })
      setForm({ item: '', amount: '', note: '' })
      reload()
      push({ title: '条目已添加', type: 'success' })
    } catch (e) {
      push({ title: '添加失败', msg: e.message, type: 'error' })
    } finally { setAdding(false) }
  }

  const toggleReim = (e) => {
    api('/api/expenses/' + e.id, {
      method: 'PATCH',
      body: { item: e.item, amount: Number(e.amount), note: e.note, reimbursed: !e.reimbursed },
    }).then(reload)
  }

  const saveEdit = async () => {
    await api('/api/expenses/' + editing.id, {
      method: 'PATCH',
      body: { item: editing.item, amount: Number(editing.amount) || 0, note: editing.note, reimbursed: !!editing.reimbursed },
    })
    setEditing(null)
    reload()
  }

  return (
    <Panel title="材料购买和报销" en="PROCUREMENT & REIMBURSEMENT" next="举办" onNext={advance}>
      <div className="field">
        <label className="field__label" htmlFor="act-proc-ddl">采购 DDL <small>惯例：活动开始前 3 天</small></label>
        <div className="panel-row" style={{ gap: 10, flexWrap: 'wrap' }}>
          <input id="act-proc-ddl" className="input" type="date" style={{ flex: '0 1 190px' }}
            value={procDdl} onChange={(e) => setProcDdl(e.target.value)} />
          <Button size="sm" onClick={() => patch({ procurement_ddl: procDdl }, '采购 DDL 已保存')}>保存日期</Button>
        </div>
      </div>
      <div className="panel-row" style={{ gap: 10, flexWrap: 'wrap' }}>
        <input className="input" style={{ flex: '2 1 160px' }} placeholder="品名" value={form.item}
          onChange={(e) => setForm((f) => ({ ...f, item: e.target.value }))}
          onKeyDown={(e) => { if (e.key === 'Enter') add() }} />
        <input className="input" style={{ flex: '1 1 110px' }} type="number" min="0" step="0.01" placeholder="金额 ¥"
          value={form.amount} onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))} />
        <input className="input" style={{ flex: '2 1 140px' }} placeholder="备注（选填）" value={form.note}
          onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
        <Button variant="primary" icon="plus" loading={adding} onClick={add}>添加条目</Button>
      </div>

      {IS_MOBILE ? (
        <div className="wb-exp-list">
          {act.expenses.map((e) => (
            <div key={e.id} className="wb-exp">
              <div className="wb-exp__main">
                <span className="wb-exp__item">{e.item}</span>
                <span className="wb-exp__amount num">{fmtMoney(e.amount)}</span>
              </div>
              {e.note && <span className="wb-card__muted">{e.note}</span>}
              <div className="row" style={{ justifyContent: 'space-between', marginTop: 4 }}>
                <span className="row" style={{ gap: 8, fontSize: 12.5, color: 'var(--ink-2)' }}>
                  <button className={`switch${e.reimbursed ? ' switch--on' : ''}`} role="switch"
                    aria-checked={!!e.reimbursed} aria-label={`报销 ${e.item}`}
                    onClick={() => toggleReim(e)}>
                    <span className="switch__thumb" />
                  </button>
                  {e.reimbursed ? '已报销' : '未报销'}
                </span>
                <div className="row" style={{ gap: 6 }}>
                  <IconButton size="sm" label="编辑条目" icon="edit" onClick={() => setEditing({ ...e })} />
                  <IconButton size="sm" label="删除条目" icon="trash" variant="ghost" className="btn--danger-text"
                    onClick={() => api('/api/expenses/' + e.id, { method: 'DELETE' }).then(reload)} />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        act.expenses.length > 0 && (
        <div className="table-wrap glass">
          <table className="table">
            <thead>
              <tr><th>品名</th><th>金额</th><th>备注</th><th>报销</th><th></th></tr>
            </thead>
            <tbody>
              {act.expenses.map((e) => (
                <tr key={e.id}>
                  <td>{e.item}</td>
                  <td className="num">{fmtMoney(e.amount)}</td>
                  <td style={{ color: 'var(--ink-3)' }}>{e.note || '—'}</td>
                  <td>
                    <button className={`switch${e.reimbursed ? ' switch--on' : ''}`} role="switch"
                      aria-checked={!!e.reimbursed} aria-label={`报销 ${e.item}`}
                      onClick={() => toggleReim(e)} style={{ transform: 'scale(0.85)' }}>
                      <span className="switch__thumb" />
                    </button>
                  </td>
                  <td>
                    <div className="row" style={{ justifyContent: 'flex-end', gap: 6 }}>
                      <IconButton size="sm" label="编辑条目" icon="edit" onClick={() => setEditing({ ...e })} />
                      <IconButton size="sm" label="删除条目" icon="trash" variant="ghost" className="btn--danger-text"
                        onClick={() => api('/api/expenses/' + e.id, { method: 'DELETE' }).then(reload)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )
      )}
      <div className="console-hint">
        合计 {fmtMoney(total)} · 已报销 {fmtMoney(reimbursed)}
      </div>

      <div className="field">
        <label className="field__label">票据（发票照片 / PDF） <ReceiptUploader activityId={activityId} me={me} onDone={reload} /></label>
        {act.uploads.filter((u) => u.category === 'receipt').length > 0 && (
          <div className="panel-stack" style={{ gap: 6 }}>
            {act.uploads.filter((u) => u.category === 'receipt').map((u) => (
              <div key={u.id} className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                <a href={`${apiBase}/api/files/${u.id}`} target="_blank" rel="noreferrer" className="wb-card__meta">
                  <Icon name="file" size={13} /> {u.filename}
                  <span className="wb-card__muted">· {fmtSize(u.size)}</span>
                </a>
                <IconButton size="sm" label="删除票据" icon="trash" variant="ghost" className="btn--danger-text"
                  onClick={() => api(`/api/files/${u.id}`, { method: 'DELETE' }).then(reload)} />
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title="编辑条目"
        width={440}
        footer={(
          <>
            <Button onClick={() => setEditing(null)}>取消</Button>
            <Button variant="primary" onClick={saveEdit}>保存</Button>
          </>
        )}
      >
        {editing && (
          <div className="panel-stack">
            <div className="field">
              <label className="field__label">品名</label>
              <input className="input" value={editing.item}
                onChange={(e) => setEditing((f) => ({ ...f, item: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label">金额</label>
              <input className="input" type="number" min="0" step="0.01" value={editing.amount}
                onChange={(e) => setEditing((f) => ({ ...f, amount: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label">备注</label>
              <input className="input" value={editing.note}
                onChange={(e) => setEditing((f) => ({ ...f, note: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label">已报销</label>
              <button className={`switch${editing.reimbursed ? ' switch--on' : ''}`} role="switch"
                aria-checked={!!editing.reimbursed}
                onClick={(e) => { e.preventDefault(); setEditing((f) => ({ ...f, reimbursed: !f.reimbursed })) }}>
                <span className="switch__thumb" />
              </button>
            </div>
          </div>
        )}
      </Modal>
    </Panel>
  )
}

function ReceiptUploader({ activityId, onDone, me }) {
  const ref = useRef(null)
  const push = useToast()
  const onPick = async (e) => {
    const files = [...e.target.files]
    e.target.value = ''
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('category', 'receipt')
      try {
        await api(`/api/activities/${activityId}/files`, { method: 'POST', body: fd })
      } catch (err) {
        push({ title: `票据 ${f.name} 上传失败`, msg: err.message, type: 'error' })
      }
    }
    onDone?.()
    if (files.length) push({ title: `已上传 ${files.length} 张票据`, type: 'success' })
  }
  return (
    <>
      <input ref={ref} type="file" accept="image/*,application/pdf" multiple hidden onChange={onPick} />
      <Button size="sm" variant="ghost" icon="upload" onClick={() => ref.current?.click()}>上传票据</Button>
    </>
  )
}

/* ④ 举办活动：日期/场地/规模/小结 */
function ExecutionPanel({ act, patch, confirmAdvance }) {
  const [date, setDate] = useDraft(act.event_date)
  const [loc, setLoc] = useDraft(act.location)
  const [ppl, setPpl] = useDraft(act.participants)
  const [sum, setSum] = useDraft(act.summary_note)
  const advance = () => confirmAdvance({
    to: 'coverage',
    fields: { event_date: date, location: loc, participants: ppl, summary_note: sum },
    lines: [
      `保存举办日期 ${date || '未定'}、地点 ${loc.trim() || '未定'}、参与 ${ppl.trim() || '未填'}`,
      `保存举办小结（${sum.trim().length} 字）`,
    ],
  })
  return (
    <Panel title="举办活动" en="EXECUTION" next="影像稿件" onNext={advance}>
      <div className="panel-row" style={{ gap: 14, flexWrap: 'wrap' }}>
        <div className="field" style={{ flex: '1 1 170px' }}>
          <label className="field__label" htmlFor="act-date">活动日期</label>
          <input id="act-date" className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="field" style={{ flex: '2 1 190px' }}>
          <label className="field__label" htmlFor="act-loc">地点</label>
          <input id="act-loc" className="input" placeholder="如：党团活动室" value={loc} onChange={(e) => setLoc(e.target.value)} />
        </div>
        <div className="field" style={{ flex: '1 1 110px' }}>
          <label className="field__label" htmlFor="act-ppl">参与人数</label>
          <input id="act-ppl" className="input" placeholder="如：12 人" value={ppl} onChange={(e) => setPpl(e.target.value)} />
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="act-sum">举办小结</label>
        <textarea id="act-sum" className="textarea" rows={4}
          placeholder="一句话复盘：亮点、不足、下次要改什么（供归档）"
          value={sum} onChange={(e) => setSum(e.target.value)} />
      </div>
      <div className="panel-row">
        <Button variant="primary" onClick={async () => { await patch({ event_date: date, location: loc, participants: ppl, summary_note: sum }, '举办信息已保存') }}>保存</Button>
      </div>
    </Panel>
  )
}

/* ⑤ 照片和通讯稿撰写 */
function CoveragePanel({ act, activityId, patch, reload, confirmAdvance, me }) {
  const [report, setReport, reset] = useDraft(act.report_text)
  const [caption, setCaption] = useState('')
  const [generating, setGenerating] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportState, setExportState] = useState(null) // {phase:'build'|'download', pct}
  const [lightbox, setLightbox] = useState(null) // 点击照片看原图
  const ref = useRef(null)
  const push = useToast()
  const photos = act.uploads.filter((u) => u.category === 'photo')
  const advance = () => {
    if (!report.trim()) {
      push({ title: '请先撰写通讯稿', type: 'warning' })
      return
    }
    confirmAdvance({ to: 'publish', fields: { report_text: report }, lines: [`保存通讯稿（${report.trim().length} 字）`] })
  }

  const [reportJob, setReportJob] = useState(() =>
    readJobs().find((j) => j.kind === 'report_generate' && j.ref_id === activityId) || null)  // 通讯稿生成中的作业标记

  const generate = async () => {
    setGenerating(true)
    try {
      const d = await api(`/api/activities/${activityId}/report/generate`, { method: 'POST' })
      const entry = { job_id: d.job_id, kind: 'report_generate', ref_id: activityId, label: act.name }
      addJob(entry)
      setReportJob(entry)
      push({ title: 'AI 正在生成通讯稿', msg: '生成在后台进行，可以离开本页；完成后会自动保存并通知你', type: 'info', duration: 5200 })
    } catch (e) {
      push({ title: '生成提交失败', msg: e.message, type: 'error' })
    } finally {
      setGenerating(false)
    }
  }

  /* 生成完成（App 级广播）：服务端已自动保存，这里同步 textarea 并刷新 */
  useEffect(() => {
    const onDone = (e) => {
      const d = e.detail
      if (!d || d.kind !== 'report_generate' || d.ref_id !== activityId) return
      setReportJob(null)
      if (d.status === 'done' && d.result?.text) setReport(d.result.text)
      reload()
    }
    window.addEventListener('shx-job-done', onDone)
    return () => window.removeEventListener('shx-job-done', onDone)
  }, [activityId, reload])

  /* Word 导出改为服务端异步作业：提交即回、可离开页面；完成事件到达后自动走 Range 断点下载 */
  const exportJobRef = useRef(null)

  const downloadDocx = async (meta) => {
    setExportState({ phase: 'download', pct: 0.3 })
    const cacheKey = `docx:${activityId}:${meta.name}`
    const cached = await idbGet(cacheKey)
    let received = cached ? cached.byteLength : 0
    if (received > meta.size) { await idbDel(cacheKey); received = 0 }
    const res = await fetch(`${apiBase}/api/activities/${activityId}/export-docx/download?name=${encodeURIComponent(meta.name)}`,
      received ? { headers: { Range: `bytes=${received}-` } } : undefined)
    if (res.status === 200 && received) { await idbDel(cacheKey); received = 0 } // 服务端不认 Range 就重来
    if (received && res.status === 206) setExportState({ phase: 'download', pct: 0.3 + 0.7 * (received / meta.size) })
    const reader = res.body.getReader()
    let bytes = cached ? new Uint8Array(cached) : new Uint8Array(0)
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      const merged = new Uint8Array(bytes.length + value.length)
      merged.set(bytes, 0)
      merged.set(value, bytes.length)
      bytes = merged
      received = bytes.length
      await idbPut(cacheKey, bytes.slice().buffer)
      setExportState({ phase: 'download', pct: 0.3 + 0.7 * Math.min(1, received / meta.size) })
    }
    if (bytes.length !== meta.size) throw new Error(`下载不完整（${bytes.length}/${meta.size}）`)
    await idbDel(cacheKey)
    const blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = meta.name
    a.click()
    URL.revokeObjectURL(a.href)
    setExportState(null)
    push({ title: 'Word 已下载', msg: '通讯稿与照片已打包，同名归档在 exports/ 目录', type: 'success' })
  }

  const exportDocx = async () => {
    if (exporting) return
    setExporting(true)
    try {
      // 先把未保存的草稿存回服务器，避免导出的 Word 缺正文
      if (report.trim() && report.trim() !== (act.report_text || '').trim()) {
        const ok = await patch({ report_text: report }, '通讯稿先存草稿，再导出')
        if (!ok) return
      }
      const d = await api(`/api/activities/${activityId}/export-docx`, { method: 'POST' })
      exportJobRef.current = d.job_id
      addJob({ job_id: d.job_id, kind: 'docx_export', ref_id: activityId, label: act.name })
      setExportState({ phase: 'build', pct: 0.04, jobId: d.job_id, waiting: true })
      push({ title: 'Word 生成已提交', msg: '生成在后台进行，可以离开本页；完成后自动开始下载', type: 'info', duration: 5200 })
    } catch (e) {
      setExportState(null)
      push({ title: '导出提交失败', msg: e.message, type: 'error' })
    } finally {
      setExporting(false)
    }
  }

  /* 生成等待期的伪进度（封顶 30%），真进度由下载阶段给出 */
  useEffect(() => {
    if (!exportState?.waiting) return undefined
    const t = setInterval(() => {
      setExportState((s) => (s && s.waiting ? { ...s, pct: Math.min(0.3, s.pct + 0.02) } : s))
    }, 240)
    return () => clearInterval(t)
  }, [exportState?.jobId, exportState?.waiting])

  /* 作业完成广播：本页提交的 Word 任务 → 自动进入下载 */
  useEffect(() => {
    const onDone = (e) => {
      const d = e.detail
      if (!d || d.kind !== 'docx_export' || d.ref_id !== activityId || d.job_id !== exportJobRef.current) return
      if (d.status !== 'done') { setExportState(null); return }
      downloadDocx({ name: d.result.name, size: d.result.size })
        .catch((err) => { setExportState(null); push({ title: '下载失败', msg: err.message, type: 'error' }) })
    }
    window.addEventListener('shx-job-done', onDone)
    return () => window.removeEventListener('shx-job-done', onDone)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activityId])

  /* ---- 分片断点续传上传（照片）：本地压缩 → 会话续传 → 瓷贴进度 ---- */
  const CHUNK = 1024 * 1024
  const [pending, setPending] = useState([]) // {key, name, phase:'compress'|'upload', pct}
  const setPendingByKey = (key, patch) =>
    setPending((list) => list.map((p) => (p.key === key ? { ...p, ...patch } : p)))

  const fingerprint = (f) => `a${activityId}:${f.name}:${f.size}:${f.lastModified}`

  const uploadOne = async (file) => {
    const key = fingerprint(file)
    setPending((l) => [...l, { key, name: file.name, phase: 'compress', pct: 0 }])
    try {
      // 1) 本地压缩（结果缓存在 IndexedDB，重试不用重压）
      let comp = await idbGet(`comp:${key}`)
      if (!comp) {
        comp = await compressImage(file, { onProgress: (p) => setPendingByKey(key, { phase: 'compress', pct: p * 0.35 }) })
        await idbPut(`comp:${key}`, comp.blob)
      }
      const blob = comp.blob
      // 2) 分片会话：本机有未完会话则续传，否则新开
      const sessKey = `up:${key}`
      const sessMap = JSON.parse(localStorage.getItem('shx-uploads') || '{}')
      let uploadId = sessMap[sessKey]?.upload_id
      let received = 0
      if (uploadId) {
        try {
          const st = await api(`/api/uploads/${uploadId}`)
          if (st.size !== blob.size) throw new Error('尺寸不符，重开会话')
          received = st.received
        } catch {
          delete sessMap[sessKey]
          localStorage.setItem('shx-uploads', JSON.stringify(sessMap))
          uploadId = null
        }
      }
      if (!uploadId || received >= blob.size) {
        const r = await api('/api/uploads/init', {
          method: 'POST',
          body: {
            activity_id: activityId, category: 'photo', caption,
            filename: comp.filename, mime: blob.type || 'image/jpeg', size: blob.size,
          },
        })
        uploadId = r.upload_id
        received = 0
      }
      sessMap[sessKey] = { upload_id: uploadId, size: blob.size, name: file.name }
      localStorage.setItem('shx-uploads', JSON.stringify(sessMap))
      // 3) 分片顺序上传（XHR 才有字节级进度）
      setPendingByKey(key, { phase: 'upload', pct: 0.35 + 0.65 * (received / blob.size) })
      while (received < blob.size) {
        const end = Math.min(received + CHUNK, blob.size)
        const piece = blob.slice(received, end)
        await xhrSend(`/api/uploads/${uploadId}`, {
          method: 'PUT',
          body: piece,
          onProgress: (loaded) => {
            const done = received + loaded
            setPendingByKey(key, { phase: 'upload', pct: 0.35 + 0.65 * Math.min(1, done / blob.size) })
          },
        })
        received = end
        sessMap[sessKey] = { upload_id: uploadId, size: blob.size, name: file.name }
        localStorage.setItem('shx-uploads', JSON.stringify(sessMap))
      }
      // 4) 收尾转正
      await api(`/api/uploads/${uploadId}/finish`, { method: 'POST' })
      delete sessMap[sessKey]
      localStorage.setItem('shx-uploads', JSON.stringify(sessMap))
      setPending((l) => l.filter((p) => p.key !== key))
      return true
    } catch (err) {
      setPending((l) => l.filter((p) => p.key !== key))
      push({ title: `照片 ${file.name} 处理失败`, msg: err.message, type: 'error' })
      return false
    }
  }

  const onPickChunked = async (e) => {
    const files = [...e.target.files]
    e.target.value = ''
    let ok = 0
    for (const f of files) ok += (await uploadOne(f)) ? 1 : 0
    if (files.length) {
      if (ok === files.length) push({ title: `已上传 ${ok} 张照片`, type: 'success' })
      else push({ title: `已上传 ${ok}/${files.length} 张`, msg: '失败的可重新选择同一批文件，会从断点继续', type: 'warning' })
    }
    reload()
  }

  return (
    <Panel title="照片和通讯稿" en="COVERAGE" next="推文发布" onNext={advance}>
      <div className="panel-row" style={{ justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <input className="input" style={{ flex: '2 1 220px' }} placeholder="上传时统一备注（如：现场大合影）"
          value={caption} onChange={(e) => setCaption(e.target.value)} />
        <input ref={ref} type="file" accept="image/*" multiple hidden onChange={onPickChunked} />
        <Button variant="primary" icon="camera" onClick={() => ref.current?.click()}>上传照片</Button>
      </div>
      {(photos.length > 0 || pending.length > 0) ? (
        <div className="att-grid">
          {photos.map((p) => (
            <div key={p.id} className="att-item">
              <img src={`${apiBase}/api/files/${p.id}/thumb`} alt={p.caption || p.filename} loading="lazy"
                onClick={() => setLightbox(p)} style={{ cursor: 'zoom-in' }} />
              {p.caption && <span className="att-item__bar">{p.caption}</span>}
              <span className="att-item__del">
                <IconButton size="sm" label="删除照片" icon="trash" variant="solid"
                  onClick={() => api(`/api/files/${p.id}`, { method: 'DELETE' }).then(reload).catch((err) => push({ title: '删除失败', msg: err.message, type: 'error' }))} />
              </span>
            </div>
          ))}
          {pending.map((p) => (
            <div key={p.key} className="att-item att-item--pending">
              <svg viewBox="0 0 64 64" className="att-ring" aria-hidden="true">
                <circle className="att-ring__bg" cx="32" cy="32" r="26" />
                <circle
                  className="att-ring__fg"
                  cx="32" cy="32" r="26"
                  strokeDasharray={`${2 * Math.PI * 26}`}
                  strokeDashoffset={`${2 * Math.PI * 26 * (1 - p.pct)}`}
                />
              </svg>
              <span className="att-item__phase">
                {p.phase === 'compress' ? '压缩中' : '上传中'} {Math.round(p.pct * 100)}%
              </span>
              <span className="att-item__name">{p.name}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty">
          <div className="empty__art"><Icon name="camera" /></div>
          <div className="empty__title">还没有照片</div>
          <div className="empty__text">现场照片上传后，可直接用于通讯稿与推文素材。</div>
        </div>
      )}
      <div className="field">
        <label className="field__label" htmlFor="act-report">通讯稿</label>
        <textarea id="act-report" className="textarea" rows={9}
          placeholder={'用数字说话，与社区推文风格一致：\n· 参与人数、作品数量、获奖情况……\n· 主标题可沿用对仗诗意双句，副标题用「——」引出内容'}
          value={report} onChange={(e) => setReport(e.target.value)} />
        <div className="panel-row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
          <Button size="sm" variant="primary" onClick={async () => { await patch({ report_text: report }, '通讯稿已保存') }}>保存通讯稿</Button>
          <Button size="sm" icon="zap" loading={generating} disabled={!!reportJob} onClick={generate}>
            {reportJob ? 'AI 生成中…' : 'AI 一键生成'}
          </Button>
          <Button size="sm" icon="archive" loading={exporting} onClick={exportDocx}>下载 Word（通讯稿+照片）</Button>
          <Button size="sm" variant="ghost" onClick={reset}>还原</Button>
        </div>
      </div>
      {exportState && (
        <div className="export-progress">
          <div className="export-progress__card">
            <svg viewBox="0 0 64 64" className="att-ring att-ring--lg" aria-hidden="true">
              <circle className="att-ring__bg" cx="32" cy="32" r="26" />
              <circle
                className="att-ring__fg"
                cx="32" cy="32" r="26"
                strokeDasharray={`${2 * Math.PI * 26}`}
                strokeDashoffset={`${2 * Math.PI * 26 * (1 - exportState.pct)}`}
              />
            </svg>
            <div className="export-progress__text">
              {exportState.phase === 'build' ? '正在生成 Word…（后台，可离开页面）' : '正在下载…'} {Math.round(exportState.pct * 100)}%
            </div>
            <div className="field__hint">中断后重新点击可从断点续传，不必从头下载</div>
          </div>
        </div>
      )}
      <Modal
        open={!!lightbox}
        onClose={() => setLightbox(null)}
        title={lightbox?.caption || lightbox?.filename || '照片原图'}
        width={960}
        footer={(lightbox && <a className="wb-card__meta" href={`${apiBase}/api/files/${lightbox.id}`} target="_blank" rel="noreferrer"><Icon name="externalLink" size={12} /> 新标签页打开原图</a>)}
      >
        {lightbox && (
          <img src={`${apiBase}/api/files/${lightbox.id}`} alt={lightbox.caption || lightbox.filename}
            style={{ display: 'block', maxWidth: '100%', maxHeight: '68vh', margin: '0 auto', borderRadius: 'var(--radius)' }} />
        )}
      </Modal>
    </Panel>
  )
}

/* ⑥ 推文制作（秀米）与发布归档 */
function PublishPanel({ act, activityId, patch, reload, confirmAdvance, me }) {
  const [url, setUrl, resetUrl] = useDraft(act.article_url)
  const [xiumi, setXiumi] = useDraft(act.xiumi_url)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState(() => readJobs().find((j) => j.kind === 'archive_article' && j.ref_id === activityId) || null)
  const push = useToast()
  const published = act.stage === 'done' && act.published_at

  /* 后台识别完成（App 级轮询广播）：本活动则刷新数据、清掉任务标记 */
  useEffect(() => {
    const onDone = (e) => {
      const d = e.detail
      if (!d || d.kind !== 'archive_article' || d.ref_id !== activityId) return
      setJob(null)
      reload()
    }
    window.addEventListener('shx-job-done', onDone)
    return () => window.removeEventListener('shx-job-done', onDone)
  }, [activityId, reload])

  const shotRef = useRef(null)

  /* 提交截图归档：仅上传并拿到任务号（服务端异步识别 20-60 秒），可离开页面 */
  const onShotPick = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    if (!url.trim()) {
      push({ title: '请先粘贴已发布的微信文章链接', type: 'warning' })
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
      const d = await api(`/api/activities/${activityId}/archive-article-image`, { method: 'POST', body: fd })
      const entry = { job_id: d.job_id, kind: 'archive_article', ref_id: activityId, label: act.name }
      addJob(entry)
      setJob(entry)
      push({ title: '已提交 AI 识别', msg: '识别在后台进行，可以离开本页；完成后会通知你', type: 'info', duration: 5200 })
    } catch (e2) {
      push({ title: '上传失败', msg: e2.message, type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const publishArchive = async () => {
    if (!url.trim()) {
      push({ title: '请先粘贴已发布的微信文章链接', type: 'warning' })
      return
    }
    if (act.stage !== 'publish') {
      push({ title: '还不能归档', msg: '请先完成影像稿件，点「完成，进入「推文发布」」推进到发布阶段', type: 'warning' })
      return
    }
    if (!(act.report_text || '').trim()) {
      push({ title: '请先完成通讯稿', msg: '确认归档前需要有一篇通讯稿', type: 'warning' })
      return
    }
    if (!(act.article_title && act.article_text)) {
      push({ title: '请先上传推文长截图完成归档', msg: '识别完成后才能进入发布归档', type: 'warning' })
      return
    }
    confirmAdvance({
      to: 'done',
      mode: 'publish',
      fields: {},
      lines: [`发布归档《${act.article_title || act.name}》`, act.article_text?.length ? '推文原文将同步收入素材库' : null].filter(Boolean),
    })
  }

  return (
    <Panel title="推文制作和发布" en="PUBLISH & ARCHIVE" next={null} onNext={null}>
      {published && (
        <div className="alert" style={{ '--tone': 'var(--sage)', '--tone-deep': 'var(--sage-deep)' }}>
          <span className="alert__icon"><Icon name="checkCircle" size={16} /></span>
          <div className="alert__body">
            <div className="alert__text">
              已发布归档 · {act.published_at} · <a href={act.article_url} target="_blank" rel="noreferrer">打开推文 ↗</a>
            </div>
          </div>
        </div>
      )}
      {act.article_title && (
        <div className="panel-row" style={{ gap: 10, flexWrap: 'wrap' }}>
          <Badge tone="sage">已归档文章 · {act.article_title}</Badge>
          {act.article_url && <a className="wb-card__meta" href={act.article_url} target="_blank" rel="noreferrer"><Icon name="externalLink" size={12} /> 查看原文</a>}
        </div>
      )}
      <div className="wb-xiumi">
        <Button variant="primary" icon="copy" onClick={async () => {
          const ok = await copyText(act.report_text || '')
          push(ok ? { title: '通讯稿全文已复制', msg: '去秀米直接粘贴即可', type: 'success' }
            : { title: '复制失败', msg: '浏览器未授权剪贴板，请手动全选复制通讯稿', type: 'warning' })
        }}>复制通讯稿全文</Button>
        <Button icon="externalLink" onClick={() => window.open('https://xiumi.us/studio', '_blank', 'noopener')}>打开秀米编辑器</Button>
        <div className="field" style={{ flex: '2 1 220px' }}>
          <label className="field__label" htmlFor="act-xiumi">我的秀米草稿链接（选填）</label>
          <input id="act-xiumi" className="input" placeholder="https://xiumi.us/..." value={xiumi} onChange={(e) => setXiumi(e.target.value)} />
        </div>
        <Button size="sm" onClick={() => patch({ xiumi_url: xiumi }, '秀米链接已保存')}>保存</Button>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="act-article">发布后的微信文章链接</label>
        <div className="panel-row" style={{ gap: 10, flexWrap: 'wrap' }}>
          <input id="act-article" className="input" style={{ flex: '3 1 260px' }}
            placeholder="https://mp.weixin.qq.com/s/..."
            value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button size="sm" variant="ghost" onClick={resetUrl}>还原</Button>
        </div>
        <div className="panel-row" style={{ gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <input ref={shotRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={onShotPick} />
          <Button variant="primary" icon="camera" loading={busy} disabled={!!job}
            onClick={() => shotRef.current?.click()}>
            {job ? 'AI 正在识别推文截图…' : '上传推文长截图归档'}
          </Button>
          <span className="wb-card__muted">
            {job ? '识别在后台进行，可以离开本页；完成后会自动通知，无需停留等待。'
              : '请截一张微信公众号推文页面的完整长图上传：AI 自动识别标题与正文入库，非推文截图会被拒收。'}
          </span>
        </div>
      </div>
      <div className="panel-row">
        <Button variant="primary" icon="archive" disabled={!!published} loading={busy}
          onClick={publishArchive}>确认发布归档</Button>
        <span className="wb-card__muted">确认后活动进入「已归档」，可在工作台「已归档」列随时回看。</span>
      </div>
    </Panel>
  )
}

/* 全屏礼花（canvas 重力粒子，四波错开爆发）；按需求不随动效开关：完成归档这一刻
   始终放花（含手机端），其余磁吸/辉光等动效仍受 data-fx 总开关控制。 */
function Fireworks() {
  useEffect(() => {
    const cv = document.createElement('canvas')
    cv.width = window.innerWidth
    cv.height = window.innerHeight
    cv.style.cssText = 'position:fixed;inset:0;z-index:60;pointer-events:none' // 全屏礼花：浮于弹窗之上，不遮挡菜单与互动
    document.body.appendChild(cv)
    const ctx = cv.getContext('2d')
    const COLORS = ['#c77b5b', '#b5a273', '#8ba888', '#a48fc4', '#d9998f', '#c9b8a3']
    const parts = []
    const burst = (x, y, n = 46) => {
      for (let i = 0; i < n; i++) {
        const ang = Math.PI * 2 * Math.random()
        const v = 2.5 + Math.random() * 5.5
        parts.push({
          x, y,
          vx: Math.cos(ang) * v,
          vy: Math.sin(ang) * v - 2.4,
          life: 0.9 + Math.random() * 0.3,
          size: 2.2 + Math.random() * 2.6,
          color: COLORS[(Math.random() * COLORS.length) | 0],
          square: Math.random() > 0.5,
        })
      }
    }
    const cx = cv.width / 2
    const cy = cv.height / 2
    const t0 = performance.now()
    const timers = [0, 380, 820, 1300].map((d) => setTimeout(() => {
      burst(
        cx + (Math.random() - 0.5) * cv.width * 0.5,
        cy + (Math.random() - 0.5) * cv.height * 0.35,
      )
    }, d))
    let raf = 0
    let over = false
    const tick = (t) => {
      if (over) return
      ctx.clearRect(0, 0, cv.width, cv.height)
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i]
        p.x += p.vx
        p.y += p.vy
        p.vy += 0.15
        p.vx *= 0.986
        p.life -= 0.012
        if (p.life <= 0) parts.splice(i, 1)
      }
      for (const p of parts) {
        ctx.globalAlpha = Math.max(0, Math.min(1, p.life))
        ctx.fillStyle = p.color
        if (p.square) ctx.fillRect(p.x, p.y, p.size, p.size * 1.4)
        else { ctx.beginPath(); ctx.arc(p.x, p.y, p.size / 2, 0, Math.PI * 2); ctx.fill() }
      }
      ctx.globalAlpha = 1
      if (t - t0 > 3400) {
        over = true
        ctx.clearRect(0, 0, cv.width, cv.height)
        return
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      over = true
      cancelAnimationFrame(raf)
      timers.forEach(clearTimeout)
      cv.remove()
    }
  }, [])
  return null
}

export default function ActivityDetail({ activityId, me, members, identityReload, onBack }) {
  const [act, setAct] = useState(null)
  const nav = useSliderNav(6)
  const [deleting, setDeleting] = useState(false)
  const [stageConfirm, setStageConfirm] = useState(null) // {to, fields, lines, note, mode}
  const [advancing, setAdvancing] = useState(false)
  const initRef = useRef(false)
  const push = useToast()

  const load = useCallback(() => {
    api(`/api/activities/${activityId}`)
      .then((d) => setAct(d))
      .catch((e) => push({ title: '加载失败', msg: e.message, type: 'error' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activityId])

  useEffect(() => { load() }, [load])

  useLayoutEffect(() => {
    if (act && !initRef.current) {
      initRef.current = true
      nav.select(Math.min(5, STAGE_TO_TAB[act.stage] ?? 0))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [act])

  /* 阶段推进后，滑块跟随新阶段（含「确认接单」触发的 planning） */
  const stageRef = useRef(null)
  useEffect(() => {
    if (act && stageRef.current !== act.stage) {
      stageRef.current = act.stage
      nav.select(Math.min(5, STAGE_TO_TAB[act.stage] ?? 0))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [act?.stage])

  /* 手机端：点选阶段后把选中项滚进视野（标签条为条内横滚） */
  useEffect(() => {
    if (!IS_MOBILE) return
    const navEl = document.querySelector('.wb-stagebar .tabs__nav')
    const on = navEl?.querySelectorAll('.tabs__item')[nav.settled]
    on?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.settled])

  const merge = (d) => setAct(d)

  const patch = async (body, msg = '已保存') => {
    try {
      const d = await api(`/api/activities/${activityId}`, { method: 'PATCH', body })
      merge(d)
      push({ title: msg, type: 'success' })
      identityReload?.()
      return d
    } catch (e) {
      push({ title: '保存失败', msg: e.message, type: 'error' })
      return null
    }
  }

  /* 推进确认：面板把未保存字段打包进来，弹窗预览，确认后先存未保存内容再推进 */
  const confirmAdvance = (cfg) => setStageConfirm(cfg)
  const doAdvance = async () => {
    const cfg = stageConfirm
    if (!cfg || advancing) return
    setAdvancing(true)
    try {
      if (cfg.mode === 'publish') {
        const f = cfg.fields || {}
        if (Object.keys(f).length) {
          const ok = await patch(f, '发布前信息已保存')
          if (!ok) throw new Error('保存未通过，请重试')
        }
        const r = await api(`/api/activities/${activityId}/publish-confirm`, { method: 'POST' })
        merge(r)
        setCelebrate({ line: pickCheer(), synced: !!r?.material_synced })
      } else {
        const n = Object.keys(cfg.fields ?? {}).length
        const d = await patch(
          { stage: cfg.to, ...(cfg.fields ?? {}) },
          `已推进到「${STAGE_LABEL[cfg.to]}」${n ? `（随行保存 ${n} 项）` : ''}`,
        )
        if (!d) throw new Error('未保存')
      }
      identityReload?.()
      setStageConfirm(null)
    } catch (e) {
      push({ title: cfg.mode === 'publish' ? '归档失败' : '推进失败', msg: e.message, type: 'error' })
    } finally {
      setAdvancing(false)
    }
  }

  /* 完成归档的庆祝：弹窗（随机鼓励语彩蛋）+ 全屏礼花动效组件，动效总开关关闭时只弹窗不放花 */
  const CHEERS = [
    '又一个活动圆满收官，十号社区的烟火气又浓了一分',
    '素材库又厚了一页，灵感银行继续复利',
    '从派发到归档全程走完，这份「完成」含金量满满',
    '把节气过成了日子，把日子过成了作品',
    '下一个活动已经在路上排队点单了，保持这个节奏',
    '照片、通讯稿、推文、素材、wiki 一次配齐，主打善始善终',
    '今天也是为十号社区存档美好的一天',
    '活动会过期，成就感不会',
    '体面收场，下一单更稳',
    '这一棒交接漂亮，接力棒抛得很高',
  ]
  const [celebrate, setCelebrate] = useState(null) // {line, synced}

  const pickCheer = () => CHEERS[Math.floor(Math.random() * CHEERS.length)]

  const confirmDispatch = async () => {
    try {
      const d = await api(`/api/activities/${activityId}/confirm`, { method: 'POST' })
      setAct(d)
      push({ title: '已确认接单', msg: '活动进入「策划中」', type: 'success' })
      return d
    } catch (e) {
      push({ title: '确认失败', msg: e.message, type: 'error' })
      return null
    }
  }

  if (!act) {
    return (
      <div className="panel-stack" style={{ gap: 18 }}>
        <Button variant="ghost" icon="chevronLeft" onClick={onBack}>返回工作台</Button>
        <div className="glass" style={{ padding: 30 }}>
          <div className="empty">
            <div className="empty__art"><Icon name="refresh" /></div>
            <div className="empty__title">加载中…</div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="panel-stack" style={{ gap: 16 }}>
      {IS_MOBILE ? (
        <div className="wb-detail-head wb-detail-head--m">
          <Button size="sm" variant="ghost" icon="chevronLeft" onClick={onBack}>工作台</Button>
          <span style={{ marginLeft: 'auto', flex: 'none' }}>
            <IconButton label="删除活动" icon="trash" variant="ghost" className="btn--danger-text" onClick={() => setDeleting(true)} />
          </span>
          <span className="wb-detail-head__name">{act.name}</span>
          <div className="wb-detail-head__info">
            <Badge tone="blue">{act.activity_type || '未分类'}</Badge>
            <Badge tone={act.stage === 'done' ? 'sage' : 'sand'}>{STAGE_LABEL[act.stage]}</Badge>
            <span className="wb-card__muted">
              进度 {act.stage === 'done' ? 6 : (STAGE_TO_TAB[act.stage] ?? 0)}/6
            </span>
            {act.lead_name && (
              <span className="wb-card__meta"><Icon name="user" size={12} /> {act.lead_name}</span>
            )}
            {act.event_date && (
              <span className="wb-card__meta"><Icon name="calendar" size={12} /> {act.event_date}</span>
            )}
          </div>
        </div>
      ) : (
        <div className="wb-detail-head">
          <Button size="sm" variant="ghost" icon="chevronLeft" onClick={onBack}>工作台</Button>
          <span className="wb-detail-head__name">{act.name}</span>
          <Badge tone="blue">{act.activity_type || '未分类'}</Badge>
          <Badge tone={act.stage === 'done' ? 'sage' : 'sand'}>{STAGE_LABEL[act.stage]}</Badge>
          {act.lead_name && (
            <span className="wb-card__meta"><Icon name="user" size={12} /> {act.lead_name}</span>
          )}
          {act.event_date && (
            <span className="wb-card__meta"><Icon name="calendar" size={12} /> {act.event_date}</span>
          )}
          <span style={{ marginLeft: 'auto' }}>
            <IconButton label="删除活动" icon="trash" variant="ghost" className="btn--danger-text" onClick={() => setDeleting(true)} />
          </span>
        </div>
      )}

      <div className="tabs wb-stagebar" role="presentation">
        {/* 手机端标签条可横滑：只保留点选与原生滚动，关闭拖拽换页以免抢滚动 */}
        <SliderNav options={STAGE_TABS.map((t) => t.label)} nav={nav} label="活动阶段" draggable={!IS_MOBILE} />
      </div>

      {/* 时间节点条：举办 / 策划 / 招募 / 采购，点选可跳到对应阶段 */}
      <div className="glass wb-ddlstrip">
        {([
          ['举办', act.event_date, 3],
          ['策划', act.plan_ddl, 1],
          ['招募推文', act.recruitment_ddl, 1],
          ['采购报销', act.procurement_ddl, 2],
        ]).map(([label, date, tabIndex]) => {
          const left = daysLeftOf(date)
          const overdue = left != null && left < 0
          const near = left != null && left >= 0 && left <= 3
          return (
            <div key={label} className="wb-ddlstrip__cell" data-over={overdue ? '1' : undefined}>
              <span className="wb-ddlstrip__label">{label}</span>
              <span className="wb-ddlstrip__date">
                {date ? date.slice(5).replace('-', '/') : '未定'}
                {left != null && date && (
                  <Badge tone={overdue ? 'clay' : near ? 'rose' : 'sand'}>
                    {overdue ? `已过 ${-left} 天` : `剩 ${left} 天`}
                  </Badge>
                )}
              </span>
              <button className="wb-ddlstrip__go" onClick={() => nav.select(tabIndex)}>
                去处理 ›
              </button>
            </div>
          )
        })}
      </div>

      {nav.settled === 0 && (
        <DispatchPanel act={act} patch={patch} me={me} members={members} onConfirm={confirmDispatch} confirmAdvance={confirmAdvance} />
      )}
      {nav.settled === 1 && <PlanningPanel act={act} patch={patch} confirmAdvance={confirmAdvance} />}
      {nav.settled === 2 && (
        <ProcurementPanel act={act} activityId={activityId} patch={(b, m) => patch(b, m)} reload={load} confirmAdvance={confirmAdvance} me={me} />
      )}
      {nav.settled === 3 && <ExecutionPanel act={act} patch={patch} confirmAdvance={confirmAdvance} />}
      {nav.settled === 4 && (
        <CoveragePanel act={act} activityId={activityId} patch={patch} reload={load} confirmAdvance={confirmAdvance} me={me} />
      )}
      {nav.settled === 5 && (
        <PublishPanel act={act} activityId={activityId} patch={patch} reload={load} confirmAdvance={confirmAdvance} me={me} />
      )}

      {celebrate && <Fireworks />}
      <Modal
        open={!!celebrate}
        onClose={() => setCelebrate(null)}
        title="🎉 恭喜完成一个活动"
        width={420}
        footer={(
          <Button variant="primary" icon="checkCircle" onClick={() => setCelebrate(null)}>好耶，收下啦</Button>
        )}
      >
        <div className="celebrate-line">{celebrate?.line}</div>
        {celebrate?.synced && (
          <div className="celebrate-sub">推文已自动收入素材库；形式/亮点/参与要点正由 AI 在后台补全，稍后回素材库即可看到</div>
        )}
      </Modal>

      <Modal
        open={deleting}
        onClose={() => setDeleting(false)}
        title="删除活动"
        width={420}
        footer={(
          <>
            <Button onClick={() => setDeleting(false)}>取消</Button>
            <Button variant="danger" icon="trash" onClick={async () => {
              try {
                await api(`/api/activities/${activityId}`, { method: 'DELETE' })
                push({ title: '活动已删除', type: 'success' })
                onBack()
              } catch (e) {
                push({ title: '删除失败', msg: e.message, type: 'error' })
              }
            }}>删除</Button>
          </>
        )}
      >
        <p style={{ marginTop: 4 }}>确定删除该活动及其全部报销条目、照片与票据？此操作不可撤销。</p>
      </Modal>

      <Modal
        open={!!stageConfirm}
        onClose={() => setStageConfirm(null)}
        title={stageConfirm?.mode === 'publish' ? '确认发布归档' : `确认推进 · ${stageConfirm ? STAGE_LABEL[stageConfirm.to] : ''}`}
        width={480}
        footer={(
          <>
            <Button onClick={() => setStageConfirm(null)}>返回</Button>
            {stageConfirm?.mode === 'publish'
              ? <Button variant="primary" icon="archive" loading={advancing} onClick={doAdvance}>完成归档</Button>
              : <Button variant="primary" icon="arrowRight" loading={advancing} onClick={doAdvance}>确认推进</Button>}
          </>
        )}
      >
        {stageConfirm && (
          <div className="panel-stack" style={{ gap: 10, marginTop: 4 }}>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              <Badge tone="blue">{act.name}</Badge>
              <span className="row" style={{ gap: 6 }}>
                <Badge tone="sand">{STAGE_LABEL[act.stage]}</Badge>
                <Icon name="chevronRight" size={13} />
                <Badge tone={stageConfirm.to === 'done' ? 'sage' : 'clay'}>
                  {stageConfirm.mode === 'publish' ? '完成归档' : STAGE_LABEL[stageConfirm.to]}
                </Badge>
              </span>
            </div>
            {stageConfirm.lines?.length > 0 && (
              <>
                <div className="cond-label">将随本次推进自动保存</div>
                <div className="panel-stack" style={{ gap: 4 }}>
                  {stageConfirm.lines.map((l, i) => (
                    <div key={i} className="row" style={{ gap: 8 }}>
                      <Icon name="check" size={13} />
                      <span style={{ color: 'var(--ink-2)' }}>{l}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
            {stageConfirm.note && <div className="field__hint">{stageConfirm.note}</div>}
            <div className="field__hint">确认后以上内容立即提交，无需再单独点保存。</div>
          </div>
        )}
      </Modal>
    </div>
  )
}
