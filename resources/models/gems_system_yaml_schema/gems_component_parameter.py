from typing import Optional, Union
from resources.models import ModifiedBaseModel


class GemsComponentParameter(ModifiedBaseModel):
    id: str
    time_dependent: bool = False
    scenario_dependent: bool = False
    value: Union[float, str]
    scenario_group: Optional[str] = None