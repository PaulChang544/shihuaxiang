/* 成员显示名：超管的对外展示名由后端统一下发为「超管」，
   前端不再持有任何系统账号名/邀请码映射。 */

export function humanName(name) {
  return name || ''
}
