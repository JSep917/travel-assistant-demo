from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent

@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """按文件名加载提示词，不包含 .md 后缀。"""
    path = PROMPT_DIR / f"{name}.md"

    if not path.exists():
        raise FileNotFoundError(f"提示词不存在: {path}")

    return path.read_text(encoding="utf-8").strip()
