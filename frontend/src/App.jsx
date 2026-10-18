import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from './ui/Icon.jsx'
import { ToastProvider, useToast } from './ui/Toast.jsx'
import { SliderNav, useSliderNav } from './ui/SliderTabs.jsx'
import WorkbenchPage from './pages/WorkbenchPage'
import IdeasPage from './pages/IdeasPage'
import MaterialsPage from './pages/MaterialsPage'
import SettingsPage from './pages/SettingsPage'
import { api } from './api'
import { readJobs, JOB_KEY, jobDoneText, jobFailText } from './jobs.js'
import IS_MOBILE from './isMobile.js'
import useIdentity, { IdentityProvider } from './hooks/useIdentity.jsx'
import { ActivationScreen } from './components/Activation.jsx'
import './styles/global.css'

const STORE_KEY = 'mrt-theme'
const FX_KEY = 'mrt-fx'

const TABS = [
  { key: 'workbench', label: '工作台', icon: 'home' },
  { key: 'ideas', label: '灵感', icon: 'zap' },
  { key: 'materials', label: '素材库', icon: 'image' },
  { key: 'settings', label: '名单', icon: 'users' },
]

function getInitialTheme() {
  try {
    const saved = localStorage.getItem(STORE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* ignore */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function isTouchDevice() {
  return (
    window.matchMedia?.('(pointer: coarse)').matches ||
    window.matchMedia?.('(hover: none)').matches
  )
}

function getInitialFx() {
  try {
    const saved = localStorage.getItem(FX_KEY)
    if (saved === 'on' || saved === 'off') return saved === 'on'
  } catch { /* ignore */ }
  return !isTouchDevice()
}

/* 首次进站 / 本机身份失效 → 以激活卡整页替换应用；激活完成后加载应用本体 */
function IdentityGate({ children }) {
  const { activated } = useIdentity()
  if (activated) return children
  return (
    <div className="page" data-mobile={IS_MOBILE || undefined}>
      <div className="container">
        <ActivationScreen />
      </div>
    </div>
  )
}

/* 服务端长任务（AI 识别/提取、Word 构建）统一在此全站轮询：
   用户切去其它页面、关掉页面再回来都能收到完成通知；结果通过事件通知对应页面刷新。 */
function JobWatcher() {
  const push = useToast()
  useEffect(() => {
    let stopped = false
    const tick = async () => {
      if (stopped) return
      const jobs = readJobs()
      if (!jobs.length) return
      const remain = []
      for (const j of jobs) {
        try {
          const st = await api(`/api/jobs/${j.job_id}`)
          if (st.status === 'done' || st.status === 'failed') {
            const detail = { ...j, status: st.status, result: st.result || {}, error: st.error }
            window.dispatchEvent(new CustomEvent('shx-job-done', { detail }))
            if (st.status === 'done') {
              push({ title: jobDoneText(detail), type: 'success', duration: 7000 })
              if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
                try { new Notification(jobDoneText(detail)) } catch { /* ignore */ }
              }
            } else {
              push({ title: jobFailText(detail), msg: st.error || '请重试', type: 'error', duration: 7000 })
              if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
                try { new Notification(jobFailText(detail), { body: st.error || '' }) } catch { /* ignore */ }
              }
            }
          } else {
            remain.push(j)
          }
        } catch {
          remain.push(j) // 网络抖动：下一轮再试
        }
      }
      if (remain.length !== jobs.length) {
        try { localStorage.setItem(JOB_KEY, JSON.stringify(remain)) } catch { /* ignore */ }
      }
    }
    const id = setInterval(tick, 3000)
    tick()
    return () => { stopped = true; clearInterval(id) }
  }, [push])
  return null
}

export default function App() {
  const [tab, setTab] = useState('workbench')
  const [theme, setTheme] = useState(getInitialTheme)
  /* 手机端强制关闭动效（触屏无 hover，磁吸/辉光无意义），不读写本地偏好以免影响 PC 设置 */
  const [fxOn, setFxOn] = useState(() => (IS_MOBILE ? false : getInitialFx()))
  const [aiReady, setAiReady] = useState(null)
  const fxRef = useRef(fxOn)
  const dockNav = useSliderNav(TABS.length)

  /* 底部坞拖动/点击（settled 变化）→ 切页 */
  useEffect(() => {
    if (!IS_MOBILE) return
    const t = TABS[dockNav.settled]
    if (t) setTab(t.key)
  }, [dockNav.settled])

  /* 程序化切页（页内跳转、状态灯点击）→ 底部坞滑块跟过去 */
  useEffect(() => {
    if (!IS_MOBILE) return
    const i = TABS.findIndex((t) => t.key === tab)
    if (i >= 0 && i !== dockNav.settled) dockNav.select(i)
  }, [tab])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem(STORE_KEY, theme) } catch { /* ignore */ }
  }, [theme])

  useEffect(() => {
    fxRef.current = fxOn
    document.documentElement.dataset.fx = fxOn ? 'on' : 'off'
    if (IS_MOBILE) return // 手机端不改写动效偏好
    try { localStorage.setItem(FX_KEY, fxOn ? 'on' : 'off') } catch { /* ignore */ }
  }, [fxOn])

  useEffect(() => {
    api('/api/settings/status')
      .then((s) => setAiReady(Boolean(s.ai_ready)))
      .catch(() => setAiReady(false))
  }, [tab])

  /* 手机端：文本框聚焦即软键盘弹出，收起底部导航坞避免遮挡输入区 */
  useEffect(() => {
    if (!IS_MOBILE) return
    const root = document.documentElement
    const onFocusIn = (e) => {
      const t = e.target
      if (t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
        root.classList.add('kb-open')
      }
    }
    const onFocusOut = () => root.classList.remove('kb-open')
    document.addEventListener('focusin', onFocusIn)
    document.addEventListener('focusout', onFocusOut)
    return () => {
      document.removeEventListener('focusin', onFocusIn)
      document.removeEventListener('focusout', onFocusOut)
      root.classList.remove('kb-open')
    }
  }, [])

  /* 鼠标聚光灯：光标坐标写入页面变量 */
  useEffect(() => {
    const root = document.documentElement
    let raf = 0
    let tx = window.innerWidth / 2
    let ty = window.innerHeight * 0.4
    const onMove = (e) => {
      tx = e.clientX
      ty = e.clientY
      if (!raf) {
        raf = requestAnimationFrame(() => {
          root.style.setProperty('--mx', `${tx}px`)
          root.style.setProperty('--my', `${ty}px`)
          raf = 0
        })
      }
    }
    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  /* 卡片辉光感应：按光标与各玻璃卡的距离写入亮度与位置 */
  useEffect(() => {
    let raf = 0
    let mx = -9999
    let my = -9999
    const PROXIMITY = 240
    const update = () => {
      raf = 0
      document.querySelectorAll('.glass').forEach((card) => {
        const r = card.getBoundingClientRect()
        const dx = Math.max(r.left - mx, 0, mx - r.right)
        const dy = Math.max(r.top - my, 0, my - r.bottom)
        const dist = Math.hypot(dx, dy)
        const t = Math.max(0, 1 - dist / PROXIMITY)
        card.style.setProperty('--px', `${(((mx - r.left) / r.width) * 100).toFixed(2)}%`)
        card.style.setProperty('--py', `${(((my - r.top) / r.height) * 100).toFixed(2)}%`)
        card.style.setProperty('--glow-o', (t * t * (3 - 2 * t)).toFixed(3))
      })
    }
    const schedule = () => {
      if (!fxRef.current) return
      if (!raf) raf = requestAnimationFrame(update)
    }
    const onMove = (e) => {
      mx = e.clientX
      my = e.clientY
      schedule()
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('scroll', schedule, { passive: true })
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('scroll', schedule)
    }
  }, [])

  /* 磁吸按钮：光标靠近时轻微吸向光标，离开后弹簧回位 */
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const state = new WeakMap()
    let raf = 0
    let mx = -9999
    let my = -9999
    const step = () => {
      raf = 0
      let busy = false
      document.querySelectorAll('.btn, .pager__btn').forEach((el) => {
        let st = state.get(el)
        if (!st) {
          st = { x: 0, y: 0, vx: 0, vy: 0 }
          state.set(el, st)
        }
        const r = el.getBoundingClientRect()
        const cx = r.left + r.width / 2 - st.x
        const cy = r.top + r.height / 2 - st.y
        const dx = mx - cx
        const dy = my - cy
        const dist = Math.hypot(dx, dy) || 1
        const ox = Math.max(0, Math.abs(dx) - r.width / 2)
        const oy = Math.max(0, Math.abs(dy) - r.height / 2)
        const dEdge = Math.hypot(ox, oy)
        const t = Math.max(0, 1 - dEdge / 36)
        const fall = t * t * (3 - 2 * t)
        const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true'
        const mag = disabled || !fxRef.current ? 0 : Math.min(8, dist * 0.16) * fall
        const tx = (dx / dist) * mag
        const ty = (dy / dist) * mag
        /* 手感调校：弹簧 0.11/0.74 比模板（0.16/0.8）从容。旧项目因 .btn 基础样式
           挂着 transform 0.3s 过渡，磁吸跟手被"拖"出黏滞感；新版按主题规范
           去掉该过渡后位移直写生效，用户反馈偏快，故仅放缓弹簧参数。 */
        st.vx = (st.vx + (tx - st.x) * 0.11) * 0.74
        st.vy = (st.vy + (ty - st.y) * 0.11) * 0.74
        st.x += st.vx
        st.y += st.vy
        if (Math.abs(st.x) < 0.05 && Math.abs(st.vx) < 0.05 && Math.abs(tx) < 0.05) {
          st.x = 0
          st.vx = 0
        }
        if (Math.abs(st.y) < 0.05 && Math.abs(st.vy) < 0.05 && Math.abs(ty) < 0.05) {
          st.y = 0
          st.vy = 0
        }
        if (st.x || st.y || st.vx || st.vy || tx || ty) busy = true
        el.style.setProperty('--magx', `${st.x.toFixed(2)}px`)
        el.style.setProperty('--magy', `${st.y.toFixed(2)}px`)
      })
      if (busy) raf = requestAnimationFrame(step)
    }
    const onMove = (e) => {
      mx = e.clientX
      my = e.clientY
      if (fxRef.current && !raf) raf = requestAnimationFrame(step)
    }
    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
    }
  }, [])

  const toggleTheme = useCallback(() => setTheme((t) => (t === 'light' ? 'dark' : 'light')), [])
  const dark = theme === 'dark'

  return (
    <IdentityProvider>
      <ToastProvider>
        <JobWatcher />
        <div className="bg-blobs" aria-hidden="true">
        <span className="blob blob--1" />
        <span className="blob blob--2" />
        <span className="blob blob--3" />
        <span className="blob blob--4" />
      </div>
      <div className="spotlight" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <IdentityGate>
      <div className="page" data-mobile={IS_MOBILE || undefined}>
        <div className="container">
          <nav className="navbar glass glass--strong" aria-label="站点导航">
            <div className="navbar__inner">
              <span className="navbar__brand">
                <span className="navbar__dot" />
                十号社区 · 活动工作台
              </span>
              {!IS_MOBILE && (
                <div className="navbar__links" role="tablist" aria-label="功能分区">
                  {TABS.map((t) => (
                    <button
                      key={t.key}
                      role="tab"
                      aria-selected={tab === t.key}
                      className={`navbar__link navbar__link--btn ${tab === t.key ? 'navbar__link--on' : ''}`}
                      onClick={() => setTab(t.key)}
                    >
                      <Icon name={t.icon} size={14} />
                      {t.label}
                    </button>
                  ))}
                </div>
              )}
              <div className="navbar__actions">
                <button
                  className={`status-chip${aiReady ? '' : ' status-chip--off'}`}
                  onClick={() => setTab('settings')}
                  title={aiReady ? 'AI 已配置' : 'AI 未配置，点此前往设置'}
                >
                  <span className="status-chip__dot" />
                  {aiReady === null ? '连接中…' : aiReady ? 'AI 就绪' : '未配置'}
                </button>
                {!IS_MOBILE && (
                  <button
                    className="btn btn--sm"
                    onClick={() => setFxOn((v) => !v)}
                    aria-pressed={fxOn}
                    title={fxOn ? '关闭磁吸、边缘高光、鼠标辉光等高级动效' : '开启高级动效'}
                  >
                    <Icon name="zap" size={14} />
                    动效{fxOn ? '开' : '关'}
                  </button>
                )}
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={toggleTheme}
                  aria-label="切换昼夜模式"
                  title={dark ? '切换到白昼' : '切换到夜晚'}
                >
                  <Icon name={dark ? 'sun' : 'moon'} size={15} />
                  {!IS_MOBILE && (dark ? '天明' : '入夜')}
                </button>
              </div>
            </div>
          </nav>

          <main className="app-main">
            {tab === 'workbench' && <WorkbenchPage />}
            {tab === 'ideas' && <IdeasPage onGoto={setTab} />}
            {tab === 'materials' && <MaterialsPage />}
            {tab === 'settings' && <SettingsPage />}
          </main>

          <footer className="footer">
            <div className="footer__mark">✦ NO.10 COMMUNITY ✦</div>
            <p>十号社区活动工作台 · 白昼 / 深夜 双模式</p>
          </footer>
        </div>

        {IS_MOBILE && (
          <nav className="mobile-dock" aria-label="功能分区">
            <SliderNav
              options={TABS.map((t) => ({ label: t.label, icon: t.icon }))}
              nav={dockNav}
              label="功能分区"
            />
          </nav>
        )}
        </div>
      </IdentityGate>
      </ToastProvider>
    </IdentityProvider>
  )
}
