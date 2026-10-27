"""AI answer engines: asks each configured engine a tracked prompt, with
web search on, and returns the answer and the sources it cited.

Deliberately multi-provider — visibility has to be measured in each
engine people actually use. Each engine uses the tenant's own key from
the super admin console: openai (ChatGPT), perplexity, anthropic (Claude)."""
from typing import Any

from src.seo.adapters.base import ProviderError, request

CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"


async def ask_chatgpt(prompt: str, creds: dict, config: dict) -> dict:
    resp = await request("POST", "https://api.openai.com/v1/responses", what="OpenAI", timeout=120.0,
                         headers={"Authorization": f"Bearer {creds['api_key']}"}, json={
                             "model": config.get("chatgpt_model") or "gpt-4.1-mini",
                             "tools": [{"type": config.get("openai_search_tool") or "web_search_preview"}], "input": prompt})
    text, urls = [], []
    for item in resp.json().get("output", []):
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text.append(part.get("text", ""))
                urls += [a["url"] for a in part.get("annotations") or [] if a.get("type") == "url_citation" and a.get("url")]
    return {"engine": "chatgpt", "answer": "\n".join(text), "citations": list(dict.fromkeys(urls))}


async def ask_perplexity(prompt: str, creds: dict, config: dict) -> dict:
    resp = await request("POST", "https://api.perplexity.ai/chat/completions", what="Perplexity", timeout=120.0,
                         headers={"Authorization": f"Bearer {creds['api_key']}"}, json={
                             "model": config.get("perplexity_model") or "sonar",
                             "messages": [{"role": "user", "content": prompt}]})
    data = resp.json()
    answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    urls = list(data.get("citations") or []) + [r.get("url") for r in data.get("search_results") or [] if r.get("url")]
    return {"engine": "perplexity", "answer": answer, "citations": list(dict.fromkeys(urls))}


async def ask_claude(prompt: str, creds: dict, config: dict) -> dict:
    """Claude with the server-side web search tool, through the official
    SDK. A turn the server pauses is resumed; a refusal counts as no
    answer."""
    import anthropic

    headers = {"anthropic-workspace-id": creds["workspace_id"]} if creds.get("workspace_id") else None
    client = anthropic.AsyncAnthropic(api_key=creds["api_key"], default_headers=headers, timeout=180.0)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    try:
        for _ in range(3):
            message = await client.beta.messages.create(
                model=config.get("claude_model") or creds.get("model") or CLAUDE_DEFAULT_MODEL,
                max_tokens=4000,
                output_config={"effort": "low"},
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
                messages=messages,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if message.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": message.content}]
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"could not reach Anthropic ({type(exc).__name__})") from exc
    except anthropic.APIStatusError as exc:
        raise ProviderError(f"Anthropic answered HTTP {exc.status_code}: {exc.message}") from exc
    finally:
        await client.close()
    if message.stop_reason == "refusal":
        return {"engine": "claude", "answer": "", "citations": []}
    text, urls = [], []
    for block in message.content:
        if block.type == "text":
            text.append(block.text)
            urls += [c.url for c in (block.citations or []) if getattr(c, "url", None)]
        elif block.type == "web_search_tool_result" and isinstance(block.content, list):
            urls += [r.url for r in block.content if getattr(r, "url", None)]
    return {"engine": "claude", "answer": "\n".join(text), "citations": list(dict.fromkeys(urls))}


ENGINES = {"chatgpt": ("openai", ask_chatgpt), "perplexity": ("perplexity", ask_perplexity), "claude": ("anthropic", ask_claude)}
