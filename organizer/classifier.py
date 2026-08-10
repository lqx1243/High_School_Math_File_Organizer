from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

from .category_rules import CategoryRules

DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
PREFERRED_MODEL_IDS = ("deepseek-v4-flash",)


@dataclass(frozen=True)
class Classification:
    kind: str
    primary: str | None
    secondary: str | None
    confidence: float
    reason: str

    @property
    def display_name(self) -> str:
        if self.kind == "secondary":
            return f"{self.primary} / {self.secondary}"
        if self.kind == "primary_only":
            return self.primary or "无法分类"
        return "综合文件" if self.kind == "comprehensive" else "无法分类"


def classify_with_deepseek(*, api_key: str, filename: str, content: str, rules: CategoryRules, api_url: str = DEFAULT_API_URL, model: str = DEFAULT_MODEL) -> Classification:
    """先选一级分类，再只在该一级的二级分类内继续判断。"""
    excerpt = content[:18000] or "（未提取到正文；请依据文件名判断，置信度应较低。）"
    primary_prompt = f"""你是高中数学教学资料整理助手。先只判断这份资料的一级分类。

一级分类目录（分类名后的“说明”是重要判定依据）：
{rules.primary_prompt()}

判定规则：
1. 文件名是重要证据，应与正文一同判断。
2. 能明确归入唯一一级分类时，kind 为 primary。
3. 内容覆盖多个一级分类，或无法放入唯一一个一级分类时，kind 为 comprehensive。
4. 不属于目录范围、信息不足或无法判断时，kind 为 unclassifiable。
5. confidence 是 0 到 1 的数字；reason 用不超过 45 字中文说明依据。

只输出 JSON，不要 Markdown：
{{"kind":"primary|comprehensive|unclassifiable","primary":"一级分类或null","confidence":0.0,"reason":"简短原因"}}

文件名：{filename}
正文摘录：
{excerpt}"""
    first = _ask_json(api_key, primary_prompt, api_url, model)
    primary_kind = str(first.get("kind", "unclassifiable"))
    primary = first.get("primary") or None
    primary_confidence = _confidence(first.get("confidence"))
    reason = str(first.get("reason", "模型未提供理由。")).replace("\n", " ")[:100]

    if primary_kind == "comprehensive":
        return Classification("comprehensive", None, None, primary_confidence, reason)
    if primary_kind != "primary" or not rules.has_primary(primary):
        return Classification("unclassifiable", None, None, primary_confidence, reason or "无法确定一级分类。")
    children = rules.groups[primary]
    if not children:
        return Classification("primary_only", primary, None, primary_confidence, reason)

    secondary_prompt = f"""你已确定这份高中数学资料属于一级分类「{primary}」。
现在仅在以下二级分类中判断（分类名后的“说明”是重要判定依据）：
{rules.secondary_prompt(primary)}

文件名是重要证据，应与正文一同判断。若不能可靠归入一个二级分类，kind 必须是 primary_only；不要猜测。
只输出 JSON，不要 Markdown：
{{"kind":"secondary|primary_only","secondary":"二级分类或null","confidence":0.0,"reason":"简短原因"}}

文件名：{filename}
正文摘录：
{excerpt}"""
    second = _ask_json(api_key, secondary_prompt, api_url, model)
    secondary = second.get("secondary") or None
    secondary_confidence = _confidence(second.get("confidence"))
    second_reason = str(second.get("reason", "")).replace("\n", " ")[:100]
    if str(second.get("kind")) == "secondary" and rules.has_secondary(primary, secondary):
        return Classification("secondary", primary, secondary, min(primary_confidence, secondary_confidence), second_reason or reason)
    return Classification("primary_only", primary, None, primary_confidence, second_reason or reason)


def _ask_json(api_key: str, prompt: str, api_url: str, model: str) -> dict:
    response = requests.post(api_url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": "你必须只返回可解析的 JSON。"}, {"role": "user", "content": prompt}], "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": 400, "response_format": {"type": "json_object"}}, timeout=75)
    response.raise_for_status()
    return _parse_json(response.json()["choices"][0]["message"]["content"])


def list_available_models(api_key: str, api_url: str = DEFAULT_API_URL) -> list[str]:
    """验证 API Key 并读取 OpenAI 兼容接口公布的模型列表。"""
    endpoint = api_url.rstrip("/")
    suffix = "/chat/completions"
    if endpoint.endswith(suffix):
        endpoint = endpoint[: -len(suffix)]
    models_url = endpoint + "/models"
    response = requests.get(models_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("模型列表响应格式无效。")
    models = sorted({str(model.get("id", "")).strip() for model in data if isinstance(model, dict) and str(model.get("id", "")).strip()}, key=str.lower)
    if not models:
        raise ValueError("API 未返回可用模型。")
    return models


def _parse_json(answer: str) -> dict:
    try:
        return json.loads(answer)
    except json.JSONDecodeError:
        matched = re.search(r"\{.*\}", answer, re.DOTALL)
        if not matched:
            raise ValueError("DeepSeek 没有返回可读取的 JSON。")
        return json.loads(matched.group())


def _confidence(value: object) -> float:
    try:
        return min(1.0, max(0.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0
