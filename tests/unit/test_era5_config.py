from pathlib import Path

import pytest

from weather_ai.config import ConfigError
from weather_ai.data.config import load_era5_download_config


def _write_config(
    tmp_path: Path,
    *,
    north: float = 31.75,
    south: float = 30.75,
    variables: str = "  - 2m_temperature\n  - surface_pressure",
) -> Path:
    path = tmp_path / "era5.yaml"
    path.write_text(
        f"""
scope: sample
dataset: reanalysis-era5-single-levels
year: 2024
month: 2
area:
  id: shanghai
  north: {north}
  west: 120.75
  south: {south}
  east: 122.0
variables:
{variables}
output:
  format: netcdf
  directory: {tmp_path.as_posix()}/raw
  manifest: {tmp_path.as_posix()}/manifests/downloads.json
""".strip(),
        encoding="utf-8",
    )
    return path


def test_valid_era5_sample_config_is_parsed(tmp_path: Path) -> None:
    config = load_era5_download_config(_write_config(tmp_path))

    assert config.scope == "sample"
    assert config.dataset == "reanalysis-era5-single-levels"
    assert config.year == 2024
    assert config.month == 2
    assert config.area.as_cds_list() == [31.75, 120.75, 30.75, 122.0]
    assert config.variables == ("2m_temperature", "surface_pressure")
    assert config.output.format == "netcdf"


def test_invalid_area_bounds_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"area\.north must be greater"):
        load_era5_download_config(_write_config(tmp_path, north=30.0, south=31.0))


def test_empty_variable_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="variables must not be empty"):
        load_era5_download_config(_write_config(tmp_path, variables="  []"))


def test_total_precipitation_is_outside_m1_a(tmp_path: Path) -> None:
    variables = "  - 2m_temperature\n  - total_precipitation"

    with pytest.raises(ConfigError, match="outside M1-A"):
        load_era5_download_config(_write_config(tmp_path, variables=variables))
