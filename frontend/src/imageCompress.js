/* 本地图片压缩：目标约 5MB 以内再上传。
   阶梯策略：先限制最长边再降 JPEG 质量；带透明通道的图先铺白底。
   进度按阶段回调（解码/缩放绘制/逐轮编码），给环形进度用。 */

const TARGET = 5 * 1024 * 1024

function drawScaled(bitmap, maxSide) {
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
  const w = Math.max(1, Math.round(bitmap.width * scale))
  const h = Math.max(1, Math.round(bitmap.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff' // 透明通道转 JPEG 时铺白底
  ctx.fillRect(0, 0, w, h)
  ctx.drawImage(bitmap, 0, 0, w, h)
  return canvas
}

const toBlob = (canvas, quality) =>
  new Promise((resolve) => canvas.toBlob((b) => resolve(b), 'image/jpeg', quality))

export async function compressImage(file, { target = TARGET, onProgress } = {}) {
  onProgress?.(0.05)
  // 已经足够小且是照片格式：直接原样上传，不做有损处理
  if (file.size <= target && file.type !== 'image/png') {
    onProgress?.(1)
    return { blob: file, filename: file.name, mime: file.type || 'application/octet-stream' }
  }
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }).catch(() => null)
  if (!bitmap) return { blob: file, filename: file.name, mime: file.type || 'application/octet-stream' } // 解不开就不压
  onProgress?.(0.15)
  let maxSide = Math.max(bitmap.width, bitmap.height)
  let quality = 0.9
  let best = null
  const rounds = 6
  for (let i = 0; i < rounds; i++) {
    const canvas = drawScaled(bitmap, maxSide)
    // eslint-disable-next-line no-await-in-loop
    const blob = await toBlob(canvas, quality)
    onProgress?.(0.15 + (0.75 * (i + 1)) / rounds)
    if (!best || blob.size < best.size) best = blob
    if (blob.size <= target) break
    if (quality > 0.5) quality -= 0.12
    else maxSide = Math.round(maxSide * 0.75)
  }
  const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
  onProgress?.(1)
  return { blob: best, filename: name, mime: 'image/jpeg' }
}
