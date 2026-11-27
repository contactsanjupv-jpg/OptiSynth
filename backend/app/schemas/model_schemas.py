from pydantic import BaseModel, ConfigDict


class RunValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str
