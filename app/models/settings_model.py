"""
Admin application-settings schemas (profile password + DB reset).
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.string_utils import strip_required


RESET_CONFIRM_PHRASE = "RESET"


class ChangeProfilePasswordRequest(BaseModel):
    """
    Change the admin Profile Password used to authorize destructive settings.

    Mirrors ``ChangePasswordRequest``: current + new + confirm.
    """

    current_profile_password: str = Field(..., min_length=6, max_length=128)
    new_profile_password: str = Field(..., min_length=6, max_length=128)
    confirm_new_profile_password: str = Field(..., min_length=6, max_length=128)

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ChangeProfilePasswordRequest":
        if self.new_profile_password != self.confirm_new_profile_password:
            raise ValueError(
                "New profile password and confirm new profile password do not match"
            )
        if self.new_profile_password == self.current_profile_password:
            raise ValueError("New profile password must differ from the current one")
        return self


class ResetDatabaseRequest(BaseModel):
    """Confirm a full application-data wipe with the admin Profile Password."""

    profile_password: str = Field(..., min_length=6, max_length=128)
    confirm_phrase: str = Field(
        ...,
        description=f'Must be exactly "{RESET_CONFIRM_PHRASE}" (case-sensitive).',
    )

    @model_validator(mode="after")
    def validate_confirm_phrase(self) -> "ResetDatabaseRequest":
        phrase = strip_required(self.confirm_phrase)
        if phrase != RESET_CONFIRM_PHRASE:
            raise ValueError(
                f'confirm_phrase must be exactly "{RESET_CONFIRM_PHRASE}"'
            )
        self.confirm_phrase = phrase
        return self


class AppSettingsResponse(BaseModel):
    """Non-secret settings flags for the Application Settings page."""

    profile_password_configured: bool = True
    default_profile_password_hint: Optional[str] = Field(
        None,
        description=(
            "Only set when the profile password is still the seeded default "
            "(hint for admins to change it)."
        ),
    )
    wipeable_collections: list[str] = Field(default_factory=list)
    evaluation_bucket_name: Optional[str] = Field(
        None,
        description=(
            "GCS bucket whose objects are wiped on Reset Database "
            "(bucket itself is kept)."
        ),
    )
    reset_confirm_phrase: str = RESET_CONFIRM_PHRASE


class ResetDatabaseResponse(BaseModel):
    """Result of a successful database reset."""

    message: str
    deleted_counts: dict[str, int]
    preserved: list[str]
