from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from .reference_video import ReferenceVideo, validate_storage_id


class ReferenceMediaError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ReferenceImage(BaseModel):
    type: Literal["image"] = "image"
    id: str = Field(min_length=1)
    originalName: str = Field(min_length=1)
    format: Literal["jpeg", "png", "webp"]
    sizeBytes: int = Field(gt=0, le=30_000_000)
    width: int = Field(ge=256, le=5760)
    height: int = Field(ge=256, le=5760)
    hasTransparency: bool

    @field_validator("id")
    @classmethod
    def id_must_be_a_safe_storage_segment(cls, value: str) -> str:
        return validate_storage_id(value)

    @model_validator(mode="after")
    def aspect_ratio_must_be_supported(self) -> "ReferenceImage":
        if self.width * 5 < self.height * 2 or self.width * 2 > self.height * 5:
            raise ValueError("参考图片宽高比必须在 2:5 至 5:2 之间")
        return self


ReferenceMedia = Annotated[Union[ReferenceImage, ReferenceVideo], Field(discriminator="type")]
