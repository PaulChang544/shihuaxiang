import { useEffect, useState } from 'react'

// 手机窄屏一律单列，避免多列挤压
function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 768px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const onChange = (e) => setNarrow(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return narrow
}

// 卡片瀑布流：columns 列各自独立堆叠，卡片上下紧贴，不因某行内容过长产生空白
// 分配方式为按序交替（第 1 张进左列、第 2 张进右列……），保持大致的左右阅读顺序
export default function CardColumns({ items, columns = 2, render, gap = 16 }) {
  const narrow = useNarrow()
  const count = narrow ? 1 : columns
  const cols = Array.from({ length: count }, () => [])
  items.forEach((it, i) => cols[i % count].push(it))
  return (
    <div style={{ display: 'flex', gap, alignItems: 'flex-start' }}>
      {cols.map((col, ci) => (
        <div key={ci} style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap }}>
          {col.map(render)}
        </div>
      ))}
    </div>
  )
}
