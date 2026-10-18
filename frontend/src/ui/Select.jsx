import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Icon from './Icon.jsx'
import { useClickOutside } from './hooks.js'

/* 自定义下拉选框：原生 <select> 的弹出列表由系统绘制、无法套用主题，
   这里用「输入框样式的触发器 + 玻璃弹层」实现同款外观，并保留键盘操作。
   弹层 portal 到 body 并按触发器矩形 fixed 定位：弹窗（overflow:hidden）等
   任何祖先都不会再把它裁掉显示不全；列表过高或贴底时自动向上展开。 */
export default function Select({
  options = [],
  value,
  defaultValue,
  onChange,
  placeholder = '请选择',
  disabled = false,
  className = '',
  id,
}) {
  const list = options.map(o => (typeof o === 'string' ? { value: o, label: o } : o))
  const [inner, setInner] = useState(defaultValue ?? list[0]?.value)
  const current = value !== undefined ? value : inner
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState(null) // 触发器矩形（打开时快照）
  const [hi, setHi] = useState(0) // 键盘高亮项
  const ref = useRef(null)
  useClickOutside(ref, () => setOpen(false), open)

  const selected = list.find(o => o.value === current)

  useEffect(() => {
    if (open) {
      const i = list.findIndex(o => o.value === current)
      setHi(i >= 0 ? i : 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const pick = v => {
    if (value === undefined) setInner(v)
    onChange?.(v)
    setOpen(false)
  }

  const onKeyDown = e => {
    if (disabled) return
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault()
        setOpen(true)
      }
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi(h => Math.min(list.length - 1, h + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi(h => Math.max(0, h - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (list[hi]) pick(list[hi].value)
    }
  }

  // 打开时记录触发器矩形；页面滚动即收起（弹层跟随失效），但菜单列表自身的
  // 滚动（长列表上下滑看选项）不算页面滚动，否则一滑就关、列表根本用不了
  const menuRef = useRef(null)
  useEffect(() => {
    if (!open) return
    const close = (e) => {
      if (e?.target && menuRef.current && menuRef.current.contains(e.target)) return
      setOpen(false)
    }
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  const toggle = (e) => {
    if (disabled) return
    if (!open) setRect(e.currentTarget.getBoundingClientRect())
    setOpen(v => !v)
  }

  // 弹层几何：默认向下展开；贴近视口底部且上方有空间时向上翻。
  // rect 为 null（尚未点开）时不做任何计算，避免组件一挂载就用 null 解引用白屏。
  const geom = !rect
    ? null
    : (() => {
        const vh = window.innerHeight
        const triggerBottom = rect.bottom + 6
        const availBelow = vh - triggerBottom - 10
        const flip = availBelow < 150 && rect.top > vh / 2
        const maxH = flip ? Math.max(140, Math.min(244, rect.top - 14)) : Math.max(120, Math.min(244, availBelow))
        const w = Math.min(window.innerWidth - 16, Math.max(rect.width, 200))
        return {
          top: flip ? Math.max(8, rect.top - maxH - 12) : triggerBottom,
          left: Math.min(rect.left, window.innerWidth - w - 8),
          w,
          maxH,
        }
      })()

  return (
    <div className={`select-field ${className}`} ref={ref}>
      <button
        type="button"
        id={id}
        className={`input select-trigger ${open ? 'select-trigger--on' : ''}`}
        onClick={toggle}
        onKeyDown={onKeyDown}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={selected ? '' : 'select-trigger__ph'}>
          {selected ? selected.label : placeholder}
        </span>
        <Icon name="chevronDown" size={15} className="select-trigger__chev" />
      </button>
      {open && geom
        ? createPortal(
            <div
              className="menu menu--select menu--portal"
              role="listbox"
              ref={menuRef}
              style={{
                position: 'fixed',
                top: geom.top,
                left: geom.left,
                width: geom.w,
                maxHeight: geom.maxH,
              }}
              onMouseDown={e => e.stopPropagation()} // 点击选项不被外点关闭判定拦下
            >
              {list.map((o, i) => (
                <button
                  key={o.value}
                  type="button"
                  role="option"
                  aria-selected={o.value === current}
                  className={`menu__item ${o.value === current ? 'menu__item--on' : ''} ${
                    i === hi ? 'menu__item--hi' : ''
                  }`}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pick(o.value)}
                >
                  <span>{o.label}</span>
                  {o.value === current ? <Icon name="check" size={14} /> : null}
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
