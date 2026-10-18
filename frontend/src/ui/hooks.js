import { useEffect } from 'react'

/* 点击外部关闭 */
export function useClickOutside(ref, onClose, active = true) {
  useEffect(() => {
    if (!active) return
    const onDown = e => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [ref, onClose, active])
}
