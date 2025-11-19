from pydantic import Field, PrivateAttr
from .gems_system_yaml_schema import GemsSystem
from .modeler_parameter_yaml_schema import ModelerParameters
from ..modified_base_model import ModifiedBaseModel

class GemsStudy(ModifiedBaseModel):
    _gems_system: GemsSystem = PrivateAttr(default=None)
    _modeler_parameters: ModelerParameters = PrivateAttr(default=None)

    def __init__(self, gems_system: GemsSystem, modeler_parameters: ModelerParameters):
        super().__init__()
        self._gems_system = gems_system
        self._modeler_parameters = modeler_parameters

    def get_system(self) -> GemsSystem:
        return self._gems_system

    def get_parameters(self) -> ModelerParameters:
        return self._modeler_parameters