from pathlib import Path

from weather_ai.data.config import load_era5_download_config
from weather_ai.data.request import build_era5_request, build_target_path


def test_request_contains_complete_leap_month_hours_and_area(tmp_path: Path) -> None:
    config = load_era5_download_config(Path("configs/data/era5_sample.yaml"))
    request = build_era5_request(config)

    assert request["year"] == ["2024"]
    assert request["month"] == ["01"]
    assert request["day"] == [f"{day:02d}" for day in range(1, 32)]
    assert request["time"] == [f"{hour:02d}:00" for hour in range(24)]
    assert request["area"] == [31.75, 120.75, 30.75, 122.0]
    assert request["data_format"] == "netcdf"
    assert request["download_format"] == "unarchived"

    target = build_target_path(config, request, working_directory=tmp_path)
    assert target.name.startswith(
        "reanalysis-era5-single-levels_2024-01_shanghai_"
    )
    assert target.suffix == ".nc"


def test_target_filename_is_independent_of_variable_order(tmp_path: Path) -> None:
    config_path = tmp_path / "ordered.yaml"
    config_path.write_text(
        f"""
scope: sample
dataset: reanalysis-era5-single-levels
year: 2024
month: 1
area: {{id: test, north: 1, west: -1, south: -1, east: 1}}
variables: [surface_pressure, 2m_temperature]
output:
  format: netcdf
  directory: {tmp_path.as_posix()}/raw
  manifest: {tmp_path.as_posix()}/manifest.json
""".strip(),
        encoding="utf-8",
    )
    config = load_era5_download_config(config_path)
    request = build_era5_request(config)

    first = build_target_path(config, request, working_directory=tmp_path)
    second = build_target_path(config, build_era5_request(config), working_directory=tmp_path)

    assert first == second
