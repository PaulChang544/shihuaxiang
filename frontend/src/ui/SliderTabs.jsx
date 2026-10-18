import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'

/* 可拖动的滑动指示条标签导航：
   点击时指示条弹性滑到目标项；按住拖动时指示条跟随手指，
   越过分段中点即切换高亮，但内容面板在松手后才滑动过去。 */
export function useSliderNav(count) {
  const navRef = useRef(null)
  const itemRefs = useRef([])
  const [active, setActive] = useState(0) // 高亮项（拖动中实时更新）
  const [settled, setSettled] = useState(0) // 内容面板项（松手/点击后才更新）
  const activeRef = useRef(0)
  const [dragging, setDragging] = useState(false)
  const draggingRef = useRef(false)
  const [thumb, setThumb] = useState({ x: 0, w: 0 })

  const select = useCallback(i => {
    activeRef.current = i
    setActive(i)
    setSettled(i)
  }, [])

  const measure = useCallback(
    i => {
      const nav = navRef.current
      const el = itemRefs.current[i]
      if (!nav || !el) return null
      const nr = nav.getBoundingClientRect()
      const er = el.getBoundingClientRect()
      // 横向可滚动容器（如活动详情的阶段条）：换算回内容坐标。
      // 直接用视口差值会混入 scrollLeft，滚动后滑块位置累积漂移。
      return { x: er.left - nr.left + nav.scrollLeft, w: er.width }
    },
    [],
  )

  useLayoutEffect(() => {
    if (draggingRef.current) return
    const m = measure(active)
    if (m) setThumb(m)
  }, [active, measure])

  useEffect(() => {
    const rer = () => {
      const m = measure(activeRef.current)
      if (m && m.w > 0) setThumb(m)
    }
    // 真机首启时首帧布局/字体可能晚于挂载测量（w=0 的滑块“消失”）：
    // 重试直到量到有效宽度（至多 ~3 秒），再由 ResizeObserver / 字体就绪 / 横竖屏切换兜底
    let alive = true
    let tries = 0
    const tick = () => {
      if (!alive) return
      const m = measure(activeRef.current)
      if (m && m.w > 0) {
        setThumb(m)
        return
      }
      if (tries++ < 50) setTimeout(tick, 60)
    }
    tick()
    const ro = new ResizeObserver(rer)
    if (navRef.current) ro.observe(navRef.current)
    let fontsAlive = true
    document.fonts?.ready?.then(() => { if (fontsAlive) rer() })
    window.addEventListener('resize', rer)
    window.addEventListener('pageshow', rer) // iOS 返回前进缓存（bfcache）恢复时重测
    return () => {
      alive = false
      fontsAlive = false
      ro.disconnect()
      window.removeEventListener('resize', rer)
      window.removeEventListener('pageshow', rer)
    }
  }, [measure])

  const onNavDown = useCallback(
    e => {
      if (e.button !== undefined && e.button !== 0) return
      const nav = navRef.current
      if (!nav) return
      const nr = nav.getBoundingClientRect()
      const startX = e.clientX
      const startY = e.clientY
      const m0 = measure(0)
      const w = m0 ? m0.w : 60
      const centers = itemRefs.current.map(el => {
        if (!el) return 0
        const er = el.getBoundingClientRect()
        return er.left - nr.left + er.width / 2
      })
      const first = centers[0]
      const last = centers[centers.length - 1]
      // 拖动期间同步维护的当前项：松手时用它，避免 React 状态尚未提交时读到旧值
      let dragged = activeRef.current

      const follow = clientX => {
        const center = Math.min(Math.max(clientX - nr.left, first), last)
        setThumb({ x: center - w / 2, w })
        // 高亮跟随手指：越过哪一段就高亮哪一段
        let nearest = 0
        for (let i = 0; i < centers.length; i++) {
          if (center >= centers[i]) nearest = i
        }
        if (nearest !== dragged) {
          dragged = nearest
          activeRef.current = nearest
          setActive(nearest)
        }
      }

      const onMove = ev => {
        if (!draggingRef.current) {
          // 超过位移阈值才进入拖动，普通点击不受影响
          if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < 6) return
          draggingRef.current = true
          setDragging(true)
        }
        follow(ev.clientX)
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        if (!draggingRef.current) return
        draggingRef.current = false
        setDragging(false)
        // 松手后才让内容面板滑到目标页（用拖动期间同步记录的值，与高亮必然一致）
        setSettled(dragged)
        const m = measure(dragged)
        if (m) setThumb(m)
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
    },
    [measure],
  )

  return { navRef, itemRefs, active, settled, select, dragging, thumb, onNavDown }
}

export function SliderNav({ options, nav, label, draggable = true }) {
  return (
    <div
      className="tabs__nav"
      role="tablist"
      aria-label={label}
      ref={nav.navRef}
      onPointerDown={draggable ? nav.onNavDown : undefined}
    >
      <span
        className="tabs__thumb"
        data-dragging={nav.dragging || undefined}
        style={{ transform: `translateX(${nav.thumb.x}px)`, width: nav.thumb.w }}
      />
      {options.map((t, i) => (
        <button
          key={typeof t === 'string' ? t : t.label}
          ref={el => (nav.itemRefs.current[i] = el)}
          className={`tabs__item ${nav.active === i ? 'tabs__item--on' : ''}`}
          role="tab"
          aria-selected={nav.active === i}
          onClick={() => nav.select(i)}
        >
          {typeof t === 'string' ? (
            t
          ) : (
            <>
              <Icon name={t.icon} size={t.iconSize ?? 18} />
              <span>{t.label}</span>
            </>
          )}
        </button>
      ))}
    </div>
  )
}
