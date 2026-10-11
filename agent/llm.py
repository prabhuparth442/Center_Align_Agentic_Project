"""LLM wrapper. One model (DeepSeek V4 Flash via OpenRouter), two modes.

think=True  -> planner / replanner (reasoning allowed)
think=False -> executor / verifier  (reasoning off, fast, cheap)

UNVERIFIED: the exact `reasoning` values DeepSeek V4 Flash accepts on OpenRouter.
IBY used {"effort": "none"} to turn it off. Check what "on" looks like before relying on it.
"""
import json
from dataclasses import dataclass

from pydantic import ValidationError


class LLMError(Exception):
    """The model call itself failed (network, rate limit, bad key). The loop should catch this."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self):
        return self.prompt_tokens + self.completion_tokens


class LLM:
    # A plain class (not a dataclass) on purpose: its default repr doesn't show the api key,
    # so print(llm) or a traceback can't leak it.
    def __init__(self, base_url, api_key, model, timeout=30.0):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout    # the OpenAI client would wait 10 minutes by default
        self.usage = Usage()
        self._client = None

    def get_client(self):
        if self._client is None:
            from openai import OpenAI   # imported here so tests that never call the model don't need it
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key,
                                  timeout=self.timeout, max_retries=2)
        return self._client

    def chat(self, messages, think):
        # lowercase "none" like the docs say. With a capital N thinking could stay on silently.
        if think:
            reasoning = {"effort": "high"}
        else:
            reasoning = {"effort": "none"}

        try:
            response = self.get_client().chat.completions.create(
                model=self.model, messages=messages,
                extra_body={"reasoning": reasoning},
            )
        except Exception as exc:
            # network and rate-limit errors would otherwise crash the loop as raw exceptions
            raise LLMError(f"model call failed: {exc}") from exc

        if response.usage:
            self.usage.prompt_tokens += response.usage.prompt_tokens
            self.usage.completion_tokens += response.usage.completion_tokens

        return response.choices[0].message.content or ""

    def structured(self, messages, schema, think, repair_tries=2):
        """Ask for JSON, validate with pydantic, feed validation errors back for repair."""
        messages = list(messages)   # copy, so the caller's list doesn't grow with repair messages
        last_error = None
        for attempt in range(repair_tries + 1):
            text = self.chat(messages, think)
            try:
                return schema.model_validate(json.loads(_extract_json(text)))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": f"Invalid JSON for the schema: {exc}. Reply with JSON only."})
        raise ValueError(f"model did not return valid JSON: {last_error}") from last_error


def _extract_json(text):
    """Strip code fences / prose around a JSON object."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return text
    return text[start:end + 1]
