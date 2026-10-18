/* 通用小件：分区标题 / 演示台 / 玻璃卡 / 徽章 / 图标气泡 / 键位 / 气泡提示 */
import Icon from './Icon.jsx'

export function Section({ id, kicker, title, desc, children }) {
  return (
    <section className="section container" id={id}>
      <header className="section__head">
        <p className="section__kicker">{kicker}</p>
        <h2 className="section__title">{title}</h2>
        {desc ? <p className="section__desc">{desc}</p> : null}
      </header>
      {children}
    </section>
  )
}

export function Demo({ label, children, className = '', style }) {
  return (
    <div className={`demo glass ${className}`} style={style}>
      <p className="demo__label">{label}</p>
      {children}
    </div>
  )
}

export function Badge({ tone = 'clay', variant = 'soft', dot = false, children }) {
  const cls = ['badge']
  if (variant !== 'soft') cls.push(`badge--${variant}`)
  if (dot) cls.push('badge--dot')
  return (
    <span className={cls.join(' ')} style={{ '--tone': `var(--${tone})` }}>
      {children}
    </span>
  )
}

export function IconBubble({ tone = 'rose', icon, size = 18, className = '' }) {
  return (
    <span className={`icon-bubble ${className}`} style={{ '--tone': `var(--${tone})` }}>
      <Icon name={icon} size={size} />
    </span>
  )
}

export function Divider({ label, vertical = false }) {
  if (vertical) return <span className="divider divider--v" aria-hidden="true" />
  if (label) return <div className="divider divider--label">{label}</div>
  return <hr className="divider" />
}

export function Kbd({ children }) {
  return <kbd className="kbd">{children}</kbd>
}

export function Tip({ label, children }) {
  return (
    <span className="tip" tabIndex={0}>
      {children}
      <span className="tip__bubble" role="tooltip">
        {label}
      </span>
    </span>
  )
}
