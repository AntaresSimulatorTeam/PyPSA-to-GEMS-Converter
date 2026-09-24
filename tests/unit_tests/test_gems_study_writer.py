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

from pathlib import Path

import yaml

from src.gems_study_writer import GemsStudyWriter


def test_write_optim_config_yml_legacy_copies_flat_schema(tmp_path: Path) -> None:
    writer = GemsStudyWriter(tmp_path, ".csv")
    writer.write_optim_config_yml()

    config = yaml.safe_load((tmp_path / "systems" / "input" / "optim-config.yml").read_text(encoding="utf-8"))
    assert config["resolution-mode"] == "benders-decomposition"
    assert "resolution" not in config


def test_write_optim_config_yml_full_gems_single_scenario(tmp_path: Path) -> None:
    writer = GemsStudyWriter(tmp_path, ".csv")
    writer.write_optim_config_yml(full_gems=True, n_scenarios=1, last_time_step=9, solver_name="highs")

    config = yaml.safe_load((tmp_path / "systems" / "input" / "optim-config.yml").read_text(encoding="utf-8"))
    assert config["time-scope"] == {"first-time-step": 0, "last-time-step": 9}
    assert config["scenario-scope"] == {"include": [0]}
    assert config["solver-options"] == {"name": "highs"}
    assert config["resolution"] == {"mode": "benders-decomposition", "block-length": 168}
    assert {model["id"] for model in config["models"]} == {
        "pypsa_models.generator",
        "pypsa_models.link",
        "pypsa_models.storage_unit",
        "pypsa_models.store",
    }


def test_write_optim_config_yml_full_gems_multi_scenario_covers_all_mc_years(tmp_path: Path) -> None:
    writer = GemsStudyWriter(tmp_path, ".csv")
    writer.write_optim_config_yml(full_gems=True, n_scenarios=5, last_time_step=167, solver_name="xpress")

    config = yaml.safe_load((tmp_path / "systems" / "input" / "optim-config.yml").read_text(encoding="utf-8"))
    assert config["scenario-scope"] == {"include": ["0-4"]}
    assert config["solver-options"] == {"name": "xpress"}
