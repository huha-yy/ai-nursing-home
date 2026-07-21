"""Content operations pipeline — automated multi-step content creation.

hotspot-monitor → relevance-judge → fact-research → content-strategy →
wechat-content → xhs-content → douyin-content → image-generator → article-composer →
humanizer → compliance-check → feishu-publisher.

Each step is a CallAgent dispatch: the workflow sends a task message to the
OpenClaw agent container, which runs the corresponding skill. Steps are
sequential — each step's output feeds into the next.

Workflow input:
  - agent_id (str, required): UUID of the content-ops agent container.
  - brand (str, optional): Brand key (e.g. "yonghe", "daien").
    Defaults to "yonghe".
  - topic (str, optional): Override topic. If omitted, hotspot-monitor runs
    first and the pipeline picks the top-ranked topic automatically.
  - skip_to (str, optional): Step key to skip to (for re-running from a
    specific step). Requires `topic` and prior step outputs in `resume_data`.
  - resume_data (dict, optional): Prior step outputs when using skip_to.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from dl_control.workflows import config_cache
from dl_control.workflows.model import CallAgent, Flow, Retry, Step, StepContext, StepResult

# ---------------------------------------------------------------------------
# Brand configuration (pure data — loaded from brand_configs/<brand>.yaml)
# ---------------------------------------------------------------------------
# To add a new brand, just drop a <slug>.yaml in brand_configs/ and create
# the corresponding configs/<slug>/ directory. No Python code changes needed.
# See brand_configs/_template.yaml and brand_config.py for details.
# ---------------------------------------------------------------------------


def _brand_config(input: dict[str, Any]) -> dict[str, Any]:
    """Resolve brand config from workflow input, falling back to default.

    Brand YAML configs were removed for the nursing MVP. Returns a minimal stub.
    """
    brand = input.get("brand", "")
    return {
        "name": brand or "default",
        "brand_short": brand or "default",
        "sector": "",
        "mission": "",
        "wechat_rule": "",
        "xhs_tags": [],
        "brand_bridge": "",
        "compliance_extra": "",
        "product_names": [],
    }


def _no_webhook_flag(input: dict[str, Any]) -> str:
    """Return ' --no-webhook' if input.no_webhook is true (Agent Manager
    private-chat path where the bot already replies in conversation)."""
    return " --no-webhook" if input.get("no_webhook") else ""


def _agent_task(
    agent_id_key: str,
    message_builder,
    workflow_id: str | None = None,
):
    """Build a deterministic prepare function for CallAgent.

    agent_id_key: key in the workflow input dict holding the agent UUID.
    message_builder: fn(input, outputs) -> str message for the agent.
    workflow_id: used to look up the DB-backed default from config_cache.
    """

    def prepare(input: dict[str, Any], outputs: dict[str, Any]):
        from dl_control.workflows.model import AgentTask

        raw = input.get(agent_id_key)
        if not raw:
            db_default = config_cache.get_default(workflow_id) if workflow_id else None
            raw = db_default or config_cache.get_hardcoded_fallback()
        if not raw:
            raise KeyError(agent_id_key)
        agent_id = UUID(raw) if isinstance(raw, str) else raw
        message = message_builder(input, outputs)
        return AgentTask(agent_id=agent_id, message=message)

    return prepare


def _extract_score(raw: Any) -> int | None:
    """Extract a 0-100 relevance score from the relevance-judge step output.

    Handles multiple wrapping formats that can arise from the OpenClaw agent
    CLI output chain:
    - {"score": 85, ...} — native structured dict
    - {"text": "{\\"score\\": 85, ...}"} — text wraps a JSON string
    - {"text": "some...\\n...score: 85..."} — text wraps natural language
    - "{\\"score\\": 85, ...}" — bare JSON string
    """
    if isinstance(raw, dict):
        if "score" in raw and isinstance(raw["score"], (int, float)):
            return int(raw["score"])
        # OpenClaw container format {"runId": "...", "result": {"payloads": [{"text": "..."}]}}
        if "runId" in raw and "result" in raw:
            payloads = raw.get("result", {}).get("payloads")
            if isinstance(payloads, list) and payloads:
                text = payloads[0].get("text")
                if isinstance(text, (str, bytes)):
                    return _extract_score(text)
        # Recurse into text wrapper
        text = raw.get("text")
        if isinstance(text, (str, bytes)):
            return _extract_score(text)
        return None

    if isinstance(raw, str):
        s = raw.strip()
        # Try direct JSON parse
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return _extract_score(data)
        except (json.JSONDecodeError, TypeError):
            pass
        # Unescape double-escaped strings (common artifact from OpenClaw output)
        if "\\\\n" in s or '\\\\"' in s:
            try:
                data = json.loads(s.encode("utf-8").decode("unicode_escape"))
                if isinstance(data, dict):
                    return _extract_score(data)
            except Exception:
                pass
        # Regex fallback: "score": 85 | score: 85 | score=85
        m = re.search(r'[{"\'"]?\s*score\s*["\']?\s*[:=]\s*(\d+)', s, re.IGNORECASE)
        if m:
            return int(m.group(1))

    return None


# --- Skill invocation prefix ---
# Unattended pipeline — agent MUST NOT ask questions, MUST NOT wait for
# confirmation, MUST execute all steps autonomously.
# Each skill is at /opt/openclaw/skills/custom/<name>/ with SKILL.md + scripts/.

_SKILL_PREFIX = (
    "⚡ 无人值守任务，铁律：\n"
    "- 禁止询问用户、禁止等待确认、禁止输出\"是否需要\"等提问\n"
    "- 所有步骤自主决策、直接执行到底\n"
    "- 遇到错误自动修复一次，失败则跳过继续，最后汇报跳过了什么\n"
    "- 飞书推送步骤必须执行，不要问\"是否需要推送\"\n"
    "- **⚠️ 最终回复必须以 text 形式包含完整的执行结果摘要**：写了什么文件、关键产出、路径等。"
    "不要只回复\"已完成\"\"已执行\"或空内容。这一步的输出将传给下一步骤作上下文。\n\n"
    "你有一个名为 '{name}' 的技能，位于 /opt/openclaw/skills/custom/{name}/。\n"
    "先读取该目录下的 SKILL.md，然后按说明逐步执行。\n"
    "使用 exec 工具运行技能中提到的脚本。\n"
)


# --- Step prepare functions ---

_prepare_hotspot = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="hotspot-monitor")
        + "操作：加载 configs/hotspot_sources.yaml，抓取所有 RSS 源，"
        "用脚本去重聚类，输出标准化热点列表 JSON。\n"
        "输出：hotspot_id, title, url, summary, tags（取排名最高的热点）。"
    ),
    workflow_id="content.pipeline",
)

_prepare_relevance = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="relevance-judge")
        + f"监控领域：{_brand_config(inp)['sector']}\n"
        + f"热点数据：{out.get('topic-gate') or out.get('hotspot-monitor', '{}')}\n"
        "操作：两级过滤——关键词初筛 + LLM 语义精判。\n"
        "评分<70 → 今日无选题，直接退出。\n"
        "输出：topic, score (0-100), reasoning, relevance_type。"
    ),
    workflow_id="content.pipeline",
)

_prepare_fact_research = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="fact-research")
        + f"主题：{out.get('relevance-judge', '{}')}\n"
        "重要：开始研究前，先搜索公司知识库（cognee）查找相关产品/品牌信息：\n"
        "  import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')\n"
        "  from handler import search\n"
        "  results = search(query='<主题>', library_slugs=['company_knowledge'])\n"
        "操作：拉取 2-3 个独立来源，多源交叉验证。\n"
        "输出：verified_facts, sources, confidence_level, uncertain_points。"
    ),
    workflow_id="content.pipeline",
)

_prepare_content_strategy = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="content-strategy")
        + _brand_config(inp)["mission"]
        + f"主题：{out.get('relevance-judge', '{}')}\n"
        f"事实：{out.get('fact-research', '{}')}\n"
        "重要：先搜索 cognee 获取品牌指南和产品定位：\n"
        "  import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')\n"
        "  from handler import search\n"
        "  results = search(query='<主题>', library_slugs=['company_knowledge'])\n"
        "操作：制定三平台（微信公众号+小红书+抖音图文轮播）内容策略。\n"
        f"强制：必须包含 {_brand_config(inp)['brand_bridge']}。\n"
        "brand_bridge 字段：hotspot_connection, primary_product, brand_value, natural_entry_point\n"
        "输出：wechat_angle, xhs_angle, douyin_angle, brand_bridge, key_messages。"
    ),
    workflow_id="content.pipeline",
)

_prepare_wechat = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="wechat-content")
        + _brand_config(inp)["mission"]
        + f"策略：{out.get('content-strategy', '{}')}\n"
        f"事实：{out.get('fact-research', '{}')}\n"
        "操作：生成公众号文章。\n"
        f"强制：{_brand_config(inp)['wechat_rule']}\n"
        "写入路径：outputs/<topic>/publish_package/wechat/article.md\n"
        "输出：标题、摘要、正文（分节）。文章本身应是可直接发布的完整内容，"
        "不要包含图片建议或工作文档说明。"
    ),
    workflow_id="content.pipeline",
)

_prepare_xhs = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="xhs-content")
        + f"策略：{out.get('content-strategy', '{}')}\n"
        f"事实：{out.get('fact-research', '{}')}\n"
        "操作：生成小红书笔记。品牌软植入，个人体验/他人推荐语气，不官方。\n"
        f"强制：必须包含 {_brand_config(inp)['xhs_tags']} 标签。\n"
        "写入路径：outputs/<topic>/publish_package/xiaohongshu/note.md\n"
        "输出：标题、正文、标签列表。"
    ),
    workflow_id="content.pipeline",
)

_prepare_douyin = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="douyin-content")
        + f"策略：{out.get('content-strategy', '{}')}\n"
        f"事实：{out.get('fact-research', '{}')}\n"
        "操作：生成抖音图文轮播（Carousel Post）。\n"
        "写入路径：outputs/<topic>/publish_package/douyin/article.md + carousel.json\n"
        "输出：标题、正文（article.md）、轮播元数据（carousel.json）。"
    ),
    workflow_id="content.pipeline",
)

_prepare_images = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="image-generator")
        + f"策略：{out.get('content-strategy', '{}')}\n"
        f"小红书文章：{out.get('xhs-content', '{}')}\n"
        f"抖音图文：{out.get('douyin-content', '{}')}\n"
        "操作：必须为三个平台分别生成配图方案并执行。\n"
        "公众号：1张封面 + 每节配图（最多5张内文配图） + 文末品牌图（landscape）\n"
        "小红书：1张封面 + 每节配图（最多4张内文配图） + 文末品牌图（portrait）\n"
        "抖音图文：封面1张 + 轮播配图（最多5张），全部 9:16 竖版\n"
        "⚠️ 每个平台配图总数（不含后续品牌素材）控制在 5-8 张以内，不要过多。\n"
        "执行 run_image_pipeline.py 生成所有图片。\n"
        "策略：Pexels 优先，ComfyUI 仅当 COMFYUI_URL 已配置时使用。\n"
        "输出：生成的图片路径列表。"
    ),
    workflow_id="content.pipeline",
)

_prepare_compose = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="article-composer")
        + f"公众号文章：{out.get('wechat-content', '{}')}\n"
        f"小红书笔记：{out.get('xhs-content', '{}')}\n"
        f"抖音图文：{out.get('douyin-content', '{}')}\n"
        f"图片：{out.get('image-generator', '{}')}\n"
        "操作：运行 insert_images.py 将图片按语义插入文章对应章节。\n"
        "公众号文章和小红书笔记、抖音图文都需要插入。\n"
        "文末附加品牌 logo + 二维码。\n"
                f"建议传入 --brand {inp.get('brand', 'yonghe')}（脚本会自动从\n"
        "  .pipeline_context.json 兜底读取，无需担心丢失）。\n"
        "输出：最终文章路径及插入图片数。"
    ),
    workflow_id="content.pipeline",
)

_prepare_humanize = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="humanizer")
        + f"公众号文章（待去AI味）：{out.get('article-composer', '{}')}\n"
        f"抖音图文（待去AI味）：{out.get('douyin-content', '{}')}\n"
        "操作：扫描 24 种 AI 写作模式并改写为自然人类写作。\n"
        "五个维度：内容模式、语言语法、风格、通信模式、填充语。\n"
        "保留所有事实、品牌提及、段落结构和图片标记（![](file://...)）。\n"
        "输出：改写后文章路径和修改摘要。"
    ),
    workflow_id="content.pipeline",
)

_prepare_compliance = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="compliance-check")
        + f"公众号文章：{out.get('humanizer', '{}')}\n"
        f"小红书笔记：{out.get('xhs-content', '{}')}\n"
        f"抖音图文：{out.get('douyin-content', '{}')}\n"
        "操作：四维风险审核——\n"
        "1. 事实准确性：来源可靠性、多源支撑\n"
        "2. 品牌合规：品牌口吻、禁用词\n"
        "3. 平台合规：广告法违禁词、平台规则、AI 生成标签\n"
        "4. AI 痕迹检测：50 分评分制\n"
        + _brand_config(inp)["compliance_extra"]
        + "\n输出：各维度 pass/fail、问题清单、总体风险等级（低/中/高）。"
    ),
    workflow_id="content.pipeline",
)

_prepare_publish = _agent_task(
    "agent_id",
    lambda inp, out: (
        _SKILL_PREFIX.format(name="feishu-publisher")
        + "⚠️ 凭据已配好，立即执行，不要问用户！\n\n"
        + "找出 outputs/ 下最多子目录的那个（包含 wechat/ xiaohongshu/ douyin/ 的），\n"
        + "用它作为 PACKAGE_DIR。\n"
        + "如果 outputs/ 下有多个目录，只取最新那个——"
        + "打开 feishu_publish.json 看已有的 URL 是哪来的。\n\n"
        + "然后分别推送三个平台：\n"
        + "  python skills/feishu-publisher/scripts/push_to_feishu.py \\\n"
        + f"    --package-dir <PACKAGE_DIR> --platform wechat{_no_webhook_flag(inp)}\n"
        + "  python skills/feishu-publisher/scripts/push_to_feishu.py \\\n"
        + f"    --package-dir <PACKAGE_DIR> --platform xhs{_no_webhook_flag(inp)}\n"
        + "  python skills/feishu-publisher/scripts/push_to_feishu.py \\\n"
        + f"    --package-dir <PACKAGE_DIR> --platform douyin{_no_webhook_flag(inp)}\n\n"
        + "🛑 关键：三个平台必须用同一个 PACKAGE_DIR！不要从不同目录各取一个。\n"
        + "如果某个目录只有部分平台，说明它是旧的、不完整的，换另一个。\n"
        + "输出：三个平台的飞书文档链接。"
    ),
    workflow_id="content.pipeline",
)


# --- Pipeline context writer: writes brand into agent workspace for
#     script-level fallback reading (e.g. insert_images.py).
#     Uses the host bind-mount path at /data/agents/<agent_id>/workspace/.


async def write_pipeline_context(ctx: StepContext) -> StepResult | None:
    """Before compose, write .pipeline_context.json into the agent workspace
    so that insert_images.py (and other scripts) can read the current brand
    even when the LLM forgets to pass --brand."""
    import json as _json
    import os as _os

    agent_id = ctx.input.get("agent_id")
    brand = ctx.input.get("brand", "yonghe")
    if agent_id:
        ctx_dir = _os.path.join("/data/agents", str(agent_id), "workspace")
        ctx_path = _os.path.join(ctx_dir, ".pipeline_context.json")
        try:
            _os.makedirs(ctx_dir, exist_ok=True)
            with open(ctx_path, "w") as _f:
                _json.dump({"brand": brand}, _f)
        except OSError:
            pass  # non-fatal — insert_images.py has its own fallback chain
    return None  # fall through to next step


# --- Topic gate: skip hotspot-monitor when topic is provided as input ---


async def topic_gate(ctx: StepContext) -> StepResult | None:
    """If the user provided a ``topic`` in the workflow input, skip the
    hotspot-monitor step and jump straight to relevance-judge with that
    topic as the hotspot output."""
    topic = ctx.input.get("topic") or ctx.input.get("theme")
    if topic and isinstance(topic, str) and topic.strip():
        return StepResult(
            output={
                "hotspot_id": "user_topic",
                "title": topic.strip(),
                "url": "",
                "summary": topic.strip(),
                "tags": ["用户指定"],
            },
            goto="relevance-judge",
        )
    return None  # no topic → fall through to hotspot-monitor


# --- Relevance gate: skip remaining steps if score < 70 ---


async def relevance_gate(ctx: StepContext) -> StepResult | None:
    """If relevance score < 70, skip to done."""
    raw = ctx.outputs.get("relevance-judge", "")
    score = _extract_score(raw)
    if score is not None and score < 70:
        return StepResult(
            output={"skipped": True, "reason": f"Relevance score {score} < 70"},
            goto="__done__",
        )
    return None


# --- Flow definition ---

flow = Flow(
    "content.pipeline",
    version="1.0.0",
    steps=[
        Step(
            "topic-gate",
            handler=topic_gate,
        ),
        Step(
            "hotspot-monitor",
            call_agent=CallAgent(prepare=_prepare_hotspot, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "relevance-judge",
            call_agent=CallAgent(prepare=_prepare_relevance, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "relevance-gate",
            handler=relevance_gate,
        ),
        Step(
            "fact-research",
            call_agent=CallAgent(prepare=_prepare_fact_research, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=30),
        ),
        Step(
            "content-strategy",
            call_agent=CallAgent(prepare=_prepare_content_strategy, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "wechat-content",
            call_agent=CallAgent(prepare=_prepare_wechat, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=30),
        ),
        Step(
            "xhs-content",
            call_agent=CallAgent(prepare=_prepare_xhs, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "douyin-content",
            call_agent=CallAgent(prepare=_prepare_douyin, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "image-generator",
            call_agent=CallAgent(prepare=_prepare_images, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=30),
        ),
        Step(
            "brand-context",
            handler=write_pipeline_context,
        ),
        Step(
            "article-composer",
            call_agent=CallAgent(prepare=_prepare_compose, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=30),
        ),
        Step(
            "humanizer",
            call_agent=CallAgent(prepare=_prepare_humanize, timeout_seconds=300),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "compliance-check",
            call_agent=CallAgent(prepare=_prepare_compliance, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=30),
        ),
        Step(
            "feishu-publisher",
            call_agent=CallAgent(prepare=_prepare_publish, timeout_seconds=600),
            retry=Retry(max_attempts=3, base_seconds=30),
        ),
    ],
)
