#!/usr/bin/env python3
"""
CLI interface for 4S1T Agent AI system.
Provides command-line access to core functionality.
"""
import argparse
import asyncio
import json
import sys
import os
from typing import Dict, Any, Optional
from getpass import getpass

import httpx

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import Settings

settings = Settings()

class CLIInterface:
    """Command-line interface for 4S1T Agent AI system."""
    
    def __init__(self):
        self.base_url = f"http://{settings.HOST}:{settings.PORT}"
        self.token: Optional[str] = None
        self.client = httpx.AsyncClient()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
    
    async def login(self, username: str, password: str) -> bool:
        """Login to the system."""
        try:
            response = await self.client.post(
                f"{self.base_url}/auth/login",
                data={"username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code == 200:
                data = response.json()
                self.token = data["access_token"]
                print(f"✅ Logged in successfully as '{username}'")
                return True
            else:
                print(f"❌ Login failed: {response.json().get('detail', 'Unknown error')}")
                return False
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    async def register(self, username: str, password: str) -> bool:
        """Register a new user."""
        try:
            response = await self.client.post(
                f"{self.base_url}/auth/register",
                json={
                    "username": username,
                    "password": password
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                user_id = data.get("id", "Unknown")
                print(f"✅ User '{username}' (id: {user_id}) registered successfully")
                return True
            else:
                print(f"❌ Registration failed: {response.json().get('detail', 'Unknown error')}")
                return False
        except Exception as e:
            print(f"❌ Registration error: {e}")
            return False
    
    async def get_profile(self) -> Optional[Dict[str, Any]]:
        """Get current user profile."""
        if not self.token:
            print("❌ Not logged in")
            return None
        
        try:
            response = await self.client.get(
                f"{self.base_url}/auth/me",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to get profile: {response.json().get('detail', 'Unknown error')}")
                return None
        except Exception as e:
            print(f"❌ Profile error: {e}")
            return None
    
    async def health_check(self) -> Optional[Dict[str, Any]]:
        """Check system health."""
        try:
            response = await self.client.get(f"{self.base_url}/health")
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Health check failed: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return None
    
    async def list_mcp_tools(self) -> Optional[Dict[str, Any]]:
        """List available MCP tools."""
        try:
            response = await self.client.get(f"{self.base_url}/mcp/tools")
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to list tools: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Tools listing error: {e}")
            return None
    
    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call an MCP tool."""
        try:
            response = await self.client.post(
                f"{self.base_url}/mcp/tools/{tool_name}",
                json=arguments
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to call tool: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Tool call error: {e}")
            return None


async def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="4S1T Agent AI CLI")
    parser.add_argument("--host", default=settings.HOST, help="API host")
    parser.add_argument("--port", type=int, default=settings.PORT, help="API port")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Auth commands
    login_parser = subparsers.add_parser("login", help="Login to the system")
    login_parser.add_argument("-u", "--username", help="Username (will prompt if not provided)")
    login_parser.add_argument("-p", "--password", help="Password (will prompt if not provided)")
    
    register_parser = subparsers.add_parser("register", help="Register a new user")
    register_parser.add_argument("-u", "--username", help="Username (will prompt if not provided)")
    register_parser.add_argument("-p", "--password", help="Password (will prompt if not provided)")
    
    # User commands
    subparsers.add_parser("profile", help="Get current user profile")
    
    # System commands
    subparsers.add_parser("health", help="Check system health")
    
    # MCP commands
    subparsers.add_parser("tools", help="List available MCP tools")
    
    tool_call_parser = subparsers.add_parser("call", help="Call an MCP tool")
    tool_call_parser.add_argument("tool_name", help="Name of the tool to call")
    tool_call_parser.add_argument("arguments", nargs="*", help="Tool arguments in key=value format")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    async with CLIInterface() as cli:
        cli.base_url = f"http://{args.host}:{args.port}"
        
        if args.command == "login":
            username = args.username or input("Username: ")
            password = args.password or getpass("Password: ")
            await cli.login(username, password)
        
        elif args.command == "register":
            username = args.username or input("Username: ")
            password = args.password or getpass("Password: ")
            await cli.register(username, password)
        
        elif args.command == "profile":
            profile = await cli.get_profile()
            if profile:
                print(json.dumps(profile, indent=2))
        
        elif args.command == "health":
            health = await cli.health_check()
            if health:
                print(json.dumps(health, indent=2))
        
        elif args.command == "tools":
            tools = await cli.list_mcp_tools()
            if tools:
                print("Available MCP Tools:")
                for tool in tools.get("tools", []):
                    print(f"  - {tool['name']}: {tool.get('description', 'No description')}")
        
        elif args.command == "call":
            # Parse arguments
            arguments = {}
            for arg in args.arguments:
                if "=" in arg:
                    key, value = arg.split("=", 1)
                    # Try to parse as JSON, fallback to string
                    try:
                        arguments[key] = json.loads(value)
                    except json.JSONDecodeError:
                        arguments[key] = value
                else:
                    arguments[arg] = True
            
            result = await cli.call_mcp_tool(args.tool_name, arguments)
            if result:
                print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
