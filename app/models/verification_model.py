"""Request/response schemas for verified student and evaluator registration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.string_utils import strip_required
from app.models.user_model import NXTWAVE_EMAIL_DOMAIN
from app.utils.phone import normalize_e164

RegistrationRole = Literal["student", "evaluator"]

_PASSWORD_LETTER = re.compile(r"[A-Za-z]")
_PASSWORD_DIGIT = re.compile(r"\d")
_OTP_DIGITS = re.compile(r"^\d{6}$")


def validate_password_strength(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not _PASSWORD_LETTER.search(password) or not _PASSWORD_DIGIT.search(password):
        raise ValueError("Password must contain at least 1 letter and 1 number")
    return password


def validate_nxtwave_email(email: str) -> str:
    normalized = email.lower()
    if not normalized.endswith(NXTWAVE_EMAIL_DOMAIN):
        raise ValueError(
            f"Evaluator email must be a Nxtwave address ({NXTWAVE_EMAIL_DOMAIN})"
        )
    return normalized


class RegisterStartRequest(BaseModel):
    session_id: str | None = Field(None, min_length=8, max_length=80)
    email: EmailStr | None = None
    mobile_number: str | None = Field(None, min_length=8, max_length=20)
    role: RegistrationRole = "student"

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return strip_required(value)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return strip_required(value).lower()

    @field_validator("mobile_number", mode="before")
    @classmethod
    def normalize_mobile(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_e164(str(value).strip())

    @model_validator(mode="after")
    def require_identifier(self) -> "RegisterStartRequest":
        if not self.email and not self.mobile_number:
            raise ValueError("At least one of email or mobile_number is required")
        if self.role == "evaluator" and self.email:
            validate_nxtwave_email(str(self.email))
        return self


class RegisterStartResponse(BaseModel):
    session_id: str


class EmailSendOtpRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return strip_required(value).lower()

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session(cls, value: str) -> str:
        return strip_required(value)


class EmailVerifyOtpRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("session_id", "code", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("code")
    @classmethod
    def six_digits(cls, value: str) -> str:
        if not _OTP_DIGITS.match(value):
            raise ValueError("Code must be 6 digits")
        return value


class VerifyPhoneTokenRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    firebase_id_token: str = Field(..., min_length=20)
    mobile_number: str = Field(..., min_length=8, max_length=20)

    @field_validator("session_id", "firebase_id_token", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        return normalize_e164(value)


class RegisterCompleteRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    university_name: str = Field(..., min_length=1, max_length=200)
    niat_id: str = Field(..., min_length=1, max_length=50)
    mobile_number: str = Field(..., min_length=8, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str | None = Field(None, min_length=8, max_length=128)

    @field_validator(
        "session_id",
        "first_name",
        "last_name",
        "university_name",
        "niat_id",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return strip_required(value).lower()

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        return normalize_e164(value)

    @field_validator("password")
    @classmethod
    def password_rules(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterCompleteRequest":
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        return self


class EvaluatorRegisterCompleteRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    employee_id: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    mobile_number: str = Field(..., min_length=8, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str | None = Field(None, min_length=8, max_length=128)

    @field_validator("session_id", "first_name", "last_name", "employee_id", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return validate_nxtwave_email(strip_required(value))

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        return normalize_e164(value)

    @field_validator("password")
    @classmethod
    def password_rules(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def passwords_match(self) -> "EvaluatorRegisterCompleteRequest":
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        return self


class ForgotPasswordStartRequest(BaseModel):
    """Start a password-reset session. Email must already be registered."""

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return strip_required(value).lower()


class ForgotPasswordStartResponse(BaseModel):
    session_id: str
    message: str
    mobile_last4: str
    mobile_number: str = Field(
        ...,
        description=(
            "Registered E.164 mobile for Firebase Phone Auth. "
            "Show only mobile_last4 in the UI."
        ),
    )


class ForgotPasswordResetRequest(BaseModel):
    """Set a new password after email OTP and phone verification succeed."""

    session_id: str = Field(..., min_length=8, max_length=80)
    email: EmailStr
    mobile_number: str = Field(..., min_length=8, max_length=20)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str | None = Field(None, min_length=8, max_length=128)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return strip_required(value).lower()

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        return normalize_e164(value)

    @field_validator("new_password")
    @classmethod
    def password_rules(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def passwords_match(self) -> "ForgotPasswordResetRequest":
        if self.confirm_password is not None and self.new_password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        return self


class ForgotPasswordResetResponse(BaseModel):
    message: str
    email: str


class VerificationOkResponse(BaseModel):
    email_verified: bool = False
    phone_verified: bool = False
    message: str
