"""Command Center (local-first)."""
__version__ = "1.1.1"


def current_version():
    """The release shown in the sidebar. A release build has its version stamped into __version__; a checkout still
    carries the 0.1.0 placeholder, so it reads the newest section of CHANGELOG.md instead."""
    if __version__ != "0.1.0":
        return __version__
    try:
        import re
        from pathlib import Path
        m = re.search(r"^## (\d+\.\d+\.\d+)", (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else __version__
    except OSError:
        return __version__
