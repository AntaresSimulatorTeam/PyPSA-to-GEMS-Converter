import pandas as pd
from pypsa import Network

from src.models.pypsa_model_schema import PyPSAComponentData, PyPSAGlobalConstraintData
from src.utils import StudyType


class PyPSARegister:
    def __init__(self, pypsa_network: Network, study_type: StudyType):
        self.pypsa_network = pypsa_network
        self.study_type = study_type
        self.pypsa_components_data: dict[str, PyPSAComponentData] = {}
        self.pypsa_globalconstraints_data: dict[str, PyPSAGlobalConstraintData] = {}

    def register(self) -> tuple[dict[str, PyPSAComponentData], dict[str, PyPSAGlobalConstraintData]]:
        self._register_pypsa_component(
            "generators",
            self.pypsa_network.generators,
            self.pypsa_network.generators_t,
            "generator",
            {
                "p_nom_min": "p_nom_min",
                "p_nom_max": "p_nom_max",
                "p_min_pu": "p_min_pu",
                "p_max_pu": "p_max_pu",
                "marginal_cost": "marginal_cost",
                "capital_cost": "capital_cost",
                "e_sum_min": "e_sum_min",
                "e_sum_max": "e_sum_max",
                "sign": "sign",
                "efficiency": "efficiency",
                "co2_emissions": "emission_factor",
            },
            {"bus": ("p_balance_port", "p_balance_port")},
        )
        ### PyPSA components : Loads
        self._register_pypsa_component(
            "loads",
            self.pypsa_network.loads,
            self.pypsa_network.loads_t,
            "load",
            {
                "p_set": "p_set",
                "q_set": "q_set",
                "sign": "sign",
            },
            {"bus": ("p_balance_port", "p_balance_port")},
        )
        ### PyPSA components : Buses
        self._register_pypsa_component(
            "buses",
            self.pypsa_network.buses,
            self.pypsa_network.buses_t,
            "bus",
            {
                "v_nom": "v_nom",
                "x": "x",
                "y": "y",
                "v_mag_pu_set": "v_mag_pu_set",
                "v_mag_pu_min": "v_mag_pu_min",
                "v_mag_pu_max": "v_mag_pu_max",
            },
            {},
        )
        ### PyPSA components : Links
        self._register_pypsa_component(
            "links",
            self.pypsa_network.links,
            self.pypsa_network.links_t,
            "link",
            {
                "efficiency": "efficiency",
                "p_nom_min": "p_nom_min",
                "p_nom_max": "p_nom_max",
                "p_min_pu": "p_min_pu",
                "p_max_pu": "p_max_pu",
                "marginal_cost": "marginal_cost",
                "capital_cost": "capital_cost",
            },
            {
                "bus0": ("p0_port", "p_balance_port"),
                "bus1": ("p1_port", "p_balance_port"),
            },
        )
        ### PyPSA components : Storage Units
        self._register_pypsa_component(
            "storage_units",
            self.pypsa_network.storage_units,
            self.pypsa_network.storage_units_t,
            "storage_unit",
            {
                "p_nom_min": "p_nom_min",
                "p_nom_max": "p_nom_max",
                "p_min_pu": "p_min_pu",
                "p_max_pu": "p_max_pu",
                "sign": "sign",
                "efficiency_store": "efficiency_store",
                "efficiency_dispatch": "efficiency_dispatch",
                "standing_loss": "standing_loss",
                "max_hours": "max_hours",
                "marginal_cost": "marginal_cost",
                "capital_cost": "capital_cost",
                "marginal_cost_storage": "marginal_cost_storage",
                "spill_cost": "spill_cost",
                "inflow": "inflow",
                "co2_emissions": "emission_factor",
            },
            {"bus": ("p_balance_port", "p_balance_port")},
        )
        ### PyPSA components : Stores
        self._register_pypsa_component(
            "stores",
            self.pypsa_network.stores,
            self.pypsa_network.stores_t,
            "store",
            {
                "sign": "sign",
                "e_nom_min": "e_nom_min",
                "e_nom_max": "e_nom_max",
                "e_min_pu": "e_min_pu",
                "e_max_pu": "e_max_pu",
                "standing_loss": "standing_loss",
                "marginal_cost": "marginal_cost",
                "capital_cost": "capital_cost",
                "marginal_cost_storage": "marginal_cost_storage",
                "co2_emissions": "emission_factor",
            },
            {"bus": ("p_balance_port", "p_balance_port")},
        )

        self._register_pypsa_globalconstraints()

        return self.pypsa_components_data, self.pypsa_globalconstraints_data

    def _register_pypsa_component(
        self,
        pypsa_model_id: str,
        constant_data: pd.DataFrame,
        time_dependent_data: dict[str, pd.DataFrame],
        gems_model_id: str,
        pypsa_params_to_gems_params: dict[str, str],
        pypsa_params_to_gems_connections: dict[str, tuple[str, str]],
    ) -> None:
        if pypsa_model_id in self.pypsa_components_data:
            raise ValueError(f"{pypsa_model_id} already registered !")

        if self.study_type == StudyType.LINEAR_OPTIMAL_POWER_FLOW:
            self.pypsa_components_data[pypsa_model_id] = PyPSAComponentData(
                pypsa_model_id,
                constant_data,
                time_dependent_data,
                gems_model_id,
                pypsa_params_to_gems_params,
                pypsa_params_to_gems_connections,
            )

    def _add_contributors_to_globalconstraints(
        self, gems_components_and_ports: list[tuple[str, str]], component_type: str
    ) -> list[tuple[str, str]]:
        df = getattr(self.pypsa_network, component_type)
        gems_components_and_ports += [(comp, "emission_port") for comp in df[df["carrier"] != "null"].index]
        return gems_components_and_ports

    def _register_pypsa_globalconstraints(self) -> None:
        gems_components_and_ports: list[tuple[str, str]] = []
        for component_type in ["generators", "stores", "storage_units"]:
            gems_components_and_ports = self._add_contributors_to_globalconstraints(
                gems_components_and_ports, component_type
            )

        for pypsa_model_id in self.pypsa_network.global_constraints.index:
            name, sense, carrier_attribute = (
                pypsa_model_id,
                self.pypsa_network.global_constraints.loc[pypsa_model_id, "sense"],
                self.pypsa_network.global_constraints.loc[pypsa_model_id, "carrier_attribute"],
            )
            if carrier_attribute == "co2_emissions" and sense == "<=":
                self.pypsa_globalconstraints_data[pypsa_model_id] = PyPSAGlobalConstraintData(
                    name,
                    carrier_attribute,
                    sense,
                    self.pypsa_network.global_constraints.loc[pypsa_model_id, "constant"],
                    "global_constraint_co2_max",
                    "emission_port",
                    gems_components_and_ports,
                )
            elif carrier_attribute == "co2_emissions" and sense == "==":
                self.pypsa_globalconstraints_data[pypsa_model_id] = PyPSAGlobalConstraintData(
                    name,
                    carrier_attribute,
                    sense,
                    self.pypsa_network.global_constraints.loc[pypsa_model_id, "constant"],
                    "global_constraint_co2_eq",
                    "emission_port",
                    gems_components_and_ports,
                )
            else:
                raise ValueError("Type of GlobalConstraint not supported.")
