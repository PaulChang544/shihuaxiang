/* 通用异步作业：localStorage 登记未完成任务，App 级 watcher 轮询 /api/jobs/{id}。
   任务形态：{job_id, kind, ref_id, label}；完成结果在任务 result 里。 */

export const JOB_KEY = 'shx-jobs'

export function readJobs() {
  try { return JSON.parse(localStorage.getItem(JOB_KEY) || '[]') } catch { return [] }
}

export function addJob(entry) {
  const jobs = readJobs().filter((j) => j.job_id !== entry.job_id)
  try { localStorage.setItem(JOB_KEY, JSON.stringify([...jobs, entry])) } catch { /* 隐私模式忽略 */ }
}

const KIND_TEXT = {
  archive_article: { done: (j, r) => `推文归档完成《${r.title || j.label}》`, fail: '推文截图识别失败' },
  docx_export: { done: (j) => `《${j.label}》Word 已生成`, fail: 'Word 生成失败' },
  material_extract: { done: (j, r) => `素材已入库《${r.name || j.label}》`, fail: '素材入库失败' },
  material_enhance: { done: (j, r) => `素材要点已补全《${r.name || j.label}》`, fail: '素材要点补全失败' },
  report_generate: { done: (j) => `《${j.label}》通讯稿已生成并保存`, fail: '通讯稿生成失败' },
}

export function jobDoneText(job) {
  const meta = KIND_TEXT[job.kind] || { done: () => '后台任务完成', fail: '后台任务失败' }
  return meta.done(job, job.result || {})
}

export function jobFailText(job) {
  return (KIND_TEXT[job.kind] || {}).fail || '后台任务失败'
}
