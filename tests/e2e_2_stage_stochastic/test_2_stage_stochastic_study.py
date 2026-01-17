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
import logging
import subprocess
from pathlib import Path

from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)
current_dir = Path(__file__).resolve().parents[2]


def test_2_stage_stochastic_study() -> None:
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")

    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)

    time_series_p_set = [100 + 10 * i for i in range(10)]
    time_series_q_set = [20 + 5 * i for i in range(10)]
    network.add("Load", "timeseries_load", bus="bus 1", p_set=time_series_p_set, q_set=time_series_q_set)

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
        marginal_cost=10,
        p_nom=100,
        p_min_pu=0.0,
        p_max_pu=[0.7 + 0.01 * i for i in range(10)],
    )

    network.add(
        "Generator",
        "gen3",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=100,
        p_min_pu=0.0,
        p_max_pu=0.9,
    )

    scenarios = {
        "low": 1,
        # "medium": 0.2,
    }  # this sum needs to be equal to 1, so current solution is that we only have one scenario

    network.set_scenarios(scenarios)

    for key, value in network.components.generators.static.p_max_pu.items():
        if key == ("low", "gen3"):
            network.components.generators.static.p_max_pu.loc[key] = value * 0.2  # type: ignore

    PyPSAStudyConverter(
        network,
        logger,
        Path("tmp") / "test_2_stage_stochastic_study",
        ".tsv",
    ).to_gems_study()

    study_dir = current_dir / "tmp" / "test_2_stage_stochastic_study"

    modeler_bin = current_dir / "antares-9.3.5-Ubuntu-22.04" / "bin" / "antares-modeler"

    try:
        result = subprocess.run(
            [str(modeler_bin), str(study_dir / "systems")],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(modeler_bin.parent),
        )

        print("returncode:", result.returncode)
        print("stdout:", result.stdout)
        print("stderr:", result.stderr)
        output_dir = study_dir / "systems" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        option_json = {
            "LOG_LEVEL": 0,
            "MAX_ITERATIONS": -1,
            "GAP": 1e-06,
            "AGGREGATION": False,
            "OUTPUTROOT": ".",
            "TRACE": True,
            "SLAVE_WEIGHT": "CONSTANT",
            "SLAVE_WEIGHT_VALUE": 1,
            "MASTER_NAME": "master",
            "LAST_MASTER_MPS": "master",
            "STRUCTURE_FILE": "structure.txt",
            "INPUTROOT": ".",
            "CSV_NAME": "benders_output_trace",
            "BOUND_ALPHA": True,
            "SOLVER_NAME": "COIN",
            "JSON_FILE": "./expansion/out.json",
            "LAST_ITERATION_JSON_FILE": "./expansion/last_iteration.json",
        }

        options_path = output_dir / "option.json"
        options_path.parent.mkdir(parents=True, exist_ok=True)
        options_path.write_text(json.dumps(option_json, indent=2), encoding="utf-8")

        (output_dir / "area.txt").touch(exist_ok=True)
        """
        result = subprocess.run(
            [str(benders_bin), str(options_path)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(output_dir),
        )
        print("returncode:", result.returncode)
        print("stdout:", result.stdout)
        print("stderr:", result.stderr)
        """
    except Exception as e:
        print(e)
        raise e

    network.optimize()
