/* ============================================================
   4S1T Agent AI — Theme System
   Applies dark/light theme immediately (no flash).
   Priority: localStorage > 'dark' default
   ============================================================ */

(function() {
  var stored = localStorage.getItem('4s1t-theme');
  var theme = (stored === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', theme);
})();

/* Toggle between dark and light */
function toggleTheme() {
  var html = document.documentElement;
  var next = html.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  localStorage.setItem('4s1t-theme', next);
  _updateThemeBtn(next);
}

function _updateThemeBtn(theme) {
  var btn = document.getElementById('v05-theme-btn');
  if (!btn) return;
  var iconEl = btn.querySelector('.v05-nav-icon');
  var labelEl = btn.querySelector('.v05-theme-btn-label');
  if (iconEl && typeof icon === 'function') {
    iconEl.innerHTML = theme === 'light' ? icon('moon', 16) : icon('sun', 16);
  }
  if (labelEl) {
    var darkLabel = btn.getAttribute('data-label-dark') || 'Dark mode';
    var lightLabel = btn.getAttribute('data-label-light') || 'Light mode';
    labelEl.textContent = theme === 'light' ? lightLabel : darkLabel;
  }
  btn.title = theme === 'light' ? 'Switch to dark' : 'Switch to light';
}

/* Sync button state once DOM is ready */
document.addEventListener('DOMContentLoaded', function() {
  var theme = document.documentElement.getAttribute('data-theme') || 'dark';
  _updateThemeBtn(theme);
});
