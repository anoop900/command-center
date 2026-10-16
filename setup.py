"""Wheels carry the page, extension and demo seed inside the package (cc/_assets), which pyproject cannot do from outside it."""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

SRC = {"web": "web", "extension": "extension", "demo": "demo/seed"}
SKIP = shutil.ignore_patterns("test", "tools", "profile.json", "__pycache__")


class Build(build_py):
    def run(self):
        super().run()
        here = Path(__file__).resolve().parent
        for name, rel in SRC.items():
            dest = Path(self.build_lib) / "cc" / "_assets" / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(here / rel, dest, ignore=SKIP)


setup(cmdclass={"build_py": Build})
