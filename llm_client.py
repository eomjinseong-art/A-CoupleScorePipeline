"""수동 스크립트용 LLM 클라이언트.

예약 워크플로(upload.yml)는 이 모듈을 호출하지 않는다.
GEMINI_API_KEY가 있으면 Gemini OpenAI 호환 엔드포인트(gemini-2.5-flash)를 쓰고,
없을 때만 OPENAI_API_KEY로 OpenAI를 호출한다.
"""
import json
import os

import requests

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_BASE_URL = "https://api.openai.com/v1"


def resolve_chat(default_openai_model=None):
    """키가 없으면 None. Gemini가 있으면 OpenAI 키는 무시한다."""
    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini_key:
        model = (os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        return {
            "provider": "gemini",
            "api_key": gemini_key,
            "model": model,
            "base_url": GEMINI_BASE_URL,
        }
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openai_key:
        fallback = default_openai_model or DEFAULT_OPENAI_MODEL
        model = (os.environ.get("OPENAI_MODEL") or fallback).strip() or fallback
        return {
            "provider": "openai",
            "api_key": openai_key,
            "model": model,
            "base_url": OPENAI_BASE_URL,
        }
    return None


def chat_completion(messages, max_tokens=4000, temperature=0.7, response_format=None, timeout=120, default_openai_model=None):
    """OpenAI 호환 chat completions. 키가 없으면 (None, None)."""
    spec = resolve_chat(default_openai_model=default_openai_model)
    if not spec:
        return None, None
    body = {
        "model": spec["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format
    resp = requests.post(
        spec["base_url"].rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {spec['api_key']}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    return resp, spec


def completion_text(resp):
    return resp.json()["choices"][0]["message"]["content"] or ""


def parse_json_content(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)
