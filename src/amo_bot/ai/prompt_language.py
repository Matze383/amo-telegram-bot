from __future__ import annotations

DEFAULT_RESPONSE_LANGUAGE_RULE = (
    "Antworte standardmäßig auf Deutsch. "
    "Wenn der Nutzer klar eine andere Sprache nutzt oder ausdrücklich eine andere Sprache verlangt, "
    "antworte in dieser Sprache."
)


def build_language_steered_prompt(user_message: str) -> str:
    cleaned = user_message.strip()
    if not cleaned:
        return DEFAULT_RESPONSE_LANGUAGE_RULE
    return f"{DEFAULT_RESPONSE_LANGUAGE_RULE}\n\nNutzeranfrage:\n{cleaned}"
