#!/usr/bin/env python3
"""
vision_analyze skill handler — v1.0.0

Analyse an image using a vision-capable LLM configured in providers.yaml.
Supports local file paths (encoded as base64 data URLs) and public https:// URLs.

Primary use case: ba_agent analysing BPMN diagrams, UML charts, process models,
and other non-text visual artifacts (G6 from KnowledgeBase_design.md).

Input:  {"parameters": {"image": "path/or/url", "question": "...", "context": "..."}}
Output: {"success": true, "result": {"analysis": "...", "model_used": "...", "image_source": "url|file"}}

Design reference: KnowledgeBase_design.md §14 G6
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Provider config loading (mirrors logic in provider_config.py)
# ---------------------------------------------------------------------------

def _load_vision_model() -> tuple[str, str, str, dict]:
    """
    Return (model_id, base_url, api_key, extra_headers) for the vision preference.

    Reads providers.yaml, falls back to reasoning model if vision slot is absent.
    Falls back to a hard-coded default if YAML is unreadable.
    """
    default_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    default_key = os.environ.get("NANO_GPT_API_KEY", "")
    fallback_model = "Qwen/Qwen2-VL-72B-Instruct"

    try:
        import yaml  # type: ignore
        config_path = Path(__file__).parent.parent.parent / "config" / "providers.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        active = os.environ.get("ACTIVE_PROVIDER", cfg.get("active", "nano_gpt"))
        provider = cfg.get("providers", {}).get(active, {})

        base_url = (
            os.environ.get(provider.get("base_url_env", ""), "").strip()
            or provider.get("base_url", default_base)
        )
        api_key_env = provider.get("api_key_env", "NANO_GPT_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        extra_headers = provider.get("extra_headers", {}) or {}

        prefs = provider.get("agent_preferences", {})
        # Prefer vision slot; fall back to reasoning
        models = prefs.get("vision") or prefs.get("reasoning") or []
        model_id = models[0] if models else fallback_model

        return model_id, base_url, api_key, extra_headers

    except Exception as exc:
        sys.stderr.write(f"Warning: could not load provider config: {exc}\n")
        return fallback_model, default_base, default_key, {}


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def _load_image(image: str) -> tuple[str, str]:
    """
    Resolve *image* to an OpenAI-compatible image_url value and source type.

    Returns (image_url_value, source_type) where:
      - image_url_value is either a public https:// URL or a data: URI
      - source_type is 'url' or 'file'
    """
    if image.startswith("https://") or image.startswith("http://"):
        return image, "url"

    # Local file — encode as base64 data URI
    path = Path(image)
    if not path.is_absolute():
        # Try relative to project root (two levels up from this file's src/)
        project_root = Path(__file__).parent.parent.parent.parent
        path = project_root / image

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {image}")

    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime_type};base64,{b64}", "file"


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    image = params.get("image", "").strip()
    if not image:
        raise ValueError("'image' parameter is required.")

    question = params.get("question") or "Describe the content of this image in detail."
    context: Optional[str] = params.get("context") or None

    model_id, base_url, api_key, extra_headers = _load_vision_model()

    image_url_value, image_source = _load_image(image)

    # Build user message content
    user_content: list = []
    if context:
        user_content.append({"type": "text", "text": f"Context: {context}\n\n{question}"})
    else:
        user_content.append({"type": "text", "text": question})

    user_content.append({
        "type": "image_url",
        "image_url": {"url": image_url_value},
    })

    messages = [{"role": "user", "content": user_content}]

    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **extra_headers,
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 1024,
    }

    resp = httpx.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    analysis = data["choices"][0]["message"]["content"].strip()

    return {
        "analysis": analysis,
        "model_used": model_id,
        "image_source": image_source,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
