import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../api'

const KEY = 'mrt-identity'
const Ctx = createContext(null)

/* 会话制身份（站点级共享）：
   - 激活：POST /api/members/claim {code}，服务端签发 HttpOnly Cookie；
     邀请码只在这次请求里出现，不再存本机
   - 刷新/重启：GET /api/auth/me 凭 Cookie 恢复身份（邀请码已不在前端）
   - 退出：POST /api/auth/logout 吊销会话
   - 本地只缓存非敏感的成员展示资料（id/name/role），供首屏秒开，随后以服务端为准 */
export function IdentityProvider({ children }) {
  const [members, setMembers] = useState([])
  const [me, setMeState] = useState(null)
  const [activated, setActivated] = useState(false)

  /* 身份落定统一走这里：广播事件供站点级监听方（如 AI 状态灯）在登录/换人/退出后重查 */
  const applyMe = useCallback((m) => {
    setMeState(m)
    try {
      window.dispatchEvent(new CustomEvent('shx-identity-changed', { detail: { id: m?.id ?? null } }))
    } catch { /* ignore */ }
  }, [])

  const readCached = () => {
    try {
      const o = JSON.parse(localStorage.getItem(KEY) || 'null')
      return o && o.id != null ? o : null
    } catch { return null }
  }

  const load = useCallback(async () => {
    const cached = readCached()
    let current = null
    // 旧版本机记录含邀请码：一次性换服务端会话（无感迁移），随后立即清除本机凭据
    if (cached?.code) {
      try {
        const r = await api('/api/members/whoami', { method: 'POST', body: { code: cached.code } })
        current = r.member
      } catch { current = null }
      try {
        const { code: _legacy, ...rest } = cached
        void _legacy
        localStorage.setItem(KEY, JSON.stringify(rest))
      } catch { /* ignore */ }
    }
    if (!current) {
      try {
        const r = await api('/api/auth/me')
        current = r.member
        try { localStorage.setItem(KEY, JSON.stringify(current)) } catch { /* ignore */ }
      } catch {
        current = null
        try { localStorage.removeItem(KEY) } catch { /* ignore */ }
      }
    }
    try {
      const list = current ? await api('/api/members') : []
      setMembers(list)
    } catch {
      setMembers([])
    }
    applyMe(current)
    setActivated(!!current)
  }, [applyMe])

  useEffect(() => { load() }, [load])

  /* 任一接口返回 401（会话过期/被吊销）→ 回到激活页 */
  useEffect(() => {
    const onExpired = () => {
      applyMe(null)
      setActivated(false)
      setMembers([])
      try { localStorage.removeItem(KEY) } catch { /* ignore */ }
    }
    window.addEventListener('shx-auth-expired', onExpired)
    return () => window.removeEventListener('shx-auth-expired', onExpired)
  }, [applyMe])

  /* 输入邀请码激活（首次进站 / 换人使用） */
  const activate = useCallback(async (code) => {
    const trimmed = String(code || '').trim().toUpperCase()
    if (!trimmed) return { ok: false, error: '请输入邀请码' }
    try {
      const r = await api('/api/members/claim', { method: 'POST', body: { code: trimmed } })
      const m = r.member
      try { localStorage.setItem(KEY, JSON.stringify(m)) } catch { /* ignore */ }
      applyMe(m)
      setActivated(true)
      try { setMembers(await api('/api/members')) } catch { setMembers([]) }
      return { ok: true }
    } catch (e) {
      return { ok: false, error: e.message }
    }
  }, [applyMe])

  /* 退出登录：吊销服务端会话 */
  const logout = useCallback(async () => {
    try { await api('/api/auth/logout', { method: 'POST' }) } catch { /* ignore */ }
    try { localStorage.removeItem(KEY) } catch { /* ignore */ }
    applyMe(null)
    setActivated(false)
    setMembers([])
  }, [applyMe])

  /* 兼容旧接口：外部需要主动刷新成员资料时调用 */
  const setMe = useCallback((m) => {
    if (m) {
      try { localStorage.setItem(KEY, JSON.stringify(m)) } catch { /* ignore */ }
      applyMe(m)
      setActivated(true)
    } else {
      logout()
    }
  }, [applyMe, logout])

  const value = useMemo(
    () => ({ me, members, activated, activate, setMe, logout, reload: load }),
    [me, members, activated, activate, setMe, logout, load],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export default function useIdentity() {
  return useContext(Ctx)
}
