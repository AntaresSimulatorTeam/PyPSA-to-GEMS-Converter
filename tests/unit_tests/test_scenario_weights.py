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

import json
from pathlib import Path

from pypsa import Network

from src.utils import configure_xpansion_slave_weights, set_pypsa_scenario_weights


def test_set_pypsa_scenario_weights_equal_split() -> None:
    network = Network()
    network.set_scenarios({"low": 1 / 3, "high": 2 / 3})
    weights = set_pypsa_scenario_weights(network)
    assert weights == {"low": 0.5, "high": 0.5}
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_set_pypsa_scenario_weights_three_custom() -> None:
    network = Network()
    network.set_scenarios({"dry": 1 / 3, "normal": 1 / 3, "wet": 1 / 3})
    weights = set_pypsa_scenario_weights(network, {"dry": 0.2, "normal": 0.5, "wet": 0.3})
    assert weights == {"dry": 0.2, "normal": 0.5, "wet": 0.3}
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_configure_xpansion_slave_weights_equal_uses_uniform(tmp_path: Path) -> None:
    (tmp_path / "problem-1-1--optim-nb-1.mps").touch()
    (tmp_path / "problem-2-1--optim-nb-1.mps").touch()
    options_path = tmp_path / "options.json"
    options_path.write_text("{}", encoding="utf-8")

    mode = configure_xpansion_slave_weights(
        tmp_path,
        options_path,
        {"alpha": 0.5, "beta": 0.5},
        scenario_order=["alpha", "beta"],
    )
    assert mode == "UNIFORM"
    options = json.loads(options_path.read_text(encoding="utf-8"))
    assert options["SLAVE_WEIGHT"] == "UNIFORM"


def test_configure_xpansion_slave_weights_three_unequal(tmp_path: Path) -> None:
    for idx in (1, 2, 3):
        (tmp_path / f"problem-{idx}-1--optim-nb-1.mps").touch()
    options_path = tmp_path / "options.json"
    options_path.write_text("{}", encoding="utf-8")

    mode = configure_xpansion_slave_weights(
        tmp_path,
        options_path,
        {"dry": 0.2, "normal": 0.5, "wet": 0.3},
        scenario_order=["dry", "normal", "wet"],
    )
    assert mode == "xpansion_slave_weights.txt"

    weights_text = (tmp_path / "xpansion_slave_weights.txt").read_text(encoding="utf-8")
    assert "0.2" in weights_text
    assert "0.5" in weights_text
    assert "0.3" in weights_text
    assert "WEIGHT_SUM 1" in weights_text

    mapping = json.loads((tmp_path / "xpansion_slave_weights_mapping.json").read_text(encoding="utf-8"))
    assert len(mapping["assignments"]) == 3
