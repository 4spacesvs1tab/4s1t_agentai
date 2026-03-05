"""
Authy service integration for 2FA in 4S1T Agent AI
"""

import requests
import logging
from typing import Optional, Dict, Any
from config.settings import settings
from services.exceptions import MFAError

logger = logging.getLogger(__name__)


class AuthyService:
    """
    Service for Authy TOTP and Push Notification 2FA.
    
    Docs: https://www.twilio.com/docs/authy/api
    """
    
    def __init__(self):
        self.api_key = settings.AUTHY_API_KEY
        self.api_url = settings.AUTHY_API_URL.rstrip('/')
        self.headers = {
            "X-Authy-API-Key": self.api_key,
            "Content-Type": "application/json"
        }
    
    def register_user(self, email: str, phone: str, country_code: str = "1") -> Dict[str, Any]:
        """
        Register a new user with Authy.
        
        Returns:
            dict: Contains 'id' (Authy ID) on success
        """
        endpoint = f"{self.api_url}/protected/json/users/new"
        
        payload = {
            "user": {
                "email": email,
                "cellphone": phone,
                "country_code": country_code
            }
        }
        
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=self.headers,
                timeout=settings.AUTHY_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("success"):
                logger.info(f"Authy user registered: {email}")
                return {"success": True, "authy_id": data["user"]["id"]}
            else:
                raise MFAError(f"Authy registration failed: {data}")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Authy API error during registration: {e}")
            raise MFAError("Failed to register with Authy") from e
    
    def verify_totp(self, authy_id: str, token: str) -> bool:
        """
        Verify a TOTP token from the Authy app.
        
        Args:
            authy_id: The Authy user ID
            token: 6-8 digit TOTP code
            
        Returns:
            bool: True if valid, False otherwise
        """
        endpoint = f"{self.api_url}/protected/json/verify/{token}/{authy_id}"
        
        try:
            response = requests.get(
                endpoint,
                headers=self.headers,
                timeout=settings.AUTHY_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            is_valid = data.get("success", False) and data.get("token") == "is valid"
            
            if is_valid:
                logger.info(f"Valid TOTP for Authy ID: {authy_id}")
            else:
                logger.warning(f"Invalid TOTP attempt for Authy ID: {authy_id}")
            
            return is_valid
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Authy API error during TOTP verification: {e}")
            return False
    
    def send_push_notification(self, authy_id: str, details: Dict[str, str]) -> Dict[str, Any]:
        """
        Send a push notification (OneTouch) approval request.
        
        Args:
            authy_id: The Authy user ID
            details: Dict with 'message', 'details' keys
            
        Returns:
            dict: Contains 'approval_request' with UUID
        """
        endpoint = f"{self.api_url}/onetouch/json/users/{authy_id}/approval_requests"
        
        payload = {
            "message": details.get("message", "Approval required"),
            "details": details.get("details", {}),
            "seconds_to_expire": 300  # 5 minutes
        }
        
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=self.headers,
                timeout=settings.AUTHY_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("success"):
                logger.info(f"Push notification sent to Authy ID: {authy_id}")
                return {
                    "success": True,
                    "approval_request_uuid": data["approval_request"]["uuid"]
                }
            else:
                raise MFAError(f"Push notification failed: {data}")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Authy API error during push notification: {e}")
            raise MFAError("Failed to send push notification") from e
    
    def check_push_status(self, approval_request_uuid: str) -> Dict[str, Any]:
        """
        Check the status of a push notification approval.
        
        Returns:
            dict: Contains 'status' (pending/approved/denied)
        """
        endpoint = f"{self.api_url}/onetouch/json/approval_requests/{approval_request_uuid}"
        
        try:
            response = requests.get(
                endpoint,
                headers=self.headers,
                timeout=settings.AUTHY_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("success"):
                status = data["approval_request"]["status"]
                return {"success": True, "status": status}
            else:
                return {"success": False, "error": "Failed to get status"}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Authy API error checking push status: {e}")
            return {"success": False, "error": str(e)}
    
    def generate_qr_code(self, authy_id: str) -> str:
        """
        Generate a QR code for manual TOTP setup (backup method).
        
        Returns:
            str: Base64-encoded QR code image
        """
        # This uses Authy's API to get the QR code for manual entry
        endpoint = f"{self.api_url}/protected/json/users/{authy_id}/secret"
        
        try:
            response = requests.post(
                endpoint,
                headers=self.headers,
                timeout=settings.AUTHY_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("success"):
                # Generate QR code from the seed
                import qrcode
                import base64
                from io import BytesIO
                
                seed = data["secret"]
                # Format as standard TOTP URI
                uri = f"otpauth://totp/4S1T-Agent:{authy_id}?secret={seed}&issuer=4S1T-Agent"
                
                qr = qrcode.QRCode(version=1, box_size=10, border=5)
                qr.add_data(uri)
                qr.make(fit=True)
                
                img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                return base64.b64encode(buffer.getvalue()).decode()
            else:
                raise MFAError("Failed to generate QR code")
                
        except Exception as e:
            logger.error(f"Error generating QR code: {e}")
            raise MFAError("Failed to generate QR code") from e


# Singleton instance
authy_service: Optional[AuthyService] = None


def get_authy_service() -> AuthyService:
    global authy_service
    if authy_service is None:
        authy_service = AuthyService()
    return authy_service
