#!/usr/bin/env bash
# Scaffold: a small module with three planted bugs.
set -euo pipefail
cat > inventory.py <<'PY'
import json


def add_item(item, items=[]):
    items.append(item)
    return items


def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}


def average_price(prices):
    return sum(prices) / len(prices)
PY
