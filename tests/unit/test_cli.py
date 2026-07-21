from pathlib import Path

import pytest

from weather_ai.cli import main


def test_cli_displays_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert "usage: weather-ai" in captured.out
    assert "config" in captured.out


def test_cli_validates_and_displays_config(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        "project: {name: weather-ai, timezone: UTC}\nlogging: {level: INFO, format: json}\n",
        encoding="utf-8",
    )

    exit_code = main(["config", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"name": "weather-ai"' in captured.out
    assert '"event": "configuration_loaded"' in captured.err
