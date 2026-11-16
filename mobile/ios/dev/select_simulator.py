#!/usr/bin/env python3
"""Choose one available iPhone simulator from simctl's JSON output."""

import json
import re
import sys

devices = json.load(sys.stdin)["devices"]

def runtime_version(name):
    match = re.search(r"iOS-(\d+)(?:-(\d+))?", name)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else (999, 999)


# Hosted runners often include a newly installed runtime that spends several
# minutes migrating on first boot. Prefer an older supported iOS runtime.
for runtime in sorted((name for name in devices if runtime_version(name)[0] >= 17),
                      key=runtime_version):
    phones = [device for device in devices[runtime]
              if device.get("isAvailable", False) and device.get("name", "").startswith("iPhone")]
    if phones:
        preferred = next((device for name in ("iPhone 16 Pro", "iPhone 16", "iPhone 15 Pro", "iPhone 15")
                          for device in phones if device["name"] == name), phones[0])
        print(f"Selected {preferred['name']} on {runtime}", file=sys.stderr)
        print(preferred["udid"])
        break
else:
    raise SystemExit("No available iPhone simulator runtime")
