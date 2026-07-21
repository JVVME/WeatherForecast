from pathlib import Path

import pytest

from weather_ai.config import ConfigError, load_config


def test_load_config_parses_and_normalizes_values(tmp_path: Path) -> None:
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        """
project:
  name: test-weather
  timezone: Asia/Shanghai
logging:
  level: debug
  format: text
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.project.name == "test-weather"
    assert config.project.timezone == "Asia/Shanghai"
    assert config.logging.level == "DEBUG"
    assert config.logging.format == "text"


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        "project: {name: weather-ai, timezone: UTC, typo: true}",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"unknown key\(s\) in project: typo"):
        load_config(config_path)
