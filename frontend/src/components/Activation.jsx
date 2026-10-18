import { useState } from 'react'
import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'
import useIdentity from '../hooks/useIdentity'
import { humanName } from '../names.js'

/* 激活码表单（首次进站 / 切换身份 / 本机失效后重激活 共用） */
export function ActivationForm({ onDone }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const { activate, me } = useIdentity()

  const submit = async (e) => {
    e?.preventDefault?.()
    if (!code.trim() || busy) return
    setBusy(true)
    const r = await activate(code)
    setBusy(false)
    if (r.ok) {
      setErr('')
      onDone?.()
    } else {
      setErr(r.error || '激活失败')
    }
  }

  return (
    <form className="act-form panel-stack" onSubmit={submit}>
      {me && (
        <div className="field__hint">本机当前身份：{humanName(me.name)}。换码后本机身份随之切换。</div>
      )}
      <div className="field">
        <label className="field__label" htmlFor="act-code">邀请码 <small>仅首次进站时输入，此后保存在本机</small></label>
        <input
          id="act-code"
          className="input act-form__input"
          placeholder="6 位邀请码，如 AB3D9F"
          value={code}
          onChange={(e) => { setCode(e.target.value.toUpperCase()); setErr('') }}
          autoFocus
        />
        {err && <div className="act-form__err">{err}</div>}
      </div>
      <Button block variant="primary" type="submit" loading={busy} icon="flag">激活</Button>
      <div className="act-form__hint">没有邀请码？联系站长领取（站长可在设置页为你生成）。</div>
    </form>
  )
}

/* 首次进站 / 身份失效：整页激活卡 */
export function ActivationScreen() {
  return (
   <div className="act-wrap">
      <div className="glass act-card">
        <div className="act-card__mark">✦ NO.10 COMMUNITY ✦</div>
        <div className="act-card__title">活动工作台</div>
        <div className="act-card__sub">十号社区</div>
        <Icon name="home" size={34} className="act-card__art" />
        <div className="act-card__body"><ActivationForm /></div>
      </div>
    </div>
  )
}
