"""
MFA (2FA) API routes for 4S1T Agent AI system.
Handles MFA enrollment, verification, and management.
"""
from typing import Dict, Any, Optional
import logging
import qrcode
import io
import base64
import pyotp

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from cachetools import TTLCache

from config.settings import settings
from services.auth_service import get_auth_service, AuthService
from api.security_dependencies import require_auth
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Create router
router = APIRouter(prefix="/auth/2fa", tags=["authentication"])


class MFASetupRequest(BaseModel):
    """Request for MFA setup verification"""
    token: str  # The TOTP token from the authenticator app


class MFAVerifyRequest(BaseModel):
    """Request for MFA verification"""
    token: str  # The TOTP token for verification


class MFAStatusResponse(BaseModel):
    """Response with MFA status"""
    mfa_enabled: bool
    backup_codes_remaining: int
    message: str


class MFASetupResponse(BaseModel):
    """Response for MFA setup"""
    qr_code: str  # Base64-encoded QR code image
    backup_codes: list
    message: str


@router.get("/setup")
async def mfa_setup(current_user: Dict[str, Any] = Depends(require_auth),
                     auth_service: AuthService = Depends(get_auth_service)):
    """
    Get QR code and backup codes for MFA setup.
    This is used when a user needs to enroll in MFA for the first time.
    """
    user_id = current_user["id"]
    
    # Check if MFA is already configured
    user_data = auth_service.get_user_by_id(user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user_data.get("mfa_secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already configured for this user"
        )
    
    # Generate new MFA secret
    mfa_secret = auth_service.generate_mfa_secret()
    backup_codes = auth_service.generate_backup_codes()
    
    # Store MFA secret temporarily (will be confirmed after verification)
    temp_storage_key = f"temp_mfa_setup_{user_id}"
    temp_data = {
        "mfa_secret": mfa_secret,
        "backup_codes": backup_codes
    }
    
    # For now, store in memory - in production, use Redis or similar
    # This is a temporary storage mechanism
    setup_cache[temp_storage_key] = temp_data
    
    # Generate QR code for TOTP
    totp = pyotp.TOTP(mfa_secret)
    provisioning_uri = totp.provisioning_uri(
        name=user_data.get("username", str(user_id)),
        issuer_name="4S1T Agent AI"
    )
    
    # Create QR code
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=5
    )
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode()
    
    return {
        "qr_code": f"data:image/png;base64,{img_base64}",
        "backup_codes": backup_codes,
        "message": "Scan this QR code with Authy or any authenticator app"
    }


# Global cache for temporary MFA setup data
setup_cache = TTLCache(maxsize=100, ttl=600)

@router.post("/verify", response_model=MFAStatusResponse)
async def mfa_verify_setup(setup_request: MFASetupRequest,
                          current_user: Dict[str, Any] = Depends(require_auth),
                          auth_service: AuthService = Depends(get_auth_service)):
    """
    Verify MFA token during setup process.
    If verification succeeds, MFA is permanently enabled for the user.
    """
    user_id = current_user["id"]
    
    # Retrieve temporary MFA data
    temp_storage_key = f"temp_mfa_setup_{user_id}"
    setup_cache = setup_cache
    
    temp_data = setup_cache.get(temp_storage_key)
    if not temp_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA setup session expired or not initiated. Please start setup again."
        )
    
    mfa_secret = temp_data["mfa_secret"]
    backup_codes = temp_data["backup_codes"]
    
    # Verify the token
    totp = pyotp.TOTP(mfa_secret)
    if not totp.verify(setup_request.token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token. Please try again."
        )
    
    # Token verified successfully - store MFA credentials permanently
    auth_service._store_mfa_credentials(user_id, mfa_secret, backup_codes)
    
    # Clear temporary data
    del setup_cache[temp_storage_key]
    
    return {
        "mfa_enabled": True,
        "backup_codes_remaining": len(backup_codes),
        "message": "MFA setup completed successfully. Save your backup codes in a safe place!"
    }


@router.post("/verify-login")
async def mfa_verify_login(verify_request: MFAVerifyRequest,
                          current_user: Dict[str, Any] = Depends(require_auth),
                          auth_service: AuthService = Depends(get_auth_service)):
    """
    Verify MFA token during login process (for already-configured MFA).
    """
    user_id = current_user["id"]
    
    # Verify the token
    if not auth_service.verify_mfa(user_id, verify_request.token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MFA token"
        )
    
    return {
        "message": "MFA verification successful",
        "mfa_valid": True
    }


@router.get("/status", response_model=MFAStatusResponse)
async def mfa_status(current_user: Dict[str, Any] = Depends(require_auth),
                     auth_service: AuthService = Depends(get_auth_service)):
    """
    Get MFA status for the current user.
    """
    user_id = current_user["id"]
    
    user_data = auth_service.get_user_by_id(user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Count remaining backup codes
    query = "SELECT COUNT(*) FROM mfa_backup_codes WHERE user_id = ? AND used = 0"
    result = auth_service.db.execute_query(query, (user_id,))
    backup_codes_remaining = result[0][0] if result else 0
    
    return {
        "mfa_enabled": bool(user_data.get("mfa_secret")),
        "backup_codes_remaining": backup_codes_remaining,
        "message": "MFA status retrieved successfully"
    }


@router.post("/backup-codes/refresh")
async def mfa_refresh_backup_codes(current_user: Dict[str, Any] = Depends(require_auth),
                                   auth_service: AuthService = Depends(get_auth_service)):
    """
    Generate new backup codes for the user.
    This will invalidate all existing unused backup codes.
    """
    user_id = current_user["id"]
    
    # Generate new backup codes
    new_codes = auth_service.generate_backup_codes()
    
    # Store them (this will replace old ones in the implementation)
    # Implementation of _replace_backup_codes would go in auth_service
    auth_service._replace_backup_codes(user_id, new_codes)
    
    return {
        "backup_codes": new_codes,
        "message": "New backup codes generated. Save them in a safe place!"
    }


@router.post("/disable")
async def mfa_disable(current_user: Dict[str, Any] = Depends(require_auth),
                      auth_service: AuthService = Depends(get_auth_service)):
    """
    Disable MFA for the current user.
    WARNING: This should require additional verification in production!
    """
    user_id = current_user["id"]
    
    # This is a security-sensitive operation
    # In production, require a recent password re-entry or admin approval
    
    # Clear MFA secret
    query = "UPDATE users SET mfa_secret = NULL WHERE id = ?"
    auth_service.db.execute_command(query, (user_id,))
    
    # Clear backup codes
    query = "DELETE FROM mfa_backup_codes WHERE user_id = ?"
    auth_service.db.execute_command(query, (user_id,))
    
    return {
        "mfa_enabled": False,
        "message": "MFA has been disabled for your account"
    }
