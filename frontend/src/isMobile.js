/* 移动端判定：真机 UA（含微信 web-view）或 ?mobile=1 强制预览。
   全站共享同一语义：底部 dock 布局 + 组件的手机变体都以此为准。 */
const IS_MOBILE =
  /Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent) ||
  new URLSearchParams(window.location.search).has('mobile')

export default IS_MOBILE
