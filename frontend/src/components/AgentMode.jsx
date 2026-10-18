import { useState } from 'react'
import Icon from '../ui/Icon.jsx'
import SessionList from './SessionList'
import AgentChat from './AgentChat'
import { api } from '../api'

export default function AgentMode({ onGoto }) {
  const [current, setCurrent] = useState(null)
  const [opening, setOpening] = useState('')

  const select = async (id) => {
    const detail = await api(`/api/agent/sessions/${id}`)
    setCurrent(detail.session)
    setOpening('')
    return detail
  }

  return (
    <div className="agent-layout">
      <SessionList current={current?.id} onSelect={select}
        onCreated={(r) => { setCurrent({ id: r.id, title: '', title_locked: 0, summary: '' }); setOpening(r.opening) }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {current
          ? <AgentChat key={current.id} session={current} opening={opening}
              onTitle={(t) => setCurrent((c) => ({ ...c, title: t }))}
              onGoto={onGoto} />
          : (
            <div className="glass">
              <div className="empty">
                <div className="empty__art"><Icon name="send" /></div>
                <div className="empty__title">还没有选择会话</div>
                <div className="empty__text">点「新对话」开始，或选择已有会话继续。</div>
              </div>
            </div>
          )}
      </div>
    </div>
  )
}
