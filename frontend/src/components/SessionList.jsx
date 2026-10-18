import { useEffect, useState } from 'react'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import { Modal } from '../ui/Overlay.jsx'
import { useToast } from '../ui/Toast.jsx'
import { api } from '../api'
import IS_MOBILE from '../isMobile.js'

export default function SessionList({ current, onSelect, onCreated }) {
  const [items, setItems] = useState([])
  const [archived, setArchived] = useState([])
  const [showArchived, setShowArchived] = useState(false)
  const [renaming, setRenaming] = useState(null)
  const [name, setName] = useState('')
  const [confirming, setConfirming] = useState(null)
  const [menuFor, setMenuFor] = useState(null)
  const push = useToast()

  const load = async () => {
    setItems(await api('/api/agent/sessions?status=active'))
    setArchived(await api('/api/agent/sessions?status=archived'))
  }
  useEffect(() => { load() }, [])

  const create = async () => {
    const r = await api('/api/agent/sessions', { method: 'POST' })
    await load()
    onCreated?.(r)
  }
  const archive = async (id, status) => {
    await api(`/api/agent/sessions/${id}`, { method: 'PATCH', body: { status } })
    load()
  }
  const remove = async (id) => {
    await api(`/api/agent/sessions/${id}`, { method: 'DELETE' })
    push({ title: '已删除', type: 'success' })
    setConfirming(null)
    load()
  }
  const rename = async () => {
    await api(`/api/agent/sessions/${renaming.id}`, { method: 'PATCH', body: { title: name } })
    setRenaming(null)
    load()
  }

  const item = (s, isArchived) => (
    <div
      key={s.id}
      className={`session-item${current === s.id ? ' session-item--on' : ''}`}
      onClick={() => onSelect?.(s.id)}
    >
      <div className="session-title">{s.title || '新对话'}</div>
      <div className="session-meta">{s.summary || s.updated_at?.slice(5, 16)}</div>
      <div className="session-ops" onClick={(e) => e.stopPropagation()}>
        <button className="session-op" onClick={() => { setRenaming(s); setName(s.title) }}>改名</button>
        {isArchived
          ? <button className="session-op" onClick={() => archive(s.id, 'active')}>恢复</button>
          : <button className="session-op" onClick={() => archive(s.id, 'archived')}>归档</button>}
        <button className="session-op session-op--danger" onClick={() => setConfirming(s)}>删除</button>
      </div>
    </div>
  )

  /* 手机端：会话管理收进「⋯」弹窗，列表本体变为顶部横滑胶囊 */
  const chip = (s, isArchived) => (
    <div
      key={s.id}
      className={`session-chip${current === s.id ? ' session-chip--on' : ''}${isArchived ? ' session-chip--arch' : ''}`}
    >
      <button className="session-chip__main" onClick={() => onSelect?.(s.id)}>
        {s.title || '新对话'}
      </button>
      <button className="session-chip__more" aria-label={`${s.title || '新对话'} 的会话操作`}
        onClick={(e) => { e.stopPropagation(); setMenuFor(s.id) }}>
        ⋯
      </button>
    </div>
  )

  const menuSession = menuFor != null ? [...items, ...archived].find((s) => s.id === menuFor) : null

  return (
    <div className={IS_MOBILE ? 'session-chips' : 'glass session-panel'}>
      {IS_MOBILE ? (
        <>
          <button className="session-chip session-chip--new" aria-label="新对话" onClick={create}>
            <Icon name="plus" size={15} />
          </button>
          {items.map((s) => chip(s, false))}
          {archived.map((s) => chip(s, true))}
        </>
      ) : (
        <>
          <Button variant="primary" block icon="plus" onClick={create}>新对话</Button>
          <div className="session-list">{items.map((s) => item(s, false))}</div>
          <button className="session-archived-toggle" onClick={() => setShowArchived(!showArchived)}>
            已归档（{archived.length}）
          </button>
          {showArchived && <div className="session-list">{archived.map((s) => item(s, true))}</div>}
        </>
      )}

      <Modal
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title="重命名会话"
        footer={(
          <div className="row">
            <Button onClick={() => setRenaming(null)}>取消</Button>
            <Button variant="primary" onClick={rename}>保存</Button>
          </div>
        )}
      >
        <input className="input" value={name} maxLength={32} onChange={(e) => setName(e.target.value)} />
      </Modal>

      <Modal
        open={!!confirming}
        onClose={() => setConfirming(null)}
        title="删除会话"
        footer={(
          <div className="row">
            <Button onClick={() => setConfirming(null)}>取消</Button>
            <Button variant="danger" icon="trash" onClick={() => remove(confirming.id)}>删除</Button>
          </div>
        )}
      >
        <p className="panel-row" style={{ margin: 0 }}>
          确定删除「{confirming?.title || '新对话'}」？该会话的对话记录与已确认的需求信息会被清除，已存入点子库的点子不受影响。
        </p>
      </Modal>

      {IS_MOBILE && (
        <Modal
          open={!!menuFor}
          onClose={() => setMenuFor(null)}
          title={menuSession?.title || '会话操作'}
          width={360}
          footer={<Button onClick={() => setMenuFor(null)}>关闭</Button>}
        >
          {menuSession && (
            <div className="panel-stack">
              <div className="panel-row" style={{ gap: 8, flexWrap: 'wrap' }}>
                <Button size="sm" onClick={() => { setRenaming(menuSession); setName(menuSession.title); setMenuFor(null) }}>改名</Button>
                <Button size="sm" onClick={() => { archive(menuSession.id, menuSession.status === 'archived' ? 'active' : 'archived'); setMenuFor(null) }}>
                  {menuSession.status === 'archived' ? '恢复' : '归档'}
                </Button>
                <Button size="sm" variant="danger" icon="trash" onClick={() => { remove(menuSession.id); setMenuFor(null) }}>删除</Button>
              </div>
              <div className="field__hint">改名 / 归档（恢复）/ 删除当前会话</div>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}
