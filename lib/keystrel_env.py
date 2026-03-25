#!/usr/bin/env python3

import os
import sys


def parse_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def env_candidates(name):
    if name.startswith("KEYSTREL_"):
        return (name, f"STT_{name.removeprefix('KEYSTREL_')}")
    return (name,)


def get_env(name, default=None, warned=None, prefix="keystrel"):
    candidates = env_candidates(name)
    primary_name = candidates[0]
    warned_set = warned if warned is not None else set()

    for candidate in candidates:
        raw = os.environ.get(candidate)
        if raw is not None and str(raw).strip():
            if candidate != primary_name and candidate not in warned_set:
                print(
                    f"[{prefix}] {candidate} is deprecated; use {primary_name} instead",
                    file=sys.stderr,
                )
                warned_set.add(candidate)
            return raw

    return default


def parse_env_int(name, default, get_env_func, prefix):
    raw = get_env_func(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[{prefix}] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_float(name, default, get_env_func, prefix):
    raw = get_env_func(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[{prefix}] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_bool(name, default, get_env_func, prefix):
    raw = get_env_func(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return parse_bool(raw)
    except ValueError:
        print(f"[{prefix}] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_choice(name, default, choices, get_env_func, prefix):
    raw = get_env_func(name)
    if raw is None or not str(raw).strip():
        return default

    value = str(raw).strip().lower()
    if value in choices:
        return value

    print(
        f"[{prefix}] invalid {name}={raw!r}, using default {default}",
        file=sys.stderr,
    )
    return default
