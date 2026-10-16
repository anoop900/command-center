"""Where the page, extension and demo seed live: inside the installed package (built wheel) or beside it (a repo checkout)."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Names inside the package -> where they sit in a checkout.
REPO = {"web": "web", "extension": "extension", "demo": "demo/seed"}


def find(name):
    if name not in REPO:
        raise KeyError(name)
    packed = HERE / "_assets" / name
    return packed if packed.is_dir() else HERE.parent / REPO[name]
