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

import logging
from pathlib import Path

import pytest
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)


def test_converter_deterministic_study() -> None:
    logger.info("Running test_converter_deterministic_study")
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")
    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)
    network.add(
        "Load",
        "timeseries_load",
        bus="bus 1",
        p_set=[100 + 10 * i for i in range(10)],
        q_set=[20 + 5 * i for i in range(10)],
    )
    network.add(
        "Generator",
        "gen1",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=50,
        p_nom=200,
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],
    )
    network.add(
        "Generator",
        "gen2",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=50,
        p_nom=200,
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],
    )
    network.add("Generator", "gen3", bus="bus 1", p_nom_extendable=False, marginal_cost=50, p_nom=200, p_max_pu=0.9)

    PyPSAStudyConverter(network, Path("tmp") / "test_one", "csv").to_gems_study()
    logger.info("Converted deterministic study to test_one")

    # test if optimi-config isn't generated
    assert not (Path("tmp") / "test_one" / "systems" / "input" / "optim-config.yml").exists()

    network.set_scenarios({"low": 0.5, "high": 0.5})
    PyPSAStudyConverter(network, Path("tmp") / "test_two", "csv", solver_name="coin").to_gems_study()
    logger.info("Converted scenario study to test_two")

    # The GEMS study + optim-config.yml drive the Antares-Xpansion launcher
    assert (Path("tmp") / "test_two" / "systems" / "input" / "optim-config.yml").exists()
    settings_ini = Path("tmp") / "test_two" / "systems" / "user" / "expansion" / "settings.ini"
    assert settings_ini.exists()
    settings_text = settings_ini.read_text(encoding="utf-8")
    assert "yearly-weights" in settings_text
    weights_file = Path("tmp") / "test_two" / "systems" / "user" / "expansion" / "weights" / "weights.txt"
    assert weights_file.exists()
    weights = [float(x) for x in weights_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(weights) == 2
    assert sum(weights) == pytest.approx(1.0)


def test_converter_multiscenario_rejects_unsupported_xpansion_solver() -> None:
    network = Network(name="Simple_Network", snapshots=range(10))
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")
    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)
    network.add("Generator", "gen1", bus="bus 1", p_nom_extendable=False, marginal_cost=50, p_nom=200, p_max_pu=0.9)
    network.set_scenarios({"low": 0.5, "high": 0.5})

    with pytest.raises(ValueError, match="coin'.*xpress"):
        PyPSAStudyConverter(
            network, Path("tmp") / "test_two_invalid_solver", "csv", solver_name="highs"
        ).to_gems_study()
