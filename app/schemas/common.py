from pydantic import BaseModel, Field
from typing import Optional

class Msg(BaseModel):
    message: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"