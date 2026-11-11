"""Runtime-neutral parsing of exact structured role results."""
import json


def parse_result(message):
    text = message.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Subagent result must be a JSON object")
    return value
