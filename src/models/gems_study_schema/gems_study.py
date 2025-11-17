from pydantic import Field, PrivateAttr
from .gems_system_yaml_schema import GemsSystem
from .gems_parameter_yaml_schema import GemsParameters
from ..modified_base_model import ModifiedBaseModel

class GemsStudy(ModifiedBaseModel):
    _gems_system: GemsSystem = PrivateAttr(default=None)
    _gems_parameters: GemsParameters = PrivateAttr(default=None)

    def __init__(self, gems_system: GemsSystem, gems_parameters: GemsParameters):
        super().__init__()
        self._gems_system = gems_system
        self._gems_parameters = gems_parameters

    def get_system(self) -> GemsSystem:
        return self._gems_system

    def get_parameters(self) -> GemsParameters:
        return self._gems_parameters