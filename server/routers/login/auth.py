import jwt
from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from config import settings

import LogAssist.log as Logger

# JWT configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

# ✅ Redis or in-memory blacklist store (example)
token_blacklist = set()  # Use Redis or similar in production

def is_token_blacklisted(token: str) -> bool:
    """ Check if token is blacklisted """
    return token in token_blacklist

def verify_token(token: str = Depends(oauth2_scheme)):
    #Logger.debug(f"🔍 Received token: {token}")

    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # ✅ Check if token is blacklisted
        if is_token_blacklisted(token):
            raise HTTPException(status_code=401, detail="Token has been logged out")

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": True})
        user_id: str = payload.get("sub")
        exp: int = payload.get("exp")
        totp_pending: bool = payload.get("totp_pending", False)

        if user_id is None or exp is None:
            raise credentials_exception

        # Access to regular APIs disallowed for totp_pending tokens
        if totp_pending:
            raise HTTPException(status_code=401, detail="2FA verification required")

        if datetime.now(timezone.utc) > datetime.fromtimestamp(exp, timezone.utc):
            raise HTTPException(status_code=401, detail="Token has expired")

        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise credentials_exception

