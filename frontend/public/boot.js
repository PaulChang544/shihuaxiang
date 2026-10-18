/* 启动错误收集器：独立文件（CSP 禁止内联脚本） */
window.__bootErrs = [];
(function () {
  var push = function (m) { try { window.__bootErrs.push(String(m).slice(0, 400)) } catch (e) {} }
  window.addEventListener('error', function (e) { push('ERR ' + (e.message || '?') + ' @' + (e.lineno || '?')) })
  window.addEventListener('unhandledrejection', function (e) { push('REJ ' + ((e.reason && e.reason.message) || e.reason)) })
  var ce = console.error
  console.error = function () { push([].map.call(arguments, function (a) { return typeof a === 'string' ? a : (a && a.message) || '' }).join(' | ')); ce.apply(console, arguments) }
})()
