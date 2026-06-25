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

from pypsa import Network

from src.utils import set_pypsa_scenario_weights


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
