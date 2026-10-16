"""Secrets (mail password, calendar URL): environment, then OS keyring, then a plaintext file as a last resort.

The file fallback (DESIGN section 6, decision 2026-10-10) is user-only where the OS supports it, but NOT encrypted;
callers surface the returned backend name so the user is warned.
"""
import json
import os

from . import config

SERVICE = "commandcenter"
FILE_NAME = "secrets.json"


def _keyring():
    """The keyring module when it has a working backend, else None."""
    try:
        import keyring
        from keyring.backends import fail
        return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring
    except Exception:
        return None


def _file():
    return config.data_dir() / FILE_NAME


def _read_file():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_file(d):
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def backend():
    return "keyring" if _keyring() else "file"


def get(name):
    """Value or None. Environment CC_SECRET_<NAME> wins, for CI and headless runs."""
    v = os.environ.get("CC_SECRET_" + name.upper())
    if v:
        return v
    kr = _keyring()
    if kr:
        v = kr.get_password(SERVICE, name)
        if v:
            return v
    return _read_file().get(name)


def set(name, value):
    """Store a secret; returns the backend used ("keyring" or "file")."""
    if not value:
        raise ValueError("empty secret")
    kr = _keyring()
    if kr:
        kr.set_password(SERVICE, name, value)
        return "keyring"
    d = _read_file()
    d[name] = value
    _write_file(d)
    return "file"


def delete(name):
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass
    d = _read_file()
    if d.pop(name, None) is not None:
        _write_file(d)
