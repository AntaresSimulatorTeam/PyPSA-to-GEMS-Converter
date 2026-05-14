# Copyright (c) 2026, RTE (https://www.rte-france.com)
#
# See AUTHORS.txt
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# SPDX-License-Identifier: MPL-2.0
#
# This file is part of the Antares project.

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pypsa import Network

from src.cli import _build_parser, run


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        run(["--help"])
    assert exc_info.value.code == 0


def test_cli_missing_network_file() -> None:
    assert run(["/nonexistent/network.nc", "-o", "/tmp/out"]) == 1


def test_cli_missing_output_flag() -> None:
    with pytest.raises(SystemExit) as exc_info:
        run(["/some/network.nc"])
    assert exc_info.value.code == 2


def test_cli_network_path_is_directory(tmp_path: Path) -> None:
    """A directory is not a valid network file."""
    assert run([str(tmp_path), "-o", str(tmp_path / "out")]) == 1


def test_cli_network_rejects_non_nc_suffix(tmp_path: Path) -> None:
    txt = tmp_path / "model.txt"
    txt.write_text("not a netcdf", encoding="utf-8")
    assert run([str(txt), "-o", str(tmp_path / "out")]) == 1


def test_cli_invalid_netcdf_file(tmp_path: Path) -> None:
    bad = tmp_path / "broken.nc"
    bad.write_bytes(b"not netcdf content")
    assert run([str(bad), "-o", str(tmp_path / "out")]) == 1


def test_cli_invalid_series_format_choice(tmp_path: Path) -> None:
    nc = tmp_path / "x.nc"
    nc.write_bytes(b"x")
    with pytest.raises(SystemExit) as exc_info:
        run([str(nc), "-o", str(tmp_path / "out"), "--series-format", "parquet"])
    assert exc_info.value.code == 2


def test_build_parser_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args(["/in/network.nc", "-o", "/tmp/study"])
    assert args.network == Path("/in/network.nc")
    assert args.output == Path("/tmp/study")
    assert args.series_format == ".tsv"
    assert args.solver == "highs"


def test_build_parser_series_format_aliases() -> None:
    parser = _build_parser()
    for fmt in (".csv", "csv", ".tsv", "tsv"):
        args = parser.parse_args(["/a.nc", "-o", "/out", "--series-format", fmt])
        assert args.series_format == fmt


def test_build_parser_custom_solver() -> None:
    parser = _build_parser()
    args = parser.parse_args(["/a.nc", "-o", "/out", "--solver", "coin"])
    assert args.solver == "coin"


@pytest.fixture
def minimal_netcdf_path(tmp_path: Path) -> Path:
    """Tiny PyPSA network written to NetCDF for CLI integration."""
    network = Network(name="cli_minimal", snapshots=[0, 1, 2])
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus0", v_nom=1, carrier="carrier")
    network.add("Load", "load0", bus="bus0", p_set=50.0, q_set=0.0)
    network.add(
        "Generator",
        "gen0",
        bus="bus0",
        p_nom_extendable=False,
        marginal_cost=40,
        p_nom=100,
        p_max_pu=1.0,
    )
    path = tmp_path / "minimal.nc"
    network.export_to_netcdf(path)
    return path


def test_cli_success_writes_study(tmp_path: Path, minimal_netcdf_path: Path) -> None:
    out = tmp_path / "gems_study"
    code = run(
        [
            str(minimal_netcdf_path),
            "-o",
            str(out),
            "--series-format",
            "csv",
            "--solver",
            "highs",
        ]
    )
    assert code == 0
    assert (out / "systems" / "input" / "system.yml").is_file()
    assert (out / "systems" / "parameters.yml").is_file()


def test_cli_conversion_failure_returns_one(tmp_path: Path, minimal_netcdf_path: Path) -> None:
    out = tmp_path / "gems_study"
    with patch("src.cli.PyPSAStudyConverter", side_effect=RuntimeError("simulated failure")):
        assert run([str(minimal_netcdf_path), "-o", str(out)]) == 1
