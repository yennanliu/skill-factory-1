from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from skill_factory.statusline import install_script, plan_settings

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hooks" / "sf_statusline.py"
DATA = {
    "session_id": "s1",
    "model": {"display_name": "Opus 5.5"},
    "cost": {"total_cost_usd": 0.42},
    "context_window": {"used_percentage": 31.4},
}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sf_statusline", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    (tmp_path / "telemetry").mkdir()
    (tmp_path / "telemetry" / "events.jsonl").write_text(
        json.dumps({"session_id": "s1", "event": "skill_invoked", "skill": "lc-add"})
        + "\n"
        + json.dumps({"session_id": "s2", "event": "skill_invoked", "skill": "other"})
        + "\n"
        + "torn {\n"
    )
    return tmp_path


def test_render_without_budget(home: Path) -> None:
    assert load().render(DATA) == "$0.42 · Opus 5.5 · ctx 31% · lc-add"


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (1.0, "$0.42"),
        (0.8, "\033[33m$0.42\033[0m"),
        (0.5, "\033[31m$0.42\033[0m"),
        (0.4, "\033[31m$0.42!\033[0m"),
    ],
)
def test_budget_colours(home: Path, budget: float, expected: str) -> None:
    (home / "config.json").write_text(json.dumps({"session_budget_usd": budget}))
    assert load().render(DATA).split(" · ")[0] == expected


def test_no_colour_still_flags_overspend(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({"session_budget_usd": 0.4}))
    assert load().render(DATA, use_colour=False).startswith("$0.42! ·")


def test_missing_fields_and_bad_input(home: Path) -> None:
    assert load().render({}) == ""
    out = subprocess.run(
        [sys.executable, str(SCRIPT)], input="not json", text=True, capture_output=True, timeout=10
    )
    assert out.returncode == 0 and out.stdout == "\n"


def test_install_writes_statusline_and_keeps_other_settings(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "env": {"A": "1"}}))
    script = install_script(ROOT, tmp_path / "sfhome")
    assert script.is_file() and script.stat().st_mode & 0o111
    _old, new, data = plan_settings(settings, script)
    assert data["model"] == "opus" and data["env"] == {"A": "1"}
    assert data["statusLine"] == {"type": "command", "command": f'python3 "{script}"'}
    settings.write_text(new)
    assert plan_settings(settings, script)[0] == plan_settings(settings, script)[1]  # idempotent
    assert plan_settings(tmp_path / "missing.json", script)[0] == "{}\n"


def test_last_skill_prefers_session_state(home: Path) -> None:
    (home / "telemetry" / "sessions").mkdir()
    (home / "telemetry" / "sessions" / "s1.json").write_text(json.dumps({"last_skill": "fresh"}))
    assert load().render(DATA).endswith("· fresh")


def test_install_is_idempotent_across_formatting_and_keeps_unicode(tmp_path: Path) -> None:
    script = tmp_path / "sf_statusline.py"
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"note": "中文"}, ensure_ascii=False))
    _, new, _ = plan_settings(settings, script)
    assert "中文" in new
    settings.write_text(json.dumps(json.loads(new), indent=4, ensure_ascii=False))  # reformatted
    old2, new2, _ = plan_settings(settings, script)
    assert old2 == new2


def test_invalid_settings_is_a_clean_error(tmp_path: Path) -> None:
    from skill_factory.statusline import SettingsError

    bad = tmp_path / "settings.json"
    bad.write_text("{trailing,}")
    with pytest.raises(SettingsError, match="not valid JSON"):
        plan_settings(bad, tmp_path / "s.py")
    bad.write_text("[]")
    with pytest.raises(SettingsError, match="JSON object"):
        plan_settings(bad, tmp_path / "s.py")
