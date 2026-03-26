"""
Encryption utility for B-SNAP
Implements AES-256-GCM for encrypting sensitive data like camera passwords
"""

import os
import base64
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger("encryption")

# Get encryption key from environment
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Marker to identify encrypted data
ENCRYPTION_MARKER = "ENC:"


def _get_key():
    """Get encryption key from environment or raise error."""
    if not ENCRYPTION_KEY:
        logger.error("ENCRYPTION_KEY environment variable not set!")
        raise ValueError("ENCRYPTION_KEY environment variable is required for encryption")
    
    # Derive 32-byte key from the provided key using SHA-256
    import hashlib
    key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return key


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt a string value using AES-256-GCM.
    
    Args:
        plaintext: The string to encrypt
        
    Returns:
        Encrypted string with marker prefix (base64 encoded)
    """
    if not plaintext:
        return plaintext
    
    # Skip if already encrypted
    if plaintext.startswith(ENCRYPTION_MARKER):
        return plaintext
    
    try:
        key = _get_key()
        aesgcm = AESGCM(key)
        
        # Generate random nonce (12 bytes for GCM)
        nonce = os.urandom(12)
        
        # Encrypt the plaintext
        plaintext_bytes = plaintext.encode('utf-8')
        ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)
        
        # Combine nonce + ciphertext and encode as base64
        combined = nonce + ciphertext
        encrypted_b64 = base64.urlsafe_b64encode(combined).decode('utf-8')
        
        return f"{ENCRYPTION_MARKER}{encrypted_b64}"
        
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise


def decrypt_value(encrypted: str) -> str:
    """
    Decrypt a string value that was encrypted with encrypt_value.
    
    Args:
        encrypted: The encrypted string (with marker prefix)
        
    Returns:
        Decrypted plaintext string
    """
    if not encrypted:
        return encrypted
    
    # Return as-is if not encrypted
    if not encrypted.startswith(ENCRYPTION_MARKER):
        return encrypted
    
    try:
        key = _get_key()
        aesgcm = AESGCM(key)
        
        # Remove marker and decode base64
        encrypted_b64 = encrypted[len(ENCRYPTION_MARKER):]
        combined = base64.urlsafe_b64decode(encrypted_b64.encode('utf-8'))
        
        # Extract nonce (first 12 bytes) and ciphertext
        nonce = combined[:12]
        ciphertext = combined[12:]
        
        # Decrypt
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext_bytes.decode('utf-8')
        
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        # Return original if decryption fails (might be legacy plain text)
        return encrypted


def is_encrypted(value: str) -> bool:
    """Check if a value is already encrypted."""
    return value and value.startswith(ENCRYPTION_MARKER)


# For database column encryption/decryption
def encrypt_for_db(plaintext: str) -> str:
    """Encrypt value for database storage. Returns original if encryption fails."""
    try:
        return encrypt_value(plaintext)
    except Exception as e:
        logger.warning(f"Failed to encrypt value, storing as plain text: {e}")
        return plaintext


def decrypt_from_db(encrypted: str) -> str:
    """Decrypt value from database. Returns original if decryption fails."""
    try:
        return decrypt_value(encrypted)
    except Exception as e:
        logger.warning(f"Failed to decrypt value, returning as-is: {e}")
        return encrypted
