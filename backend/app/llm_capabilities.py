"""What a language model is good for (coding, roleplay...) and what it can do (vision, tool calling...).

Built from what Hugging Face publishes, each item with the evidence behind it, so the dashboard can say
why: the repository's tags and pipeline, its name and its files, the chat template (whether it takes
tools, whether it has a thinking mode), the model card, and the base model's own tags. These are hints
from metadata, not benchmarks."""
import re

# Use cases: label, then a pattern matched against names and tags (strong evidence) and, for the model
# card, a pattern that has to appear at least CARD_MIN times, since cards mention many things in passing.
USES = [
    ("Coding", r"coder|codestral|codegemma|starcoder|deepseek-coder|devstral|(?<![a-z])code(?![a-z])|codeqwen|programming",
     r"(?<![a-z])(coding|code generation|programming|software engineering)(?![a-z])"),
    ("Roleplay & creative writing",
     r"(?<![a-z])(rp|roleplay|role-play|rpmax|creative|storywriter|story|nsfw|erp)(?![a-z])|cydonia|rocinante|magnum|"
     r"euryale|stheno|lumimaid|mythomax|mythalion|nemomix|celeste|thedrummer|sao10k|anthracite|behemoth|skyfall|"
     r"unslopnemo|gutenberg|darkest|midnight-miqu",
     r"(?<![a-z])(roleplay|role-play|role play|creative writing|storytelling|story writing|characters?)(?![a-z])"),
    ("Reasoning", r"(?<![a-z])(r1|qwq|reason(?:ing|er)?|thinking|think|o1|cot)(?![a-z])|gpt-oss|magistral|phi-4-reasoning",
     r"(?<![a-z])(reasoning|chain[- ]of[- ]thought|thinking mode)(?![a-z])"),
    ("Math", r"(?<![a-z])(math|mathstral|numina)(?![a-z])|qwen2\.5-math|deepseek-math", r"(?<![a-z])(math|mathematics|gsm8k|aime)(?![a-z])"),
    ("Agents & tool use", r"(?<![a-z])(agent|agentic|function-calling|tool-use|tool-calling|hermes|functionary)(?![a-z])",
     r"(?<![a-z])(agentic|function calling|tool use|tool calling)(?![a-z])"),
    ("Multilingual & translation", r"(?<![a-z])(multilingual|translat\w*|aya|seamless|nllb)(?![a-z])",
     r"(?<![a-z])(multilingual|translation)(?![a-z])"),
    ("Medical", r"(?<![a-z])(medical|medgemma|meditron|biomistral|clinical|med42|openbiollm|biomedical)(?![a-z])",
     r"(?<![a-z])(medical|clinical)(?![a-z])"),
]
CARD_MIN = 3
CHAT = re.compile(r"(?<![a-z])(instruct|chat|it|assistant|conversational)(?![a-z])", re.I)
BASE_ONLY = re.compile(r"(?<![a-z])base(?![a-z])", re.I)
UNCENSORED = re.compile(r"abliterat|uncensored|(?<![a-z])(heretic|decensored)(?![a-z])", re.I)
VISION_PIPELINES = {"image-text-to-text", "visual-question-answering", "video-text-to-text", "any-to-any"}
VISION_NAMES = re.compile(r"(?<![a-z])(vl|vision|llava|pixtral|minicpm-v|moondream|internvl|smolvlm|qwen\d?(?:\.\d)?-?vl|gemma-?3n?)(?![a-z])", re.I)
# A chat template that formats a tools list and tool calls. Templates name these in different ways.
TOOLS_TEMPLATE = re.compile(r"(?<![a-z_])tools(?![a-z_])", re.I)
TOOL_CALLS = re.compile(r"tool_call|tool_calls|function_call|\[TOOL_CALLS\]|<\|tool|\"function\"|'function'|ipython", re.I)
THINKING_TEMPLATE = re.compile(r"<think>|enable_thinking|reasoning_content|reasoning_effort|<\|channel\|>|\[THINK\]", re.I)
LONG_CONTEXT = 100_000


def _prose(readme):
    """The model card without code blocks, HTML and markdown tables: benchmark tables name every skill."""
    text = re.sub(r"```.*?```", "", (readme or "")[:60_000], flags=re.S).lower()
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("|", "<")))


def _text(*parts):
    return " ".join(str(p) for p in parts if p).lower().replace("_", "-")


def _base_names(card):
    base = (card or {}).get("base_model")
    return [str(b) for b in (base if isinstance(base, list) else [base]) if b]


def describe(api, files, readme=None, base=None):
    """{"uses": [{"label", "evidence"}], "features": [{"label", "available", "evidence"}]} for a language model.

    api is the repository's Hugging Face API record (tags, pipeline_tag, cardData, gguf); files its file list;
    readme its model card text; base the API record of the model it was made from, if it has one."""
    card, gguf = api.get("cardData") or {}, api.get("gguf") or {}
    base = base or {}
    names = _text(api.get("id"), " ".join(f.get("name", "") for f in files[:40]))
    tags = _text(" ".join(map(str, api.get("tags") or [])), " ".join(map(str, card.get("tags") or [])))
    base_text = _text(" ".join(_base_names(card)), base.get("id"), " ".join(map(str, base.get("tags") or [])))
    card_text = _prose(readme)
    template = str(gguf.get("chat_template") or "")
    uses = []
    for label, strong, mention in USES:
        if re.search(strong, names):
            uses.append({"label": label, "evidence": "name"})
        elif re.search(strong, tags):
            uses.append({"label": label, "evidence": "tags"})
        elif re.search(strong, base_text):
            uses.append({"label": label, "evidence": "base model"})
        elif len(re.findall(mention, card_text)) >= CARD_MIN:
            uses.append({"label": label, "evidence": "model card"})
    chat = CHAT.search(names) or "conversational" in tags or template
    if chat:
        uses.insert(0, {"label": "General chat & assistant", "evidence": "chat template" if template else "name"})
    elif BASE_ONLY.search(names):
        uses.insert(0, {"label": "Base model: text completion, not tuned for chat", "evidence": "name"})

    features = []
    mmproj = any("mmproj" in f.get("name", "").lower() for f in files)
    vision_model = (api.get("pipeline_tag") in VISION_PIPELINES or base.get("pipeline_tag") in VISION_PIPELINES
                    or bool(VISION_NAMES.search(names)) or bool(set(api.get("tags") or []) & VISION_PIPELINES))
    if mmproj:
        features.append({"label": "Vision (image input)", "available": True,
                         "evidence": "has an mmproj vision projector; load it with the model (llama.cpp --mmproj)"})
    elif vision_model:
        features.append({"label": "Vision (image input)", "available": False,
                         "evidence": "the model can see images, but this repository has no mmproj file, so a GGUF "
                                     "from here runs text-only"})
    if template:
        tools = bool(TOOLS_TEMPLATE.search(template) and TOOL_CALLS.search(template))
        features.append({"label": "Tool calling", "available": tools,
                         "evidence": "chat template formats tools and tool calls" if tools
                         else "chat template has no tool support; tools need a custom prompt"})
        if THINKING_TEMPLATE.search(template):
            features.append({"label": "Thinking mode", "available": True,
                             "evidence": "chat template has a reasoning/thinking section"})
    elif re.search(r"function-calling|tool-use|tool-calling", tags):
        features.append({"label": "Tool calling", "available": True, "evidence": "tags"})
    context = gguf.get("context_length")
    if isinstance(context, int) and context >= LONG_CONTEXT:
        features.append({"label": f"Long context ({context // 1024}K)", "available": True, "evidence": "GGUF metadata"})
    languages = card.get("language")
    if isinstance(languages, list) and len(languages) >= 5:
        features.append({"label": f"{len(languages)} languages", "available": True, "evidence": "model card"})
    if UNCENSORED.search(names) or UNCENSORED.search(tags):
        features.append({"label": "Uncensored (refusals removed)", "available": True, "evidence": "name"})
    return {"uses": uses, "features": features}


def base_repo(api):
    """The first base model this repository names, as "owner/name", or None."""
    for name in _base_names(api.get("cardData")):
        if re.fullmatch(r"[\w.-]+/[\w.-]+", name):
            return name
    return None
