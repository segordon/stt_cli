#!/usr/bin/env bash
# shellcheck shell=bash

_stt_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VIRTUAL_ENV="$_stt_env_dir"
export PATH="$VIRTUAL_ENV/bin:$PATH"

_stt_site_packages=""
for candidate in "$VIRTUAL_ENV"/lib/python*/site-packages; do
  if [[ -d "$candidate" ]]; then
    _stt_site_packages="$candidate"
    break
  fi
done

_stt_cuda_libs=""
if [[ -n "$_stt_site_packages" ]]; then
  if [[ -d "$_stt_site_packages/nvidia/cublas/lib" ]]; then
    _stt_cuda_libs="$_stt_site_packages/nvidia/cublas/lib"
  fi
  if [[ -d "$_stt_site_packages/nvidia/cudnn/lib" ]]; then
    _stt_cuda_libs="${_stt_cuda_libs:+$_stt_cuda_libs:}$_stt_site_packages/nvidia/cudnn/lib"
  fi
fi

if [[ -n "$_stt_cuda_libs" ]]; then
  export LD_LIBRARY_PATH="$_stt_cuda_libs:${LD_LIBRARY_PATH:-}"
fi

unset _stt_env_dir
unset _stt_site_packages
unset _stt_cuda_libs
