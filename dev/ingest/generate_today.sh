#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 PROFILE_JSON OUTPUT_DIR" >&2
  exit 2
fi
python3 "$(dirname "$0")/generate_now.py" --profile "$1" --output-dir "$2"
