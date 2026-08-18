"""
User data models and schemas
"""

from typing import Literal, Optional

from app.utils.time import OptionalISTDateTime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.string_utils import strip_optional, strip_required


UserRole = Literal["admin", "evaluator", "student"]
ApprovalStatus = Literal["pending", "approved"]

USER_ROLES: tuple[UserRole, ...] = ("admin", "evaluator", "student")
NXTWAVE_EMAIL_DOMAIN = "@nxtwave.co.in"


class TeamMember(BaseModel):
    """A non-leader member on a student hackathon team."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return strip_required(value)


class StudentRegisterRequest(BaseModel):
    """Schema for student team self-registration (team size 3–5)."""

    team_name: str = Field(..., min_length=1, max_length=100)
    university: str = Field(..., min_length=1, max_length=200)
    team_leader_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr = Field(..., description="Team leader email (used for login)")
    niat_id: str = Field(..., min_length=1, max_length=50)
    mobile_no: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)
    team_member_1_name: str = Field(..., min_length=1, max_length=100)
    team_member_1_email: EmailStr
    team_member_2_name: str = Field(..., min_length=1, max_length=100)
    team_member_2_email: EmailStr
    team_member_3_name: Optional[str] = Field(None, max_length=100)
    team_member_3_email: Optional[EmailStr] = None
    team_member_4_name: Optional[str] = Field(None, max_length=100)
    team_member_4_email: Optional[EmailStr] = None

    @field_validator(
        "team_name",
        "university",
        "team_leader_name",
        "niat_id",
        "team_member_1_name",
        "team_member_2_name",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("mobile_no", mode="before")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("team_member_3_name", "team_member_4_name", mode="before")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        return strip_optional(value)

    @model_validator(mode="after")
    def validate_registration(self) -> "StudentRegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        if not self.mobile_no.isdigit():
            raise ValueError("Mobile number must contain digits only")

        member_pairs = [
            (self.team_member_1_name.strip(), self.team_member_1_email),
            (self.team_member_2_name.strip(), self.team_member_2_email),
            (self.team_member_3_name, self.team_member_3_email),
            (self.team_member_4_name, self.team_member_4_email),
        ]

        for index, (name, email) in enumerate(member_pairs[2:], start=3):
            has_name = bool(name)
            has_email = bool(email)
            if has_name ^ has_email:
                raise ValueError(
                    f"Team member {index} name and email must both be provided or both omitted"
                )

        if member_pairs[3][0] or member_pairs[3][1]:
            if not (member_pairs[2][0] and member_pairs[2][1]):
                raise ValueError("Team member 3 is required when team member 4 is provided")

        emails = [
            self.email.lower(),
            self.team_member_1_email.lower(),
            self.team_member_2_email.lower(),
        ]
        if member_pairs[2][1]:
            emails.append(member_pairs[2][1].lower())
        if member_pairs[3][1]:
            emails.append(member_pairs[3][1].lower())

        if len(emails) != len(set(emails)):
            raise ValueError("All team member emails must be unique")

        team_size = 1 + sum(1 for name, email in member_pairs if name and email)
        if team_size < 3 or team_size > 5:
            raise ValueError("Team size must be between 3 and 5 members (including the leader)")

        return self

    def team_members(self) -> list[TeamMember]:
        """Return the registered non-leader team members."""
        members = [
            TeamMember(name=self.team_member_1_name.strip(), email=self.team_member_1_email),
            TeamMember(name=self.team_member_2_name.strip(), email=self.team_member_2_email),
        ]
        if self.team_member_3_name and self.team_member_3_email:
            members.append(
                TeamMember(name=self.team_member_3_name.strip(), email=self.team_member_3_email)
            )
        if self.team_member_4_name and self.team_member_4_email:
            members.append(
                TeamMember(name=self.team_member_4_name.strip(), email=self.team_member_4_email)
            )
        return members


class EvaluatorRegisterRequest(BaseModel):
    """Schema for evaluator self-registration."""

    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    employee_id: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)

    @field_validator("first_name", "last_name", "employee_id", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("email")
    @classmethod
    def validate_nxtwave_email(cls, value: str) -> str:
        if not value.lower().endswith(NXTWAVE_EMAIL_DOMAIN):
            raise ValueError(f"Evaluator email must be a Nxtwave address ({NXTWAVE_EMAIL_DOMAIN})")
        return value.lower()

    @model_validator(mode="after")
    def validate_registration(self) -> "EvaluatorRegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        return self


class UserUpdate(BaseModel):
    """Schema for user updates"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class UserResponse(BaseModel):
    """Schema for user response"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str = ""
    last_name: str = ""
    name: str
    email: str
    role: UserRole
    niat_id: Optional[str] = None
    employee_id: Optional[str] = None
    mobile_no: Optional[str] = None
    team_name: Optional[str] = None
    university: Optional[str] = None
    team_leader_name: Optional[str] = None
    team_members: Optional[list[TeamMember]] = None
    approval_status: Optional[ApprovalStatus] = None
    created_at: OptionalISTDateTime = None
    updated_at: OptionalISTDateTime = None


class RegisterResponse(BaseModel):
    """Schema for registration response"""

    user_id: str
    email: str
    first_name: str
    last_name: str
    role: UserRole
    approval_status: ApprovalStatus
    team_name: Optional[str] = None
    university: Optional[str] = None
    message: str


class LoginRequest(BaseModel):
    """Schema for login request"""

    email: EmailStr
    password: str = Field(..., min_length=6)


class ChangePasswordRequest(BaseModel):
    """Schema for changing the authenticated user's password."""

    new_password: str = Field(..., min_length=6, max_length=128)
    confirm_new_password: str = Field(..., min_length=6, max_length=128)
    current_password: Optional[str] = Field(
        None,
        min_length=6,
        max_length=128,
        description=(
            "Current password. Required when REQUIRE_CURRENT_PASSWORD_ON_CHANGE=true "
            "(default)."
        ),
    )

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_new_password:
            raise ValueError("New password and confirm new password do not match")
        return self


class LoginResponse(BaseModel):
    """
    Schema for login response.

    The Firebase ID token is stored in an HttpOnly ``access_token`` cookie.
    ``csrf_token`` is also returned in the body for cross-origin SPAs (e.g.
    Vercel frontend → Cloud Run API) where ``document.cookie`` cannot read
    API-domain cookies — the SPA must echo it as ``X-CSRF-Token`` on POST/PUT/PATCH/DELETE.
    """

    user_id: str
    email: str
    name: str
    role: UserRole
    approval_status: Optional[ApprovalStatus] = None
    message: str = "Login successful"
    csrf_token: str


class CsrfTokenResponse(BaseModel):
    """CSRF token for cookie-authenticated cross-origin clients."""

    csrf_token: str


class CurrentUser(BaseModel):
    """Schema for current authenticated user"""

    user_id: str
    email: str
    role: UserRole
    name: str
    approval_status: Optional[ApprovalStatus] = None
