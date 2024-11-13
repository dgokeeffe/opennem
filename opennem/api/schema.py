from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from opennem.utils.dates import get_today_opennem
from opennem.utils.version import get_version


class ApiBase(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        use_enum_values=True,
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )


class UpdateResponse(BaseModel):
    success: bool = True
    records: list = []


class FueltechResponse(ApiBase):
    success: bool = True

    # @TODO fix circular references
    # records: List[FueltechSchema]


class APINetworkRegion(ApiBase):
    code: str
    timezone: str | None = None


class APINetworkSchema(ApiBase):
    code: str
    country: str
    label: str

    regions: list[APINetworkRegion] | None = None
    timezone: str | None = Field(None, description="Network timezone")
    interval_size: int = Field(..., description="Size of network interval in minutes")


class APIV4ResponseSchema(ApiBase):
    version: str = Field(default_factory=get_version)
    created_at: datetime = Field(default_factory=get_today_opennem)
    success: bool = True
    error: str | None = None
    data: list = []
    total_records: int | None = None
