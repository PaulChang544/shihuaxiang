import { createContext, useCallback, useContext, useRef, useState } from 'react'
import Icon from './Icon.jsx'

const ToastCtx = createContext(() => {})

const META = {
  info: { icon: 'info', tone: 'blue' },
  success: { icon: 'checkCircle', tone: 'sage' },
  warning: { icon: 'alertTriangle', tone: 'sand' },
  error: { icon: 'xCircle', tone: 'rose' },
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const idRef = useRef(0)

  const remove = useCallback(id => {
    setToasts(ts => ts.filter(t => t.id !== id))
  }, [])

  const push = useCallback(
    ({ title, msg, type = 'info', duration = 3600 }) => {
      const id = ++idRef.current
      setToasts(ts => [...ts.slice(-4), { id, title, msg, type, duration }])
      setTimeout(() => remove(id), duration)
    },
    [remove],
  )

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {toasts.map(t => {
          const { icon, tone } = META[t.type] || META.info
          return (
            <div key={t.id} className="toast" style={{ '--tone': `var(--${tone})` }}>
              <span className="toast__icon">
                <Icon name={icon} size={17} />
              </span>
              <div className="toast__msg">
                <b>{t.title}</b>
                {t.msg ? t.msg : null}
              </div>
              <button className="toast__close" onClick={() => remove(t.id)} aria-label="关闭通知">
                <Icon name="x" />
              </button>
              <span className="toast__life" style={{ animationDuration: `${t.duration}ms` }} />
            </div>
          )
        })}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}
