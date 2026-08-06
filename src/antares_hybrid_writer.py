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
Builds a companion classic Antares study that makes a multi-scenario, non-investment
GEMS study directly runnable end-to-end by the real antares-solver binary
(antares-solver -i <path>), in addition to antares-modeler/GemsPy.

Why this exists
----------------
antares-modeler invoked directly on a GEMS systems/ folder has no notion of
Monte-Carlo years / generaldata.ini: it silently only ever solves scenario 0,
regardless of how many scenarios the PyPSA/GEMS study actually declares.

antares-solver (the real Antares engine) does understand Monte-Carlo years, but it
only understands classic Antares studies (areas/links/thermal clusters) -- it has no notion of a GEMS system on its own.
This writer builds a "hybrid" study to get the best of both, validated end-to-end in tests/e2e/test_hybrid_study_comparison.py:

1. Create a bare-bones classic Antares study with antares-craft, containing a single
   "virtual_area" that is completely inert: zero unsupplied/spilled energy cost, no
   load, no generation, no links, no thermal/renewable/storage clusters. It exists
   purely so antares-solver has a classic area to load -- it is deliberately NOT
   wired to the real system (no area_connections), so it cannot perturb the
   objective value in any way.
2. Copy the converted GEMS system (system.yml, model-libraries/, data-series/)
   directly into the classic study's own input/ directory. antares-solver's hybrid
   loader reads input/system.yml relative to the Antares study root
3. Set nb_years (Monte-Carlo years) in generaldata.ini, via antares-craft, to the
   number of scenarios declared by the GEMS/PyPSA study, so Antares' own MC-year loop
   lines up with the GEMS scenario axis.
4. Add resolution-mode: benders-decomposition to optim-config.yml. This is required
   by antares-solver's hybrid loader for ANY model with an investment-style variable --
   even non-extendable (p_nom_extendable=False) generators declare p_nom as a
   "master-and-subproblems" decomposition variable in pypsa_models.yml (see
   resources/optim-config.yml) -- so this applies even though this writer is only
   ever invoked for non-investment studies (see PyPSAStudyConverter._has_extendable_capacity).
   Without it, the solver refuses to load with "Scenario-independent variables are not
   supported in hybrid studies with sequential-subproblems resolution mode."
5. Tag every component with scenario-group: default in system.yml and add
   input/data-series/modeler-scenariobuilder.dat with an identity mapping
   (MC year i -> data-series column i + 1). Without this, antares-solver's hybrid
   loader silently reuses the SAME data-series column for every MC year (unlike
   GemsPy's own Python loader, which defaults ungrouped components to an identity
   mapping automatically) -- so multi-scenario studies would silently solve every MC
   year against scenario 0 only.
6. Run antares-solver -i <study_dir> (NOT antares-modeler), these studies are non-investment

All of the above (steps 2-5 in particular) were reverse-engineered empirically against
the actual antares-solver binary -- none of it is documented, hence the heavy
comments. If a newer Antares/GemsPy version changes this behaviour, this is the place
to update (both this writer and tests/e2e/test_hybrid_study_comparison.py, which
validates it).
"""

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import antares.craft as antares_craft
import yaml


@dataclass
class AntaresHybridStudyWriter:
    study_dir: Path
    study_name: str
    study_version: str = "9.3"

    def write(self, gems_systems_dir: Path, n_timesteps: int, n_scenarios: int) -> Path:
        """Build the hybrid study and return its directory (self.study_dir / self.study_name)."""
        antares_study_dir = self._build_virtual_antares_study(n_timesteps, n_scenarios)
        self._add_gems_system(antares_study_dir, gems_systems_dir, n_scenarios)
        return antares_study_dir

    def _build_virtual_antares_study(self, n_timesteps: int, n_scenarios: int) -> Path:
        """
                                                       ^
                                                       |
                                                       |
        Steps 1 & 3 of the trick (see module docstring |): a bare classic Antares
        study with one inert area, and nb_years matching the GEMS scenario count.
        """
        study_dir = self.study_dir / self.study_name
        if study_dir.exists():
            shutil.rmtree(study_dir)
        self.study_dir.mkdir(parents=True, exist_ok=True)

        study = antares_craft.create_study_local(self.study_name, self.study_version, str(self.study_dir))
        study.create_area(
            "virtual_area",
            properties=antares_craft.AreaProperties(
                energy_cost_unsupplied=0.0,
                energy_cost_spilled=0.0,
                non_dispatch_power=False,
                dispatch_hydro_power=False,
                other_dispatch_power=False,
            ),
            # Deliberately no create_link, create_thermal_cluster, etc.: this area
            # is connected to nothing and contributes nothing to the objective.
        )
        simulation_end_days = max(1, math.ceil(n_timesteps / 24))
        study.update_settings(
            antares_craft.StudySettingsUpdate(
                general_parameters=antares_craft.GeneralParametersUpdate(
                    simulation_start=1,
                    simulation_end=simulation_end_days,
                    nb_years=n_scenarios,
                ),
                optimization_parameters=antares_craft.OptimizationParametersUpdate(
                    simplex_range=antares_craft.SimplexOptimizationRange.WEEK,
                ),
            )
        )
        return study_dir

    def _add_gems_system(self, antares_study_dir: Path, gems_systems_dir: Path, n_scenarios: int) -> None:
        """
                                                          ^
                                                          |
                                                          |
                                                          |
        Steps 2, 4 & 5 of the trick (see module docstring |): graft the GEMS system
        directly into the classic study's own input/ directory.
        """
        gems_input = gems_systems_dir / "input"
        antares_input = antares_study_dir / "input"

        shutil.copy(gems_input / "system.yml", antares_input / "system.yml")
        shutil.copytree(gems_input / "model-libraries", antares_input / "model-libraries", dirs_exist_ok=True)
        shutil.copytree(gems_input / "data-series", antares_input / "data-series", dirs_exist_ok=True)

        # Step 4: hybrid loader requires benders-decomposition for any study whose
        # models declare master/subproblem variables (including fixed p_nom). Without
        # this file, antares-solver refuses to load the hybrid study.
        # resources/optim-config.yml already sets resolution-mode: benders-decomposition.
        optim_config_src = Path(__file__).parent.parent / "resources" / "optim-config.yml"
        shutil.copy(optim_config_src, antares_input / "optim-config.yml")

        # Tag every component with the same scenario-group and add an identity
        # modeler-scenariobuilder.dat (MC year i -> data-series column i+1), so
        # antares-solver picks a distinct data-series column per MC year instead of
        # silently reusing column 1 for every year (see module docstring, step 5).
        system_yml_path = antares_input / "system.yml"
        with system_yml_path.open() as f:
            system_data: dict[str, Any] = yaml.safe_load(f)
        for component in system_data["system"].get("components", []):
            component["scenario-group"] = "default"
        with system_yml_path.open("w") as f:
            yaml.dump(
                {"system": system_data["system"]}, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )

        scenariobuilder_lines = [f"default, {i} = {i + 1}" for i in range(n_scenarios)]
        (antares_input / "data-series" / "modeler-scenariobuilder.dat").write_text(
            "\n".join(scenariobuilder_lines) + "\n"
        )
