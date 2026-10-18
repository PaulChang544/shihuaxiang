import { useState } from 'react'
import Icon from '../ui/Icon.jsx'

export default function TracePanel({ steps, running }) {
  const [open, setOpen] = useState(false)
  const [userCollapsed, setUserCollapsed] = useState(() => new Set())

  const toggle = (key) => {
    setUserCollapsed((prev) => {
      const n = new Set(prev)
      if (n.has(key)) n.delete(key)
      else n.add(key)
      return n
    })
  }

  const doneCount = steps.filter((s) => s.status === 'done').length
  const showBody = running || open

  return (
    <div className="glass" style={{ padding: '14px 18px' }}>
      <div className="panel-row--between" style={{ display: 'flex', alignItems: 'center' }}>
        <span className="cond-label" style={{ margin: 0 }}>
          AI 工作轨迹 · TRACE（{doneCount}/{steps.length}）
        </span>
        {running ? (
          <span className="console-hint">RUNNING…</span>
        ) : (
          <button className="trace-caret" onClick={() => setOpen(!open)}>
            {open ? '收起轨迹' : `查看轨迹（${steps.length} 步）`}
          </button>
        )}
      </div>
      {showBody && steps.map((t) => {
        const output = (t.output || '').trim()
        const expanded = !userCollapsed.has(t.key)
        return (
          <div key={t.key}>
            <div className="trace-row">
              <span className={`trace-dot trace-dot--${t.status || 'start'}`} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div>
                  <span className="trace-title">{t.title}</span>
                  {t.detail && <span className="trace-detail">{t.detail}</span>}
                </div>
                {t.brief && <div className="trace-brief">{t.brief}</div>}
              </div>
              {output && (
                <button className="trace-caret" onClick={() => toggle(t.key)}>
                  {expanded ? '收起输出' : '展开输出'}
                </button>
              )}
            </div>
            {output && expanded && (
              <pre
                className="trace-output"
                ref={(el) => { if (el) el.scrollTop = el.scrollHeight }}
              >
                {t.output}
              </pre>
            )}
          </div>
        )
      })}
      {running && steps.length === 0 && (
        <div className="panel-row" style={{ marginTop: 8 }}>
          <span className="console-hint"><Icon name="refresh" size={13} /> 正在启动…</span>
        </div>
      )}
    </div>
  )
}
