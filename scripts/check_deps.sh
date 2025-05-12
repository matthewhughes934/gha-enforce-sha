#!/usr/bin/env bash

set -o errexit -o pipefail -o nounset

HERE="$(cd -- "$(dirname "$0")" >/dev/null 2>&1 && pwd -P)"

cd "$HERE/.."
exec py-unused-deps --distribution gha-enforce-sha gha_enforce_sha
