from pydantic import Field

from opennem.schema.core import BaseConfig


class UnitDefinition(BaseConfig):
    name: str = Field(..., description="Name of the unit")
    name_alias: str | None = Field(None, description="Name alias")
    unit_type: str = Field(..., description="Type of unit")
    round_to: int = 2
    unit: str = Field(..., description="Unit abbreviation")

    # should nulls in the unit series be cast
    cast_nulls: bool = True

    @property
    def value(self) -> str:
        return self.unit
