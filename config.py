from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from pydantic import BaseModel, ConfigDict
from utils import atomic_json_write, read_json_or_empty

CONFIG_DIR_NAME = "memento"
CONFIG_FILE_NAME = "config.json"
CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "api_url",
        "description": "Hindsight API URL",
        "default": "http://hindsight:8888",
        "required": True,
    },
    {
        "key": "api_key",
        "description": "Hindsight API Key (optional)",
        "env_var": "MEMENTO_API_KEY",
        "secret": True,
    },
    {
        "key": "bank_id",
        "description": "Memory bank name",
        "default": "hermes",
        "required": True,
    },
    {
        "key": "agent_id",
        "description": "Default agent identity",
    },
    {
        "key": "min_score",
        "description": "Recall min score",
        "default": 0.2,
    },
    {
        "key": "recall_n",
        "description": "Recall N records",
        "default": 10,
    },
    {
        "key": "do_not_retain",
        "description": "Do not retain (platforms, comma-separated)",
        "default": "cron",
    },
]


class MementoConfig(BaseModel):
    model_config = ConfigDict()
    api_url: str
    bank_id: str
    api_key: str = ""
    agent_id: str = "default"
    min_score: float = 0.2
    recall_n: int = 10
    do_not_retain: str = "cron"


def memento_config_path() -> Path:
    return Path(get_hermes_home()) / "memento" / "config.json"


def memento_load_config() -> MementoConfig:
    return MementoConfig.model_validate(read_json_or_empty(memento_config_path()))


def memento_save_config(values: dict[str, Any]) -> None:
    config_path = memento_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(config_path, values, mode=0o600)
