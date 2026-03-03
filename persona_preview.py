import os
import sys


def load_prompt_file(path: str = "bot_persona.txt") -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"Missing persona file: {path}")
    with open(path, "r", encoding="utf-8-sig") as prompt_file:
        return prompt_file.read().strip()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    persona = load_prompt_file("bot_persona.txt")
    print(persona)
