"""`market.yaml` — marketplace identity and per-plugin versions (plugins/ itself is generated)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from skill_factory.models.spec import Kebab, SemVer


class PluginDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: SemVer = "0.1.0"
    description: str = ""


class Market(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Kebab
    owner: str
    repository: str  # "<owner>/<repo>" on GitHub, used in install instructions
    description: str = ""
    plugins: dict[str, PluginDef] = {}
