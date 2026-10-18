import Icon from './Icon.jsx'

const VARIANTS = ['primary', 'secondary', 'ghost', 'outline', 'danger']

export default function Button({
  variant = 'secondary',
  size = 'md',
  tone,
  icon,
  iconEnd,
  loading = false,
  disabled = false,
  block = false,
  className = '',
  children,
  ...rest
}) {
  const cls = ['btn', `btn--${variant}`]
  if (size !== 'md') cls.push(`btn--${size}`)
  if (block) cls.push('btn--block')
  if (className) cls.push(className)
  return (
    <button
      className={cls.join(' ')}
      style={tone ? { '--btn-tint': `var(--${tone})`, '--btn-tint-deep': `var(--${tone}-deep)` } : undefined}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="btn__spin" aria-hidden="true" /> : icon ? <Icon name={icon} /> : null}
      {children}
      {iconEnd ? <Icon name={iconEnd} /> : null}
    </button>
  )
}

export function IconButton({ icon, label, size = 'md', tone, className = '', ...rest }) {
  const cls = ['btn', `btn--${tone ? 'secondary' : 'ghost'}`, 'btn--icon']
  if (size !== 'md') cls.push(`btn--${size}`)
  if (className) cls.push(className)
  return (
    <button
      className={cls.join(' ')}
      aria-label={label}
      title={label}
      style={tone ? { '--btn-tint': `var(--${tone})`, '--btn-tint-deep': `var(--${tone}-deep)` } : undefined}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  )
}

export function ButtonGroup({ children, ...rest }) {
  return (
    <div className="btn-group" role="group" {...rest}>
      {children}
    </div>
  )
}
