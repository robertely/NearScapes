from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    analyzer: str
    parameters: dict = Field(default_factory=dict)
