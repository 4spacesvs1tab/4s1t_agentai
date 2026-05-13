"""
Authentication API routes for 4S1T Agent AI system.
Handles login, logout, and user management endpoints.
"""
from datetime import timedelta
from typing import Dict, Any, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
import jwt
from jwt.exceptions import PyJWTError
from pydantic import BaseModel

from config.settings import settings
from core.csrf import require_csrf_token
from services.auth_service import get_auth_service, AuthService
from services.exceptions import AccountLockedError, DatabaseError, AuthError, ValidationError
from utils.logger import setup_logger
from utils.rate_limit import rate_limit

logger = setup_logger(__name__)

# Rate limiters: 5 attempts per 60 s per IP
_login_limiter = rate_limit(max_calls=5, window_seconds=60)
_mfa_limiter = rate_limit(max_calls=5, window_seconds=60)

# P3-3: fallback Retry-After when locked_until cannot be parsed (seconds)
_LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes

# Create router
router = APIRouter(prefix="/auth", tags=["authentication"])

# OAuth2 scheme for token verification
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class Token(BaseModel):
    """Token response model."""
    access_token: str
    token_type: str


class TokenData(BaseModel):
    """Token data model."""
    user_id: str = None


class UserCreate(BaseModel):
    """User creation request model."""
    username: str
    password: str


class UserResponse(BaseModel):
    """User response model."""
    id: str
    role: str
    is_active: bool
    created_at: str
    last_login: Optional[str] = None


async def get_current_user(token: str = Depends(oauth2_scheme), 
                          auth_service: AuthService = Depends(get_auth_service)):
    """
    Get current user from token.
    
    Args:
        token: JWT token from Authorization header
        auth_service: Authentication service dependency
        
    Returns:
        User data
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Decode JWT token
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("token_type", "access")
        
        if user_id is None:
            raise credentials_exception
        
        # Only allow access tokens for normal requests
        if token_type != "access":
            raise credentials_exception
            
        token_data = TokenData(user_id=user_id)
    except PyJWTError:
        raise credentials_exception
    
    # Get user from database
    query = "SELECT * FROM users WHERE id = ?"
    users = auth_service.db.execute_query(query, (token_data.user_id,))
    
    if not users:
        raise credentials_exception
    
    user = users[0]
    
    # Return user data (excluding password hash)
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
        "created_at": user["created_at"],
        "last_login": user["last_login"] if user["last_login"] else None
    }


async def get_current_user_for_mfa_enrollment(
    token: str = Depends(oauth2_scheme), 
    auth_service: AuthService = Depends(get_auth_service)
):
    """
    Get current user from MFA enrollment token.
    
    Args:
        token: JWT token from Authorization header
        auth_service: Authentication service dependency
        
    Returns:
        User data
        
    Raises:
        HTTPException: If token is invalid, not an enrollment token, or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired enrollment token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Decode JWT token
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("token_type", "access")
        
        if user_id is None or token_type != "mfa_enrollment":
            raise credentials_exception
            
        token_data = TokenData(user_id=user_id)
    except PyJWTError:
        raise credentials_exception
    
    # Get user from database
    query = "SELECT * FROM users WHERE id = ?"
    users = auth_service.db.execute_query(query, (token_data.user_id,))
    
    if not users:
        raise credentials_exception
    
    user = users[0]
    
    # Return user data (excluding password hash)
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
        "created_at": user["created_at"],
        "last_login": user["last_login"] if user["last_login"] else None
    }


@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(),
                auth_service: AuthService = Depends(get_auth_service),
                _rl=Depends(_login_limiter),
                _csrf=Depends(require_csrf_token)):
    """
    Authenticate user with credentials.
    If MFA is required, returns session for 2FA verification.
    Otherwise returns access token.
    
    Phase 2: Mandatory 2FA - Modified to support MFA flow
    """
    from services.mfa.service import MFAService
    
    logger.info(f"Login attempt for user: {form_data.username}")
    
    try:
        # Authenticate user with username and password
        user = auth_service.authenticate_user(form_data.username, form_data.password)
        if not user:
            logger.warning(f"Login failed for user: {form_data.username}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except AccountLockedError as e:
        # P3-3: account locked — RFC 7231 Retry-After in seconds
        from datetime import datetime, timezone
        try:
            locked_until = datetime.fromisoformat(e.locked_until)
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            retry_after = max(0, int((locked_until - datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            retry_after = _LOCKOUT_DURATION_SECONDS
        logger.warning(f"Login blocked — account locked for user '{form_data.username}' (retry_after={retry_after}s)")
        raise HTTPException(
            status_code=423,
            detail="Account temporarily locked due to repeated failed login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    except DatabaseError as e:
        logger.error(f"Database error during login for user {form_data.username}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AuthError as e:
        logger.error(f"Authentication service error for user {form_data.username}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service error",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Phase 2: Check MFA status
    mfa_service = MFAService()
    user_id = user["id"]
    
    mfa_status = mfa_service.get_user_mfa_status(user_id)
    
    # MFA is mandatory - check enrollment status
    if mfa_status["mfa_required"]:
        if not mfa_status.get("enrollment_complete", False):
            # User needs to enroll in MFA first
            logger.info(f"User {user_id} requires MFA enrollment")
            
            # Issue temporary token for enrollment only
            temp_token = auth_service.create_access_token(
                user, 
                expires_delta=timedelta(minutes=30),
                token_type="mfa_enrollment"
            )
            
            return {
                "requires_mfa_enrollment": True,
                "message": "Two-Factor Authentication setup required",
                "redirect": "/auth/2fa/enroll",
                "access_token": temp_token,
                "token_type": "bearer",
                "mfa_status": mfa_status
            }
        
        # MFA enrolled - create verification session
        logger.info(f"User {user_id} requires MFA verification")
        session_token = mfa_service.create_verification_session(user_id)
        
        return {
            "requires_mfa": True,
            "message": "Two-Factor Authentication required",
            "session_token": session_token,
            "mfa_status": mfa_status
        }
    
    # MFA not required (fallback for legacy/edge cases)
    # Create access token
    access_token = auth_service.create_access_token(user)
    
    logger.info(f"Login successful for user: {user['username']} (id: {user['id']})")
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/register", response_model=UserResponse)
async def register_user(user_data: UserCreate,
                        auth_service: AuthService = Depends(get_auth_service),
                        _csrf=Depends(require_csrf_token)):
    """
    Register a new user.
    
    Args:
        user_data: User registration data
        auth_service: Authentication service dependency
        
    Returns:
        Registered user data
        
    Raises:
        HTTPException: If user creation fails
    """
    logger.info(f"User registration attempt for username: {user_data.username}")
    
    try:
        # Create user with username and password
        success = auth_service.create_user(user_data.username, user_data.password)
        
        if not success:
            logger.warning("User registration failed")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User registration failed"
            )
        
        # Retrieve and return created user (get the most recent one with matching username)
        query = "SELECT * FROM users WHERE username = ? ORDER BY created_at DESC LIMIT 1"
        users = auth_service.db.execute_query(query, (user_data.username,))
        
        if not users:
            logger.error("User registration succeeded but user not found")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Registration succeeded but user not found"
            )
        
        user = users[0]
        logger.info(f"User registration successful: {user_data.username} (id: {user['id']})")
        
        # Prepare user response data
        user_response_data = {
            "id": user["id"],
        "username": user["username"],
            "role": user["role"],
            "is_active": bool(user["is_active"]),
            "created_at": user["created_at"]
        }
        
        # Only include last_login if it's not None
        if user["last_login"] is not None:
            user_response_data["last_login"] = user["last_login"]
        
        return UserResponse(**user_response_data)
        
    except HTTPException:
        raise

    except ValidationError as e:
        logger.warning(f"Registration validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except DatabaseError as e:
        logger.error(f"Database error during registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )

    except AuthError as e:
        logger.error(f"Authentication error during registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal authentication error"
        )

    except Exception as e:
        logger.error(f"Unexpected error during registration: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


class MFAVerifyRequest(BaseModel):
    """MFA verification request model."""
    session_token: str
    verification_code: str


@router.post("/verify-2fa")
async def verify_2fa(
    verify_data: MFAVerifyRequest,
    auth_service: AuthService = Depends(get_auth_service),
    _rl=Depends(_mfa_limiter),
):
    """
    Verify MFA code and complete authentication.
    Sets HTTP-only cookie for browser navigation and returns token for API use.
    
    Args:
        verify_data: MFA verification data (session_token and code)
        auth_service: Authentication service dependency
        
    Returns:
        Access token if verification successful
        
    Raises:
        HTTPException: If verification fails
    """
    from services.mfa.service import MFAService
    from fastapi.responses import JSONResponse
    
    logger.info("MFA verification attempt")
    
    try:
        mfa_service = MFAService()
        
        # Verify the MFA code
        result = mfa_service.verify_session(
            verify_data.session_token,
            verify_data.verification_code
        )
        
        if not result.get("success", False):
            logger.warning("MFA verification failed: invalid code")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid verification code",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Get user data
        user_id = result["user_id"]
        query = "SELECT * FROM users WHERE id = ?"
        users = auth_service.db.execute_query(query, (user_id,))
        
        if not users:
            logger.error(f"User not found after MFA verification: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User not found"
            )
        
        user = users[0]

        # Create access token with mfa_verified=True so require_2fa guards can
        # confirm the user actually completed TOTP in this session (not just enrolled).
        access_token = auth_service.create_access_token(
            user, extra_claims={"mfa_verified": True}
        )

        logger.info(f"MFA verification successful for user: {user['username']} (id: {user['id']})")

        # Create response with cookie for browser navigation
        response = JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "message": "Two-factor authentication successful"
        })
        
        # Set HTTP-only cookie for page navigation authentication
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite="lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MFA verification error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MFA verification failed"
        )


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: dict = Depends(get_current_user)):
    """
    Get current user information.
    
    Args:
        current_user: Current user data from token verification
        
    Returns:
        Current user data
    """
    logger.info(f"User profile requested for: {current_user['id']}")
    return UserResponse(**current_user)


# MFA Enrollment Endpoints
from pydantic import BaseModel
from typing import List

class MFASetupResponse(BaseModel):
    """MFA setup response with QR code."""
    qr_code_url: str
    secret: str
    backup_codes: List[str]


class MFASetupVerifyRequest(BaseModel):
    """MFA setup verification request."""
    code: str


class MFASetupVerifyResponse(BaseModel):
    """MFA setup verification response."""
    success: bool
    backup_codes: List[str]


@router.get("/mfa/setup")
async def get_mfa_setup(
    current_user: dict = Depends(get_current_user_for_mfa_enrollment)
):
    """
    Get MFA setup data (QR code and secret) for enrollment.
    Requires MFA enrollment token.
    """
    from services.mfa.service import MFAService
    import pyotp
    import qrcode
    import qrcode.image.svg
    import io
    import base64
    
    logger.info(f"MFA setup requested for user: {current_user['id']}")
    
    try:
        # Generate TOTP secret
        secret = pyotp.random_base32()
        
        # Generate QR code
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=current_user['id'],
            issuer_name="4S1T Agent AI"
        )
        
        # Generate QR code as base64
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        
        # Create image
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        # Generate backup codes
        import secrets
        backup_codes = [secrets.token_hex(4).upper() for _ in range(8)]
        
        # Store enrollment data temporarily (in memory or cache - using MFA methods table is fine)
        mfa_service = MFAService()
        # Store pending enrollment
        mfa_id = secrets.token_hex(16)
        mfa_service.db.execute_command("""
            INSERT OR REPLACE INTO mfa_methods (id, user_id, method_type, secret, backup_codes, is_enabled, created_at)
            VALUES (?, ?, 'totp_pending', ?, ?, 0, datetime('now'))
        """, (mfa_id, current_user['id'], secret, ','.join(backup_codes)))
        
        return {
            "qr_code_url": f"data:image/png;base64,{img_str}",
            "secret": secret,
            "backup_codes": backup_codes
        }
        
    except Exception as e:
        logger.error(f"MFA setup error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate MFA setup data"
        )


@router.post("/mfa/setup")
async def verify_mfa_setup(
    verify_data: MFASetupVerifyRequest,
    current_user: dict = Depends(get_current_user_for_mfa_enrollment)
):
    """
    Verify MFA setup code and complete enrollment.
    Requires MFA enrollment token.
    """
    from services.mfa.service import MFAService
    import pyotp
    
    logger.info(f"MFA setup verification for user: {current_user['id']}")
    
    try:
        mfa_service = MFAService()
        
        # Get pending enrollment data
        results = mfa_service.db.execute_query("""
            SELECT secret, backup_codes FROM mfa_methods
            WHERE user_id = ? AND method_type = 'totp_pending' AND is_enabled = 0
        """, (current_user['id'],))
        
        if not results:
            logger.warning(f"No pending MFA enrollment found for user: {current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No pending MFA enrollment found. Please start enrollment again."
            )
        
        pending = results[0]
        secret = pending['secret']
        backup_codes = pending['backup_codes'].split(',') if pending['backup_codes'] else []
        
        # Verify the code
        totp = pyotp.TOTP(secret)
        if not totp.verify(verify_data.code, valid_window=1):
            logger.warning(f"Invalid MFA setup code for user: {current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code. Please try again."
            )
        
        # Activate MFA
        mfa_service.db.execute_command("""
            UPDATE mfa_methods
            SET method_type = 'totp', is_enabled = 1
            WHERE user_id = ? AND method_type = 'totp_pending'
        """, (current_user['id'],))
        
        logger.info(f"MFA enrollment completed for user: {current_user['id']}")
        
        return {
            "success": True,
            "backup_codes": backup_codes
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MFA setup verification error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify MFA setup"
        )


class TokenExchangeRequest(BaseModel):
    """Token exchange request model."""
    pass  # No fields needed - uses Authorization header


@router.post("/token-exchange")
async def exchange_enrollment_token(
    request: TokenExchangeRequest,
    current_user: dict = Depends(get_current_user_for_mfa_enrollment),
    auth_service: AuthService = Depends(get_auth_service)
):
    """
    Exchange MFA enrollment token for a full access token after enrollment completion.
    Sets HTTP-only cookie for browser navigation and returns token for API use.
    Requires valid MFA enrollment token.
    """
    from fastapi.responses import JSONResponse
    
    logger.info(f"Token exchange requested for user: {current_user['id']}")
    
    try:
        from services.mfa.service import MFAService
        mfa_service = MFAService()
        
        # Verify user has completed MFA enrollment
        mfa_status = mfa_service.get_user_mfa_status(current_user['id'])
        
        if not mfa_status.get("enrollment_complete", False):
            logger.warning(f"Token exchange rejected - MFA enrollment not complete for user: {current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA enrollment must be completed before exchanging token"
            )
        
        # Get full user data
        query = "SELECT * FROM users WHERE id = ?"
        users = auth_service.db.execute_query(query, (current_user['id'],))
        
        if not users:
            logger.error(f"User not found during token exchange: {current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        user = users[0]

        # Create full access token. Enrollment itself is proof of MFA possession,
        # so mfa_verified=True is appropriate here.
        access_token = auth_service.create_access_token(
            user, extra_claims={"mfa_verified": True}
        )

        # Update last login
        auth_service.update_last_login(user['id'])
        
        logger.info(f"Token exchange successful for user: {user['id']}")
        
        # Create response with cookie for browser navigation
        response = JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "message": "Token exchange successful"
        })
        
        # Set HTTP-only cookie for page navigation authentication
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite="lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token exchange error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token exchange failed"
        )


@router.post("/refresh")
async def refresh_token(request: Request):
    """
    Silent token refresh for sliding session support.

    Called by the frontend activity monitor when the user is active.
    Validates the current cookie token and issues a fresh one with a
    full ACCESS_TOKEN_EXPIRE_MINUTES lifetime, transparently extending
    the session without any user interaction.

    Returns 401 if the current token is missing or already expired so
    the frontend can redirect to login.
    """
    from fastapi import Request as FastAPIRequest
    from fastapi.responses import JSONResponse
    from core.security import create_access_token

    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    # Re-issue a fresh token carrying the same claims (sub, mfa_verified, etc.)
    # but a new expiry and a new JTI.
    new_token = create_access_token({
        "sub": payload["sub"],
        "token_type": payload.get("token_type", "access"),
        "mfa_verified": payload.get("mfa_verified", False),
    })

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response
