"""What a language model is good for and can do, from Hugging Face metadata."""
import json
from unittest.mock import patch

from backend.app.hf_inspect import inspect_huggingface
from backend.app.llm_capabilities import base_repo, describe

QWEN_TEMPLATE = ("{%- if tools %}<tools>{% for tool in tools %}{{ tool | tojson }}{% endfor %}</tools>"
                 "<tool_call>{%- endif %}{% if enable_thinking %}<think>{% endif %}")
GEMMA_TEMPLATE = "{{ bos_token }}{% for message in messages %}<start_of_turn>{{ message['role'] }}{% endfor %}"


def gguf(name):
    return {"path": name, "name": name}


def labels(items, available=None):
    return [i["label"] for i in items if available is None or i["available"] is available]


def test_coder_from_its_name_with_tool_calling_from_the_template():
    api = {"id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF", "tags": ["gguf", "code", "conversational"],
           "gguf": {"chat_template": QWEN_TEMPLATE, "context_length": 32768}}
    c = describe(api, [gguf("Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf")])
    assert labels(c["uses"])[:2] == ["General chat & assistant", "Coding"]
    assert c["uses"][1]["evidence"] == "name"
    assert labels(c["features"], True) == ["Tool calling", "Thinking mode"]


def test_roleplay_finetune_without_tags_and_no_tool_template():
    api = {"id": "TheDrummer/Cydonia-24B-v4.1-GGUF", "tags": ["gguf", "conversational"],
           "cardData": {"base_model": ["mistralai/Mistral-Small-3.2-24B-Instruct-2507"]},
           "gguf": {"chat_template": "[INST]{{ message }}[/INST]", "context_length": 131072}}
    c = describe(api, [gguf("Cydonia-24B-v4.1-Q4_K_M.gguf")])
    assert "Roleplay & creative writing" in labels(c["uses"])
    assert labels(c["features"], False) == ["Tool calling"]
    assert "Long context (128K)" in labels(c["features"], True)
    assert base_repo(api) == "mistralai/Mistral-Small-3.2-24B-Instruct-2507"


def test_vision_needs_an_mmproj_file_in_the_repository():
    api = {"id": "unsloth/gemma-3-12b-it-GGUF", "pipeline_tag": "image-text-to-text", "gguf": {"chat_template": GEMMA_TEMPLATE}}
    with_projector = describe(api, [gguf("gemma-3-12b-it-Q4_K_M.gguf"), gguf("mmproj-F16.gguf")])
    assert labels(with_projector["features"], True)[0] == "Vision (image input)"
    text_only = describe(api, [gguf("gemma-3-12b-it-Q4_K_M.gguf")])
    vision = text_only["features"][0]
    assert (vision["label"], vision["available"]) == ("Vision (image input)", False) and "mmproj" in vision["evidence"]
    # The base model's pipeline counts too: a GGUF re-upload often has none of its own.
    reupload = describe({"id": "someone/Model-GGUF"}, [gguf("Model-Q4_K_M.gguf")], base={"pipeline_tag": "image-text-to-text"})
    assert labels(reupload["features"], False) == ["Vision (image input)"]


def test_model_card_mentions_need_repeating_and_ignore_benchmark_tables():
    api = {"id": "org/Generalist-GGUF", "gguf": {"chat_template": GEMMA_TEMPLATE}}
    table = "| Benchmark | Score |\n|---|---|\n" + "| math reasoning coding | 1 |\n" * 10
    assert labels(describe(api, [gguf("Generalist-Q4_K_M.gguf")], readme=table)["uses"]) == ["General chat & assistant"]
    prose = "Strong at math. Solves math word problems. Great math tutor.\n```\ncoding coding coding\n```"
    uses = describe(api, [gguf("Generalist-Q4_K_M.gguf")], readme=prose)["uses"]
    assert [(u["label"], u["evidence"]) for u in uses[1:]] == [("Math", "model card")]


def test_base_and_uncensored_and_reasoning_names():
    base = describe({"id": "meta-llama/Llama-3.1-8B-base-GGUF"}, [gguf("Llama-3.1-8B-base-Q4_K_M.gguf")])
    assert labels(base["uses"]) == ["Base model: text completion, not tuned for chat"]
    other = describe({"id": "x/DeepSeek-R1-Distill-Qwen-7B-abliterated-GGUF", "gguf": {"chat_template": "<think>"}},
                     [gguf("DeepSeek-R1-Distill-Qwen-7B-abliterated-Q4_K_M.gguf")])
    assert "Reasoning" in labels(other["uses"])
    assert "Uncensored (refusals removed)" in labels(other["features"], True)
    assert "Medical" not in labels(describe({"id": "mistralai/Mistral-Medium-GGUF"}, [])["uses"])


def test_inspect_adds_capabilities_for_llms_only():
    api = {"id": "Qwen/Qwen3-8B-GGUF", "pipeline_tag": "text-generation", "tags": ["gguf"],
           "cardData": {"base_model": "Qwen/Qwen3-8B"}, "gguf": {"chat_template": QWEN_TEMPLATE}}
    base = {"id": "Qwen/Qwen3-8B", "tags": ["transformers", "text-generation"]}
    calls = []

    def get(url, accept="application/json"):
        calls.append(url)
        if url.endswith("README.md"):
            return b"Thinking mode for reasoning. Strong reasoning. Reasoning benchmarks."
        return json.dumps(base if url.endswith("/Qwen/Qwen3-8B") else api).encode()
    files = {"files": [{"path": "Qwen3-8B-Q4_K_M.gguf", "name": "Qwen3-8B-Q4_K_M.gguf", "sha256": "a" * 64}]}
    with patch("backend.app.hf_inspect.fetch", side_effect=get), \
            patch("backend.app.hf_inspect.list_model_files", return_value=files):
        data = inspect_huggingface("Qwen/Qwen3-8B-GGUF")
    assert "Reasoning" in labels(data["capabilities"]["uses"])
    assert labels(data["capabilities"]["features"], True) == ["Tool calling", "Thinking mode"]
    assert any(u.endswith("README.md") for u in calls), calls
    assert any(u.endswith("/api/models/Qwen/Qwen3-8B") for u in calls)
    lora = {"id": "a/b-lora", "pipeline_tag": "text-to-image", "tags": ["lora"]}
    with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(lora).encode()), \
            patch("backend.app.hf_inspect.list_model_files", return_value={"files": []}):
        assert inspect_huggingface("a/b-lora")["capabilities"] is None
