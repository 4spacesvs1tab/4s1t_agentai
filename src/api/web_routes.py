"""
Web interface routes for 4S1T Agent AI system.
Provides HTML web interface for user interaction.
"""
from typing import Dict, Any, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from services.auth_service import get_auth_service, AuthService
from services.mfa.service import get_mfa_service
from api.security_dependencies import require_auth, require_2fa, optional_auth, revoke_token
from core.security import decode_access_token
from core.csrf import generate_csrf_token
from i18n import get_t, LANGUAGES
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Create router
router = APIRouter(prefix="", tags=["web"])

# Templates - Fixed path resolution
import os
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "web", "templates")
)


def _lang(user: Optional[Dict[str, Any]]) -> str:
    """Return the user's language preference, defaulting to 'en'."""
    return (user or {}).get("language_preference", "en") or "en"


def _tctx(user: Optional[Dict[str, Any]], request: Request, **extra) -> Dict[str, Any]:
    """
    Build a Jinja2 template context that includes i18n helpers.

    Every template rendered via this helper automatically receives:
      - ``t``    : translation function for the user's language
      - ``lang`` : current language code string (e.g. 'en', 'pl')
      - ``languages`` : dict of {code: display_name} for the language picker
    """
    lang = _lang(user)
    return {
        "request": request,
        "user": user,
        "lang": lang,
        "t": get_t(lang),
        "languages": LANGUAGES,
        **extra,
    }

# HTML templates with terminal-like dark theme
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>4S1T Agent AI</title>
    <style>
        :root {
            --terminal-bg: #1e1e1e;
            --terminal-fg: #d4d4d4;
            --terminal-blue: #569cd6;
            --terminal-green: #6a9955;
            --terminal-yellow: #d7ba7d;
            --terminal-orange: #ce9178;
            --terminal-purple: #c586c0;
            --terminal-red: #f48771;
            --terminal-cyan: #4ec9b0;
            --terminal-border: #3c3c3c;
            --primary: #569cd6;
            --primary-dark: #4285c9;
            --secondary: #9cdcfe;
            --success: #6a9955;
            --danger: #f48771;
            --warning: #d7ba7d;
            --light: #2d2d30;
            --dark: #cccccc;
            --border: #3c3c3c;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Courier New', Consolas, Monaco, monospace;
            background-color: var(--terminal-bg);
            color: var(--terminal-fg);
            line-height: 1.6;
            font-size: 14px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1rem;
        }
        
        header {
            background: var(--terminal-bg);
            border-bottom: 1px solid var(--terminal-border);
            padding: 1rem 0;
        }
        
        .header-content {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.5rem;
            font-weight: bold;
            color: var(--terminal-blue);
            text-shadow: 0 0 5px rgba(86, 156, 214, 0.5);
        }
        
        nav ul {
            display: flex;
            list-style: none;
            gap: 1.5rem;
        }
        
        nav a {
            text-decoration: none;
            color: var(--terminal-cyan);
            font-weight: 500;
            transition: color 0.2s;
            text-shadow: 0 0 3px rgba(78, 201, 176, 0.3);
        }
        
        nav a:hover {
            color: var(--terminal-yellow);
            text-shadow: 0 0 5px rgba(215, 186, 125, 0.5);
        }
        
        .auth-section {
            background: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            border-radius: 4px;
            padding: 2rem;
            margin: 2rem 0;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
        }
        
        .form-group {
            margin-bottom: 1rem;
        }
        
        label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 500;
            color: var(--terminal-cyan);
        }
        
        input, textarea {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid var(--terminal-border);
            border-radius: 0;
            font-size: 1rem;
            background-color: var(--light);
            color: var(--terminal-fg);
            font-family: 'Courier New', Consolas, Monaco, monospace;
        }
        
        input:focus, textarea:focus {
            outline: 1px solid var(--terminal-blue);
            box-shadow: 0 0 5px rgba(86, 156, 214, 0.5);
        }
        
        button {
            background: var(--terminal-blue);
            color: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            padding: 0.75rem 1.5rem;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            font-family: 'Courier New', Consolas, Monaco, monospace;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        button:hover {
            background: var(--terminal-cyan);
            color: var(--terminal-bg);
            box-shadow: 0 0 8px rgba(78, 201, 176, 0.7);
        }
        
        .btn-danger {
            background: var(--terminal-red);
        }
        
        .btn-danger:hover {
            background: #ff5555;
            box-shadow: 0 0 8px rgba(255, 85, 85, 0.7);
        }
        
        .btn-success {
            background: var(--terminal-green);
        }
        
        .btn-success:hover {
            background: #7dbc4e;
            box-shadow: 0 0 8px rgba(125, 188, 78, 0.7);
        }
        
        .dashboard {
            display: grid;
            grid-template-columns: 1fr 3fr;
            gap: 2rem;
            margin: 2rem 0;
        }
        
        .sidebar {
            background: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            border-radius: 4px;
            padding: 1.5rem;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
        }
        
        .sidebar h3 {
            margin-bottom: 1rem;
            color: var(--terminal-blue);
            text-shadow: 0 0 3px rgba(86, 156, 214, 0.3);
        }
        
        .sidebar ul {
            list-style: none;
        }
        
        .sidebar li {
            margin-bottom: 0.5rem;
        }
        
        .sidebar a {
            text-decoration: none;
            color: var(--secondary);
            display: block;
            padding: 0.5rem;
            border-radius: 0;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        
        .sidebar a:hover {
            background: var(--light);
            color: var(--terminal-yellow);
            border: 1px solid var(--terminal-border);
        }
        
        .main-content {
            background: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            border-radius: 4px;
            padding: 2rem;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
        }
        
        .card {
            background: var(--light);
            border: 1px solid var(--terminal-border);
            border-radius: 0;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }
        
        .card h3 {
            margin-bottom: 1rem;
            color: var(--terminal-blue);
        }
        
        .alert {
            padding: 1rem;
            border-radius: 0;
            margin-bottom: 1rem;
            border: 1px solid var(--terminal-border);
        }
        
        .alert-success {
            background: rgba(106, 153, 85, 0.2);
            color: var(--terminal-green);
            border-left: 3px solid var(--terminal-green);
        }
        
        .alert-error {
            background: rgba(244, 135, 113, 0.2);
            color: var(--terminal-red);
            border-left: 3px solid var(--terminal-red);
        }
        
        .hidden {
            display: none;
        }
        
        footer {
            text-align: center;
            padding: 2rem 0;
            color: var(--secondary);
            margin-top: 2rem;
            border-top: 1px solid var(--terminal-border);
            font-size: 0.9rem;
        }
        
        pre {
            background: var(--light);
            border: 1px solid var(--terminal-border);
            padding: 1rem;
            overflow-x: auto;
            color: var(--terminal-fg);
            font-family: 'Courier New', Consolas, Monaco, monospace;
        }
        
        code {
            color: var(--terminal-orange);
            background: var(--light);
            padding: 0.2rem 0.4rem;
            border: 1px solid var(--terminal-border);
        }
        
        a {
            color: var(--terminal-cyan);
            text-decoration: none;
        }
        
        a:hover {
            color: var(--terminal-yellow);
            text-decoration: underline;
        }
        
        @media (max-width: 768px) {
            .dashboard {
                grid-template-columns: 1fr;
            }
            
            .header-content {
                flex-direction: column;
                gap: 1rem;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container header-content">
            <div class="logo">4S1T Agent AI</div>
            <nav>
                <ul>
                    <li><a href="/">Home</a></li>
                    <li><a href="/dashboard">Dashboard</a></li>
                    <li><a href="/dashboard/chat">Chat</a></li>
                    <li><a href="/dashboard/health">Health</a></li>
                </ul>
            </nav>
        </div>
    </header>
    
    <main class="container">
        {% block content %}{% endblock %}
    </main>
    
    <footer>
        <div class="container">
            <p>4S1T Agent AI &copy; 2025 - Terminal Interface</p>
        </div>
    </footer>
    
    <script>
        // Simple client-side JavaScript for basic interactions
        document.addEventListener('DOMContentLoaded', function() {
            // Toggle password visibility
            const togglePassword = document.querySelector('.toggle-password');
            if (togglePassword) {
                togglePassword.addEventListener('click', function() {
                    const passwordInput = document.getElementById('password');
                    const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
                    passwordInput.setAttribute('type', type);
                    this.textContent = type === 'password' ? 'Show' : 'Hide';
                });
            }
            
            // Form submission with fetch API
            const forms = document.querySelectorAll('form[data-fetch]');

            forms.forEach(function(form, index) {
                form.addEventListener('submit', async function(e) {
                    e.preventDefault();
                    
                    const submitButton = this.querySelector('button[type="submit"]');
                    const originalText = submitButton.textContent;
                    submitButton.textContent = 'Processing...';
                    submitButton.disabled = true;
                    
                    try {
                        // Special handling for login form
                        if (this.id === 'login-form') {
                            // For login, use form data (not JSON) as required by OAuth2
                            const formData = new FormData(this);
                            
                            const response = await fetch(this.action, {
                                method: this.method,
                                body: formData  // Send as form data, not JSON
                            });
                            
                            const result = await response.json();
                            
                            if (response.ok) {
                                // Handle MFA enrollment required (user needs to set up 2FA)
                                if (result.requires_mfa_enrollment) {
                                    // Store enrollment token and redirect to enrollment page
                                    sessionStorage.setItem('mfa_enrollment_token', result.access_token);
                                    window.location.href = result.redirect || '/auth/2fa/enroll';
                                }
                                // Handle MFA verification required (user has 2FA, needs to verify)
                                else if (result.requires_mfa) {
                                    // Store MFA session token and redirect to verification
                                    sessionStorage.setItem('mfa_session_token', result.session_token);
                                    window.location.href = '/auth/mfa/verify';
                                }
                                // Handle success - store token and redirect
                                else if (result.access_token) {
                                    localStorage.setItem('access_token', result.access_token);
                                    window.location.href = '/dashboard';
                                } else {
                                    showMessage('Login successful!', 'success');
                                }
                            } else {
                                // Handle error
                                let errorMessage = 'Login failed';
                                if (result.detail) {
                                    if (typeof result.detail === 'string') {
                                        errorMessage = result.detail;
                                    } else if (Array.isArray(result.detail)) {
                                        errorMessage = result.detail.map(item => item.msg || item).join(', ');
                                    } else if (typeof result.detail === 'object') {
                                        errorMessage = result.detail.msg || JSON.stringify(result.detail);
                                    }
                                }
                                showMessage(errorMessage, 'error');
                            }
                        } else {
                            // For other forms (registration, etc.), use JSON
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
                                    window.location.href = '/dashboard';
                                } else {
                                    showMessage('Success!', 'success');
                                }
                            } else {
                                // Handle error
                                let errorMessage = 'An error occurred';
                                if (result.detail) {
                                    if (typeof result.detail === 'string') {
                                        errorMessage = result.detail;
                                    } else if (Array.isArray(result.detail)) {
                                        errorMessage = result.detail.map(item => item.msg || item).join(', ');
                                    } else if (typeof result.detail === 'object') {
                                        errorMessage = result.detail.msg || JSON.stringify(result.detail);
                                    }
                                }
                                showMessage(errorMessage, 'error');
                            }
                        }
                    } catch (error) {
                        showMessage('Network error: ' + error.message, 'error');
                    } finally {
                        submitButton.textContent = originalText;
                        submitButton.disabled = false;
                    }
                });
            });
            
            // Show message function
            function showMessage(message, type) {
                // Remove existing alerts
                const existingAlerts = document.querySelectorAll('.alert');
                existingAlerts.forEach(alert => alert.remove());
                
                // Create new alert
                const alert = document.createElement('div');
                alert.className = `alert alert-${type}`;
                alert.textContent = message;
                
                // Insert at the top of main content
                const main = document.querySelector('main');
                if (main.firstChild) {
                    main.insertBefore(alert, main.firstChild);
                } else {
                    main.appendChild(alert);
                }
                
                // Auto-remove after 5 seconds
                setTimeout(() => {
                    if (alert.parentNode) {
                        alert.parentNode.removeChild(alert);
                    }
                }, 5000);
            }
        });
    </script>
</body>
</html>
"""

LOGIN_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="auth-section">
    <h2>Login to 4S1T Agent AI</h2>
    <form id="login-form" action="/auth/login" method="post" data-fetch="true">
        <div class="form-group">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" required>
        </div>
        <div class="form-group">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required>
            <small><a href="#" class="toggle-password">Show</a></small>
        </div>
        <button type="submit">Login</button>
    </form>
    <p>Don't have an account? <a href="/register">Register here</a></p>
</div>
""")

REGISTER_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="auth-section">
    <h2>Register for 4S1T Agent AI</h2>
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
    <p>Already have an account? <a href="/login">Login here</a></p>
</div>
""")

MCP_TOOLS_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="dashboard">
    <div class="sidebar">
        <h3>Navigation</h3>
        <ul>
            <li><a href="/dashboard">Overview</a></li>
            <li><a href="/dashboard/profile">Profile</a></li>
            <li><a href="/dashboard/mcp">MCP Tools</a></li>
            <li><a href="/dashboard/health">System Health</a></li>
        </ul>
        
        <h3>Quick Actions</h3>
        <ul>
            <li><a href="#" onclick="logout()">Logout</a></li>
        </ul>
    </div>
    
    <div class="main-content">
        <div class="card" style="border-left: 3px solid var(--terminal-yellow); margin-bottom: 2rem;">
            <h2 style="color: var(--terminal-yellow); margin-top: 0;">⚠️ MCP Tools - Developer Utility</h2>
            <p style="color: var(--terminal-yellow); font-size: 0.9rem; margin-bottom: 0.5rem;">
                <strong>MCP</strong> stands for <strong>Model Context Protocol</strong> - a standard for AI tool integration.
            </p>
            <p style="color: var(--text-muted); font-size: 0.85rem;">
                In normal operation, these tools are used <strong>automatically by the AI agent in the background</strong> during chat conversations.
                This page provides a <strong>direct testing interface</strong> for debugging and development purposes.
            </p>
        </div>
        
        <h2>Available Tools</h2>
        <div id="tools-content">
            <p>Loading available tools...</p>
        </div>
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
                document.getElementById('tools-content').innerHTML = `
                    <div class="card">
                        <h3>No Tools Available</h3>
                        <p>No MCP tools are currently registered with the system.</p>
                    </div>
                `;
                return;
            }
            
            let toolsHtml = `
                <div class="card">
                    <h3>Available MCP Tools</h3>
                    <p>Select a tool below to execute it:</p>
                </div>
            `;
            
            tools.forEach(tool => {
                toolsHtml += `
                    <div class="card">
                        <h3>${tool.name}</h3>
                        <p>${tool.description || 'No description available'}</p>
                        <button onclick="executeTool('${tool.name}')">Execute Tool</button>
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
    // For now, just show a simple execution form
    const formHtml = `
        <div class="card">
            <h3>Execute Tool: ${toolName}</h3>
            <form id="tool-form" onsubmit="runTool(event, '${toolName}')">
                <div class="form-group">
                    <label for="arguments">Arguments (JSON format):</label>
                    <textarea id="arguments" name="arguments" rows="4" placeholder='{"key": "value"}'>{"operation": "add", "a": 5, "b": 3}</textarea>
                </div>
                <button type="submit">Run Tool</button>
                <button type="button" onclick="loadTools()">Cancel</button>
            </form>
            <div id="tool-result" style="margin-top: 1rem;"></div>
        </div>
    `;
    
    document.getElementById('tools-content').innerHTML = formHtml;
}

async function runTool(event, toolName) {
    event.preventDefault();
    
    const argsTextarea = document.getElementById('arguments');
    let arguments;
    
    try {
        arguments = JSON.parse(argsTextarea.value);
    } catch (e) {
        document.getElementById('tool-result').innerHTML = '<div class="alert alert-error">Invalid JSON in arguments</div>';
        return;
    }
    
    const submitButton = event.target.querySelector('button[type="submit"]');
    const originalText = submitButton.textContent;
    submitButton.textContent = 'Running...';
    submitButton.disabled = true;
    
    try {
        const response = await fetch(`/mcp/tools/${toolName}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(arguments)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            document.getElementById('tool-result').innerHTML = `
                <div class="alert alert-success">
                    <h4>Result:</h4>
                    <pre>${JSON.stringify(result, null, 2)}</pre>
                </div>
            `;
        } else {
            document.getElementById('tool-result').innerHTML = `
                <div class="alert alert-error">
                    <h4>Error:</h4>
                    <pre>${JSON.stringify(result, null, 2)}</pre>
                </div>
            `;
        }
    } catch (error) {
        document.getElementById('tool-result').innerHTML = `
            <div class="alert alert-error">
                <h4>Network Error:</h4>
                <p>${error.message}</p>
            </div>
        `;
    } finally {
        submitButton.textContent = originalText;
        submitButton.disabled = false;
    }
}

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}
</script>
""")

DASHBOARD_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="dashboard">
    <div class="sidebar">
        <h3>Navigation</h3>
        <ul>
            <li><a href="/dashboard">Overview</a></li>
            <li><a href="/dashboard/profile">Profile</a></li>
            <li><a href="/dashboard/mcp">MCP Tools</a></li>
            <li><a href="/dashboard/health">System Health</a></li>
        </ul>
        
        <h3>Quick Actions</h3>
        <ul>
            <li><a href="#" onclick="logout()">Logout</a></li>
        </ul>
    </div>
    
    <div class="main-content">
        <h2>Dashboard Overview</h2>
        
        <div class="card">
            <h3>Welcome to 4S1T Agent AI</h3>
            <p>You are successfully logged in to the 4S1T Agent AI system. This dashboard provides access to all core functionality including:</p>
            <ul>
                <li>MCP (Model Context Protocol) tool integration</li>
                <li>AI model management and orchestration</li>
                <li>System health monitoring</li>
                <li>User profile management</li>
            </ul>
        </div>
        
        <div class="card">
            <h3>Getting Started</h3>
            <p>Navigate using the sidebar to access different features:</p>
            <ul>
                <li><strong>Profile</strong>: View and update your user information</li>
                <li><strong>MCP Tools</strong>: Access and execute MCP-compatible tools</li>
                <li><strong>System Health</strong>: Monitor the status of all system components</li>
            </ul>
        </div>
    </div>
</div>

<script>
function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}
</script>
""")

PROFILE_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="dashboard">
    <div class="sidebar">
        <h3>Navigation</h3>
        <ul>
            <li><a href="/dashboard">Overview</a></li>
            <li><a href="/dashboard/profile">Profile</a></li>
            <li><a href="/dashboard/mcp">MCP Tools</a></li>
            <li><a href="/dashboard/health">System Health</a></li>
        </ul>
        
        <h3>Quick Actions</h3>
        <ul>
            <li><a href="#" onclick="logout()">Logout</a></li>
        </ul>
    </div>
    
    <div class="main-content">
        <h2>User Profile</h2>
        <div id="profile-content">
            <p>Loading profile...</p>
        </div>
    </div>
</div>

<script>
// Load user profile
document.addEventListener('DOMContentLoaded', async function() {
    try {
        const token = localStorage.getItem('access_token');
        if (!token) {
            window.location.href = '/login';
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
                <div class="card">
                    <h3>Profile Information</h3>
                    <p><strong>User ID:</strong> ${user.id}</p>
                    <p><strong>Role:</strong> ${user.role}</p>
                    <p><strong>Account Status:</strong> ${user.is_active ? 'Active' : 'Inactive'}</p>
                    <p><strong>Member Since:</strong> ${user.created_at}</p>
                    ${user.last_login ? `<p><strong>Last Login:</strong> ${user.last_login}</p>` : ''}
                </div>
            `;
        } else {
            document.getElementById('profile-content').innerHTML = '<p>Error loading profile information.</p>';
        }
    } catch (error) {
        document.getElementById('profile-content').innerHTML = `<p>Error: ${error.message}</p>`;
    }
});

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}
</script>
""")

HEALTH_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="dashboard">
    <div class="sidebar">
        <h3>Navigation</h3>
        <ul>
            <li><a href="/dashboard">Overview</a></li>
            <li><a href="/dashboard/profile">Profile</a></li>
            <li><a href="/dashboard/mcp">MCP Tools</a></li>
            <li><a href="/dashboard/health">System Health</a></li>
        </ul>
        
        <h3>Quick Actions</h3>
        <ul>
            <li><a href="#" onclick="logout()">Logout</a></li>
            <li><a href="#" onclick="refreshHealth()">Refresh</a></li>
        </ul>
    </div>
    
    <div class="main-content">
        <h2>System Health</h2>
        <div id="health-content">
            <p>Loading system health information...</p>
        </div>
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
            document.getElementById('health-content').innerHTML = `
                <div class="card">
                    <h3>System Status</h3>
                    <p><strong>Status:</strong> <span style="color: ${health.status === 'healthy' ? '#6a9955' : '#f48771'}">${health.status}</span></p>
                    <p><strong>Components:</strong></p>
                    <ul>
                        ${Object.entries(health.components || {}).map(([key, value]) => 
                            `<li>${key}: <span style="color: ${value === 'operational' ? '#6a9955' : '#f48771'}">${value}</span></li>`
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

function refreshHealth() {
    document.getElementById('health-content').innerHTML = '<p>Loading system health information...</p>';
    loadHealth();
}

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}
</script>
""")



from typing import Optional, Dict, Any
from fastapi import Request
import jwt
from jose import JWTError
from config.settings import settings
from services.auth_service import AuthService, get_auth_service

async def get_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """Extract and verify user from request token for template rendering."""
    token = None

    # Check Authorization header
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # Fallback to cookie
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        return None

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None

        auth_service = get_auth_service()
        return auth_service.get_user_by_id(user_id)

    except (JWTError, ValueError, AttributeError):
        return None
    except Exception as e:
        logger.error("Unexpected error in get_user_from_request: %s", e)
        return None

@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Home page with login option."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("login.html", _tctx(
        user, request, csrf_token=generate_csrf_token("login")
    ))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("login.html", _tctx(
        user, request, csrf_token=generate_csrf_token("login")
    ))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("register.html", _tctx(
        user, request, csrf_token=generate_csrf_token("register")
    ))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """User dashboard page - requires authentication."""
    if not current_user:
        return RedirectResponse(url="/login")
    db_user = await get_user_from_request(request)
    try:
        from config.provider_config import get_active_provider
        api_provider = get_active_provider().display_name
    except Exception:
        api_provider = "Nano-GPT"
    u = db_user or current_user
    return templates.TemplateResponse("dashboard.html", _tctx(u, request, api_provider=api_provider))


@router.get("/dashboard/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """User profile page."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    
    # Check MFA status
    mfa_service = get_mfa_service()
    user_id = user["id"]
    mfa_enabled = mfa_service.is_totp_enabled(user_id)
    
    backup_codes_remaining = 0
    if mfa_enabled:
        codes = mfa_service.get_backup_codes(user_id)
        backup_codes_remaining = len([c for c in codes if not c["used"]])
    
    return templates.TemplateResponse(
        "profile.html",
        _tctx(user, request, mfa_enabled=mfa_enabled, backup_codes_remaining=backup_codes_remaining)
    )


@router.get("/dashboard/health", response_class=HTMLResponse)
async def health_dashboard_page(request: Request):
    """System health dashboard page - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("health.html", _tctx(user, request))


@router.get("/dashboard/mcp", response_class=HTMLResponse)
async def mcp_tools_page(request: Request):
    """MCP tools page - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("mcp_tools.html", _tctx(user, request))


@router.get("/dashboard/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """
    Modern chat interface page - REQUIRES COMPLETED 2FA SESSION.

    P1 Security Fix: Upgraded from require_auth to require_2fa so a bare
    access token (mfa_verified absent) cannot reach this page.
    Unauthenticated users are redirected to login; users who haven't
    completed TOTP are redirected to the enrollment/verification flow.
    """
    if not current_user:
        return RedirectResponse(url="/login?redirect=/dashboard/chat")

    try:
        current_user = await require_2fa(current_user)
    except HTTPException as e:
        if e.status_code == 403:
            return RedirectResponse(url="/auth/mfa/verify", status_code=303)
        raise

    db_user = await get_user_from_request(request)
    u = db_user or current_user
    return templates.TemplateResponse("chat.html", _tctx(u, request))


@router.get("/chat", response_class=HTMLResponse)
async def chat_terminal_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """
    Chat interface at clean URL - REDIRECTS TO LOGIN if not authenticated.

    Extended Security Fix: Check authentication first, then 2FA enrollment status.
    Redirects to appropriate page for complete security flow.
    """
    if not current_user:
        return RedirectResponse(url="/login?redirect=/chat")

    try:
        current_user = await require_2fa(current_user)
    except HTTPException as e:
        if e.status_code == 403:
            return RedirectResponse(url="/auth/2fa/enroll", status_code=303)
        raise

    db_user = await get_user_from_request(request)
    u = db_user or current_user
    return templates.TemplateResponse("chat.html", _tctx(u, request))


@router.get("/profile", response_class=HTMLResponse)
async def profile_terminal_page(request: Request):
    """User profile page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("profile.html", _tctx(user, request))


@router.post("/profile/language", response_class=RedirectResponse)
async def update_language_preference(request: Request):
    """
    Handle language preference form submission from profile page.
    Updates user's language preference in database and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    form = await request.form()
    lang = form.get("language_preference")

    if lang not in LANGUAGES:
        logger.warning(f"Invalid language preference from user {user['id']}: {lang}")
        return RedirectResponse(url="/profile", status_code=303)

    auth_service = get_auth_service()
    try:
        auth_service.update_user_language(user["id"], lang)
        logger.info(f"Updated language preference for user {user['id']}: {lang}")
    except Exception as e:
        logger.error(f"Failed to update language preference: {str(e)}", exc_info=True)

    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/pii-scrubbing", response_class=RedirectResponse)
async def update_pii_scrubbing_preference(request: Request):
    """
    Handle PII scrubbing toggle form submission from profile page.
    Updates user's pii_scrubbing_enabled flag and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    form = await request.form()
    enabled = form.get("pii_scrubbing_enabled") == "on"

    auth_service = get_auth_service()
    try:
        auth_service.update_user_pii_scrubbing(user["id"], enabled)
        logger.info(f"Updated PII scrubbing for user {user['id']}: {enabled}")
    except Exception as e:
        logger.error(f"Failed to update PII scrubbing preference: {str(e)}", exc_info=True)

    return RedirectResponse(url="/profile", status_code=303)


@router.get("/mcp", response_class=HTMLResponse)
async def mcp_terminal_page(request: Request):
    """MCP tools page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("mcp_tools.html", _tctx(user, request))

@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_terminal_page(request: Request):
    """API Keys management page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("api_keys.html", _tctx(user, request))


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Admin user management panel — requires admin role."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    auth_service = get_auth_service()
    users = auth_service.get_all_users()
    return templates.TemplateResponse("users.html", _tctx(user, request, users=users))


@router.get("/logout", response_class=RedirectResponse)
async def logout_page(request: Request):
    """Logout endpoint - revokes JTI, clears cookie and redirects to login."""
    token = request.cookies.get("access_token") or (
        request.headers.get("Authorization", "")[7:]
        if request.headers.get("Authorization", "").startswith("Bearer ")
        else None
    )
    if token:
        payload = decode_access_token(token)
        if payload:
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                revoke_token(jti, float(exp))
    response = RedirectResponse(url="/login")
    response.delete_cookie("access_token", path="/")
    return response

@router.get("/auth/mfa/verify", response_class=HTMLResponse)
async def mfa_verify_page(request: Request):
    """MFA verification page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("mfa_verify.html", _tctx(user, request))

@router.get("/auth/2fa/enroll", response_class=HTMLResponse)
async def mfa_enroll_page():
    """2FA enrollment page for setting up TOTP."""
    return MFA_ENROLL_HTML

@router.get("/api/models", response_class=HTMLResponse)
async def models_api_page():
    """Models API page for testing."""
    return MODELS_API_HTML

# MFA Enrollment page for setting up TOTP
MFA_ENROLL_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="auth-section">
    <h2>Set Up Two-Factor Authentication (2FA)</h2>
    <p>Enhance your account security by enabling two-factor authentication.</p>
    
    <div id="enrollment-step-1">
        <h3>Step 1: Scan QR Code</h3>
        <p>Use your authenticator app (Google Authenticator, Authy, etc.) to scan this QR code:</p>
        <div id="qr-code-container" style="text-align: center; padding: 2rem; background: var(--light); border: 1px solid var(--terminal-border); margin: 1rem 0;">
            <img id="qr-code" src="" alt="QR Code" style="max-width: 200px;">
        </div>
        <p style="font-size: 0.9rem; color: var(--secondary);">Can't scan? Enter this secret manually: <code id="totp-secret"></code></p>
        
        <div class="form-group" style="margin-top: 2rem;">
            <button type="button" onclick="showStep2()" class="btn-success">I've scanned the code</button>
            <button type="button" onclick="logout()" class="btn-danger">Cancel</button>
        </div>
    </div>
    
    <div id="enrollment-step-2" style="display: none;">
        <h3>Step 2: Verify Code</h3>
        <p>Enter the 6-digit verification code from your authenticator app:</p>
        
        <form id="enroll-form">
            <div class="form-group">
                <label for="verification-code">Verification Code</label>
                <input type="text" id="verification-code" name="code" required maxlength="6" pattern="[0-9]{6}" placeholder="123456">
            </div>
            <button type="submit">Complete Setup</button>
            <button type="button" onclick="showStep1()" style="background: var(--secondary); color: var(--terminal-bg);">Back</button>
        </form>
        
        <div id="backup-codes" style="display: none; margin-top: 2rem; padding: 1rem; background: rgba(106, 153, 85, 0.1); border: 1px solid var(--terminal-green);">
            <h4>Save These Backup Codes</h4>
            <p style="font-size: 0.9rem;">Store these somewhere safe. If you lose access to your authenticator app, you can use these to log in:</p>
            <pre id="backup-codes-list" style="margin-top: 1rem; background: var(--light); padding: 1rem; font-size: 1.2rem; letter-spacing: 2px;"></pre>
            <p style="margin-top: 1rem;"><strong>Important:</strong> These codes will only be shown once!</p>
            <button type="button" onclick="finishEnrollment()" class="btn-success" style="margin-top: 1rem;">I've saved the codes - Continue to Dashboard</button>
        </div>
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', async function() {
    // Check if we have enrollment token
    const token = sessionStorage.getItem('mfa_enrollment_token');
    if (!token) {
        showMessage('No enrollment session found. Please login again.', 'error');
        setTimeout(() => window.location.href = '/login', 2000);
        return;
    }
    
    // Fetch enrollment data (QR code and secret)
    try {
        const response = await fetch('/auth/mfa/setup', {
            method: 'GET',
            headers: {
                'Authorization': 'Bearer ' + token
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            document.getElementById('qr-code').src = data.qr_code_url;
            document.getElementById('totp-secret').textContent = data.secret;
        } else {
            const errorText = await response.text();
            console.error('DEBUG: Error response:', errorText);
            showMessage('Failed to load enrollment data: ' + response.status, 'error');
        }
    } catch (error) {
        console.error('DEBUG: Network error:', error);
        showMessage('Network error: ' + error.message, 'error');
    }
    
    // Handle enrollment form submission
    const form = document.getElementById('enroll-form');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const code = document.getElementById('verification-code').value;
        const token = sessionStorage.getItem('mfa_enrollment_token');
        
        if (!code || code.length !== 6 || !/^\d{6}$/.test(code)) {
            showMessage('Please enter a valid 6-digit code', 'error');
            return;
        }
        
        try {
            const response = await fetch('/auth/mfa/setup', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token
                },
                body: JSON.stringify({ code: code })
            });
            
            const result = await response.json();
            
            if (response.ok) {
                // Show backup codes
                document.getElementById('backup-codes-list').textContent = result.backup_codes.join('\\n');
                document.getElementById('backup-codes').style.display = 'block';
                form.style.display = 'none';
                showMessage('2FA setup successful!', 'success');
            } else {
                showMessage(result.detail || 'Invalid verification code', 'error');
            }
        } catch (error) {
            showMessage('Network error: ' + error.message, 'error');
        }
    });
});

function showStep2() {
    document.getElementById('enrollment-step-1').style.display = 'none';
    document.getElementById('enrollment-step-2').style.display = 'block';
    document.getElementById('verification-code').focus();
}

function showStep1() {
    document.getElementById('enrollment-step-2').style.display = 'none';
    document.getElementById('enrollment-step-1').style.display = 'block';
}

function finishEnrollment() {
    const token = sessionStorage.getItem('mfa_enrollment_token');
    if (token) {
        // Exchange enrollment token for access token
        fetch('/auth/token-exchange', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.access_token) {
                localStorage.setItem('access_token', data.access_token);
                // HttpOnly cookie is set server-side by /auth/token-exchange; do not overwrite via JS
                sessionStorage.removeItem('mfa_enrollment_token');
                window.location.href = '/dashboard';
            } else {
                window.location.href = '/login';
            }
        })
        .catch(() => {
            window.location.href = '/login';
        });
    } else {
        window.location.href = '/login';
    }
}

function logout() {
    sessionStorage.removeItem('mfa_enrollment_token');
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}

function showMessage(message, type) {
    const existingAlerts = document.querySelectorAll('.alert');
    existingAlerts.forEach(alert => alert.remove());
    
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.textContent = message;
    
    const main = document.querySelector('main');
    if (main.firstChild) {
        main.insertBefore(alert, main.firstChild);
    } else {
        main.appendChild(alert);
    }
    
    setTimeout(() => {
        if (alert.parentNode) {
            alert.parentNode.removeChild(alert);
        }
    }, 5000);
}
</script>
""")

# MFA Verification page - Minimal version without header/footer
MFA_VERIFY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>4S1T Agent AI - Two-Factor Authentication</title>
    <style>
        :root {
            --terminal-bg: #1e1e1e;
            --terminal-fg: #d4d4d4;
            --terminal-blue: #569cd6;
            --terminal-green: #6a9955;
            --terminal-yellow: #d7ba7d;
            --terminal-orange: #ce9178;
            --terminal-purple: #c586c0;
            --terminal-red: #f48771;
            --terminal-cyan: #4ec9b0;
            --terminal-border: #3c3c3c;
            --primary: #569cd6;
            --primary-dark: #4285c9;
            --secondary: #9cdcfe;
            --success: #6a9955;
            --danger: #f48771;
            --warning: #d7ba7d;
            --light: #2d2d30;
            --dark: #cccccc;
            --border: #3c3c3c;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Courier New', Consolas, Monaco, monospace;
            background-color: var(--terminal-bg);
            color: var(--terminal-fg);
            line-height: 1.6;
            font-size: 14px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 2rem;
        }
        
        .auth-section {
            background: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            border-radius: 4px;
            padding: 2rem;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
        }
        
        .form-group {
            margin-bottom: 1rem;
        }
        
        label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 500;
            color: var(--terminal-cyan);
        }
        
        input {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid var(--terminal-border);
            border-radius: 0;
            font-size: 1rem;
            background-color: var(--light);
            color: var(--terminal-fg);
            font-family: 'Courier New', Consolas, Monaco, monospace;
        }
        
        input:focus {
            outline: 1px solid var(--terminal-blue);
            box-shadow: 0 0 5px rgba(86, 156, 214, 0.5);
        }
        
        button {
            background: var(--terminal-blue);
            color: var(--terminal-bg);
            border: 1px solid var(--terminal-border);
            padding: 0.75rem 1.5rem;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            font-family: 'Courier New', Consolas, Monaco, monospace;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-right: 0.5rem;
        }
        
        button:hover {
            background: var(--terminal-cyan);
            color: var(--terminal-bg);
            box-shadow: 0 0 8px rgba(78, 201, 176, 0.7);
        }
        
        .btn-danger {
            background: var(--terminal-red);
        }
        
        .btn-danger:hover {
            background: #ff5555;
            box-shadow: 0 0 8px rgba(255, 85, 85, 0.7);
        }
        
        .alert {
            padding: 1rem;
            border-radius: 0;
            margin-bottom: 1rem;
            border: 1px solid var(--terminal-border);
            display: none;
        }
        
        .alert-error {
            background: rgba(244, 135, 113, 0.2);
            color: var(--terminal-red);
            border-left: 3px solid var(--terminal-red);
        }
        
        h2 {
            color: var(--terminal-blue);
            margin-bottom: 1rem;
            text-shadow: 0 0 5px rgba(86, 156, 214, 0.5);
        }
        
        p {
            margin-bottom: 1rem;
            color: var(--terminal-fg);
        }
        
        small {
            color: var(--secondary);
            font-size: 0.8rem;
        }
    </style>
</head>
<body>
    <div class="auth-section">
        <h2>Two-Factor Authentication</h2>
        <p>Enter the verification code from your authenticator app.</p>
        <form id="mfa-form" data-fetch="true">
            <div class="form-group">
                <label for="mfa-code">Verification Code</label>
                <input type="text" id="mfa-code" name="code" required maxlength="6" pattern="[0-9]{6}" placeholder="123456">
                <small>Enter the 6-digit code from your authenticator app</small>
            </div>
            <button type="submit" id="verify-button">Verify</button>
            <button type="button" onclick="logout()" class="btn-danger">Cancel</button>
        </form>
        <div id="message" class="alert alert-error"></div>
    </div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    // Check if we have MFA token
    const mfaToken = sessionStorage.getItem('mfa_session_token');
    if (!mfaToken) {
        showMessage('No MFA session found. Please login again.', 'error');
        setTimeout(() => window.location.href = '/login', 2000);
        return;
    }
    
    // Handle MFA form submission
    const form = document.getElementById('mfa-form');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const code = document.getElementById('mfa-code').value;
        const button = document.getElementById('verify-button');
        
        if (!code || code.length !== 6 || !/^\d{6}$/.test(code)) {
            showMessage('Please enter a valid 6-digit code', 'error');
            return;
        }
        
        button.textContent = 'Verifying...';
        button.disabled = true;
        
        try {
            // Get MFA token from sessionStorage
            const token = sessionStorage.getItem('mfa_session_token');
            
            // Submit to MFA verification endpoint
            const response = await fetch('/auth/verify-2fa', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    session_token: token,
                    verification_code: code
                })
            });
            
            const result = await response.json();
            
            if (response.ok) {
                // Success! Store access token and redirect
                if (result.access_token) {
                    localStorage.setItem('access_token', result.access_token);
                    // HttpOnly cookie is set server-side by /auth/verify-2fa; do not overwrite via JS
                    sessionStorage.removeItem('mfa_session_token');
                    sessionStorage.removeItem('mfa_user_id');
                    // Use replace instead of href to prevent back button issues
                    window.location.replace('/dashboard');
                } else {
                    showMessage('Verification failed: No access token received', 'error');
                    button.textContent = 'Verify';
                    button.disabled = false;
                }
            } else {
                // Failed verification
                let errorMessage = 'Invalid verification code';
                if (result.detail) {
                    errorMessage = typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail);
                }
                showMessage(errorMessage, 'error');
                button.textContent = 'Verify';
                button.disabled = false;
            }
        } catch (error) {
            showMessage('Network error: ' + error.message, 'error');
            button.textContent = 'Verify';
            button.disabled = false;
        }
    });
    
    // Auto-focus on the code input
    document.getElementById('mfa-code').focus();
});

// Show message function
function showMessage(message, type) {
    const existingAlerts = document.querySelectorAll('.alert');
    existingAlerts.forEach(alert => alert.remove());
    
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.textContent = message;
    
    const main = document.querySelector('main');
    if (main.firstChild) {
        main.insertBefore(alert, main.firstChild);
    } else {
        main.appendChild(alert);
    }
    
    setTimeout(() => {
        if (alert.parentNode) {
            alert.parentNode.removeChild(alert);
        }
    }, 5000);
}
</script>
"""

MODELS_API_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="dashboard">
    <div class="sidebar">
        <h3>Navigation</h3>
        <ul>
            <li><a href="/dashboard">Overview</a></li>
            <li><a href="/dashboard/profile">Profile</a></li>
            <li><a href="/dashboard/mcp">MCP Tools</a></li>
            <li><a href="/dashboard/chat">Chat</a></li>
            <li><a href="/dashboard/health">System Health</a></li>
            <li><a href="/api/models" style="color: var(--terminal-yellow); font-weight: bold;">Models API</a></li>
        </ul>
        
        <h3>Quick Actions</h3>
        <ul>
            <li><a href="#" onclick="logout()">Logout</a></li>
            <li><a href="#" onclick="refreshModels()">Refresh Models</a></li>
        </ul>
    </div>
    
    <div class="main-content">
        <h2>Model Registry API Test</h2>
        <div id="models-content">
            <p>Loading available models...</p>
        </div>
    </div>
</div>

<script>
// Load available models
document.addEventListener('DOMContentLoaded', loadModels);

async function loadModels() {
    try {
        const response = await fetch('/api/v1/models');
        if (response.ok) {
            const data = await response.json();
            const models = data.models || [];
            
            if (models.length === 0) {
                document.getElementById('models-content').innerHTML = `
                    <div class="card">
                        <h3>No Models Available</h3>
                        <p>No models are currently registered with the system.</p>
                    </div>
                `;
                return;
            }
            
            let modelsHtml = `
                <div class="card">
                    <h3>Available Models (${models.length} total)</h3>
                    <p>These models are available through the model registry API:</p>
                </div>
            `;
            
            // Group by provider
            const modelsByProvider = {};
            models.forEach(model => {
                const provider = model.provider || 'unknown';
                if (!modelsByProvider[provider]) {
                    modelsByProvider[provider] = [];
                }
                modelsByProvider[provider].push(model);
            });
            
            Object.entries(modelsByProvider).forEach(([provider, providerModels]) => {
                modelsHtml += `
                    <div class="card">
                        <h3>Provider: ${provider} (${providerModels.length} models)</h3>
                        <div style="overflow-x: auto;">
                            <table style="width: 100%; border-collapse: collapse; font-family: monospace;">
                                <thead>
                                    <tr>
                                        <th style="border: 1px solid var(--terminal-border); padding: 8px; text-align: left;">Model ID</th>
                                        <th style="border: 1px solid var(--terminal-border); padding: 8px; text-align: left;">Name</th>
                                        <th style="border: 1px solid var(--terminal-border); padding: 8px; text-align: left;">Category</th>
                                        <th style="border: 1px solid var(--terminal-border); padding: 8px; text-align: left;">Available For</th>
                                        <th style="border: 1px solid var(--terminal-border); padding: 8px; text-align: left;">Price/Million</th>
                                    </tr>
                                </thead>
                                <tbody>
                `;
                
                providerModels.forEach(model => {
                    modelsHtml += `
                        <tr>
                            <td style="border: 1px solid var(--terminal-border); padding: 8px;">${model.id}</td>
                            <td style="border: 1px solid var(--terminal-border); padding: 8px;">${model.name}</td>
                            <td style="border: 1px solid var(--terminal-border); padding: 8px;">${model.category || 'general'}</td>
                            <td style="border: 1px solid var(--terminal-border); padding: 8px;">
                                ${model.available_for ? model.available_for.join(', ') : 'all'}
                            </td>
                            <td style="border: 1px solid var(--terminal-border); padding: 8px;">
                                ${model.pricing && model.pricing.price_per_million ? '$' + model.pricing.price_per_million : 'N/A'}
                            </td>
                        </tr>
                    `;
                });
                
                modelsHtml += `
                                </tbody>
                            </table>
                        </div>
                    </div>
                `;
            });
            
            document.getElementById('models-content').innerHTML = modelsHtml;
        } else {
            document.getElementById('models-content').innerHTML = '<p>Error loading models information.</p>';
        }
    } catch (error) {
        document.getElementById('models-content').innerHTML = `<p>Error: ${error.message}</p>`;
    }
}

function refreshModels() {
    document.getElementById('models-content').innerHTML = '<p>Loading available models...</p>';
    loadModels();
}

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
}
</script>
""")


@router.post("/auth/mfa/verify")
async def verify_mfa(request: Request):
    """
    Verify MFA code and complete authentication.
    This endpoint receives form data from the MFA verification page.
    """
    import jwt
    from datetime import timedelta
    from jose import JWTError
    from fastapi.responses import JSONResponse
    from config.settings import settings
    from services.auth_service import get_auth_service
    from services.exceptions import AuthenticationError
    from services.mfa.service import MFAService
    import logging
    
    logger = logging.getLogger(__name__)
    
    form = await request.form()
    code = form.get("code")
    mfa_token = form.get("token")
    
    if not code or not mfa_token:
        return JSONResponse(
            status_code=400,
            content={"error": "Code and MFA token are required"}
        )
    
    # Verify MFA token
    try:
        # Decode the mfa_token to get the user_id
        mfa_payload = jwt.decode(mfa_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = mfa_payload.get("sub")
        token_type = mfa_payload.get("token_type")
        
        if not user_id or token_type != "mfa":
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid MFA token"}
            )
        
        # Get auth service and MFA service
        auth_service = get_auth_service()
        mfa_service = MFAService(auth_service.db)
        
        # Verify the MFA code
        if not mfa_service.verify_mfa_code(user_id, code):
            logger.warning(f"MFA verification failed for user: {user_id}")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid MFA code"}
            )
        
        # Generate a proper access token
        access_token = auth_service.create_access_token(user_id)
        
        # Update last login
        auth_service.update_last_login(user_id)
        
        logger.info(f"MFA verification successful for user: {user_id}")
        
        return JSONResponse(
            status_code=200,
            content={
                "access_token": access_token,
                "token_type": "bearer",
                "message": "MFA verification successful"
            }
        )
        
    except JWTError:
        logger.error("Invalid MFA token - JWT decode failed")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid MFA token"}
        )
    except Exception as e:
        logger.error(f"MFA verification error: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error during MFA verification"}
        )


@router.post("/profile/theme", response_class=RedirectResponse)
async def update_theme_preference(request: Request):
    """
    Handle theme preference form submission from profile page.
    Updates user's theme preference in database and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    
    form = await request.form()
    theme_preference = form.get("theme_preference")
    
    # Validate theme preference
    valid_themes = ["terminal", "dark_grey_technical", "teal_modern", "blue_professional"]
    if not theme_preference or theme_preference not in valid_themes:
        logger.warning(f"Invalid theme preference from user {user['id']}: {theme_preference}")
        return RedirectResponse(url="/profile")
    
    # Update theme preference in database
    auth_service = get_auth_service()
    try:
        auth_service.update_user_theme(user['id'], theme_preference)
        logger.info(f"Updated theme preference for user {user['id']}: {theme_preference}")
    except Exception as e:
        logger.error(f"Failed to update theme preference: {str(e)}", exc_info=True)
    
    return RedirectResponse(url="/profile", status_code=303)


# Mount static files
router.mount("/web/static", StaticFiles(directory="web/static"), name="web_static")
