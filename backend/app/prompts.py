"""提示词层：LangChain ChatPromptTemplate 构建，运行时渲染为消息列表。

社区档案（COMMUNITY）与风格指纹（_style_block）作为变量注入模板，
所有含花括号的 JSON 规范以变量（json_spec）传入，避免模板转义问题。
"""

import datetime

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate

# 十号社区档案：硬约束集中在这里，所有生成提示词共用
COMMUNITY = {
    "name": "上海应用技术大学十号楼学生社区",
    "team": "约 10 余人的学生团队，设社长、副社长及秘书部、宣发部、组织策划部",
    "venue": "十号楼党团活动室（会议圆桌固定、不可移动；配有投影大屏；舒适容纳 10 人以内）",
    "equipment": "无音响、无麦克风、无饮水设备（需要背景音乐请自备蓝牙音箱；提醒参与者自带饮用水）",
    "budget": "无硬性上限，常规活动建议控制在 200 元以内，由学校报销，须保留发票",
    "approval": "活动需提前一周报备给指导老师，并报备宿管以借用党团活动室",
    "calendar": "节气活动必做；每学期至少一次志愿者活动；每年一次校庆活动（约 4 月，面向全校师生）",
    "audience": "日常活动面向社区内部成员（10 人以内小规模）；需要扩大参与面时可通过第二课堂面向全校招募",
    "departments": (
        "社长/副社长（总体统筹、重要事项把关与对外联络）、"
        "秘书部（统筹协调、文案、档案、第二课堂申报、报销材料归集）、"
        "宣发部（海报、推文、摄影摄像）、"
        "组织策划部（活动策划落地、物资采购、场地布置、现场执行与秩序）"
    ),
    "notes": "校方不要求安全预案，但需注意用电安全与楼内作息（晚间控制音量）",
}

ACTIVITY_TYPES = [
    "节气时令", "手工 DIY", "茶话会", "志愿服务", "党团学习",
    "学习互助", "职业发展", "心理关怀", "技能工作坊",
    "文体活动", "文艺演出", "社会实践", "校庆活动", "其他",
]

SYSTEM = (
    "你是上海应用技术大学十号楼学生社区的活动策划助手，熟悉高校学生组织的活动组织与校内流程，"
    "擅长设计适配小场地（10 人以内、圆桌 + 投影）、低成本（200 元内）、分工明确、当天就能落地执行的学生活动。"
)

IDEA_JSON_SPEC = (
    '只输出一个 JSON 对象：{"ideas": [{"name": "活动名称，可带热词梗", '
    '"highlight": "一句话亮点", "hotword": "关联热词", '
    '"hotword_source": "抖音|微博|小红书|B站|知乎|模型知识 之一", '
    '"activity_type": "最匹配的活动类型", "feasibility": "可行性简评（50字内）", '
    '"star_rating": 1到5的整数}]}，不要输出其他文字。'
)


def _community_block() -> str:
    return f"""十号社区基本情况（设计活动与写策划案时必须遵守）：
- 主体：{COMMUNITY["name"]}
- 团队：{COMMUNITY["team"]}
- 场地：{COMMUNITY["venue"]}
- 设备：{COMMUNITY["equipment"]}
- 预算：{COMMUNITY["budget"]}
- 审批：{COMMUNITY["approval"]}
- 必做节点：{COMMUNITY["calendar"]}
- 对象：{COMMUNITY["audience"]}
- 部门分工：{COMMUNITY["departments"]}
- 其他：{COMMUNITY["notes"]}"""


def _materials_block(materials_text: str) -> str:
    if materials_text.strip():
        return f"往年活动参考素材：\n{materials_text}"
    return "往年活动参考素材：（无，按高校学生社区通用情况合理定制）"


def _style_block() -> str:
    """从近一年 45 篇推文提炼的社区活动风格指纹。"""
    return """社区活动风格指纹（参考近一年 45 场真实活动总结，设计与写作请向它靠拢）：
- 招牌系列（做新活动优先考虑在它们身上延伸新主题）：「我与导师面对面」师生圆桌座谈（班导师+辅导员双师答疑，围绕专业学习/考研就业/AI 赋能）；「明学沙龙」讲座研讨（常借网络热词切入，如"邪修爆火"）；「高数朋辈互助」（夜学共进、小老师结对帮扶、优秀小老师评选）；「定格书香光影」（读书/摄影线上征集+投票）。
- 惯用形式：圆桌面对面座谈（先主题分享后自由交流，会后留联系方式结对）；观影+要点梳理+研讨；节气主题手作（不织布、漆扇、拓印、绘画）；线上征集作品+人气投票+奖品激励；知识讲解+案例视频（反诈、消费维权、安全教育）；桌游破冰+户外慢聊（如 UNO 牌局）；城市定向闯关、打卡集章（校庆模式：五大打卡点位集章抽奖）。
- 命名风格：主标题用对仗诗意双句（如"暮秋已别 顺问冬安""小得盈满，心绘美好""漆扇绘新岁，拓印承古香"），副标题用"——"引出活动内容；也敢直接玩热词梗（如"邪修"爆火）。
- 奖品偏好：实用文创小物（CCD 相机、手持风扇、蒸汽眼罩、书签、趣味魔方等），常叠加第二课堂学分/志愿时长。
- 成果表达：推文惯用具体数字说话（参与人数、小老师人数、平均提分、及格率提升幅度、作品编号 01-017），策划案的目标与预期请沿用这种可量化表达。
- 资源联动习惯：常邀请校外/校内力量进社区（劳动模范、法学博士、反诈中心、税务局、消防站、毕业生校友），活动可与学部、其他社区联合举办。"""


_HOTWORD_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM),
    ("human", """今天是 {today}。请联网检索「当下」抖音、微博、小红书、B站、知乎上的热门活动 / 话题 / 网红玩法，
优先关注大学生感兴趣、且适合在校园小场地落地（圆桌围坐、10 人以内、200 元内）的内容，与这些活动类型相关更好：{activity_types}。
社区的招牌系列：「我与导师面对面」「明学沙龙」「高数朋辈互助」「定格书香光影」——热词如果能与这些系列结合（借热词做新主题的座谈/沙龙/手作/征集），权重更高。
{json_spec}
把外层 "ideas" 换成 "hotwords"，每个元素改为：
word(热词或活动名)、platform(抖音|微博|小红书|B站|知乎 之一)、
desc(一句话说明它为什么火、什么玩法)、fit(它与上述活动类型或招牌系列的匹配点)。
输出 8~12 条，按热度从高到低排序。不要输出其他文字。"""),
])


def build_hotword_messages(activity_types: list[str]) -> list[BaseMessage]:
    return _HOTWORD_PROMPT.format_messages(
        today=datetime.date.today().isoformat(),
        activity_types="、".join(activity_types),
        json_spec=IDEA_JSON_SPEC,
    )


def build_article_from_image_messages(image_data_url: str) -> list[dict]:
    """推文长截图 → {title, text}：单图（短截图）用。载荷直接用 dict（ChatOpenAI 支持透传）。"""
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": (
                "这是一篇微信公众号推文页面的长截图。请完整提取：\n"
                "1) title：文章标题（去掉「|」与装饰符号两侧的空白）。\n"
                "2) text：正文全文——按截图自上而下的顺序完整输出，保留原有的分行与段落结构；"
                "跳过页头、引导关注、广告、二维码、阅读原文等界面元素。\n"
                "只输出一个 JSON 对象 {\"title\": \"...\", \"text\": \"...\"}，不要输出其他文字。"
            )},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }]


def build_article_tile_messages(image_data_url: str, first: bool) -> list[dict]:
    """超长截图切片逐段转写：首片额外取标题（JSON），后续片只转写正文（纯文本）。
    视觉 API 对图片高度有上限（上万像素的长图会被拒），必须切片。
    两处防幻觉措辞：只转录真实可见文字；无字片（装饰/空白）输出空——模型有续写倾向。"""
    guard = "只转录片中确实可见的文字，绝不编造、补全或续写任何内容；如果本片没有可读正文（纯装饰、空白或 END 之后的区域），就输出空内容。"
    if first:
        instruction = (
            "这是微信公众号推文长截图的第一个纵向切片。请输出 JSON：\n"
            "1) is_article：这张截图是否确实是微信公众号推文页面（有文章标题与正文排版）；"
            "若是聊天记录、网页、表格、海报等其它内容，给 false。\n"
            "2) title：文章标题（去掉装饰符号两侧空白）；若首片里没有标题则给空串。\n"
            "3) text：本片内所有可读正文文字，按自上而下顺序保留分行与段落；"
            "跳过页头账号名、引导关注、阅读数、二维码等界面元素与纯装饰图上的字。\n"
            f"{guard}\n"
            "只输出一个 JSON 对象 {\"is_article\": true, \"title\": \"...\", \"text\": \"...\"}。"
        )
    else:
        instruction = (
            "这是微信公众号推文长截图的一个纵向切片（非首片）。请按自上而下顺序识别并输出"
            "片中所有可读文字（正文段落原样，含小标题）；跳过纯装饰图上的字与界面元素。\n"
            f"{guard}\n"
            "只输出文字，不要解释。"
        )
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }]


_IDEA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM),
    ("human", """今天是 {today}。请为十号楼学生社区生成 8 个活动点子。
活动类型（每个点子尽量贴合其中一种）：{activity_types}
条件：预算 {budget}；参与人数 {people}；时间 {month}；场地 {venue}。
{hotword_block}
{materials_block}
{style_block}
{community_block}
{past_block}
要求：点子的场地、人数、预算必须与上面的社区条件相容（圆桌不可移动，适合围坐型活动；人数超过 10 人需说明更换场地）；党团/思政元素仅在主题团日、党建或重要节点活动中自然融入，日常活动不强加；活动名称尽量沿用社区的对仗诗意命名风格；不要与已办活动主题重复。
{json_spec}"""),
])


def build_idea_messages(
    activity_types: list[str], conditions: dict, materials_text: str,
    hotwords_text: str = "", past_names: list[str] | None = None,
) -> list[BaseMessage]:
    if hotwords_text.strip():
        hotword_block = (
            "已检索到的当下平台热词（请优先围绕它们设计点子，"
            "hotword / hotword_source 字段从下面选取；确需补充时可用模型知识）：\n"
            f"{hotwords_text}"
        )
    else:
        hotword_block = "热词参考：（本次未联网检索，请基于你掌握的抖音/微博/小红书/B站/知乎热门活动知识，并如实标注来源）"
    past_block = ""
    if past_names:
        listing = "\n".join(f"- {n}" for n in past_names)
        past_block = (
            f"近一年已办过的活动主题（避免重复；招牌系列可以做新主题、新节气的延伸，但不要原样重办）：\n{listing}"
        )
    return _IDEA_PROMPT.format_messages(
        today=datetime.date.today().isoformat(),
        activity_types="、".join(activity_types),
        budget=conditions.get("budget") or "200 元以内",
        people=conditions.get("people") or "10 人以内",
        month=conditions.get("month") or "近期",
        venue=conditions.get("venue") or "十号楼党团活动室",
        hotword_block=hotword_block,
        materials_block=_materials_block(materials_text),
        style_block=_style_block(),
        community_block=_community_block(),
        past_block=past_block,
        json_spec=IDEA_JSON_SPEC,
    )


_MATERIAL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM),
    ("human", """请从下面的活动推文正文提取往年活动信息，{json_spec}
把外层 "ideas" 换成单个对象，字段改为：name(活动名称)、activity_type({activity_types} 之一)、time(时间)、form_points(形式要点)、highlights(亮点)、participation(参与情况)。
推文链接备注：{url}
推文正文：
{text}"""),
])


def build_material_messages(text: str, url: str = "") -> list[BaseMessage]:
    return _MATERIAL_PROMPT.format_messages(
        json_spec=IDEA_JSON_SPEC,
        activity_types=" / ".join(ACTIVITY_TYPES),
        url=url or "无",
        text=text[:6000],
    )



_REPORT_SYSTEM = (
    "你是上海应用技术大学十号楼学生社区公众号的宣发干事写手，为活动撰写用于发布的通讯稿。"
    "文风要求：亲切真实、有画面感，用具体数字与细节说话，不堆大词空话；"
    "命名排版与社区既有推文一致（主标题可对仗诗意双句，副标题用「——」引出活动内容）。"
)

_REPORT_HUMAN = """请为以下活动撰写一篇可直接发布的通讯稿。

【活动信息】（举办日期、地点、人数与举办小结为权威事实，直接引用，不得改动日期与数字）
- 名称：{name}（类型：{activity_type}）
- 举办日期：{event_date}；地点：{location}；参与人数：{participants}
- 负责人：{lead_name}
{plan_block}
{photo_note}
【范文参考】（只学笔风、句式与篇幅，不照抄其内容）
{samples_block}

写作要求：
1) 判断范文题材与本次活动不同时，句子结构、开头结尾节奏与整体篇幅向范文看齐；
2) 正文用词贴合大学生视角，数字用阿拉伯数字，段落 3~5 段；
3) 现场照片已随本消息附上：请按画面内容（人物神态、手作环节、成品细节、场景布置）在相应段落自然呼应，写出照片里具体可见的内容；不要罗列文件名或逐张描述；
4) 只输出通讯稿正文本身，不要任何解释、不要 markdown 记号。"""


def build_report_messages(
    act: dict,
    samples_text: str,
    plan_text: str,
    photos_note: str,
    image_urls: list[str] | None = None,
) -> list[dict]:
    """通讯稿生成消息：正文提示词 + 现场照片以多模态图片随消息附带（非文件名清单）。"""
    human_text = _REPORT_HUMAN.format(
        name=act.get("name") or "",
        activity_type=act.get("activity_type") or "未分类",
        event_date=act.get("event_date") or "待定",
        location=act.get("location") or "待定",
        participants=act.get("participants") or "待统计",
        lead_name=act.get("lead_name") or "—",
        plan_block=(f"【策划与前期信息】\n{plan_text}" if plan_text.strip() else "【策划与前期信息】（暂无）"),
        photo_note=photos_note,
        samples_block=(samples_text[:2600] if samples_text.strip() else "（素材库暂无范文，请按社区风格指纹自由发挥）"),
    )
    content: list[dict] = [{"type": "text", "text": human_text}]
    for url in (image_urls or []):
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [
        {"role": "system", "content": _REPORT_SYSTEM},
        {"role": "user", "content": content},
    ]
