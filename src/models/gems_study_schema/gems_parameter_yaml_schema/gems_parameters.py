from pydantic import PrivateAttr
from ...modified_base_model import ModifiedBaseModel
import yaml

class GemsParameters(ModifiedBaseModel):
    _solver: str = PrivateAttr(default=None)
    _solver_logs: bool = PrivateAttr(default=None)
    _solver_parameters: str = PrivateAttr(default=None)
    _no_output: bool = PrivateAttr(default=None)
    _first_time_step: int = PrivateAttr(default=None)
    _last_time_step: int = PrivateAttr(default=None)


    def __init__(self, solver: str, solver_logs: bool, solver_parameters: str, no_output: bool, first_time_step: int, last_time_step: int):
        super().__init__()
        self._solver = solver
        self._solver_logs = solver_logs
        self._solver_parameters = solver_parameters
        self._no_output = no_output
        self._first_time_step = first_time_step
        self._last_time_step = last_time_step

    def to_dict(self, by_alias: bool = True, exclude_unset: bool = True) -> dict:
        """Convert GemsParameters object to dictionary, handling PrivateAttr fields."""
        return {
            "solver": self._solver,
            "solver-logs": self._solver_logs,
            "solver-parameters": self._solver_parameters,
            "no-output": self._no_output,
            "first-time-step": self._first_time_step,
            "last-time-step": self._last_time_step,
        }


    def to_yaml(self, output_path: str) -> None:
        converted_data = self.to_dict(by_alias=True, exclude_unset=True)

        with open(output_path, "w", encoding="utf-8") as yaml_file:
            yaml.dump(
                converted_data,
                yaml_file,
                allow_unicode=True,
                sort_keys=False,
            )
    
    def set_solver(self, solver: str) -> None:
        self._solver = solver

    def set_solver_logs(self, solver_logs: bool) -> None:
        self._solver_logs = solver_logs

    def set_solver_parameters(self, solver_parameters: str) -> None:
        self._solver_parameters = solver_parameters

    def set_no_output(self, no_output: bool) -> None:
        self._no_output = no_output

    def get_solver(self) -> str:
        return self._solver

    def get_solver_logs(self) -> bool:
        return self._solver_logs

    def get_solver_parameters(self) -> str:
        return self._solver_parameters

    def get_no_output(self) -> bool:
        return self._no_output

    def get_first_time_step(self) -> int:
        return self._first_time_step

    def get_last_time_step(self) -> int:
        return self._last_time_step