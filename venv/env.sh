#!/usr/bin/env bash
# shellcheck shell=bash

_keystrel_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${KEYSTREL_VENV_DIR:-}" ]]; then
  export VIRTUAL_ENV="$KEYSTREL_VENV_DIR"
elif [[ -n "${STT_VENV_DIR:-}" ]]; then
  export VIRTUAL_ENV="$STT_VENV_DIR"
elif [[ -x "$_keystrel_env_dir/bin/python" ]]; then
  export VIRTUAL_ENV="$_keystrel_env_dir"
else
  export VIRTUAL_ENV="$HOME/.venvs/faster-whisper"
fi

if [[ ! -x "$VIRTUAL_ENV/bin/python" ]]; then
  printf '[keystrel-env] expected python at %s\n' "$VIRTUAL_ENV/bin/python" >&2
  printf '[keystrel-env] set KEYSTREL_VENV_DIR (or legacy STT_VENV_DIR) to your faster-whisper venv path\n' >&2
  return 2 2>/dev/null || exit 2
fi

export PATH="$VIRTUAL_ENV/bin:$PATH"

_keystrel_site_packages=""
for candidate in "$VIRTUAL_ENV"/lib/python*/site-packages; do
  if [[ -d "$candidate" ]]; then
    _keystrel_site_packages="$candidate"
    break
  fi
done

_keystrel_cuda_libs=""
if [[ -n "$_keystrel_site_packages" ]]; then
  if [[ -d "$_keystrel_site_packages/nvidia/cublas/lib" ]]; then
    _keystrel_cuda_libs="$_keystrel_site_packages/nvidia/cublas/lib"
  fi
  if [[ -d "$_keystrel_site_packages/nvidia/cudnn/lib" ]]; then
    _keystrel_cuda_libs="${_keystrel_cuda_libs:+$_keystrel_cuda_libs:}$_keystrel_site_packages/nvidia/cudnn/lib"
  fi
fi

if [[ -n "$_keystrel_cuda_libs" ]]; then
  export LD_LIBRARY_PATH="$_keystrel_cuda_libs:${LD_LIBRARY_PATH:-}"
fi

unset _keystrel_env_dir
unset _keystrel_site_packages
unset _keystrel_cuda_libs
