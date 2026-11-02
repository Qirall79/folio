from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, EmailStr, Field, SecretStr, StringConstraints


class RegisterCredentials(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)


class Credentials(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3,
                                           max_length=254)] = Field(
        validation_alias=AliasChoices("email", "username")
    )
    password: SecretStr = Field(min_length=1, max_length=128)


class UserOutput(BaseModel):
    id: str
    username: str
    email: str | None = None
    is_admin: bool = False


class SessionOutput(BaseModel):
    user: UserOutput
    csrf_token: str
    providers: list[str]


class TokenOutput(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
