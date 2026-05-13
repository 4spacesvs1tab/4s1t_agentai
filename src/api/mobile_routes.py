"""
Mobile web interface routes for 4S1T Agent AI system.
Provides mobile-optimized web interface for user interaction.
"""
from typing import Dict, Any, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse

from services.auth_service import get_auth_service, AuthService
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Create router
router = APIRouter(prefix="/mobile", tags=["mobile"])

# Mobile-optimized HTML templates
MOBILE_BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>4S1T Agent AI - Mobile</title>
    <style>
        :root {
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --secondary: #64748b;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --light: #f8fafc;
            --dark: #0f172a;
            --border: #e2e8f0;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #f8fafc;
            color: var(--dark);
            line-height: 1.6;
            padding: 0;
            margin: 0;
        }
        
        .mobile-container {
            max-width: 100%;
            padding: 0;
        }
        
        header {
            background: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            padding: 1rem;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .header-content {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.25rem;
            font-weight: bold;
            color: var(--primary);
        }
        
        .menu-btn {
            background: none;
            border: none;
            font-size: 1.5rem;
            cursor: pointer;
            color: var(--secondary);
        }
        
        .mobile-menu {
            position: fixed;
            top: 0;
            left: 0;
            width: 80%;
            height: 100%;
            background: white;
            box-shadow: 2px 0 5px rgba(0,0,0,0.1);
            z-index: 1000;
            transform: translateX(-100%);
            transition: transform 0.3s ease;
        }
        
        .mobile-menu.open {
            transform: translateX(0);
        }
        
        .menu-header {
            background: var(--primary);
            color: white;
            padding: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .close-menu {
            background: none;
            border: none;
            color: white;
            font-size: 1.5rem;
            cursor: pointer;
        }
        
        .menu-items {
            padding: 1rem;
        }
        
        .menu-items a {
            display: block;
            padding: 1rem;
            text-decoration: none;
            color: var(--dark);
            border-bottom: 1px solid var(--border);
        }
        
        .menu-items a:last-child {
            border-bottom: none;
        }
        
        .overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 999;
            opacity: 0;
            visibility: hidden;
            transition: opacity 0.3s ease;
        }
        
        .overlay.show {
            opacity: 1;
            visibility: visible;
        }
        
        .mobile-card {
            background: white;
            border-radius: 8px;
            padding: 1.5rem;
            margin: 1rem;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        
        .mobile-card h2 {
            margin-bottom: 1rem;
            color: var(--primary);
        }
        
        .form-group {
            margin-bottom: 1rem;
        }
        
        label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 500;
        }
        
        input, textarea, select {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 1rem;
        }
        
        button {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0.75rem;
            border-radius: 6px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
            width: 100%;
            margin-top: 0.5rem;
        }
        
        button:hover {
            background: var(--primary-dark);
        }
        
        .btn-block {
            display: block;
            width: 100%;
        }
        
        .btn-outline {
            background: transparent;
            border: 1px solid var(--primary);
            color: var(--primary);
        }
        
        .btn-outline:hover {
            background: var(--primary);
            color: white;
        }
        
        .btn-danger {
            background: var(--danger);
        }
        
        .btn-danger:hover {
            background: #dc2626;
        }
        
        .btn-success {
            background: var(--success);
        }
        
        .btn-success:hover {
            background: #059669;
        }
        
        .action-buttons {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            margin-top: 1rem;
        }
        
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 0.5rem;
        }
        
        .status-healthy {
            background: var(--success);
        }
        
        .status-unhealthy {
            background: var(--danger);
        }
        
        .status-degraded {
            background: var(--warning);
        }
        
        footer {
            text-align: center;
            padding: 1rem;
            color: var(--secondary);
            font-size: 0.875rem;
        }
        
        .notification {
            position: fixed;
            top: 60px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--success);
            color: white;
            padding: 1rem;
            border-radius: 6px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            z-index: 1000;
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        
        .notification.show {
            opacity: 1;
        }
    </style>
</head>
<body>
    <div class="mobile-container">
        <header>
            <div class="header-content">
                <button class="menu-btn" id="menuBtn">☰</button>
                <div class="logo">4S1T Agent AI</div>
                <div style="width: 24px;"></div> <!-- Spacer -->
            </div>
        </header>
        
        <div class="overlay" id="overlay"></div>
        
        <div class="mobile-menu" id="mobileMenu">
            <div class="menu-header">
                <h3>Menu</h3>
                <button class="close-menu" id="closeMenu">×</button>
            </div>
            <div class="menu-items">
                <a href="/mobile/dashboard">Dashboard</a>
                <a href="/mobile/profile">Profile</a>
                <a href="/mobile/tools">MCP Tools</a>
                <a href="/mobile/health">System Health</a>
                <a href="#" onclick="logout()">Logout</a>
            </div>
        </div>
        
        <div class="notification" id="notification"></div>
        
        <main>
            {% block content %}{% endblock %}
        </main>
        
        <footer>
            <p>4S1T Agent AI Mobile &copy; 2025</p>
        </footer>
    </div>
    
    <script>
        // Mobile menu functionality
        document.addEventListener('DOMContentLoaded', function() {
            const menuBtn = document.getElementById('menuBtn');
            const closeMenu = document.getElementById('closeMenu');
            const mobileMenu = document.getElementById('mobileMenu');
            const overlay = document.getElementById('overlay');
            
            menuBtn.addEventListener('click', function() {
                mobileMenu.classList.add('open');
                overlay.classList.add('show');
                document.body.style.overflow = 'hidden';
            });
            
            closeMenu.addEventListener('click', function() {
                mobileMenu.classList.remove('open');
                overlay.classList.remove('show');
                document.body.style.overflow = '';
            });
            
            overlay.addEventListener('click', function() {
                mobileMenu.classList.remove('open');
                overlay.classList.remove('show');
                document.body.style.overflow = '';
            });
        });
        
        // Show notification function
        function showNotification(message, type = 'success') {
            const notification = document.getElementById('notification');
            notification.textContent = message;
            notification.style.background = type === 'success' ? '#10b981' : '#ef4444';
            notification.classList.add('show');
            
            setTimeout(() => {
                notification.classList.remove('show');
            }, 3000);
        }
        
        // Logout function
        function logout() {
            localStorage.removeItem('access_token');
            window.location.href = '/mobile/login';
        }
        
        // Form handling with fetch API
        document.addEventListener('DOMContentLoaded', function() {
            const forms = document.querySelectorAll('form[data-fetch]');
            forms.forEach(form => {
                form.addEventListener('submit', async function(e) {
                    e.preventDefault();
                    
                    const submitButton = this.querySelector('button[type="submit"]');
                    const originalText = submitButton.textContent;
                    submitButton.textContent = 'Processing...';
                    submitButton.disabled = true;
                    
                    try {
                        const formData = new FormData(this);
                        const data = {};
                        formData.forEach((value, key) => {
                            data[key] = value;
                        });
                        
                        const response = await fetch(this.action, {
                            method: this.method,
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify(data)
                        });
                        
                        const result = await response.json();
                        
                        if (response.ok) {
                            // Handle success
                            if (result.access_token) {
                                // Store token in localStorage
                                localStorage.setItem('access_token', result.access_token);
                                window.location.href = '/mobile/dashboard';
                            } else {
                                showNotification('Success!', 'success');
                            }
                        } else {
                            // Handle error
                            showNotification(result.detail || 'An error occurred', 'error');
                        }
                    } catch (error) {
                        showNotification('Network error: ' + error.message, 'error');
                    } finally {
                        submitButton.textContent = originalText;
                        submitButton.disabled = false;
                    }
                });
            });
        });
    </script>
</body>
</html>
"""

MOBILE_LOGIN_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>Login</h2>
    <form id="login-form" action="/auth/login" method="post" data-fetch="true">
        <div class="form-group">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" required>
        </div>
        <div class="form-group">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required>
        </div>
        <button type="submit">Login</button>
    </form>
    <div style="text-align: center; margin-top: 1rem;">
        <a href="/mobile/register" style="color: var(--primary); text-decoration: none;">Don't have an account? Register</a>
    </div>
</div>
""")

MOBILE_REGISTER_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>Register</h2>
    <form id="register-form" action="/auth/register" method="post" data-fetch="true">
        <div class="form-group">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" required>
        </div>
        <div class="form-group">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required>
        </div>
        <button type="submit">Register</button>
    </form>
    <div style="text-align: center; margin-top: 1rem;">
        <a href="/mobile/login" style="color: var(--primary); text-decoration: none;">Already have an account? Login</a>
    </div>
</div>
""")

MOBILE_DASHBOARD_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>Dashboard</h2>
    <p>Welcome to 4S1T Agent AI Mobile!</p>
    <div class="action-buttons">
        <button class="btn-outline" onclick="window.location.href='/mobile/profile'">Profile</button>
        <button class="btn-outline" onclick="window.location.href='/mobile/tools'">MCP Tools</button>
        <button class="btn-outline" onclick="window.location.href='/mobile/health'">Health</button>
        <button class="btn-danger" onclick="logout()">Logout</button>
    </div>
</div>

<div class="mobile-card">
    <h3>Quick Stats</h3>
    <div style="display: flex; justify-content: space-around; text-align: center; margin-top: 1rem;">
        <div>
            <div style="font-size: 1.5rem; font-weight: bold;">0</div>
            <div style="font-size: 0.875rem; color: var(--secondary);">Tools Used</div>
        </div>
        <div>
            <div style="font-size: 1.5rem; font-weight: bold;">0</div>
            <div style="font-size: 0.875rem; color: var(--secondary);">Sessions</div>
        </div>
        <div>
            <div style="font-size: 1.5rem; font-weight: bold;">0</div>
            <div style="font-size: 0.875rem; color: var(--secondary);">Tasks</div>
        </div>
    </div>
</div>
""")

MOBILE_PROFILE_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>Profile</h2>
    <div id="profile-content">
        <p>Loading profile...</p>
    </div>
</div>

<script>
// Load user profile
document.addEventListener('DOMContentLoaded', async function() {
    try {
        const token = localStorage.getItem('access_token');
        if (!token) {
            window.location.href = '/mobile/login';
            return;
        }
        
        const response = await fetch('/auth/me', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const user = await response.json();
            document.getElementById('profile-content').innerHTML = `
                <div style="margin-bottom: 1rem;">
                    <strong>User ID:</strong> ${user.id}
                </div>
                <div style="margin-bottom: 1rem;">
                    <strong>Role:</strong> ${user.role}
                </div>
                <div style="margin-bottom: 1rem;">
                    <strong>Status:</strong> ${user.is_active ? 'Active' : 'Inactive'}
                </div>
                <div style="margin-bottom: 1rem;">
                    <strong>Member Since:</strong> ${user.created_at}
                </div>
                ${user.last_login ? `<div style="margin-bottom: 1rem;"><strong>Last Login:</strong> ${user.last_login}</div>` : ''}
            `;
        } else {
            document.getElementById('profile-content').innerHTML = '<p>Error loading profile information.</p>';
        }
    } catch (error) {
        document.getElementById('profile-content').innerHTML = `<p>Error: ${error.message}</p>`;
    }
});
</script>
""")

MOBILE_TOOLS_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>MCP Tools</h2>
    <div id="tools-content">
        <p>Loading available tools...</p>
    </div>
</div>

<script>
// Load available tools
document.addEventListener('DOMContentLoaded', loadTools);

async function loadTools() {
    try {
        const response = await fetch('/mcp/tools');
        if (response.ok) {
            const data = await response.json();
            const tools = data.tools || [];
            
            if (tools.length === 0) {
                document.getElementById('tools-content').innerHTML = '<p>No MCP tools are currently registered with the system.</p>';
                return;
            }
            
            let toolsHtml = '<div style="margin-bottom: 1rem;"><strong>Available Tools:</strong></div>';
            
            tools.forEach(tool => {
                toolsHtml += `
                    <div style="background: var(--light); padding: 1rem; border-radius: 6px; margin-bottom: 1rem;">
                        <div style="font-weight: bold; margin-bottom: 0.5rem;">${tool.name}</div>
                        <div style="font-size: 0.875rem; color: var(--secondary); margin-bottom: 0.5rem;">
                            ${tool.description || 'No description available'}
                        </div>
                        <button class="btn-outline" onclick="executeTool('${tool.name}')" style="width: auto; display: inline-block;">Execute</button>
                    </div>
                `;
            });
            
            document.getElementById('tools-content').innerHTML = toolsHtml;
        } else {
            document.getElementById('tools-content').innerHTML = '<p>Error loading tools information.</p>';
        }
    } catch (error) {
        document.getElementById('tools-content').innerHTML = `<p>Error: ${error.message}</p>`;
    }
}

async function executeTool(toolName) {
    // Simple execution with default arguments
    const defaultArgs = {"operation": "add", "a": 5, "b": 3};
    
    try {
        const response = await fetch(`/mcp/tools/${toolName}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(defaultArgs)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification(`Tool executed successfully! Result: ${JSON.stringify(result.result)}`);
        } else {
            showNotification(`Error: ${result.error?.message || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        showNotification(`Network error: ${error.message}`, 'error');
    }
}

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/mobile/login';
}
</script>
""")

MOBILE_HEALTH_HTML = MOBILE_BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="mobile-card">
    <h2>System Health</h2>
    <div id="health-content">
        <p>Loading system health...</p>
    </div>
</div>

<script>
// Load health information
document.addEventListener('DOMContentLoaded', loadHealth);

async function loadHealth() {
    try {
        const response = await fetch('/health');
        if (response.ok) {
            const health = await response.json();
            const statusClass = health.status === 'healthy' ? 'status-healthy' : 
                              health.status === 'degraded' ? 'status-degraded' : 'status-unhealthy';
            
            document.getElementById('health-content').innerHTML = `
                <div style="margin-bottom: 1rem;">
                    <span class="status-indicator ${statusClass}"></span>
                    <strong>System Status:</strong> ${health.status}
                </div>
                <div>
                    <strong>Components:</strong>
                    <ul style="margin-top: 0.5rem; padding-left: 1rem;">
                        ${Object.entries(health.components || {}).map(([key, value]) => 
                            `<li>${key}: ${value}</li>`
                        ).join('')}
                    </ul>
                </div>
            `;
        } else {
            document.getElementById('health-content').innerHTML = '<p>Error loading health information.</p>';
        }
    } catch (error) {
        document.getElementById('health-content').innerHTML = `<p>Error: ${error.message}</p>`;
    }
}
</script>
""")


@router.get("/", response_class=HTMLResponse)
async def mobile_home():
    """Mobile home page."""
    return MOBILE_LOGIN_HTML


@router.get("/login", response_class=HTMLResponse)
async def mobile_login():
    """Mobile login page."""
    return MOBILE_LOGIN_HTML


@router.get("/register", response_class=HTMLResponse)
async def mobile_register():
    """Mobile registration page."""
    return MOBILE_REGISTER_HTML


@router.get("/dashboard", response_class=HTMLResponse)
async def mobile_dashboard():
    """Mobile dashboard page."""
    return MOBILE_DASHBOARD_HTML


@router.get("/profile", response_class=HTMLResponse)
async def mobile_profile():
    """Mobile profile page."""
    return MOBILE_PROFILE_HTML


@router.get("/tools", response_class=HTMLResponse)
async def mobile_tools():
    """Mobile tools page."""
    return MOBILE_TOOLS_HTML


@router.get("/health", response_class=HTMLResponse)
async def mobile_health():
    """Mobile health page."""
    return MOBILE_HEALTH_HTML
