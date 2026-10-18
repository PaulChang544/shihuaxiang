import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import Select from '../ui/Select.jsx'
import { Modal } from '../ui/Overlay.jsx'
import { Badge, IconBubble } from '../ui/bits.jsx'
import { humanName } from '../names.js'
import { useToast } from '../ui/Toast.jsx'
import useIdentity from '../hooks/useIdentity'
import ActivityDetail from '../components/ActivityDetail'
import SectionHead from '../components/SectionHead'
import { ActivationForm } from '../components/Activation.jsx'
import IS_MOBILE from '../isMobile.js'

const TYPES = ['节气时令', '手工 DIY', '茶话会', '志愿服务', '党团学习', '学习互助',
  '职业发展', '心理关怀', '技能工作坊', '文体活动', '文艺演出', '社会实践', '校庆活动', '其他']

const todayStr = () => new Date().toISOString().slice(0, 10)

function StatCard({ label, num, tone, icon, onClick }) {
  const clickable = !!onClick
  return (
    <div
      className="glass"
      style={{ padding: 18, cursor: clickable ? 'pointer' : undefined }}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={clickable ? (e) => { if (e.key === 'Enter') onClick() } : undefined}
    >
      <div className="stat">
        <div className="stat__top">
          <span className="stat__label">{label}</span>
          <IconBubble tone={tone} icon={icon} size={15} />
        </div>
        <div className="stat__num">{num}</div>
        {clickable && <div className="stat__hint">查看 ›</div>}
      </div>
    </div>
  )
}

const STAGE_NODES = [
  { key: 'dispatch', label: '派发', icon: 'flag' },
  { key: 'planning', label: '策划', icon: 'edit' },
  { key: 'procurement', label: '采购报销', icon: 'package' },
  { key: 'execution', label: '举办', icon: 'calendar' },
  { key: 'coverage', label: '影像稿件', icon: 'camera' },
  { key: 'publish', label: '推文发布', icon: 'send' },
]
const STAGE_INDEX = { dispatch: 0, planning: 1, procurement: 2, execution: 3, coverage: 4, publish: 5 }
const fmtYuan = (n) => `¥${Number(n || 0)}`

/* 每条阶段节点下的小注（有数据才显示） */
function nodeSub(act, i) {
  switch (STAGE_NODES[i].key) {
    case 'dispatch':
      return act.lead_name
        ? (act.dispatch_status === 'confirmed' ? '已接单' : `待 ${act.lead_name} 确认`)
        : '未指派'
    case 'planning':
      return act.plan_note ? '要点已写' : ''
    case 'procurement':
      return act.expense_total > 0
        ? `${fmtYuan(act.expense_total)}（已报 ${fmtYuan(act.expense_reimbursed)}）`
        : ''
    case 'execution':
      return act.event_date || ''
    case 'coverage':
      return act.photo_count > 0 ? `${act.photo_count} 张照片` : (act.report_text ? '通讯稿 ✓' : '')
    case 'publish':
      return act.article_url ? '文章已归档' : ''
    default:
      return ''
  }
}

function ActivityTimelineRow({ act, overdue, onOpen }) {
  const open = () => onOpen?.(act.id)
  const idx = STAGE_INDEX[act.stage] ?? 6
  return (
    <div
      className="glass glass--hover wb-tl"
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => { if (e.key === 'Enter') open() }}
    >
      <div className="wb-tl__head">
        <Badge tone="blue">{act.activity_type || '未分类'}</Badge>
        <span className="wb-tl__name">{act.name}</span>
        {overdue && <Badge tone="clay">活动日已过</Badge>}
        <span className="wb-tl__spacer" />
        <span className="wb-card__meta">
          <Icon name="user" size={12} />
          {act.lead_name || '未指派负责人'}
        </span>
        {act.event_date && (
          <span className="wb-card__meta wb-card__meta--date">
            <Icon name="calendar" size={12} /> {act.event_date}
          </span>
        )}
        {act.article_url && (
          <a className="wb-card__meta" href={act.article_url} target="_blank" rel="noreferrer"
            aria-label="打开活动推文" onClick={(e) => e.stopPropagation()}>
            <Icon name="externalLink" size={12} /> 推文 ↗
          </a>
        )}
        <Icon name="chevronRight" size={15} />
      </div>
      <div className="wb-tl__rail">
        {STAGE_NODES.map((st, i) => {
          const state = act.stage === 'done' || i < idx
            ? 'done'
            : i === idx
              ? (st.key === 'dispatch' && act.dispatch_status === 'assigned' ? 'wait' : 'cur')
              : 'todo'
          const sub = nodeSub(act, i)
          return (
            <div key={st.key} className={`wb-tl__node wb-tl__node--${state}`}>
              <span className="wb-tl__dot">
                <Icon name={state === 'done' ? 'check' : st.icon} size={12} />
              </span>
              <span className="wb-tl__label">{st.label}</span>
              {sub && <span className="wb-tl__sub">{sub}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CreateModal({ open, onClose, favs, members, onCreated }) {
  const [mode, setMode] = useState('blank') // blank | idea
  const [form, setForm] = useState({ name: '', type: '', lead: '', event_date: '' })
  const [saving, setSaving] = useState(false)
  const push = useToast()

  const pickIdea = (id) => {
    const idea = favs.find((f) => f.id === Number(id))
    if (idea) setForm((f) => ({ ...f, name: idea.name, type: idea.activity_type }))
  }

  const create = async () => {
    if (!form.name.trim()) {
      push({ title: '请填写活动名称', type: 'warning' })
      return
    }
    if (!form.event_date) {
      push({ title: '请填写活动举办日期', type: 'warning' })
      return
    }
    setSaving(true)
    try {
      const act = await api('/api/activities', {
        method: 'POST',
        body: {
          name: form.name.trim(), activity_type: form.type,
          idea_id: mode === 'idea' ? Number(form.idea_id) || null : null,
          event_date: form.event_date,
        },
      })
      if (form.lead) {
        await api(`/api/activities/${act.id}`, { method: 'PATCH', body: { lead_ids: [Number(form.lead)] } })
      }
      push({ title: '活动已创建', msg: '策划/招募 DDL 与采购 DDL 已按惯例自动排期', type: 'success' })
      onCreated?.(act.id)
    } catch (e) {
      push({ title: '创建失败', msg: e.message, type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建活动"
      width={520}
      footer={(
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" icon="plus" loading={saving} onClick={create}>创建</Button>
        </>
      )}
    >
      <div className="panel-stack">
        <div className="row" style={{ gap: 10 }}>
          <Button size="sm" variant={mode === 'blank' ? 'primary' : 'outline'} onClick={() => setMode('blank')}>空白新建</Button>
          <Button size="sm" variant={mode === 'idea' ? 'primary' : 'outline'} onClick={() => setMode('idea')}>
            从收藏点子{favs.length ? `（${favs.length}）` : ''}
          </Button>
        </div>
        {mode === 'idea' && (
          <div className="field">
            <label className="field__label">收藏点子</label>
            {favs.length ? (
              <Select
                options={favs.map((f) => ({ value: f.id, label: `${f.name}（${f.activity_type || '未分类'}）` }))}
                placeholder="选择一个点子"
                onChange={pickIdea}
              />
            ) : (
              <div className="field__hint">还没有收藏点子</div>
            )}
          </div>
        )}
        <div className="field">
          <label className="field__label">活动名称 <small>必填</small></label>
          <input
            className="input"
            placeholder="如：暮秋已别，顺问冬安 —— 小雪手作茶会"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
        </div>
        <div className="field">
          <label className="field__label" htmlFor="create-event-date">活动举办日期 <small>必填</small></label>
          <input
            id="create-event-date"
            className="input"
            type="date"
            required
            value={form.event_date}
            onChange={(e) => setForm((f) => ({ ...f, event_date: e.target.value }))}
          />
          <div className="field__hint">未填写的 DDL 按惯例自动算出，详情里可改</div>
        </div>
        <div className="field">
          <label className="field__label">活动类型</label>
          <Select
            options={['', ...TYPES]}
            value={form.type}
            placeholder="选择类型"
            onChange={(v) => setForm((f) => ({ ...f, type: v }))}
          />
        </div>
        <div className="field">
          <label className="field__label">负责人 <small>可选，派发后再定亦可</small></label>
          <Select
            options={[{ value: '', label: '暂不指定' }, ...members.filter((m) => m.role !== 'super').map((m) => ({ value: m.id, label: humanName(m.name) }))]}
            value={form.lead}
            placeholder="暂不指定"
            onChange={(v) => setForm((f) => ({ ...f, lead: v }))}
          />
        </div>
      </div>
    </Modal>
  )
}

export default function WorkbenchPage() {
  const [acts, setActs] = useState([])
  const [favs, setFavs] = useState([])
  const [ddls, setDdls] = useState([])
  const [openId, setOpenId] = useState(null)
  const [creating, setCreating] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [switching, setSwitching] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [logOpen, setLogOpen] = useState(false)
  const [logState, setLogState] = useState(null)   // {items, has_more, total}
  const [logLoading, setLogLoading] = useState(false)
  const [logActorF, setLogActorF] = useState('')
  const [logActF, setLogActF] = useState('')
  const { me, members, reload: reloadMembers, logout } = useIdentity()
  const push = useToast()

  const load = useCallback(() => {
    // /api/log?limit=1 只为拿 total：首页「操作记录」卡不点击也能直接显示总数
    Promise.all([
      api('/api/activities'),
      api('/api/ideas?favorited=1'),
      api('/api/activities/ddl-summary'),
      // 操作记录总数是锦上添花：它失败绝不能拖垮整个首页（列表/统计必须照常显示）
      api('/api/log?limit=1').catch(() => ({ total: 0, has_more: false, items: [] })),
    ])
      .then(([a, f, d, l]) => { setActs(a); setFavs(f); setDdls(d); setLogState(l) })
      .catch((e) => push({ title: '加载失败', msg: e.message, type: 'error' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { load() }, [load])

  /* 操作记录弹窗：分页（加载更多）+ 操作人/活动筛选 */
  const openLog = () => setLogOpen(true)
  const fetchLogs = (offset) => {
    setLogLoading(true)
    const actor = logActorF ? Number(logActorF) : null
    const act = logActF ? Number(logActF) : null
    api(`/api/log?limit=120&offset=${offset}${actor != null ? `&actor_id=${actor}` : ''}${act != null ? `&activity_id=${act}` : ''}`)
      .then((r) => {
        setLogState((s) => (offset === 0 ? r : { ...r, items: [...(s?.items || []), ...r.items] }))
      })
      .catch((e) => push({ title: '操作记录加载失败', msg: e.message, type: 'error' }))
      .finally(() => setLogLoading(false))
  }
  useEffect(() => {
    if (!logOpen) return
    fetchLogs(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logOpen, logActorF, logActF])

  const jumpToPipeline = () => {
    document.querySelector('.wb-section--pipeline')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  /* DDL 提醒只关注「我负责的活动」 */
  const myActIds = new Set(
    acts.filter((a) => (a.lead_ids || []).includes(me?.id)).map((a) => a.id),
  )
  const myDdls = me ? ddls.filter((d) => myActIds.has(d.activity_id)) : []

  const reload = () => { load(); reloadMembers() }

  const confirmTake = async (act) => {
    try {
      await api(`/api/activities/${act.id}/confirm`, { method: 'POST', body: { member_id: me.id } })
      push({ title: '已确认接单', msg: '活动进入「策划中」', type: 'success' })
      setConfirming(null)
      load()
    } catch (e) {
      push({ title: '确认失败', msg: e.message, type: 'error' })
    }
  }

  if (openId != null) {
    return (
      <ActivityDetail
        activityId={openId}
        me={me}
        members={members}
        identityReload={reloadMembers}
        onBack={() => { setOpenId(null); load() }}
      />
    )
  }

  // 待我确认：已指派给我且我还没接单
  // 待我确认：已指派给我且我还没接单
  const myConfirming = me ? acts.filter((a) => a.dispatch_status === 'assigned' && a.lead_id === me.id) : []
  // 本机构选择的身份但不在名册（即时性与统计无碍，仅提示）
  const active = acts.filter((a) => a.stage !== 'done')
  const done = acts.filter((a) => a.stage === 'done')
  const overdue = (a) => a.event_date && a.event_date < todayStr() && a.stage !== 'dispatch' && a.stage !== 'done'

  return (
    <div className="panel-stack" style={{ gap: 26 }}>
      {IS_MOBILE ? (
        <div className="wb-idbar glass">
          <Icon name="user" size={15} />
          <span className="wb-idbar__name">{me ? humanName(me.name) || '—' : '—'}</span>
          <Badge tone={me?.role === 'owner' ? 'clay' : me?.role === 'super' ? 'lilac' : me?.role === 'admin' ? 'lilac' : 'sage'}>
            {me?.role === 'owner' ? '站长' : me?.role === 'super' ? '超管' : me?.role === 'admin' ? '管理员' : '成员'}
          </Badge>
          <span className="wb-idbar__spacer" />
          <Button size="sm" variant="ghost" icon="refresh" onClick={() => setSwitching(true)}>切换身份</Button>
        </div>
      ) : (
        <div className="wb-bar">
          <div>
            <div style={{ fontSize: 26, fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
              活动工作台
            </div>
          </div>
          <div className="wb-bar__tools">
            <span className="wb-mechip">
              <Icon name="user" size={14} />
              <span>我是 · {me ? humanName(me.name) : '—'}</span>
              <Button size="sm" variant="ghost" onClick={() => setSwitching(true)}>切换</Button>
            </span>
            <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>新建活动</Button>
          </div>
        </div>
      )}

      <div className="wb-stats">
        <StatCard label="进行中" num={active.length} tone="blue" icon="zap" onClick={jumpToPipeline} />
        <StatCard label="已归档" num={done.length} tone="sage" icon="archive" onClick={() => setArchiveOpen(true)} />
        <StatCard label="操作记录" num={logState ? logState.total : '—'} tone="lilac" icon="users" onClick={openLog} />
      </div>

      <Modal
        open={archiveOpen}
        onClose={() => setArchiveOpen(false)}
        title={`已归档项目（${done.length}）`}
        width={560}
        footer={<Button onClick={() => setArchiveOpen(false)}>关闭</Button>}
      >
        {done.length ? (
          <div className="panel-stack" style={{ gap: 10, marginTop: 4 }}>
            {done.map((a) => (
              <div key={a.id} className="row wb-archrow" role="button" tabIndex={0}
                onClick={() => { setArchiveOpen(false); setOpenId(a.id) }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setArchiveOpen(false); setOpenId(a.id) } }}>
                <div style={{ minWidth: 0 }}>
                  <div className="row" style={{ gap: 8 }}>
                    <Badge tone="blue">{a.activity_type || '未分类'}</Badge>
                    <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{a.name}</span>
                  </div>
                  <div className="wb-card__muted">举办 {a.event_date || '—'} · 负责人 {a.lead_name || '—'}</div>
                </div>
                <span style={{ marginLeft: 'auto', flex: 'none' }} className="wb-card__meta">
                  {a.published_at ? `归档于 ${a.published_at.slice(0, 10)}` : ''}
                </span>
                <Icon name="chevronRight" size={14} />
              </div>
            ))}
          </div>
        ) : (
          <p style={{ marginTop: 4 }}>还没有已完成归档的活动。</p>
        )}
      </Modal>

      <Modal
        open={logOpen}
        onClose={() => setLogOpen(false)}
        title="操作记录"
        width={560}
        footer={
          <>
            {logState?.has_more && (
              <Button size="sm" loading={logLoading} onClick={() => fetchLogs(logState.items.length)}>加载更多</Button>
            )}
            <Button onClick={() => setLogOpen(false)}>关闭</Button>
          </>
        }
      >
        <div className="row" style={{ gap: 10, marginTop: 4 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Select
              options={[{ value: '', label: '全部操作人' }, ...members.map((m) => ({ value: m.id, label: humanName(m.name) }))]}
              value={logActorF}
              placeholder="全部操作人"
              onChange={(v) => setLogActorF(String(v))}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Select
              options={[{ value: '', label: '全部活动' }, ...acts.map((a) => ({ value: a.id, label: a.name }))]}
              value={logActF}
              placeholder="全部活动"
              onChange={(v) => setLogActF(String(v))}
            />
          </div>
        </div>
        {logState?.items?.length ? (
          <div className="panel-stack wb-loglist" style={{ gap: 10, marginTop: 10, maxHeight: 420, overflowY: 'auto' }}>
            {logState.items.map((l) => (
              <div key={l.id} className="row wb-logrow" style={{ gap: 10, alignItems: 'flex-start' }}>
                <span className="wb-card__muted" style={{ flex: 'none', width: 76 }}>{l.created_at?.slice(5, 16)}</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div>
                    {l.actor_name ? <b>{humanName(l.actor_name)}</b> : <span className="wb-card__muted">有人</span>}
                    {' '}
                    {l.detail}
                    {l.activity_name && l.detail.indexOf(l.activity_name) === -1
                      ? <span className="wb-card__muted">（{l.activity_name}）</span> : null}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : logLoading ? (
          <p style={{ marginTop: 4 }}>加载中……</p>
        ) : (
          <p style={{ marginTop: 4 }}>
            {logState?.total ? '当前筛选下没有匹配的记录。' : '暂无操作记录（升级后新发生的动作才会逐条记录）。'}
          </p>
        )}
        {logState && (
          <div className="field__hint" style={{ marginTop: 8 }}>
            共 {logState.total} 条记录，当前展示 {logState.items.length} 条{logState.has_more ? '，还有更早的记录' : ''}
          </div>
        )}
      </Modal>

      <Modal
        open={switching}
        onClose={() => setSwitching(false)}
        title="切换身份 / 退出登录"
        width={420}
        footer={(
          <>
            <Button onClick={() => setSwitching(false)}>关闭</Button>
            <Button variant="ghost" icon="refresh" onClick={async () => { await logout(); setSwitching(false) }}>退出登录</Button>
          </>
        )}
      >
        <div style={{ marginTop: 4 }}>
          直接输入另一位的邀请码即可切换（会话由服务器签发，邀请码不再保存在本机）。
          <ActivationForm onDone={() => { setSwitching(false); reload() }} />
        </div>
      </Modal>

      {myDdls.length > 0 && (
        <div className="wb-section">
          <SectionHead idx="00" cn="DDL 提醒" en="DEADLINES"
            tail={`${ddls.length} 项`} />
          <div className="glass wb-ddlcard">
            {myDdls.map((d) => (
              <div key={d.id} className="wb-ddlcard__row" role="button" tabIndex={0}
                onClick={() => setOpenId(d.activity_id)}
                onKeyDown={(e) => { if (e.key === 'Enter') setOpenId(d.activity_id) }}>
                <Badge tone={d.kind === 'plan' ? 'lilac' : d.kind === 'procurement' ? 'sand' : 'blue'}>
                  {d.label}
                </Badge>
                <span className="wb-ddlcard__name">{d.name}</span>
                <span className="wb-card__meta">{d.date}</span>
                <span style={{ marginLeft: 'auto', flex: 'none' }}>
                  {d.days_left < 0
                    ? <Badge tone="clay">已过 {-d.days_left} 天</Badge>
                    : <Badge tone={d.days_left === 0 ? 'rose' : d.days_left <= 3 ? 'rose' : 'sand'}>
                        {d.days_left === 0 ? '今天到期' : `剩 ${d.days_left} 天`}
                      </Badge>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {myConfirming.length > 0 && (
        <div className="wb-section">
          <SectionHead idx="01" cn="待我确认" en="Todo"
            tail={me ? `身份：${humanName(me.name)}` : 'WAITING'} />
          <div className="wb-todo">
            {myConfirming.map((a) => (
              <div key={a.id} className="glass wb-todo__row" role="button" tabIndex={0}
                onClick={() => setOpenId(a.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpenId(a.id) }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                  <span className="wb-card__name">{a.name}</span>
                  <span className="wb-card__meta">
                    <Badge tone="blue">{a.activity_type || '未分类'}</Badge>
                    {a.event_date && <span>· {a.event_date}</span>}
                  </span>
                </div>
                <div className="row" style={{ gap: 8, flex: 'none' }}>
                  <Button size="sm" variant="primary" icon="check" onClick={() => setConfirming(a)}>确认接单</Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="wb-section wb-section--pipeline" id="pipelineAnchor">
        <SectionHead idx="02" cn="活动流水线" en="Pipeline"
          tail={`${acts.length} 项`} />
        <div className="wb-tl-list">
          {acts.length === 0 ? (
            <div className="empty glass">
              <div className="empty__art"><Icon name="home" /></div>
              <div className="empty__title">还没有活动</div>
              <div className="empty__text">点「新建活动」创建，或去「灵感」页生成点子后一键转为活动。</div>
            </div>
          ) : (
            // 进行中在前，已归档沉底；组内按举办日期从近到远
            [...acts]
              .sort((a, b) => (a.stage === 'done' ? 1 : 0) - (b.stage === 'done' ? 1 : 0)
                || String(a.event_date || '9999').localeCompare(String(b.event_date || '9999'))
                || a.id - b.id)
              .map((a) => (
                <ActivityTimelineRow key={a.id} act={a} overdue={overdue(a)} onOpen={setOpenId} />
              ))
          )}
        </div>
      </div>

      <CreateModal
        open={creating}
        onClose={() => setCreating(false)}
        favs={favs}
        members={members}
        onCreated={(id) => { setCreating(false); reload(); setOpenId(id) }}
      />

      {IS_MOBILE && (
        <button className="wb-fab" aria-label="新建活动" onClick={() => setCreating(true)}>
          <Icon name="plus" size={22} />
        </button>
      )}

      <Modal
        open={!!confirming}
        onClose={() => setConfirming(null)}
        title="确认接单"
        width={430}
        footer={(
          <>
            <Button onClick={() => setConfirming(null)}>取消</Button>
            <Button variant="primary" icon="check" onClick={() => confirming && confirmTake(confirming)}>
              确认接单
            </Button>
          </>
        )}
      >
        <p style={{ marginTop: 4 }}>以本机身份「{me ? humanName(me.name) : '未选择'}」确认接收该活动并将其推进到「策划中」？</p>
        {confirming && <p className="console-hint" style={{ marginTop: 6 }}>{confirming.name}</p>}
      </Modal>
    </div>
  )
}
