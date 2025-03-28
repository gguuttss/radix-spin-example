#!/usr/bin/env python3
import requests
import secrets
import os
import binascii
from dotenv import load_dotenv
import json
from typing import Tuple, Optional, Dict, Any, List
import asyncio

# Load environment variables
load_dotenv()

# Get environment variables
NETWORK_ID = int(os.getenv("NETWORK_ID", "0x01"), 0)  # Default to mainnet
GAME_OWNER_TELEGRAM_ID = int(os.getenv("GAME_OWNER_TELEGRAM_ID", "0"))
XRD_ADDRESS = os.getenv("XRD_ADDRESS", "resource_rdx1tknxxxxxxxxxradxrdxxxxxxxxx009923554798xxxxxxxxxradxrd")

# Simulation mode toggle
SIMULATION_MODE = False  # Hardcoded to False to test real toolkit

# Track whether we're using real toolkit or simulation
USING_REAL_TOOLKIT = False

# Import Radix Engine Toolkit if not in simulation mode
if not SIMULATION_MODE:
    try:
        from radix_engine_toolkit import (
            TransactionBuilder, TransactionHeader, TransactionManifest, 
            NotarizedTransaction, Instructions, PrivateKey, PublicKey, 
            Address, derive_virtual_account_address_from_public_key, 
            TransactionHash, Message
        )
        USING_REAL_TOOLKIT = True
        print("Successfully imported Radix Engine Toolkit")
    except ImportError as e:
        print(f"Warning: Failed to import radix_engine_toolkit: {e}")
        print("Falling back to simulation mode")
        SIMULATION_MODE = True
    except Exception as e:
        print(f"Warning: Error initializing radix_engine_toolkit: {e}")
        print("Falling back to simulation mode")
        SIMULATION_MODE = True

# If we're in simulation mode, don't try to import toolkit
if SIMULATION_MODE:
    print("Using simulation mode for Radix integration")

class RadixClient:
    """Client for interacting with the Radix network."""
    
    BASE_URL = os.getenv("RADIX_GATEWAY_API_URL", "https://mainnet.radixdlt.com")
    
    @staticmethod
    async def current_epoch() -> int:
        """Get the current epoch from the Radix network."""
        if SIMULATION_MODE:
            return 42  # Simulation mode

        try:
            response = requests.post(f"{RadixClient.BASE_URL}/status/gateway-status")
            response.raise_for_status()
            data = response.json()
            return data['ledger_state']['epoch']
        except Exception as e:
            print(f"Error fetching current epoch: {e}")
            raise

    @staticmethod
    async def submit_transaction(transaction) -> dict:
        """Submit a transaction to the Radix network."""
        if SIMULATION_MODE:
            return {"status": "SUCCESS", "details": {"transaction_id": "sim_transaction_id"}}

        try:
            transaction_hex = transaction.compile().hex()
            payload = {"notarized_transaction_hex": transaction_hex}
            response = requests.post(f"{RadixClient.BASE_URL}/transaction/submit", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error submitting transaction: {e}")
            raise

    @staticmethod
    async def get_entity_details(addresses: List[str]) -> dict:
        """Get details for entities (accounts)."""
        if SIMULATION_MODE:
            # Return simulated entity details for testing
            return {
                "items": [
                    {
                        "address": addresses[0],
                        "fungible_resources": {
                            "total_count": 1,
                            "items": [
                                {
                                    "resource_address": XRD_ADDRESS,
                                    "vaults": {
                                        "items": [
                                            {
                                                "amount": "10000"
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

        try:
            payload = {
                "addresses": addresses,
                "aggregation_level": "Vault"
            }
            response = requests.post(f"{RadixClient.BASE_URL}/state/entity/details", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error getting entity details: {e}")
            raise

def random_nonce() -> int:
    """Generate a random nonce for transactions."""
    return secrets.randbelow(0xFFFFFFFF)

def create_radix_account() -> Tuple[str, str, str]:
    """Create a new Radix account and return the address, private key, and public key as strings."""
    if not SIMULATION_MODE and USING_REAL_TOOLKIT:
        # Use actual Radix Engine Toolkit
        private_key_bytes = secrets.token_bytes(32)
        private_key = PrivateKey.new_secp256k1(private_key_bytes)
        public_key = private_key.public_key()
        account_address = derive_virtual_account_address_from_public_key(
            public_key, NETWORK_ID
        )
        
        # Convert to strings - the actual format depends on the Radix toolkit implementation
        # Using hex representation instead of as_str() which might not exist
        private_key_str = private_key_bytes.hex()
        public_key_str = public_key.compressed_bytes.hex() if hasattr(public_key, 'compressed_bytes') else str(public_key)
        address_str = account_address.as_str() if hasattr(account_address, 'as_str') else str(account_address)
        
        return (
            address_str,
            private_key_str,
            public_key_str
        )
    else:
        # Simulation mode - generate random hex strings
        private_key = binascii.hexlify(secrets.token_bytes(32)).decode('utf-8')
        public_key = binascii.hexlify(secrets.token_bytes(32)).decode('utf-8')
        address = f"sim_address_{binascii.hexlify(secrets.token_bytes(8)).decode('utf-8')}"
        
        return (address, private_key, public_key)

async def get_radix_balance(address: str) -> float:
    """Get the balance of a Radix account."""
    if SIMULATION_MODE:
        # In simulation mode, just return a default balance
        return 10000.0
    
    # Get entity details from Radix API
    entity_details = await RadixClient.get_entity_details([address])
    
    # Extract XRD balance
    try:
        print(f"Getting balance for address: {address}")
        
        # Debug output to check structure
        if "items" not in entity_details or not entity_details["items"]:
            print("No items found in entity_details response")
            return 0.0
            
        # Check the fungible_resources field which is directly under each item
        if "fungible_resources" in entity_details["items"][0]:
            # New format - fungible_resources is directly under the item
            fungible_resources = entity_details["items"][0]["fungible_resources"]
            if "items" in fungible_resources:
                for resource_item in fungible_resources["items"]:
                    if resource_item["resource_address"] == XRD_ADDRESS:
                        # Get amount from the first vault
                        if resource_item["vaults"]["items"]:
                            amount = resource_item["vaults"]["items"][0]["amount"]
                            return float(amount)
        
        # Alternative format - check if in the older format under details.state
        if "details" in entity_details["items"][0] and "state" in entity_details["items"][0]["details"]:
            state = entity_details["items"][0]["details"]["state"]
            if "fungible_resources" in state and XRD_ADDRESS in state["fungible_resources"]:
                return float(state["fungible_resources"][XRD_ADDRESS]["amount"])
        
        # If we got here, we couldn't find the XRD balance
        print(f"Could not find XRD balance in entity details: {entity_details}")
        return 0.0
    except (KeyError, IndexError, ValueError) as e:
        print(f"Error extracting balance from entity details: {e}")
        print(f"Entity details: {json.dumps(entity_details, indent=2)}")
        return 0.0

async def check_transaction_status(transaction_id: str) -> dict:
    """Check the status of a transaction and wait for it to be committed."""
    if SIMULATION_MODE:
        return {"status": "CommittedSuccess"}

    url = f"{RadixClient.BASE_URL}/transaction/status"
    payload = {"intent_hash": transaction_id}
    
    # Try up to 10 times, waiting 1 second between attempts
    for _ in range(10):
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            status = result.get("status", "Unknown")
            
            # Return immediately if we have a final status
            if status in ["CommittedSuccess", "CommittedFailure", "Rejected"]:
                return result
            
            # Wait a second before trying again for Pending or Unknown status
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"Error checking transaction status: {e}")
            await asyncio.sleep(1)
    
    # If we get here, we timed out waiting for a final status
    return {"status": "Unknown", "error_message": "Timed out waiting for transaction confirmation"}

async def submit_transaction_with_manifest(
    manifest_string: str, 
    sender_address: str, 
    private_key_str: str, 
    public_key_str: str,
    message: str = None  # Add message parameter
) -> Dict[str, Any]:
    """Submit a transaction with the given manifest string and optional message."""
    if SIMULATION_MODE:
        # Simulated transaction submission
        print(f"SIMULATION: Transaction with manifest: {manifest_string}")
        print(f"SIMULATION: Message: {message}")
        transaction_id = f"sim_tx_{binascii.hexlify(secrets.token_bytes(4)).decode('utf-8')}"
        return {
            "transaction_id": transaction_id,
            "status": "CommittedSuccess"
        }
    
    if USING_REAL_TOOLKIT:    
        try:
            # Create transaction manifest
            manifest = TransactionManifest(
                Instructions.from_string(manifest_string, NETWORK_ID),
                []  # No attached blobs
            )
            manifest.statically_validate()
            
            # Convert string representations to objects
            try:
                private_key_bytes = bytes.fromhex(private_key_str)
                private_key = PrivateKey.new_secp256k1(private_key_bytes)
                public_key = private_key.public_key()
            except Exception as e:
                print(f"Error converting keys from hex: {e}")
                try:
                    private_key = PrivateKey.from_str(private_key_str)
                    public_key = PublicKey.from_str(public_key_str)
                except Exception as e2:
                    print(f"Error converting keys using from_str: {e2}")
                    return {"error": "Failed to process keys for transaction"}
            
            # Get current epoch for transaction validity window
            current_epoch = await RadixClient.current_epoch()
            
            # Build and notarize transaction
            transaction = (
                TransactionBuilder()
                .header(
                    TransactionHeader(
                        NETWORK_ID,
                        current_epoch,
                        current_epoch + 10,  # Valid for 10 epochs
                        random_nonce(),
                        public_key,
                        True,  # is_notary
                        0,     # tip_percentage
                    )
                )
                .manifest(manifest)
                .message(Message.NONE())  # Go back to what we know works
                .notarize_with_private_key(private_key)
            )
            
            # Get transaction hash
            transaction_id = transaction.intent_hash().as_str()
            
            # Submit transaction
            submit_response = await RadixClient.submit_transaction(transaction)
            
            # Check transaction status
            status_result = await check_transaction_status(transaction_id)
            
            # Return combined result
            return {
                "transaction_id": transaction_id,
                "status": status_result.get("status", "Unknown"),
                "error_message": status_result.get("error_message", "")
            }
            
        except Exception as e:
            print(f"Error submitting transaction: {e}")
            return {"error": str(e)}
    else:
        print("Error: Attempting to use real toolkit in simulation mode")
        return {"error": "Configuration error"}

def buy_vouchers_manifest(
    player_address: str,
    game_address: str,
    voucher_cost: float,
    voucher_amount: int
) -> str:
    """Generate manifest for buying vouchers."""
    total_cost = voucher_cost * voucher_amount
    
    return f"""
        CALL_METHOD
            Address("{player_address}")
            "lock_fee"
            Decimal("2")
        ;
        
        # Withdraw tokens from player account
        CALL_METHOD
            Address("{player_address}")
            "withdraw"
            Address("{XRD_ADDRESS}")
            Decimal("{total_cost}")
        ;
        
        TAKE_FROM_WORKTOP
            Address("{XRD_ADDRESS}")
            Decimal("{total_cost}")
            Bucket("payment")
        ;
        
        # Deposit tokens to game account
        CALL_METHOD
            Address("{game_address}")
            "try_deposit_or_abort"
            Bucket("payment")
            Enum<0u8>( )
        ;
    """

def spin_manifest(
    player_address: str,
    game_address: str,
    spin_amount: float,
    num_spins: int = 1
) -> str:
    """Generate manifest for spinning with XRD.
    Player sends XRD to game account, and if they win, game account sends winnings back minus fee."""
    total_amount = spin_amount * num_spins
    return f"""
        # Player locks the fee
        CALL_METHOD
            Address("{player_address}")
            "lock_fee"
            Decimal("0.5")
        ;
        
        # Withdraw tokens from player account
        CALL_METHOD
            Address("{player_address}")
            "withdraw"
            Address("{XRD_ADDRESS}")
            Decimal("{total_amount}")
        ;
        
        TAKE_FROM_WORKTOP
            Address("{XRD_ADDRESS}")
            Decimal("{total_amount}")
            Bucket("payment")
        ;
        
        # Deposit tokens to game account
        CALL_METHOD
            Address("{game_address}")
            "try_deposit_or_abort"
            Bucket("payment")
            Enum<0u8>( )
        ;
    """

def claim_winnings_manifest(
    player_address: str,
    game_address: str,
    winnings_amount: float
) -> str:
    """Generate manifest for claiming winnings.
    
    The game account pays the network fee and signs the transaction.
    0.5 token is deducted from the winnings as a fee.
    """
    # Deduct 0.5 token for transaction fee
    actual_payout = max(0, winnings_amount - 0.5)
    
    return f"""
        # Game account locks the fee
        CALL_METHOD
            Address("{game_address}")
            "lock_fee"
            Decimal("2")
        ;
        
        # Withdraw winnings from game account
        CALL_METHOD
            Address("{game_address}")
            "withdraw"
            Address("{XRD_ADDRESS}")
            Decimal("{actual_payout}")
        ;
        
        TAKE_FROM_WORKTOP
            Address("{XRD_ADDRESS}")
            Decimal("{actual_payout}")
            Bucket("winnings")
        ;
        
        # Deposit winnings to player account
        CALL_METHOD
            Address("{player_address}")
            "try_deposit_or_abort"
            Bucket("winnings")
            Enum<0u8>( )
        ;
    """

def withdraw_tokens_manifest(
    player_address: str,
    destination_address: str, 
    amount: float
) -> str:
    """Generate manifest for withdrawing tokens to another account."""
    return f"""
        CALL_METHOD
            Address("{player_address}")
            "lock_fee"
            Decimal("1")
        ;
        
        # Withdraw tokens from player account
        CALL_METHOD
            Address("{player_address}")
            "withdraw"
            Address("{XRD_ADDRESS}")
            Decimal("{amount-1.000001}")
        ;
        
        TAKE_ALL_FROM_WORKTOP
            Address("{XRD_ADDRESS}")
            Bucket("tokens")
        ;
        
        # Deposit tokens to destination account
        CALL_METHOD
            Address("{destination_address}")
            "try_deposit_or_abort"
            Bucket("tokens")
            Enum<0u8>( )
        ;
    """

def send_winnings_manifest(
    game_address: str,
    player_address: str,
    winnings_amount: float
) -> str:
    """Generate manifest for sending winnings to player.
    
    The game account pays the network fee and signs the transaction.
    0.5 token is deducted from the winnings as a fee.
    """
    # Deduct 0.5 token for transaction fee
    actual_payout = max(0, winnings_amount - 0.5)
    
    return f"""
        # Game account locks the fee
        CALL_METHOD
            Address("{game_address}")
            "lock_fee"
            Decimal("2")
        ;
        
        # Withdraw winnings from game account
        CALL_METHOD
            Address("{game_address}")
            "withdraw"
            Address("{XRD_ADDRESS}")
            Decimal("{actual_payout}")
        ;
        
        TAKE_FROM_WORKTOP
            Address("{XRD_ADDRESS}")
            Decimal("{actual_payout}")
            Bucket("winnings")
        ;
        
        # Deposit winnings to player account
        CALL_METHOD
            Address("{player_address}")
            "try_deposit_or_abort"
            Bucket("winnings")
            Enum<0u8>( )
        ;
    """

async def send_winnings_with_retry(
    game_address: str,
    game_private_key: str,
    game_public_key: str,
    player_address: str,
    winnings_amount: float,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Send winnings to player with retry logic."""
    for attempt in range(max_retries):
        try:
            manifest = send_winnings_manifest(
                game_address,
                player_address,
                winnings_amount
            )
            
            result = await submit_transaction_with_manifest(
                manifest,
                game_address,
                game_private_key,
                game_public_key
            )
            
            if "error" in result:
                print(f"Attempt {attempt + 1} failed: {result['error']}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)  # Wait before retrying
                    continue
                return result
            
            # Check transaction status
            transaction_status = result.get("status", "")
            if SIMULATION_MODE or transaction_status == "CommittedSuccess":
                return result
            elif attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed with status: {transaction_status}")
                await asyncio.sleep(1)
                continue
            else:
                return {"error": f"Transaction failed after {max_retries} attempts with status: {transaction_status}"}
                
        except Exception as e:
            print(f"Attempt {attempt + 1} failed with error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            return {"error": str(e)}
    
    return {"error": f"Failed to send winnings after {max_retries} attempts"}

async def verify_payment_received(game_address: str, expected_amount: float, transaction_id: str, timeout_seconds: int = 30) -> bool:
    """Verify if a payment transaction has been committed successfully.
    Returns True if transaction is committed within timeout, False otherwise."""
    if SIMULATION_MODE:
        return True  # In simulation mode, always assume payment is received
        
    try:
        # Check the transaction status
        status_result = await check_transaction_status(transaction_id)
        return status_result.get("status") == "CommittedSuccess"
    except Exception as e:
        print(f"Error verifying payment: {e}")
        return False

def settle_spin_manifest(
    game_address: str,
    player_address: str,
    net_result: float,
    num_spins: int = 1
) -> str:
    """Generate manifest for settling spin results.
    If net_result is positive, game pays player.
    If net_result is negative, player pays game."""
    if net_result > 0:
        # Game pays player
        return f"""
            CALL_METHOD
                Address("{game_address}")
                "lock_fee"
                Decimal("0.5")
            ;
            
            CALL_METHOD
                Address("{game_address}")
                "withdraw"
                Address("{XRD_ADDRESS}")
                Decimal("{net_result}")
            ;
            
            TAKE_FROM_WORKTOP
                Address("{XRD_ADDRESS}")
                Decimal("{net_result}")
                Bucket("winnings")
            ;
            
            CALL_METHOD
                Address("{player_address}")
                "try_deposit_or_abort"
                Bucket("winnings")
                Enum<0u8>( )
            ;
        """
    else:
        # Player pays game
        return f"""
            CALL_METHOD
                Address("{player_address}")
                "lock_fee"
                Decimal("0.5")
            ;
            
            CALL_METHOD
                Address("{player_address}")
                "withdraw"
                Address("{XRD_ADDRESS}")
                Decimal("{abs(net_result)}")
            ;
            
            TAKE_FROM_WORKTOP
                Address("{XRD_ADDRESS}")
                Decimal("{abs(net_result)}")
                Bucket("payment")
            ;
            
            CALL_METHOD
                Address("{game_address}")
                "try_deposit_or_abort"
                Bucket("payment")
                Enum<0u8>( )
            ;
        """

# For testing the module
if __name__ == "__main__":
    async def test_radix_integration():
        print("Testing Radix integration module...")
        
        address, private_key, public_key = create_radix_account()
        print(f"Created account: {address}")
        print(f"Private key: {private_key[:10]}...")
        print(f"Public key: {public_key[:10]}...")
        
        balance = await get_radix_balance(address)
        print(f"Account balance: {balance} XRD")
        
        if SIMULATION_MODE:
            print("Running in simulation mode. No actual blockchain transactions will occur.")
    
    # Run the async test function
    asyncio.run(test_radix_integration()) 