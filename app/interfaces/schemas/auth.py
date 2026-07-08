from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    id: str
    email: str
    username: str
    avatar_url: str = ""
    is_active: bool = True
    last_login_at: Optional[datetime] = None
    created_at: datetime


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    username: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    username: str = Field(min_length=1)
    avatar_url: str = ""


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
