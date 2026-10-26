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
            raise ValueError("New profile password and confirm new profile password do not match")
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
            raise ValueError(f'confirm_phrase must be exactly "{RESET_CONFIRM_PHRASE}"')
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
            "GCS bucket whose objects are wiped on Reset Database " "(bucket itself is kept)."
        ),
    )
    reset_confirm_phrase: str = RESET_CONFIRM_PHRASE


class RegistrationConfigResponse(BaseModel):
    """Which registration checks are on. Applies to students and evaluators."""

    email_verification_enabled: bool = True
    mobile_verification_enabled: bool = True


class RegistrationConfigUpdate(BaseModel):
    """Super Admin toggles for student and evaluator registration."""

    email_verification_enabled: bool
    mobile_verification_enabled: bool


class RegistrationStatusResponse(RegistrationConfigResponse):
    """Toggles plus how many accounts are waiting or already asked to verify."""

    pending_count: int = Field(
        0,
        ge=0,
        description=(
            "Students and evaluators who still need a required channel "
            "and have not been asked yet."
        ),
    )
    released_count: int = Field(
        0,
        ge=0,
        description="Accounts that will see the verify popup the next time they sign in.",
    )
    released_now: int = Field(
        0,
        ge=0,
        description="How many accounts this request just added to that popup.",
    )


class RegistrationReleaseRequest(BaseModel):
    """Ask the next batch of unverified students and evaluators to verify on login."""

    batch_size: int = Field(..., ge=1, le=500)


class HackathonDeleteListItem(BaseModel):
    """One live hackathon the Super Admin can remove from Application Settings."""

    hackathon_id: str
    name: str


class HackathonDeletePreview(BaseModel):
    """What a hackathon delete will remove, before the Profile Password is sent."""

    hackathon_id: str
    name: str
    submission_count: int = 0
    team_count: int = 0
    enrollment_count: int = 0


class DeleteHackathonRequest(BaseModel):
    """Confirm deletion of one hackathon and everything stored for it."""

    profile_password: str = Field(..., min_length=6, max_length=128)
    confirm_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Must match the hackathon name exactly.",
    )

    @model_validator(mode="after")
    def normalize_confirm_name(self) -> "DeleteHackathonRequest":
        self.confirm_name = strip_required(self.confirm_name)
        return self


class DeleteHackathonResponse(BaseModel):
    """Result of deleting one hackathon and its submissions."""

    message: str
    hackathon_id: str
    hackathon_name: str
    deleted_counts: dict[str, int]


class ResetDatabaseResponse(BaseModel):
    """Result of a successful database reset."""

    message: str
    deleted_counts: dict[str, int]
    preserved: list[str]
