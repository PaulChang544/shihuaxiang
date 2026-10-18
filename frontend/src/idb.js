/* 极简 IndexedDB kv 存储：断点续传的半成品字节与压缩缓存都放这里
   （localStorage 存不了大块二进制）。 */

const DB_NAME = 'shx-cache'
const STORE = 'kv'

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function withStore(mode, fn) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const t = db.transaction(STORE, mode)
        const req = fn(t.objectStore(STORE))
        t.oncomplete = () => { db.close(); resolve(req.result) }
        t.onerror = () => { db.close(); reject(req.error) }
      }),
  )
}

export const idbGet = (key) => withStore('readonly', (s) => s.get(key))
export const idbPut = (key, val) => withStore('readwrite', (s) => s.put(val, key))
export const idbDel = (key) => withStore('readwrite', (s) => s.delete(key))
