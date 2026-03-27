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

"""
Simple example: 1 bus, 1 load, 1 generator, 1 battery — 3 time steps.

Network description:
  - 1 bus
  - 1 load:      constant 50 MW over 3 time steps
  - 1 generator: p_nom = 50 MW, marginal cost = 10 €/MWh
  - 1 battery:   p_nom = 40 MW (injection / withdrawal),
                 energy capacity = 40 * 3 = 120 MWh (max_hours = 3)

Run from the repository root:
    python examples/simple_battery_case.py
"""

import logging
from pathlib import Path

from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "simple_battery_case"


def build_network() -> Network:
    network = Network(name="simple_battery", snapshots=[0, 1, 2])

    network.add("Bus", "bus", v_nom=1)

    # Constant load of 50 MW over the 3 time steps
    network.add("Load", "load", bus="bus", p_set=50)

    # Generator: 50 MW capacity, linear marginal cost
    network.add(
        "Generator",
        "generator",
        bus="bus",
        p_nom=50,
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
    )

    # Battery: 40 MW power rating, 120 MWh energy capacity (max_hours=3)
    network.add(
        "StorageUnit",
        "battery",
        bus="bus",
        p_nom=40,  # MW — max injection / withdrawal
        max_hours=3,  # energy capacity = 40 * 3 = 120 MWh
        p_min_pu=-1,  # full discharge allowed
        p_max_pu=1,  # full charge allowed
        cyclic_state_of_charge=True,  # required by the converter
        state_of_charge_initial=0.0,  # start empty
        efficiency_store=1.0,
        efficiency_dispatch=1.0,
        standing_loss=0.0,
        marginal_cost=0.0,
    )

    return network


def main() -> None:
    network = build_network()

    logger.info("Converting PyPSA network '%s' to GEMS study ...", network.name)
    logger.info("  buses:          %s", list(network.buses.index))
    logger.info("  loads:          %s", list(network.loads.index))
    logger.info("  generators:     %s", list(network.generators.index))
    logger.info("  storage units:  %s", list(network.storage_units.index))
    logger.info("  snapshots:      %s", list(network.snapshots))

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=OUTPUT_DIR,
        series_file_format=".tsv",
    ).to_gems_study()

    systems_dir = OUTPUT_DIR / "systems"
    logger.info("GEMS study written to: %s", OUTPUT_DIR)
    logger.info("  system.yml      : %s", systems_dir / "input" / "system.yml")
    logger.info("  parameters.yml  : %s", systems_dir / "parameters.yml")
    logger.info("  model-libraries : %s", systems_dir / "input" / "model-libraries")
    logger.info("  data-series     : %s", systems_dir / "input" / "data-series")


if __name__ == "__main__":
    main()
