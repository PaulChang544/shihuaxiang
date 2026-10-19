// 子路径部署（如 https://example.com/shihuaxiang）：BASE_URL 由 vite base 注入，
// 反代剥离前缀后转发到容器根。本地构建 base='/' 时与原行为完全一致。
export const BASE = import.meta.env.BASE_URL.replace(/\/+$/, '')

/* FastAPI 的 detail 可能是字符串、校验错误数组或对象，统一拼成可读文案 */
export function describeDetail(detail, fallback = '') {
  if (detail == null) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((e) => (typeof e?.msg === 'string' && e.msg) || e?.type || '').filter(Boolean).join('；') || fallback
  }
  if (typeof detail === 'object') {
    return (typeof detail.msg === 'string' && detail.msg) || fallback
  }
  return String(detail) || fallback
}

const readDetail = async (res, fallback) => {
  let msg = fallback
  try {
    msg = describeDetail((await res.json()).detail, fallback)
  } catch { /* 非 JSON 响应体，用 HTTP 状态码兜底 */ }
  return msg
}

/* 抛错时带上 HTTP 状态码（e.status），调用方可区分未登录（401）等场景 */
const httpError = (status, msg) => {
  const err = new Error(msg)
  err.status = status
  return err
}

export async function api(path, { method = 'GET', body } = {}) {
  const isForm = body instanceof FormData
  const res = await fetch(BASE + path, {
    method,
    headers: body && !isForm ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
  })
  if (!res.ok) {
    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('shx-auth-expired'))
    }
    let msg = `HTTP ${res.status}`
    msg = await readDetail(res, msg)
    throw httpError(res.status, msg)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('json') ? res.json() : res.blob()
}

// 读取 POST SSE 流，逐事件回调 onEvent(eventName, data)
export async function streamSSE(path, body, onEvent) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('shx-auth-expired'))
    }
    let msg = `HTTP ${res.status}`
    msg = await readDetail(res, msg)
    throw httpError(res.status, msg)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      const datas = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) datas.push(line.slice(5).trim())
      }
      if (!datas.length) continue
      try {
        onEvent(event, JSON.parse(datas.join('\n')))
      } catch { /* 忽略无法解析的事件 */ }
    }
  }
}

// 带上传进度的 XHR（fetch 拿不到上传字节进度；分片续传用）
export function xhrSend(path, { method = 'PUT', body, onProgress, contentType = 'application/octet-stream' } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open(method, BASE + path)
    xhr.setRequestHeader('Content-Type', contentType)
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total)
      }
    }
    xhr.onload = () => {
      if (xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)) } catch { resolve(xhr.responseText) }
      } else {
        let msg = `HTTP ${xhr.status}`
        try { msg = describeDetail(JSON.parse(xhr.responseText).detail, msg) } catch { /* ignore */ }
        reject(httpError(xhr.status, msg))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.send(body)
  })
}

// 流式读取响应体（Range 断点续传下载用）：逐块回调，返回累计字节
export async function streamBody(res, onChunk) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let received = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    received += value.byteLength
    onChunk?.(value, received)
    void decoder
  }
  return received
}
