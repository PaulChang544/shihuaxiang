import { useState } from 'react'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import { useToast } from '../ui/Toast.jsx'
import { useSliderNav, SliderNav } from '../ui/SliderTabs.jsx'
import { useClickOutside } from '../ui/hooks.js'
import { useRef } from 'react'
import { api, streamSSE } from '../api'
import AgentMode from '../components/AgentMode'
import IdeaCard from '../components/IdeaCard'
import SectionHead from '../components/SectionHead'
import TracePanel from '../components/TracePanel'
import CardColumns from '../components/CardColumns'

const TYPES = ['节气时令', '手工 DIY', '茶话会', '志愿服务', '党团学习', '学习互助',
  '职业发展', '心理关怀', '技能工作坊', '文体活动', '文艺演出', '社会实践', '校庆活动', '其他']

/* 活动类型多选：玻璃控件 + 已选标签 + 浮层菜单 */
function TypePicker({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useClickOutside(ref, () => setOpen(false), open)

  const toggle = (t) => {
    onChange(value.includes(t) ? value.filter((x) => x !== t) : [...value, t])
  }

  return (
    <div className="picker" ref={ref}>
      <div
        className="picker__control"
        role="combobox"
        aria-expanded={open}
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((v) => !v) } }}
      >
        {value.length === 0
          ? <span className="picker__placeholder">选择活动类型（可多选）</span>
          : value.map((t) => (
            <span key={t} className="tag">
              {t}
              <button
                className="tag__close"
                aria-label={`移除 ${t}`}
                onClick={(e) => { e.stopPropagation(); toggle(t) }}
              >
                <Icon name="x" />
              </button>
            </span>
          ))}
        <span className="picker__chevron"><Icon name="chevronDown" size={16} /></span>
      </div>
      {open ? (
        <div className="menu picker__menu" role="listbox" aria-multiselectable="true">
          {TYPES.map((t) => {
            const on = value.includes(t)
            return (
              <button
                key={t}
                className="menu__item"
                role="option"
                aria-selected={on}
                onClick={() => toggle(t)}
              >
                {on ? <Icon name="check" size={15} /> : <span style={{ width: 15 }} />}
                {t}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

export default function IdeasPage({ onGoto }) {
  const [activityTypes, setActivityTypes] = useState([])
  const [ideas, setIdeas] = useState([])
  const [degraded, setDegraded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [favs, setFavs] = useState(new Set())
  const [trace, setTrace] = useState([])
  const push = useToast()
  const modeNav = useSliderNav(2)
  // 面板由 settled 驱动：点击与拖动都会更新它，避免出现「高亮已切换、内容没变」
  const mode = modeNav.settled === 0 ? 'types' : 'agent'

  const upsertStep = (patch) => {
    setTrace((prev) => {
      const i = prev.findIndex((t) => t.key === patch.key)
      if (i === -1) return [...prev, { output: '', ...patch }]
      const next = [...prev]
      next[i] = { ...next[i], ...patch }
      return next
    })
  }

  const appendChunk = (key, text) => {
    setTrace((prev) => {
      const i = prev.findIndex((t) => t.key === key)
      if (i === -1) return [...prev, { key, title: '生成输出', status: 'start', output: text }]
      const next = [...prev]
      next[i] = { ...next[i], output: (next[i].output || '') + text }
      return next
    })
  }

  const generate = async () => {
    if (!activityTypes.length) {
      push({ title: '请至少选择一个活动类型', type: 'warning' })
      return
    }
    setLoading(true)
    setTrace([])
    setIdeas([])
    setDegraded(false)
    try {
      await streamSSE('/api/ideas/generate/stream', { activity_types: activityTypes }, (event, d) => {
        if (event === 'step') {
          upsertStep({ key: d.key, title: d.title, brief: d.brief, status: d.status, detail: d.detail })
        } else if (event === 'chunk') {
          appendChunk(d.key, d.text)
        } else if (event === 'error') {
          push({ title: '生成失败', msg: d.message, type: 'error' })
        } else if (event === 'final') {
          setIdeas(d.ideas)
          setDegraded(d.degraded)
        }
      })
    } catch (e) {
      push({ title: '生成失败', msg: e.message, type: 'error' })
    } finally {
      setLoading(false)
    }
  }

  const favorite = async (id) => {
    try {
      const r = await api(`/api/ideas/${id}/favorite`, { method: 'POST' })
      const next = new Set(favs)
      if (r.favorited) next.add(id)
      else next.delete(id)
      setFavs(next)
      if (r.favorited) push({ title: '已收藏', msg: '可去工作台把收藏点子转为活动', type: 'success' })
    } catch (e) {
      push({ title: '操作失败', msg: e.message, type: 'error' })
    }
  }

  const toActivity = async (idea) => {
    try {
      await api('/api/activities', { method: 'POST', body: { idea_id: idea.id } })
      push({ title: '活动已创建', msg: `《${idea.name}》已进入工作台待派发`, type: 'success' })
      onGoto?.('workbench')
    } catch (e) {
      push({ title: '创建失败', msg: e.message, type: 'error' })
    }
  }

  return (
    <div className="panel-stack" style={{ gap: 26 }}>
      <div className="tabs" style={{ alignSelf: 'flex-start' }}>
        <SliderNav options={['固定类型模式', 'Agent 对话模式']} nav={modeNav} label="工作模式" />
      </div>

      {mode === 'types' ? (
        <>
          <div>
            <SectionHead idx="01" cn="策划条件" en="Condition Input"
              tail={activityTypes.length ? `已选 ${activityTypes.length} 类` : 'BY TYPES'} />
            <div className="glass" style={{ padding: '18px 20px 20px' }}>
              <div className="cond-label">活动类型 · TYPES</div>
              <TypePicker value={activityTypes} onChange={setActivityTypes} />
              <div className="panel-row--between ideas-actions" style={{ display: 'flex', marginTop: 18, alignItems: 'center' }}>
                <span className="console-hint">// 按社区默认条件生成：10 人以内 · 200 元内 · 党团活动室</span>
                <div className="row">
                  {favs.size > 0 && (
                    <Button variant="ghost" iconEnd="arrowRight" onClick={() => onGoto('workbench')}>
                      已收藏 {favs.size} 个 · 去转活动
                    </Button>
                  )}
                  {ideas.length > 0 && (
                    <Button icon="refresh" onClick={() => { setFavs(new Set()); generate() }}>换一批</Button>
                  )}
                  <Button variant="primary" icon="zap" loading={loading} onClick={generate}>
                    {loading ? 'AI 工作中…' : '生成点子'}
                  </Button>
                </div>
              </div>
            </div>
          </div>

          <div>
            <SectionHead idx="02" cn="灵感输出" en="Idea Output"
              tail={ideas.length ? `${ideas.length} 个点子` : (loading ? 'GENERATING…' : 'STANDBY')} />
            {trace.length > 0 && <TracePanel steps={trace} running={loading} />}
            {degraded && (
              <div className="alert" style={{ '--tone': 'var(--sand)', '--tone-deep': 'var(--sand-deep)', marginTop: 16 }}>
                <span className="alert__icon"><Icon name="alertTriangle" size={16} /></span>
                <div className="alert__body">
                  <div className="alert__text">当前服务商不支持联网搜索，已改用模型内置知识生成。</div>
                </div>
              </div>
            )}
            {ideas.length === 0 && !loading && (
              <div className="glass" style={{ marginTop: 16 }}>
                <div className="empty">
                  <div className="empty__art"><Icon name="zap" /></div>
                  <div className="empty__title">等待指令</div>
                  <div className="empty__text">选好活动类型，点「生成点子」，AI 将结合平台热词与往年活动给出 8 个灵感。</div>
                </div>
              </div>
            )}
            {ideas.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <CardColumns
                  items={ideas}
                  columns={3}
                  render={(it) => (
                    <IdeaCard
                      key={it.id}
                      idea={it}
                      favorited={favs.has(it.id)}
                      onFavorite={() => favorite(it.id)}
                      onConvert={() => toActivity(it)}
                    />
                  )}
                />
              </div>
            )}
          </div>
        </>
      ) : (
        <AgentMode onGoto={onGoto} />
      )}
    </div>
  )
}
