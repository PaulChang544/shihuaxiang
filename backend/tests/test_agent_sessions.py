import asyncio
import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from backend.app import db
from backend.app.agent import sessions


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_session_crud_lifecycle():
    s = sessions.create_session()
    assert s["status"] == "active" and s["title"] == "" and s["title_locked"] == 0

    sessions.update_session(s["id"], title="重阳敬老活动")
    got = sessions.get_session(s["id"])
    assert got["title"] == "重阳敬老活动" and got["title_locked"] == 1  # 手动改名即锁定

    sessions.update_session(s["id"], status="archived")
    assert sessions.list_sessions("active") == []
    assert sessions.list_sessions("archived")[0]["id"] == s["id"]
    sessions.update_session(s["id"], status="active")
    assert sessions.list_sessions("active")[0]["id"] == s["id"]

    sessions.append_message(s["id"], "user", "想办个重阳活动")
    sessions.append_message(s["id"], "assistant", "好的，想在室内还是户外？")
    msgs = sessions.get_messages(s["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]

    sessions.delete_session(s["id"])
    assert sessions.get_session(s["id"]) is None
    assert sessions.get_messages(s["id"]) == []


def test_generate_title_from_first_input():
    fake = GenericFakeChatModel(messages=iter([AIMessage(content="重阳敬老志愿活动策划")]))
    title = asyncio.run(sessions.generate_title("我们想在重阳节给社区老人做点事", model=fake))
    assert title == "重阳敬老志愿活动策划"


def test_generate_title_failure_returns_empty():
    class Boom(GenericFakeChatModel):
        def _generate(self, *a, **k):
            raise RuntimeError("boom")

    db.set_setting("api_key", "sk-test")
    assert asyncio.run(sessions.generate_title("x", model=Boom(messages=iter([])))) == ""
