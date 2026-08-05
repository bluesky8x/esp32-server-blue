import os, json, uuid, re, time
from types import SimpleNamespace
from typing import Any, Dict, List

import requests
from google import generativeai as genai
from google.generativeai import types, GenerationConfig
from google.api_core import exceptions as gcp_exceptions

from core.providers.llm.base import LLMProviderBase
from core.utils.util import check_model_key
from config.logger import setup_logging
from google.generativeai.types import GenerateContentResponse
from requests import RequestException

log = setup_logging()
TAG = __name__


def test_proxy(proxy_url: str, test_url: str) -> bool:
    try:
        resp = requests.get(test_url, proxies={"http": proxy_url, "https": proxy_url})
        return 200 <= resp.status_code < 400
    except RequestException:
        return False


def setup_proxy_env(http_proxy: str | None, https_proxy: str | None):
    """
    分别测试 HTTP 和 HTTPS 代理是否可用，并设置环境变量。
    如果 HTTPS 代理不可用但 HTTP 可用，会将 HTTPS_PROXY 也指向 HTTP。
    """
    test_http_url = "http://www.google.com"
    test_https_url = "https://www.google.com"

    ok_http = ok_https = False

    if http_proxy:
        ok_http = test_proxy(http_proxy, test_http_url)
        if ok_http:
            os.environ["HTTP_PROXY"] = http_proxy
            log.bind(tag=TAG).info(f"配置提供的Gemini HTTPS代理连通成功: {http_proxy}")
        else:
            log.bind(tag=TAG).warning(f"配置提供的Gemini HTTP代理不可用: {http_proxy}")

    if https_proxy:
        ok_https = test_proxy(https_proxy, test_https_url)
        if ok_https:
            os.environ["HTTPS_PROXY"] = https_proxy
            log.bind(tag=TAG).info(f"配置提供的Gemini HTTPS代理连通成功: {https_proxy}")
        else:
            log.bind(tag=TAG).warning(
                f"配置提供的Gemini HTTPS代理不可用: {https_proxy}"
            )

    # 如果https_proxy不可用，但http_proxy可用且能走通https，则复用http_proxy作为https_proxy
    if ok_http and not ok_https:
        if test_proxy(http_proxy, test_https_url):
            os.environ["HTTPS_PROXY"] = http_proxy
            ok_https = True
            log.bind(tag=TAG).info(f"复用HTTP代理作为HTTPS代理: {http_proxy}")

    if not ok_http and not ok_https:
        log.bind(tag=TAG).error(
            f"Gemini 代理设置失败: HTTP 和 HTTPS 代理都不可用，请检查配置"
        )
        raise RuntimeError("HTTP 和 HTTPS 代理都不可用，请检查配置")


def _parse_retry_delay_seconds(exc: BaseException) -> float | None:
    match = re.search(r"retry in ([0-9.]+)s", str(exc), re.IGNORECASE)
    if match:
        try:
            return max(float(match.group(1)), 0.5)
        except ValueError:
            pass
    return None


def _is_daily_quota_exhausted(exc: BaseException) -> bool:
    msg = str(exc)
    return "PerDay" in msg or "PerDayPerProjectPerModel" in msg


def _is_retryable_gemini_quota(exc: BaseException) -> bool:
    if not isinstance(exc, gcp_exceptions.ResourceExhausted):
        return False
    if _is_daily_quota_exhausted(exc):
        return False
    return True


def _is_retryable_gemini_timeout(exc: BaseException) -> bool:
    if isinstance(exc, gcp_exceptions.DeadlineExceeded):
        return True
    msg = str(exc)
    return "504" in msg or "Deadline Exceeded" in msg or "DEADLINE_EXCEEDED" in msg


class LLMProvider(LLMProviderBase):
    def __init__(self, cfg: Dict[str, Any]):
        self.model_name = cfg.get("model_name", "gemini-flash-latest")
        raw_fallbacks = cfg.get("fallback_models") or [
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
        ]
        self.fallback_models = [
            m for m in raw_fallbacks
            if isinstance(m, str) and m.strip() and m.strip() != self.model_name
        ]
        self.api_key = cfg["api_key"]
        http_proxy = cfg.get("http_proxy")
        https_proxy = cfg.get("https_proxy")

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            log.bind(tag=TAG).error(model_key_msg)

        if http_proxy or https_proxy:
            log.bind(tag=TAG).info(
                f"检测到Gemini代理配置，开始测试代理连通性和设置代理环境..."
            )
            setup_proxy_env(http_proxy, https_proxy)
            log.bind(tag=TAG).info(
                f"Gemini 代理设置成功 - HTTP: {http_proxy}, HTTPS: {https_proxy}"
            )
        genai.configure(api_key=self.api_key)

        self.timeout = cfg.get("timeout", 120)
        try:
            self.quota_retries = max(0, int(cfg.get("quota_retries", 2)))
        except (TypeError, ValueError):
            self.quota_retries = 2
        try:
            self.timeout_retries = max(0, int(cfg.get("timeout_retries", 2)))
        except (TypeError, ValueError):
            self.timeout_retries = 2

        temperature = cfg.get("temperature", 0.7)
        try:
            temperature = round(float(temperature), 1)
        except (TypeError, ValueError):
            temperature = 0.7

        max_tokens = cfg.get("max_tokens", 2048)
        try:
            max_output_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_output_tokens = 2048

        # Gemini Flash/thinking models spend internal reasoning tokens inside
        # max_output_tokens. Chat-oriented limits (e.g. 120) truncate speech to
        # fragments — apply a Gemini-only floor; other LLM providers unchanged.
        _flash_min = 1024
        model_lc = self.model_name.lower()
        if (
            ("flash" in model_lc or "gemini-2." in model_lc or "gemini-3" in model_lc)
            and max_output_tokens < _flash_min
        ):
            log.bind(tag=TAG).warning(
                f"Gemini max_tokens={max_output_tokens} too low for {self.model_name} "
                f"(thinking shares output budget); raising to {_flash_min}"
            )
            max_output_tokens = _flash_min

        top_p = cfg.get("top_p", 0.9)
        try:
            top_p = round(float(top_p), 2)
        except (TypeError, ValueError):
            top_p = 0.9

        self.model = genai.GenerativeModel(self.model_name)

        self.gen_cfg = GenerationConfig(
            temperature=temperature,
            top_p=top_p,
            top_k=int(cfg.get("top_k", 40)),
            max_output_tokens=max_output_tokens,
        )

        log.bind(tag=TAG).info(
            f"Gemini LLM: model={self.model_name} "
            f"(v1beta generateContent), max_output_tokens={max_output_tokens}"
        )

    @staticmethod
    def _build_tools(funcs: List[Dict[str, Any]] | None):
        if not funcs:
            return None
        return [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=f["function"]["name"],
                        description=f["function"]["description"],
                        parameters=f["function"]["parameters"],
                    )
                    for f in funcs
                ]
            )
        ]

    # Gemini文档提到，无需维护session-id，直接用dialogue拼接而成
    def response(self, session_id, dialogue, **kwargs):
        yield from self._generate(dialogue, None, locale=kwargs.get("locale"))

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        yield from self._generate(
            dialogue, self._build_tools(functions), locale=kwargs.get("locale")
        )

    @staticmethod
    def _resolve_locale(locale: str | None, system_instruction: str | None) -> str | None:
        loc = (locale or "").lower()
        if loc in ("vi", "en"):
            return loc
        if system_instruction:
            if "ACTIVE LOCALE: English" in system_instruction:
                return "en"
            if "ACTIVE LOCALE: Vietnamese" in system_instruction:
                return "vi"
        return None

    @staticmethod
    def _inject_locale_hint(contents: list, locale: str | None) -> None:
        """Gemini-only: reinforce reply language on the latest user turn.

        OpenAI keeps system in messages[] and follows locale well; Gemini uses
        system_instruction and often drifts to Vietnamese from character memory.
        """
        if locale == "en":
            hint = "[Reply in English only for this turn.]"
        elif locale == "vi":
            hint = "[Reply in Vietnamese only for this turn.]"
        else:
            return
        for entry in reversed(contents):
            if entry.get("role") != "user":
                continue
            parts = entry.get("parts") or []
            if not parts:
                continue
            part = parts[0]
            if "text" in part:
                text = str(part["text"] or "")
                if hint not in text:
                    part["text"] = f"{text}\n{hint}".strip()
            break

    def _dialogue_to_contents(self, dialogue):
        """Map OpenAI-style dialogue to Gemini contents + system_instruction."""
        role_map = {"assistant": "model", "user": "user"}
        system_parts: list[str] = []
        contents: list = []

        for m in dialogue:
            r = m["role"]

            if r == "system":
                text = str(m.get("content", "")).strip()
                if text:
                    system_parts.append(text)
                continue

            if r == "assistant" and "tool_calls" in m:
                tc = m["tool_calls"][0]
                contents.append(
                    {
                        "role": "model",
                        "parts": [
                            {
                                "function_call": {
                                    "name": tc["function"]["name"],
                                    "args": json.loads(tc["function"]["arguments"]),
                                }
                            }
                        ],
                    }
                )
                continue

            if r == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": m.get("name", "tool"),
                                    "response": {"result": str(m.get("content", ""))},
                                }
                            }
                        ],
                    }
                )
                continue

            contents.append(
                {
                    "role": role_map.get(r, "user"),
                    "parts": [{"text": str(m.get("content", ""))}],
                }
            )

        system_instruction = "\n\n".join(system_parts).strip() or None
        return system_instruction, contents

    def _generate(self, dialogue, tools, locale=None):
        system_instruction, contents = self._dialogue_to_contents(dialogue)
        active_locale = self._resolve_locale(locale, system_instruction)
        self._inject_locale_hint(contents, active_locale)

        last_exc: BaseException | None = None
        models_to_try = [self.model_name, *self.fallback_models]
        for model_name in models_to_try:
            attempt_model = (
                genai.GenerativeModel(
                    model_name,
                    system_instruction=system_instruction,
                )
                if system_instruction
                else genai.GenerativeModel(model_name)
            )
            for attempt in range(max(self.quota_retries, self.timeout_retries) + 1):
                try:
                    if model_name != self.model_name:
                        log.bind(tag=TAG).warning(
                            f"Gemini falling back to model={model_name}"
                        )
                    yield from self._generate_stream(attempt_model, contents, tools)
                    if model_name != self.model_name:
                        self.model_name = model_name
                        self.model = genai.GenerativeModel(model_name)
                    return
                except gcp_exceptions.NotFound as exc:
                    last_exc = exc
                    log.bind(tag=TAG).warning(
                        f"Gemini model unavailable: {model_name} ({exc})"
                    )
                    break
                except gcp_exceptions.DeadlineExceeded as exc:
                    last_exc = exc
                    if attempt >= self.timeout_retries:
                        raise
                    delay = 2 ** attempt
                    log.bind(tag=TAG).warning(
                        f"Gemini timeout (504), retry {attempt + 1}/"
                        f"{self.timeout_retries} in {delay:.1f}s"
                    )
                    time.sleep(delay)
                except gcp_exceptions.ResourceExhausted as exc:
                    last_exc = exc
                    if _is_daily_quota_exhausted(exc):
                        log.bind(tag=TAG).error(
                            f"Gemini daily quota exceeded for {model_name}. "
                            f"Try another model in GeminiLLM.fallback_models, "
                            f"wait for quota reset, or enable billing in Google AI Studio."
                        )
                        raise
                    if attempt >= self.quota_retries or not _is_retryable_gemini_quota(exc):
                        raise
                    delay = _parse_retry_delay_seconds(exc) or (2 ** attempt)
                    log.bind(tag=TAG).warning(
                        f"Gemini rate-limited (429), retry {attempt + 1}/"
                        f"{self.quota_retries} in {delay:.1f}s"
                    )
                    time.sleep(delay)
                except Exception as exc:
                    if (
                        _is_retryable_gemini_timeout(exc)
                        and attempt < self.timeout_retries
                    ):
                        last_exc = exc
                        delay = 2 ** attempt
                        log.bind(tag=TAG).warning(
                            f"Gemini timeout ({exc}), retry {attempt + 1}/"
                            f"{self.timeout_retries} in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    raise

        if last_exc is not None:
            log.bind(tag=TAG).error(
                f"All Gemini models failed. Tried: {models_to_try}. "
                f"Set GeminiLLM.model_name to one available in Google AI Studio."
            )
            raise last_exc

    def _generate_stream(self, model, contents, tools):
        stream: GenerateContentResponse = model.generate_content(
            contents=contents,
            generation_config=self.gen_cfg,
            tools=tools,
            stream=True,
            request_options={"timeout": self.timeout},
        )

        emitted_chars = 0
        last_finish_reason = None
        try:
            for chunk in stream:
                if not chunk.candidates:
                    continue
                cand = chunk.candidates[0]
                finish_reason = getattr(cand, "finish_reason", None)
                if finish_reason is not None:
                    last_finish_reason = finish_reason

                if not cand.content or not cand.content.parts:
                    continue
                for part in cand.content.parts:
                    # a) 函数调用-通常是最后一段话才是函数调用
                    if getattr(part, "function_call", None):
                        fc = part.function_call
                        yield None, [
                            SimpleNamespace(
                                id=uuid.uuid4().hex,
                                type="function",
                                function=SimpleNamespace(
                                    name=fc.name,
                                    arguments=json.dumps(
                                        dict(fc.args), ensure_ascii=False
                                    ),
                                ),
                            )
                        ]
                        return
                    # b) 普通文本
                    if getattr(part, "text", None):
                        emitted_chars += len(part.text)
                        yield part.text if tools is None else (part.text, None)

        finally:
            self._safe_finish_stream(stream)
            if tools is not None:
                yield None, None  # function‑mode 结束，返回哑包
            elif last_finish_reason is not None:
                fr_name = getattr(last_finish_reason, "name", str(last_finish_reason))
                if "MAX_TOKENS" in fr_name and emitted_chars < 80:
                    log.bind(tag=TAG).warning(
                        f"Gemini reply truncated ({emitted_chars} chars, "
                        f"finish_reason={fr_name}). Increase GeminiLLM.max_tokens "
                        f"(thinking tokens share this budget on flash models)."
                    )

    # 关闭stream，预留后续打断对话功能的功能方法，官方文档推荐打断对话要关闭上一个流，可以有效减少配额计费和资源占用
    @staticmethod
    def _safe_finish_stream(stream: GenerateContentResponse):
        if hasattr(stream, "resolve"):
            stream.resolve()  # Gemini SDK version ≥ 0.5.0
        elif hasattr(stream, "close"):
            stream.close()  # Gemini SDK version < 0.5.0
        else:
            for _ in stream:  # 兜底耗尽
                pass
