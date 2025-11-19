from typing import List, Optional
from pydantic import PrivateAttr
import yaml
from ..modified_base_model import ModifiedBaseModel
from .gems_component import GemsComponent
from .gems_port_connection import GemsPortConnection
from .gems_area_connection import GemsAreaConnection




class GemsSystem(ModifiedBaseModel):
    _id: str = PrivateAttr(default=None)
    _model_libraries: Optional[str] = PrivateAttr(default=None)
    _components: List[GemsComponent] = PrivateAttr(default=[])
    _connections: Optional[List[GemsPortConnection]] = PrivateAttr(default=None)
    _area_connections: Optional[List[GemsAreaConnection]] = PrivateAttr(default=None)
    _nodes: Optional[List[GemsComponent]] = PrivateAttr(default=[])

    def __init__(self, id: str, 
                model_libraries: Optional[str], 
                components: List[GemsComponent], 
                connections: Optional[List[GemsPortConnection]], 
                area_connections: Optional[List[GemsAreaConnection]], 
                nodes: Optional[List[GemsComponent]]):
        super().__init__()
        self._id = id
        self._model_libraries = model_libraries
        self._components = components
        self._connections = connections
        self._area_connections = area_connections
        self._nodes = nodes


    def to_yaml(self, output_path: str) -> None:
        ordered_data = self.to_dict(by_alias=True, exclude_unset=True)
        
        with open(output_path, "w", encoding="utf-8") as yaml_file:
            yaml.dump(
                {"system": ordered_data},
                yaml_file,
                allow_unicode=True,
                sort_keys=False,
            )

    
    def to_dict(self, by_alias: bool = True, exclude_unset: bool = True) -> dict:
        """Convert GemsSystem object to dictionary, handling PrivateAttr fields and nested Pydantic models."""
        return {
            "id": self._id,
            "model_libraries": self._model_libraries,
            "components": [
                component.model_dump(by_alias=by_alias, exclude_unset=exclude_unset)
                for component in (self._components or [])
            ],
            "connections": [
                connection.model_dump(by_alias=by_alias, exclude_unset=exclude_unset)
                for connection in (self._connections or [])
            ] if self._connections else None,
            "area_connections": [
                area_conn.model_dump(by_alias=by_alias, exclude_unset=exclude_unset)
                for area_conn in (self._area_connections or [])
            ] if self._area_connections else None,
            "nodes": [
                node.model_dump(by_alias=by_alias, exclude_unset=exclude_unset)
                for node in (self._nodes or [])
            ],
            
        }