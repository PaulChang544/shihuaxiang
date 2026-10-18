"""通用异步作业查询：前端轮询。任务结果只对创建者或管理员可见。"""

from fastapi import APIRouter, HTTPException, Request

from .. import auth, jobs

router = APIRouter(prefix="/api/jobs")


@router.get("/{jid}")
def get_job(jid: int, request: Request):
    row = jobs.get_job(jid)
    viewer = auth.current_member(request)
    if row is None:
        raise HTTPException(404, "任务不存在")
    owner = row.get("op_id")
    if owner is not None and owner != viewer["id"] and viewer["role"] not in auth.MANAGER_ROLES:
        raise HTTPException(404, "任务不存在")
    return row
