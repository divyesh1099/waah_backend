from pydantic import BaseModel

class Msg(BaseModel):
    message: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"