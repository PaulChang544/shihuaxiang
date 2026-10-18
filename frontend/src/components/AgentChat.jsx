import { useEffect, useRef, useState } from 'react'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import { useToast } from '../ui/Toast.jsx'
import { api, streamSSE } from '../api'

const TOOL_LABELS = {
  wiki_index_tool: '历史活动索引',
  wiki_search_tool: '检索历史活动',
  wiki_read_tool: '阅读推文原文',
  web_search_tool: '联网搜索',
  update_brief_tool: '记录需求',
}

const TOOL_ICONS = {
  wiki_index_tool: 'file',
  wiki_search_tool: 'search',
  wiki_read_tool: 'eye',
  web_search_tool: 'refresh',
  update_brief_tool: 'edit',
}

const inputPreview = (s) => {
  const text = (s || '').replace(/\s+/g, ' ').trim()
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

const prettyJson = (s) => {
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s || '' }
}

export default function AgentChat({ session, opening, onTitle, onGoto }) {
  const [items, setItems] = useState([])          // {kind:'user'|'agent'|'tool'|'step', ...}
  const [brief, setBrief] = useState({})
  const [running, setRunning] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirmIdea, setConfirmIdea] = useState(null)
  const bottomRef = useRef(null)
  const push = useToast()

  useEffect(() => {
    api(`/api/agent/sessions/${session.id}`).then((d) => {
      setBrief(d.brief || {})  // 还原已持久化的需求卡（已确认信息）
      const restored = d.messages.map((m) => {
        if (m.role === 'user') return { kind: 'user', text: m.content }
        if (m.role === 'tool') {
          // 落库的工具调用记录（JSON：tool/call_id/input/output）还原成工具卡片
          try {
            const t = JSON.parse(m.content)
            return { kind: 'tool', tool: t.tool, call_id: t.call_id || '', running: false, input: t.input || '', output: t.output || '' }
          } catch { /* 内容异常时按普通消息兜底 */ }
        }
        return { kind: 'agent', text: m.content }
      })
      // 开场白已在后端落库并随历史返回，避免重复追加
      const hasOpening = restored.some((m) => m.kind === 'agent' && m.text === opening)
      setItems(opening && !hasOpening ? [...restored, { kind: 'agent', text: opening }] : restored)
    })
  }, [session.id])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [items, running])

  const consume = (path, body) => streamSSE(path, body, (event, d) => {
    if (event === 'token') {
      setItems((prev) => {
        const last = prev[prev.length - 1]
        if (last && last.kind === 'agent' && last.streaming) {
          return [...prev.slice(0, -1), { ...last, text: last.text + d.text }]
        }
        return [...prev, { kind: 'agent', text: d.text, streaming: true }]
      })
    } else if (event === 'step') {
      setItems((prev) => [...prev, { kind: 'step', title: d.title || d.detail || '', status: d.status }])
    } else if (event === 'tool') {
      // 同一次调用的 start（流式参数逐步补全）与 done 合并进一张卡片：
      // 优先按 call_id 匹配，桩路径无 id 时退回同名最后一张进行中的卡片
      setItems((prev) => {
        const at = [...prev].reverse().findIndex((it) => it.kind === 'tool' && it.running &&
          (d.call_id ? it.call_id === d.call_id : it.tool === d.tool))
        if (at === -1) {
          return [...prev, {
            kind: 'tool', tool: d.tool, call_id: d.call_id || '',
            running: d.status !== 'done',
            input: d.input || '', output: d.status === 'done' ? (d.detail || '') : '',
          }]
        }
        // 注意：at 是反转数组的下标，取合并基准与回写位置都必须换算回正向下标 i
        const i = prev.length - 1 - at
        const merged = { ...prev[i], tool: d.tool || prev[i].tool, input: d.input || prev[i].input }
        if (d.status === 'done') { merged.running = false; merged.output = d.detail || '' }
        return [...prev.slice(0, i), merged, ...prev.slice(i + 1)]
      })
    } else if (event === 'brief') {
      setBrief(d.fields || {})
    } else if (event === 'title') {
      onTitle?.(d.title)
    } else if (event === 'confirm') {
      setConfirmIdea(d.idea || {})
    } else if (event === 'final') {
      setConfirmIdea(null)
      push({ title: '点子已入库', msg: `《${d.title}》已存入点子库，可去工作台转为活动`, type: 'success' })
      setItems((prev) => [...prev, { kind: 'agent', text: `点子《${d.title}》已存入点子库，可在「灵感」页点子库查看，或去工作台转为活动。` }])
    } else if (event === 'error') {
      push({ title: '运行出错', msg: d.message, type: 'error' })
    }
  })

  const send = async () => {
    const text = draft.trim()
    if (!text || running) return
    setDraft('')
    setConfirmIdea(null)
    setItems((prev) => [...prev, { kind: 'user', text }])
    setRunning(true)
    try {
      await consume(`/api/agent/sessions/${session.id}/stream`, { message: text })
    } catch (e) {
      push({ title: '发送失败', msg: e.message, type: 'error' })
    } finally {
      setRunning(false)
    }
  }

  const generateNow = async () => {
    setRunning(true)
    setConfirmIdea(null)
    try {
      await consume(`/api/agent/sessions/${session.id}/generate`, {})
    } catch (e) {
      push({ title: '生成失败', msg: e.message, type: 'error' })
    } finally {
      setRunning(false)
    }
  }

  const briefEntries = Object.entries(brief)

  return (
    <div className="glass chat-panel">
      <div className="chat-head">
        <span className="chat-head__title">{session.title || '新对话'}</span>
        <Button size="sm" variant="outline" icon="zap" onClick={generateNow} disabled={running}>
          汇总成点子
        </Button>
      </div>
      {briefEntries.length > 0 && (
        <div className="brief-card">
          <span className="cond-label" style={{ margin: 0 }}>已确认信息</span>
          {briefEntries.map(([k, v]) => (
            <span key={k} className="brief-chip">{k}：{Array.isArray(v) ? v.join('、') : String(v)}</span>
          ))}
        </div>
      )}
      <div className="chat-flow">
        {items.map((it, i) => it.kind === 'user' || it.kind === 'agent' ? (
          <div key={i} className={`chat-bubble chat-bubble--${it.kind}`}>{it.text}</div>
        ) : it.kind === 'tool' ? (
          <details key={i} className={`tool-card${it.running ? ' tool-card--running' : ''}`}>
            <summary>
              <span className="tool-name">
                <Icon name={TOOL_ICONS[it.tool] || 'settings'} size={13} /> {TOOL_LABELS[it.tool] || it.tool}
              </span>
              <span className="tool-preview">{inputPreview(it.input)}</span>
              <span className="tool-state">{it.running ? '调用中…' : '完成'}</span>
            </summary>
            <div className="tool-body">
              <div className="tool-sec">输入</div>
              <pre>{prettyJson(it.input) || '（无参数）'}</pre>
              <div className="tool-sec">输出</div>
              <pre>{it.running ? '（等待返回…）' : (it.output || '（空）')}</pre>
            </div>
          </details>
        ) : (
          <div key={i} className="chat-step">{it.status === 'start' ? '▶' : '✓'} {it.title}</div>
        ))}
        {confirmIdea && (
          <div className="confirm-card">
            <div className="confirm-card__title">信息齐了，是否存入点子库？</div>
            <div className="console-hint" style={{ marginTop: 6 }}>
              {confirmIdea.name}｜{confirmIdea.activity_type}｜{confirmIdea.star_rating} 星
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <Button size="sm" variant="primary" onClick={generateNow}>存入点子库</Button>
              <Button size="sm" onClick={() => setConfirmIdea(null)}>继续聊</Button>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="chat-input">
        <textarea
          className="textarea"
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="描述你的想法，或回答 agent 的问题…（Enter 发送，Shift+Enter 换行）"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <Button variant="primary" loading={running} onClick={send} icon="send">发送</Button>
      </div>
    </div>
  )
}
