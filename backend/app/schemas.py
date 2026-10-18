"""AI 输出的 pydantic 模型（LangChain 结构化校验层）。"""

from pydantic import BaseModel, Field, field_validator


def _as_text(value) -> str:
    """AI 有时会把字段返回成数组/对象，统一转成字符串。"""
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(_as_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return "；".join(f"{k}：{_as_text(v)}" for k, v in value.items())
    return str(value).strip()


class Idea(BaseModel):
    name: str = "未命名"
    highlight: str = ""
    hotword: str = ""
    hotword_source: str = "模型知识"
    activity_type: str = ""
    feasibility: str = ""
    star_rating: int = Field(default=3, ge=1, le=5)

    @field_validator("name", "highlight", "hotword", "hotword_source", "activity_type", "feasibility", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return _as_text(v)

    @field_validator("star_rating", mode="before")
    @classmethod
    def _coerce_star(cls, v):
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return 3


class IdeaList(BaseModel):
    ideas: list[Idea] = Field(min_length=1)


class Hotword(BaseModel):
    word: str = ""
    platform: str = ""
    desc: str = ""
    fit: str = ""

    @field_validator("word", "platform", "desc", "fit", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return _as_text(v)


class HotwordList(BaseModel):
    hotwords: list[Hotword] = Field(min_length=1)


class MaterialInfo(BaseModel):
    name: str = "未命名活动"
    activity_type: str = "其他"
    time: str = ""
    form_points: str = ""
    highlights: str = ""
    participation: str = ""

    @field_validator("name", "activity_type", "time", "form_points", "highlights", "participation", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return _as_text(v) or ""

    @field_validator("name", mode="after")
    @classmethod
    def _default_name(cls, v):
        return v or "未命名活动"

    @field_validator("activity_type", mode="after")
    @classmethod
    def _default_type(cls, v):
        return v or "其他"


class ArticleInfo(BaseModel):
    """推文长截图的多模态解析结果（首片附带 is_article 判定是否为推文页面）。"""
    is_article: bool = True
    title: str = ""
    text: str = ""

    @field_validator("title", mode="after")
    @classmethod
    def _strip_title(cls, v):
        return (v or "").strip()

    @field_validator("text", mode="after")
    @classmethod
    def _strip_text(cls, v):
        return (v or "").strip()
