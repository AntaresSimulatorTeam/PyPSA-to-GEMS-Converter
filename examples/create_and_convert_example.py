#!/usr/bin/env python3
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
Example: Create a simple deterministic PyPSA study and convert it to GEMS format.

Network topology
----------------
- 24 hourly time steps (hours 1 to 24)
- busA and busB connected by a bidirectional link (capacity 100 MW, efficiency 1.0)

Components
----------
busA:
  - Generator A1  : available capacity = t MW at hour t, marginal cost = 1 €/MWh
  - Generator A2  : available capacity = t MW at hour t, marginal cost = 2 €/MWh
  - Load AL       : fixed consumption = 100 MW

busB:
  - Generator B1  : capacity = 100 MW, marginal cost = 10 €/MWh

Expected dispatch (intuition)
------------------------------
At hour t, busA has A1 + A2 offering up to 2t MW at low cost (1 and 2 €/MWh).
The load is 100 MW. When 2t < 100, busB's generator B1 and/or the link imports
the shortfall. When 2t >= 100 (t >= 50), busA can self-supply — but since t <= 24,
busA never fully covers the 100 MW load on its own; the link and B1 always contribute.
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import pypsa

# Make sure the src package is importable when running from the examples/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pypsa_converter import PyPSAStudyConverter


def create_network() -> pypsa.Network:
    """Build and return the PyPSA network described above."""
    n = pypsa.Network(name="two_bus_example")

    # 24 hourly snapshots: 2024-01-01 01:00 … 24:00 (hours 1 – 24)
    snapshots = pd.date_range("2024-01-01 01:00", periods=24, freq="h")
    n.set_snapshots(snapshots)

    # ------------------------------------------------------------------
    # Buses
    # ------------------------------------------------------------------
    n.add("Bus", "busA")
    n.add("Bus", "busB")

    # ------------------------------------------------------------------
    # Link: busA <-> busB, bidirectional, 100 MW
    # p_min_pu = -1 allows reverse flow (busB -> busA) up to p_nom.
    # ------------------------------------------------------------------
    n.add(
        "Link",
        "link_AB",
        bus0="busA",
        bus1="busB",
        p_nom=100.0,
        p_min_pu=-1.0,
        efficiency=1.0,
    )

    # ------------------------------------------------------------------
    # Generators in busA
    # Available capacity at hour t (1-indexed) = t MW
    #   => p_nom = 24 (maximum at hour 24)
    #   => p_max_pu[t] = t / 24
    # ------------------------------------------------------------------
    hours = range(1, 25)  # 1 to 24 inclusive
    p_max_pu_variable = pd.Series([t / 24.0 for t in hours], index=snapshots)

    n.add(
        "Generator",
        "A1",
        bus="busA",
        p_nom=24.0,
        p_max_pu=p_max_pu_variable,
        marginal_cost=1.0,
    )
    n.add(
        "Generator",
        "A2",
        bus="busA",
        p_nom=24.0,
        p_max_pu=p_max_pu_variable,
        marginal_cost=2.0,
    )

    # ------------------------------------------------------------------
    # Load in busA: constant 100 MW
    # ------------------------------------------------------------------
    n.add("Load", "AL", bus="busA", p_set=100.0)

    # ------------------------------------------------------------------
    # Generator in busB: 100 MW, marginal cost 10 €/MWh
    # ------------------------------------------------------------------
    n.add("Generator", "B1", bus="busB", p_nom=100.0, marginal_cost=10.0)

    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # 1. Create the PyPSA network
    # ------------------------------------------------------------------
    logger.info("Creating PyPSA network...")
    n = create_network()
    logger.info("Network '%s' created:", n.name)
    logger.info("  Snapshots  : %d steps (%s to %s)", len(n.snapshots), n.snapshots[0], n.snapshots[-1])
    logger.info("  Buses      : %s", list(n.buses.index))
    logger.info("  Generators : %s", list(n.generators.index))
    logger.info("  Links      : %s", list(n.links.index))
    logger.info("  Loads      : %s", list(n.loads.index))

    # ------------------------------------------------------------------
    # 2. Convert to GEMS
    # ------------------------------------------------------------------
    output_dir = Path(__file__).resolve().parent / "example_gems_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Converting to GEMS study → %s", output_dir)

    converter = PyPSAStudyConverter(
        pypsa_network=n,
        logger=logger,
        study_dir=output_dir,
        series_file_format="csv",
    )
    converter.to_gems_study()

    logger.info("Conversion complete. GEMS study written to: %s", output_dir)


if __name__ == "__main__":
    main()
