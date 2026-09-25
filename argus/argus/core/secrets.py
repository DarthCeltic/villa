"""Credentials in the OS keyring, and never anywhere a value can be read back."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from importlib import import_module

SERVICE = "argus"
STATE = Path(_argus_public_path('home', 'state/secret_status.json'))
_DEFAULT_STATE = STATE


def _state_path() -> Path:
    """The status file (presence and freshness only -- never a value)."""
    if STATE != _DEFAULT_STATE:
        return STATE
    try:
        from argus.core import paths as _paths
        try:
            _ud = import_module("argus.core.user_data")
        except ModuleNotFoundError as exc:
            if exc.name == "argus.core.user_data":
                return _DEFAULT_STATE
            raise
        try:
            return _ud.resolve("secret_status", allow_absent=True).path
        except (_ud.UserDataError, _paths.PathContractViolation) as exc:
            raise SecretRefusal("the secret status file cannot be resolved: %s" % exc)
    except _paths.PathContractViolation as exc:
        raise SecretRefusal("the secret status file cannot be resolved: %s" % exc)

PROVIDERS = {
    "github": ("GITHUB_TOKEN", "https://api.github.com/user", "Bearer %s"),
    "huggingface": ("HF_TOKEN", "https://huggingface.co/api/whoami-v2", "Bearer %s"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/key", "Bearer %s"),
    "aws": ("AWS_ACCESS_KEY_ID", None, None),
}


class SecretRefusal(Exception):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretRefusal(
            "keyring is not installed, and this module will not fall back to a file. A "
            "secret in a file this process wrote is a secret in a backup, a screenshot and "
            "a support bundle: %s" % exc)
    return keyring


def _load_status() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_status(d: dict) -> None:
    state = _state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(state)


def set_secret(provider: str, value: str, *, actor: str) -> dict:
    """Store it."""
    if provider not in PROVIDERS:
        raise SecretRefusal("unknown provider %r; known: %s" % (provider, sorted(PROVIDERS)))
    if not value or len(value) < 8:
        raise SecretRefusal("that is too short to be a credential; nothing was stored")
    try:
        _keyring().set_password(SERVICE, provider, value)
    except Exception as exc:
        raise SecretRefusal("the OS credential manager could not store this credential (%s)"
                            % type(exc).__name__) from exc
    st = _load_status()
    st[provider] = {"configured": True, "length": len(value),
                    "set_by": actor,
                    "set_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "last_validated": None, "last_validation_result": None}
    _save_status(st)
    return {"provider": provider, "configured": True, "length": len(value), "value": None}


def remove(provider: str, *, actor: str) -> dict:
    """Delete, then READ BACK."""
    if provider not in PROVIDERS:
        raise SecretRefusal("unknown provider %r" % provider)
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, provider)
    except Exception:
        pass
    still = kr.get_password(SERVICE, provider)
    if still is not None:
        raise SecretRefusal("the keyring still holds %r after a delete" % provider)
    st = _load_status()
    st[provider] = {"configured": False, "removed_by": actor,
                    "removed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save_status(st)
    return {"provider": provider, "configured": False, "confirmed_absent": True}


def validate(provider: str) -> dict:
    """One cheap read-only call."""
    if provider not in PROVIDERS:
        raise SecretRefusal("unknown provider %r" % provider)
    env, url, header = PROVIDERS[provider]
    if url is None:
        return {"provider": provider, "validated": None,
                "why": "no cheap read-only validation endpoint is configured for this provider"}
    secret = _keyring().get_password(SERVICE, provider)
    if not secret:
        return {"provider": provider, "validated": False, "why": "nothing stored"}
    req = urllib.request.Request(url)
    req.add_header("Authorization", header % secret)
    req.add_header("User-Agent", "argus-secret-validate")
    ok, detail, status = False, None, None
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            ok = 200 <= r.status < 300
            detail = "reachable"
    except urllib.error.HTTPError as exc:
        status, detail = exc.code, "rejected"
    except Exception as exc:
        detail = str(exc)[:160].replace(secret, "<redacted>")
    st = _load_status()
    st.setdefault(provider, {})
    st[provider].update({"configured": True,
                         "last_validated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "last_validation_result": ("ok" if ok else (detail or "failed")),
                         "http": status})
    _save_status(st)
    return {"provider": provider, "validated": ok, "http": status, "detail": detail,
            "value": None}


def rotate(provider: str, new_value: str, *, actor: str) -> dict:
    """Set the new one, validate it, and only then consider the old one replaced."""
    kr = _keyring()
    previous = kr.get_password(SERVICE, provider)
    set_secret(provider, new_value, actor=actor)
    v = validate(provider)
    if v.get("validated") is False and previous is not None:
        kr.set_password(SERVICE, provider, previous)
        raise SecretRefusal(
            "the new credential did not validate (%s); the previous one was restored"
            % v.get("detail"))
    return {"provider": provider, "rotated": True, "validated": v.get("validated"),
            "value": None}


def status() -> dict:
    """Presence, scope and freshness."""
    kr = None
    try:
        kr = _keyring()
    except SecretRefusal:
        pass
    st = _load_status()
    rows = []
    for name, (env, url, _) in sorted(PROVIDERS.items()):
        stored = None
        if kr is not None:
            try:
                stored = kr.get_password(SERVICE, name)
            except Exception:
                stored = None
        rec = dict(st.get(name) or {})
        rec.update({"provider": name, "env_var": env,
                    "in_keyring": stored is not None,
                    "length": len(stored) if stored else 0,
                    "validatable": url is not None,
                    "value": None})
        rows.append(rec)
    return {"backend": (kr.get_keyring().__class__.__name__ if kr else "unavailable"),
            "service": SERVICE, "providers": rows,
            "value_ever_returned": False,
            "storage_rule": ("OS credential manager only. Not Git, not a dotfile, not browser "
                             "storage, not a response body. Only .env.example ships")}
