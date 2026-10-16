"""Prompt files: cc/ai/prompts/<task>.md plus _common.md (rules shared by every task). The text is fixed; only the
fenced input blocks vary, so the same inputs always give the same prompt."""
from functools import lru_cache
from importlib import resources

MAX_BYTES = 6 * 1024


@lru_cache(maxsize=None)
def _read(name):
    data = (resources.files("cc.ai") / "prompts" / f"{name}.md").read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError(f"prompt {name} is larger than {MAX_BYTES} bytes")
    return data.decode("utf-8")


def load(task_name):
    """Shared rules, then the task's own instructions. Only registered task names are accepted."""
    from .registry import TASKS
    if task_name not in TASKS:
        raise ValueError(f"unknown task {task_name!r}")
    return _read("_common").rstrip() + "\n\n" + _read(TASKS[task_name].prompt_file[:-3]).rstrip()
