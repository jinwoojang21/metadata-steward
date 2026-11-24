"""Generic Claude tool-use loop shared by the documentation and Q&A agents."""

from __future__ import annotations

from typing import Any

from .tools import Toolbox


def run_loop(toolbox: Toolbox, *, system: str, tools: list[dict[str, Any]], brief: str, model: str,
             max_turns: int, client: Any | None = None) -> dict[str, Any]:
    """Run until the terminal tool sets `toolbox.done` or the turn budget runs out.

    Returns usage stats; the result itself lives on the toolbox (catalog writes / `done`).
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": brief}]
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0, "completed": False}
    nudged = False
    for turn in range(max_turns):
        resp = client.messages.create(model=model, max_tokens=8192, system=system, tools=tools,
                                      messages=messages)
        usage["turns"] = turn + 1
        if getattr(resp, "usage", None):
            usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
            usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Please continue with tool calls until you call the "
                                                        "final tool."})
            continue
        results = []
        for b in uses:
            text, is_error = toolbox.call(b.name, b.input)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
        if toolbox.done is not None:
            usage["completed"] = True
            break
    return usage
