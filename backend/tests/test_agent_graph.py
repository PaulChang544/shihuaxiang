import asyncio
import json
import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from backend.app import db
from backend.app.agent import graph as agent_graph, sessions, tools as agent_tools


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO materials(name, activity_type, highlights) VALUES('重阳敬老','志愿服务','暖夕阳')")


def _model_script(contents):
    return GenericFakeChatModel(messages=iter([AIMessage(content=c) for c in contents]))


IDEA_JSON = json.dumps({"name": "重阳敬老手作茶话会", "highlight": "给独居老人送手作",
                        "hotword": "抱抱桶", "hotword_source": "小红书", "activity_type": "志愿服务",
                        "feasibility": "200 元内可完成", "star_rating": 4}, ensure_ascii=False)


def _collect(agen):
    async def run():
        return [ev async for ev in agen]
    return asyncio.run(run())


def test_chat_mode_replies_and_generates_title():
    s = sessions.create_session()
    fake = _model_script(["重阳敬老志愿活动策划", "想办在什么时间？大概多少人参加？"])
    events = _collect(agent_graph.stream_turn(
        s["id"], "我们想重阳节给社区老人做点事",
        model=fake, tools=[], checkpointer=InMemorySaver(), mode="chat",
    ))
    types = [e["type"] for e in events]
    assert "title" in types and "token" in types and "confirm" not in types
    assert [e["title"] for e in events if e["type"] == "title"] == ["重阳敬老志愿活动策划"]
    assert sessions.get_session(s["id"])["title"] == "重阳敬老志愿活动策划"
    assert [m["role"] for m in sessions.get_messages(s["id"])] == ["user", "assistant"]


def test_force_mode_saves_idea():
    s = sessions.create_session()
    fake = _model_script([IDEA_JSON])  # 汇总出 Idea 后直接落库（截断版：不再生成策划案）
    events = _collect(agent_graph.force_generate(
        s["id"], model=fake, tools=[], checkpointer=InMemorySaver(),
    ))
    final = [e for e in events if e["type"] == "final"][0]
    assert final["idea_id"] > 0 and final["title"] == "重阳敬老手作茶话会"
    with db.get_conn() as c:
        row = c.execute("SELECT * FROM ideas WHERE id=?", (final["idea_id"],)).fetchone()
    assert row["name"] == "重阳敬老手作茶话会" and row["activity_type"] == "志愿服务"


def test_tool_round_merges_brief():
    s = sessions.create_session()
    tool_call = AIMessage(content="", tool_calls=[{
        "name": "update_brief_tool", "id": "call_1",
        "args": {"fields_json": json.dumps({"when": "10 月下旬", "people": "10 人"}, ensure_ascii=False)},
    }])
    follow_up = AIMessage(content="收到，10 月下旬、10 人左右对吧？还想要什么形式？")
    fake = GenericFakeChatModel(messages=iter([tool_call, follow_up]))
    events = _collect(agent_graph.stream_turn(
        s["id"], "十月底办，十个人左右", model=fake,
        tools=[agent_tools.update_brief_tool], checkpointer=InMemorySaver(), mode="chat",
    ))
    brief_events = [e for e in events if e["type"] == "brief"]
    assert brief_events and brief_events[-1]["fields"]["when"] == "10 月下旬"
    tool_events = [e for e in events if e["type"] == "tool"]
    assert any(e["tool"] == "update_brief_tool" and e["status"] == "done" for e in tool_events)
    # start 与 done 通过 call_id 关联，start 携带给工具的入参
    starts = [e for e in tool_events if e["status"] == "start"]
    dones = [e for e in tool_events if e["status"] == "done"]
    assert starts and starts[0]["input"] and "10 月下旬" in starts[0]["input"]
    assert any(d["call_id"] and any(s["call_id"] == d["call_id"] for s in starts) for d in dones)
    # 工具调用落库（role=tool，JSON 含 tool/input/output），供历史对话回放
    tool_rows = [m for m in sessions.get_messages(s["id"]) if m["role"] == "tool"]
    assert len(tool_rows) == 1
    rec = json.loads(tool_rows[0]["content"])
    assert rec["tool"] == "update_brief_tool" and "10 月下旬" in rec["input"] and rec["output"]
    # 工具记录在最终回复之前
    roles = [m["role"] for m in sessions.get_messages(s["id"])]
    assert roles == ["user", "tool", "assistant"]
    # 需求卡持久化，重开会话可还原
    assert sessions.get_brief(s["id"]).get("when") == "10 月下旬"


def test_confirm_interrupt_then_force_resume():
    s = sessions.create_session()
    cp = InMemorySaver()
    events = _collect(agent_graph.stream_turn(
        s["id"], "就按你说的来", model=_model_script([IDEA_JSON]),
        tools=[], checkpointer=cp, mode="confirm",
    ))
    confirm = [e for e in events if e["type"] == "confirm"]
    assert confirm and confirm[0]["idea"]["name"] == "重阳敬老手作茶话会"

    events2 = _collect(agent_graph.force_generate(
        s["id"], model=_model_script([]), tools=[], checkpointer=cp,  # 确认后直接落库，不再调用模型
    ))
    final2 = [e for e in events2 if e["type"] == "final"][0]
    assert final2["idea_id"] > 0


def test_resume_with_message_returns_to_interview():
    s = sessions.create_session()
    cp = InMemorySaver()
    _collect(agent_graph.stream_turn(s["id"], "开始", model=_model_script([IDEA_JSON]),
                                     tools=[], checkpointer=cp, mode="confirm"))
    events = _collect(agent_graph.stream_turn(
        s["id"], "再加一个手作环节", model=_model_script(["好，加一个扭扭棒手作，还有别的吗？"]),
        tools=[], checkpointer=cp, mode="chat",
    ))
    assert any(e["type"] == "token" for e in events)
    assert not any(e["type"] == "final" for e in events)
