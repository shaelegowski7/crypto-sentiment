"""Claude-backed adapters for ScraperAI's LLM interfaces.

ScraperAI ships only OpenAI implementations (`JsonOpenAI`, `VisionOpenAI`,
`PythonCodeOpenAI`, all via `langchain_openai`).  SentimentFX already uses
Anthropic (see ``app/brief.py``) and has no OpenAI dependency, so instead of
adding one we implement ScraperAI's ``BaseJsonLM`` / ``BaseVision`` /
``BasePythonCodeLM`` interfaces on top of the ``anthropic`` SDK.

**This module is offline/build-time only** — it is imported exclusively by
``backend/build_scraper_config.py`` (the config generator), never by the running
app.  It therefore depends on the full ``scraperai`` package (langchain,
selenium, ...), which is installed only in the build tool's own virtualenv and
is deliberately absent from the production image.  The runtime replay path uses
the vendored engine under ``app/vendor/scraperai_replay/`` and imports nothing
from here.

The adapters translate LangChain message lists (what ScraperAI's parsers build)
into Anthropic ``messages.create`` calls:
  * ``SystemMessage`` content is collected into the ``system`` parameter.
  * ``HumanMessage`` / ``AIMessage`` become ``user`` / ``assistant`` turns.
  * A ``HumanMessage`` whose content is a list of blocks (text + an
    ``image_url`` data-URI, as ``WebpageVisionClassifier`` sends) is converted
    to Anthropic text + base64 ``image`` blocks.
  * Consecutive same-role turns are coalesced (ScraperAI sometimes appends two
    ``HumanMessage``s in a row, which the Anthropic API rejects).

ScraperAI's ``ChatModelAgent.query_with_validation`` re-prompts on invalid
output, so best-effort JSON parsing here is safe.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re

from anthropic import Anthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from scraperai.llm.base import BaseJsonLM, BasePythonCodeLM, BaseVision

logger = logging.getLogger("scraperai")

# Per the claude-api guidance, default to the most capable model; the build
# step is run rarely and offline, so cost/latency don't matter here.  Override
# with SCRAPERAI_CLAUDE_MODEL if desired.
DEFAULT_MODEL = os.getenv("SCRAPERAI_CLAUDE_MODEL", "claude-opus-5")
DEFAULT_MAX_TOKENS = int(os.getenv("SCRAPERAI_CLAUDE_MAX_TOKENS", "8192"))

_DATA_URI_RE = re.compile(r"^data:(?P<media>[^;]+);base64,(?P<data>.+)$", re.DOTALL)


def _blocks_from_langchain_content(content) -> list[dict]:
    """Translate one LangChain message's ``content`` into Anthropic blocks."""
    if isinstance(content, str):
        text = content if content.strip() else " "  # Anthropic rejects empty text
        return [{"type": "text", "text": text}]

    blocks: list[dict] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                blocks.append({"type": "text", "text": part})
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text", "")
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif ptype == "image_url":
            url = part.get("image_url", {}).get("url", "")
            m = _DATA_URI_RE.match(url)
            if not m:
                # Only data-URIs are supported by the vision path ScraperAI uses.
                logger.warning("scraperai_claude: skipping non-data-URI image_url")
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": m.group("media"),
                    "data": m.group("data"),
                },
            })
        else:
            logger.warning("scraperai_claude: skipping unknown content part %r", ptype)
    return blocks or [{"type": "text", "text": " "}]


def _split_messages(messages: list[BaseMessage]) -> tuple[str, list[dict]]:
    """Return (system_prompt, anthropic_messages) with consecutive roles merged."""
    system_parts: list[str] = []
    turns: list[dict] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(msg.content if isinstance(msg.content, str) else str(msg.content))
            continue
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        blocks = _blocks_from_langchain_content(msg.content)
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    # Anthropic requires the first turn to be a user turn.
    if turns and turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": " "}]})
    return "\n\n".join(p for p in system_parts if p), turns


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json\n...\n```  or  ```\n...\n```
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        text = re.sub(r"\n```$", "", text.strip())
    return text.strip()


class _ClaudeBase:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
                 client: Anthropic | None = None):
        # Anthropic() reads ANTHROPIC_API_KEY from the environment, matching
        # app/brief.py's client construction.
        self.client = client or Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self._total_cost = 0.0  # ScraperAI reads this on OpenAI models; kept for parity.

    @property
    def total_cost(self) -> float:
        return self._total_cost

    def _raw(self, messages: list[BaseMessage], system_suffix: str = "") -> str:
        system, turns = _split_messages(messages)
        if system_suffix:
            system = (system + "\n\n" + system_suffix).strip()
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system or None,
            messages=turns,
        )
        return "".join(b.text for b in resp.content if b.type == "text")


class ClaudeJsonLM(_ClaudeBase, BaseJsonLM):
    """JSON-returning adapter (mirrors ScraperAI's ``JsonOpenAI``)."""

    _JSON_SUFFIX = "Respond with a single valid JSON object and nothing else. Do not wrap it in markdown fences."

    def invoke(self, messages: list[BaseMessage]) -> dict:
        text = self._raw(messages, system_suffix=self._JSON_SUFFIX)
        cleaned = _strip_json_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Last resort: grab the outermost {...} span.
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start != -1 and end > start:
                return json.loads(cleaned[start:end + 1])
            raise


class ClaudeVisionLM(_ClaudeBase, BaseVision):
    """Vision adapter (mirrors ScraperAI's ``VisionOpenAI``) — returns raw text."""

    def invoke(self, messages: list[BaseMessage]) -> str:
        return self._raw(messages).strip()


class ClaudePythonCodeLM(_ClaudeBase, BasePythonCodeLM):
    """Python-code adapter (mirrors ScraperAI's ``PythonCodeOpenAI``).

    ScraperAI extracts a fenced code block from the reply, so returning the raw
    text (fences included) is fine.
    """

    def invoke(self, messages: list[BaseMessage]) -> str:
        return self._raw(messages)
