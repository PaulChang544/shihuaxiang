import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import SectionHead from '../components/SectionHead'
import Button, { IconButton } from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import Select from '../ui/Select.jsx'
import { Modal } from '../ui/Overlay.jsx'
import { Badge } from '../ui/bits.jsx'
import { useToast } from '../ui/Toast.jsx'
import useIdentity from '../hooks/useIdentity'
import { humanName } from '../names.js'
import IS_MOBILE from '../isMobile.js'
import { useClickOutside } from '../ui/hooks.js'

const MODE_OPTIONS = [
  { value: 'responses', label: 'Responses API（DeepSeek 推荐，支持联网搜索）' },
  { value: 'chat', label: 'Chat Completions（其他 OpenAI 兼容服务商）' },
]


export default function SettingsPage() {
  const [form, setForm] = useState({ api_mode: 'responses', api_base: '', api_key: '', model: '', web_search: false })
  const [testing, setTesting] = useState(false)
  const [cur, setCur] = useState({})   // GET /api/settings：密钥只回传掩码与来源，不回传原文
  const push = useToast()

  /* ---- 成员名册（站长管理） ---- */
  const { members, me, setMe, reload: reloadMembers } = useIdentity()
  const canManage = me?.role === 'admin' || me?.role === 'owner' || me?.role === 'super'
  const isSuper = me?.role === 'super'
  const isOwner = me?.role === 'owner'
  // 内置超管（系统账号）不在名单中显示
  const roster = members.filter((m) => m.role !== 'super')
  const [draft, setDraft] = useState({ name: '', contact: '', role_note: '' })
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState(null)
  const [confirming, setConfirming] = useState(null)
  const [expanded, setExpanded] = useState(null)        // 邀请码展开的成员 id
  const [invites, setInvites] = useState({})            // memberId -> [{id, code, created_at}]
  const [transferring, setTransferring] = useState(null)
  const [menuFor, setMenuFor] = useState(null)         // 手机端行内「⋯」：{member, top, right}
  const menuRef = useRef(null)
  useClickOutside(menuRef, () => setMenuFor(null), menuFor != null)
  useEffect(() => {
    if (!menuFor) return
    const close = () => setMenuFor(null)
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    // 页面滚动在内层容器发生，scroll 不冒泡 → 用捕获阶段兜住
    window.addEventListener('scroll', close, { capture: true, passive: true })
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, { capture: true })
    }
  }, [menuFor])

  const loadMembers = async () => {
    try { await reloadMembers() } catch { /* ignore */ }
  }

  const addMember = async () => {
    if (!canManage) return
    if (!draft.name.trim()) {
      push({ title: '请填写姓名', type: 'warning' })
      return
    }
    try {
      const m = await api('/api/members', {
        method: 'POST',
        body: { name: draft.name.trim(), contact: draft.contact.trim(), role_note: draft.role_note.trim() },
      })
      push({ title: `${m.name} 已加入名册`, type: 'success' })
      setDraft({ name: '', contact: '', role_note: '' })
      setAdding(false)
      reloadMembers()
    } catch (e) {
      push({ title: '添加失败', msg: e.message, type: 'error' })
    }
  }

  const saveMember = async () => {
    if (!editing || !canManage) return
    try {
      await api(`/api/members/${editing.id}`, {
        method: 'PUT',
        body: { name: editing.name.trim(), contact: editing.contact.trim(), role_note: editing.role_note.trim(), active: !!editing.active },
      })
      push({ title: '已保存', type: 'success' })
      setEditing(null)
      reloadMembers()
    } catch (e) {
      push({ title: e.message, type: 'error' })
    }
  }

  const removeMember = async () => {
    const target = confirming
    setConfirming(null)
    if (!target || !canManage) return
    try {
      await api(`/api/members/${target.id}`, { method: 'DELETE' })
      if (me && me.id === target.id) setMe(null) // 本机身份对应的成员被删则清空
      push({ title: `${target.name} 已移出名册`, type: 'success' })
      reloadMembers()
    } catch (e) {
      push({ title: '删除失败', msg: e.message, type: 'error' })
    }
  }

  /* ---- 邀请码 / 转让站长 ---- */
  const fetchInvites = useCallback(async (mid) => {
    try {
      const list = await api(`/api/members/${mid}/invites`)
      setInvites((s) => ({ ...s, [mid]: list }))
    } catch (e) {
      push({ title: '邀请码获取失败', msg: e.message, type: 'error' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.id])

  const openInvites = async (m) => {
    const id = expanded === m.id ? null : m.id
    setExpanded(id)
    if (id != null && invites[m.id] == null) await fetchInvites(m.id)
  }

  const genCode = async (m) => {
    if (!canManage) return
    try {
      const r = await api(`/api/members/${m.id}/invites`, { method: 'POST' })
      setInvites((s) => ({ ...s, [m.id]: [r, ...(s[m.id] || [])] }))
      // 明文只在生成那一刻有效：立即复制并直接提示，避免从列表翻取
      try { await navigator.clipboard?.writeText(r.code) } catch { /* 无剪贴板权限则静默 */ }
      push({
        title: `新邀请码：${r.code}（已复制）`,
        msg: '明文仅此一次可见——发送给该成员即可激活；列表中不再显示完整码，遗失由其作废后重发',
        type: 'success',
      })
    } catch (e) {
      push({ title: '生成失败', msg: e.message, type: 'error' })
    }
  }

  const delCode = async (m, iid) => {
    try {
      await api(`/api/members/invites/${iid}`, { method: 'DELETE' })
      setInvites((s) => ({ ...s, [m.id]: (s[m.id] || []).filter((c) => c.id !== iid) }))
      push({ title: '邀请码已作废', type: 'success' })
    } catch (e) {
      push({ title: e.message, type: 'error' })
    }
  }

  const doTransfer = async () => {
    const target = transferring
    setTransferring(null)
    if (!target || !canManage) return
    try {
      await api(`/api/members/${target.id}/transfer`, { method: 'POST' })
      push({
        title: me?.role === 'super' ? `${target.name} 已被指定为站长` : `站长已转让给 ${target.name}`,
        msg: me?.role === 'super' ? '原站长（若有）已自动降为管理员；你的超管身份不变' : '你现在是管理员，名册管理权保留',
        type: 'success',
      })
      await reloadMembers()
    } catch (e) {
      push({ title: '转让失败', msg: e.message, type: 'error' })
    }
  }

  /* ---- 管理员任免（QQ 群化）：成员→任命；管理员行→卸任（仅自身，或站长/超管对他人） ---- */
  const canToggleAdmin = (m) => {
    if (!canManage) return false
    if (m.role === 'member') return true
    if (m.role === 'admin') return isOwner || isSuper || m.id === me?.id
    return false
  }

  const toggleAdmin = async (m) => {
    const promoting = m.role === 'member'
    try {
      await api(`/api/members/${m.id}/${promoting ? 'appoint' : 'dismiss'}`, { method: 'POST' })
      push({
        title: promoting ? `${m.name} 已被任命为管理员` : (m.id === me?.id ? '你已卸任管理员' : `${m.name} 已卸任管理员`),
        type: 'success',
      })
      reloadMembers()
    } catch (e) {
      push({ title: promoting ? '任命失败' : '卸任失败', msg: e.message, type: 'error' })
    }
  }

  const refresh = () => api('/api/settings').then((s) => {
    setCur(s)
    // 密钥字段保持留空（占位符提示当前状态），回填掩码会被误当成新值
    setForm({
      api_base: s.api_base, model: s.model,
      api_mode: s.api_mode, web_search: s.web_search === '1',
      api_key: '',
    })
  })

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (isSuper) refresh()   // 完整 AI 配置仅超管可见，其他人不请求
    loadMembers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSave = async (e) => {
    e.preventDefault()
    await api('/api/settings', {
      method: 'PUT',
      body: { ...form, web_search: form.web_search ? '1' : '0' },
    })
    push({ title: '已保存', type: 'success' })
    refresh()
  }

  const onClear = async (flag) => {
    await api('/api/settings', {
      method: 'PUT',
      body: { ...form, web_search: form.web_search ? '1' : '0', [flag]: true },
    })
    push({ title: '已清除该 Key', type: 'success' })
    refresh()
  }

  const onTest = async () => {
    setTesting(true)
    try {
      const r = await api('/api/settings/test', { method: 'POST', body: {} })
      if (r.ok) push({ title: `连接成功：${r.message}`, type: 'success' })
      else push({ title: r.message, type: 'error' })
    } catch (e) {
      push({ title: e.message, type: 'error' })
    } finally {
      setTesting(false)
    }
  }

  const keyExtra = (source, envName) => (source === 'env'
    ? `当前由环境变量 ${envName} 覆盖，此表单保存的 Key 在移除该环境变量后才会生效`
    : '留空保持已保存的 Key 不变；服务器部署可用环境变量覆盖')

  const roleBadge = (m) => (m.role === 'owner'
    ? <Badge tone="clay">站长</Badge>
    : m.role === 'admin'
      ? <Badge tone="lilac">管理员</Badge>
      : <Badge tone={m.active ? 'sage' : 'sand'}>{m.active ? '成员' : '已停用'}</Badge>)

  return (
    <div className="settings-grid">
      {isSuper && <>
      <section>
        <SectionHead idx="01" cn="AI 接入（仅超级管理员可见）" en="Connection" />
        <form className="glass panel-stack" style={{ padding: '20px 22px' }} onSubmit={onSave}>
          <div className="field">
            <label className="field__label" htmlFor="settings-mode">调用方式 <small>必选</small></label>
            <Select
              id="settings-mode"
              options={MODE_OPTIONS}
              value={form.api_mode ?? ''}
              onChange={v => setForm(f => ({ ...f, api_mode: v }))}
            />
          </div>

          <div className="field">
            <label className="field__label" htmlFor="settings-base">API 地址 <small>必填</small></label>
            <input id="settings-base" className="input" required placeholder="https://api.deepseek.com" value={form.api_base ?? ''}
              onChange={e => setForm(f => ({ ...f, api_base: e.target.value }))} />
            <div className="field__hint">Responses API 用服务商根地址（如 https://api.deepseek.com），不用带 /v1</div>
          </div>

          <div className="field">
            <label className="field__label" htmlFor="settings-key">
              API Key
              {cur.has_api_key && <span className="key-state">已配置（{cur.api_key}）</span>}
            </label>
            <input id="settings-key" className="input" type="password" autoComplete="new-password"
              placeholder={cur.has_api_key ? '留空保持不变，输入新值即更换' : 'sk-...'} value={form.api_key ?? ''}
              onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))} />
            <div className="field__hint">{keyExtra(cur.api_key_source, 'DEEPSEEK_API_KEY')}</div>
          </div>

          <div className="field">
            <label className="field__label" htmlFor="settings-model">模型名 <small>必填</small></label>
            <input id="settings-model" className="input" required placeholder="deepseek-flash" value={form.model ?? ''}
              onChange={e => setForm(f => ({ ...f, model: e.target.value }))} />
          </div>

          <div className="field">
            <label className="field__label" htmlFor="settings-websearch">联网搜索增强（用 DeepSeek Search 检索最新热词与案例，复用上方 API Key）</label>
            <button type="button" id="settings-websearch" role="switch" aria-checked={!!form.web_search}
              className={`switch${form.web_search ? ' switch--on' : ''}`}
              onClick={() => setForm(f => ({ ...f, web_search: !f.web_search }))}>
              <span className="switch__thumb" />
            </button>
            <div className="field__hint">开启后「生成点子」先真检索再生成，Agent 对话也能联网搜热词与活动案例；按 DeepSeek 搜索计费（¥5/千次）</div>
          </div>

          <div className="panel-row" style={{ marginTop: 4 }}>
            <Button variant="primary" type="submit">保存</Button>
            <Button type="button" loading={testing} onClick={onTest}>测试连接</Button>
            {cur.has_api_key && <Button type="button" variant="ghost" className="btn--danger-text" onClick={() => onClear('clear_api_key')}>清除 API Key</Button>}
          </div>
        </form>
      </section>

      </>}

      <section>
        <SectionHead idx={isSuper ? '02' : '01'} cn="成员名册" en="Members"
          tail={roster.length ? `${roster.length} 人` : 'EMPTY'} />
        <div className="glass panel-stack" style={{ padding: '20px 22px' }}>
          {!canManage && roster.length > 0 && (
            <div className="field__hint">成员与邀请码由管理级成员（<b>{humanName(members.find((m) => m.role === 'owner')?.name || members.find((m) => m.role === 'super')?.name) || '站长'}</b>）管理。</div>
          )}
          {roster.length > 0 ? (
            <div className="wb-roster">
              {roster.map((m) => (
                <div className="wb-member" key={m.id}>
                  <div className="wb-member__row">
                    <div className="wb-member__id">
                      <span className="wb-member__name">{m.name}</span>
                      {roleBadge(m)}
                    </div>
                    <div className="wb-member__meta">
                      {m.contact && <span>{m.contact}</span>}
                      {m.role_note && <span>{m.role_note}</span>}
                      {!m.contact && !m.role_note && <span>未留联系方式</span>}
                    </div>
                    {canManage && (
                      <div className="wb-member__ops">
                        {IS_MOBILE ? (
                          <IconButton
                            size="sm"
                            label={`${m.name} 的更多操作`}
                            icon="more"
                            onClick={(e) => {
                              // 菜单锚定按钮下缘、右缘对齐（桌面磁吸对 rect 的扰动可忽略）
                              const btn = e.currentTarget.getBoundingClientRect();
                              setMenuFor({
                                member: m,
                                top: btn.bottom + 6,
                                right: document.documentElement.clientWidth - btn.right,
                              });
                            }}
                          />
                        ) : (
                          <>
                            {canToggleAdmin(m) && (
                              <IconButton size="sm"
                                label={m.role === 'member' ? `任命 ${m.name} 为管理员`
                                  : (m.id === me?.id ? '卸任我的管理员身份' : `卸任 ${m.name} 的管理员`)}
                                icon="user"
                                onClick={() => toggleAdmin(m)} />
                            )}
                            <IconButton size="sm" label={`${m.name} 的邀请码`} icon="send" onClick={() => openInvites(m)} />
                            <IconButton size="sm" label={`编辑 ${m.name}`} icon="edit" onClick={() => setEditing({ ...m })} />
                            {(isOwner || isSuper) && m.id !== me?.id && (m.role === 'member' || m.role === 'admin') && (
                              <IconButton size="sm" label={isSuper ? `指定 ${m.name} 为站长` : `转让站长给 ${m.name}`} icon="users" onClick={() => setTransferring(m)} />
                            )}
                            {m.role !== 'owner' && (
                              <IconButton size="sm" label={`删除 ${m.name}`} icon="trash" className="btn--danger-text"
                                onClick={() => setConfirming(m)} />
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                  {expanded === m.id && (
                    <div className="wb-member__invites panel-stack">
                      <div className="cond-label">邀请码 · 该成员首次进站激活用</div>
                      {(invites[m.id] || []).length === 0 && (
                        <div className="wb-card__muted">暂无邀请码，点下方「生成新邀请码」，明文仅在生成那一刻展示一次。</div>
                      )}
                      {(invites[m.id] || []).map((c) => (
                        <div key={c.id} className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                          <span className="wb-invcode" title="出于安全仅显示掩码；明文丢失请作废后重新生成">{c.code}</span>
                          <span className="wb-card__muted">{c.created_at}</span>
                          {c.fixed
                            ? <span className="wb-card__muted">固定保留</span>
                            : <Button size="sm" variant="ghost" className="btn--danger-text"
                              onClick={() => delCode(m, c.id)}>作废</Button>}
                        </div>
                      ))}
                      <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                        <Button size="sm" variant="outline" icon="plus" onClick={() => genCode(m)}>生成新邀请码</Button>
                        <span className="field__hint" style={{ textAlign: 'right' }}>
                          明文仅生成时可见；遗失请作废重发
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="empty">
              <div className="empty__art"><Icon name="users" /></div>
              <div className="empty__title">名册为空</div>
              <div className="empty__text">
                {canManage ? '先把团队成员加进来，活动派发时才能选择负责人。' : '等站长加入成员并给你邀请码后即可激活使用。'}
              </div>
            </div>
          )}

          {canManage && (
            <button type="button" className="wb-addmember" onClick={() => setAdding(true)}>
              <Icon name="plus" size={16} />
              添加新成员
            </button>
          )}

          <div className="field__hint">
            成员信息经邀请码激活后保存在本机；失效时重新输入邀请码即可。
          </div>
        </div>
      </section>

      <Modal
        open={!!transferring}
        onClose={() => setTransferring(null)}
        title={isSuper ? '指定站长' : '转让站长'}
        width={420}
        footer={(
          <>
            <Button onClick={() => setTransferring(null)}>取消</Button>
            <Button variant="danger" icon="users" onClick={doTransfer}>{isSuper ? '指定' : '转让'}</Button>
          </>
        )}
      >
        <p style={{ marginTop: 4 }}>
          {isSuper
            ? <>确定指定 <b>{transferring?.name}</b> 为站长？原站长（若有）将自动降为管理员。你的超级管理员身份不受影响。</>
            : <>确定将站长转让给 <b>{transferring?.name}</b>？转让后你将自动降为<b>管理员</b>（名册管理权保留），全站始终只有一位站长。</>}
        </p>
      </Modal>

      {menuFor && createPortal(
        <div
          className="menu member-menu"
          role="menu"
          ref={menuRef}
          style={{ position: 'fixed', top: menuFor.top, left: 'auto', right: menuFor.right }}
        >
          <button role="menuitem" className="menu__item"
            onClick={() => { setExpanded(menuFor.member.id); fetchInvites(menuFor.member.id); setMenuFor(null) }}>
            邀请码管理
          </button>
          <button role="menuitem" className="menu__item"
            onClick={() => { setEditing({ ...menuFor.member }); setMenuFor(null) }}>
            编辑成员
          </button>
          {canToggleAdmin(menuFor.member) && (
            <button role="menuitem" className="menu__item"
              onClick={() => { toggleAdmin(menuFor.member); setMenuFor(null) }}>
              {menuFor.member.role === 'member' ? '任命为管理员' : '卸任管理员'}
            </button>
          )}
          {(isOwner || isSuper) && menuFor.member.id !== me?.id
            && (menuFor.member.role === 'member' || menuFor.member.role === 'admin') && (
            <button role="menuitem" className="menu__item"
              onClick={() => { setTransferring(menuFor.member); setMenuFor(null) }}>
              {isSuper ? '指定为站长' : '转让站长'}
            </button>
          )}
          {menuFor.member.role !== 'owner' && (
            <button role="menuitem" className="menu__item member-menu--danger"
              onClick={() => { setConfirming(menuFor.member); setMenuFor(null) }}>
              删除成员
            </button>
          )}
        </div>,
        document.body,
      )}

      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title="添加新成员"
        width={460}
        footer={(
          <>
            <Button onClick={() => setAdding(false)}>取消</Button>
            <Button variant="primary" icon="plus" onClick={addMember}>加入名册</Button>
          </>
        )}
      >
        <div className="panel-stack">
          <div className="field">
            <label className="field__label" htmlFor="member-add-name">姓名</label>
            <input id="member-add-name" className="input" placeholder="必填" value={draft.name}
              onChange={(e) => setDraft((f) => ({ ...f, name: e.target.value }))} />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="member-add-contact">联系方式</label>
            <input id="member-add-contact" className="input" value={draft.contact}
              onChange={(e) => setDraft((f) => ({ ...f, contact: e.target.value }))} />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="member-add-role">部门 / 职责</label>
            <input id="member-add-role" className="input" value={draft.role_note}
              onChange={(e) => setDraft((f) => ({ ...f, role_note: e.target.value }))} />
          </div>
        </div>
      </Modal>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title="编辑成员"
        width={460}
        footer={(
          <>
            <Button onClick={() => setEditing(null)}>取消</Button>
            <Button variant="primary" onClick={saveMember}>保存</Button>
          </>
        )}
      >
        {editing && (
          <div className="panel-stack">
            <div className="field">
              <label className="field__label" htmlFor="member-edit-name">姓名</label>
              <input id="member-edit-name" className="input" value={editing.name}
                onChange={(e) => setEditing((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="member-edit-contact">联系方式</label>
              <input id="member-edit-contact" className="input" value={editing.contact}
                onChange={(e) => setEditing((f) => ({ ...f, contact: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="member-edit-role">部门 / 职责</label>
              <input id="member-edit-role" className="input" value={editing.role_note}
                onChange={(e) => setEditing((f) => ({ ...f, role_note: e.target.value }))} />
            </div>
            <div className="field">
              <label className="field__label">在册</label>
              <button
                type="button"
                className={`switch${editing.active ? ' switch--on' : ''}`}
                role="switch"
                aria-checked={!!editing.active}
                onClick={(e) => { e.preventDefault(); setEditing((f) => ({ ...f, active: !f.active })) }}
              >
                <span className="switch__thumb" />
              </button>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={!!confirming}
        onClose={() => setConfirming(null)}
        title="删除成员"
        width={420}
        footer={(
          <>
            <Button onClick={() => setConfirming(null)}>取消</Button>
            <Button variant="danger" icon="trash" onClick={removeMember}>删除</Button>
          </>
        )}
      >
        <p style={{ marginTop: 4 }}>确定将该成员移出名册？其名下被指派的活动将回到「未指派负责人」。</p>
        {confirming && <p className="console-hint" style={{ marginTop: 6 }}>{confirming.name}</p>}
      </Modal>
    </div>
  )
}
