// Auto-expanding textarea
const promptInput = document.getElementById('prompt-input');

function autoExpand() {
  promptInput.style.height = 'auto';
  const maxHeight = window.innerHeight * 0.2;
  const newHeight = Math.min(promptInput.scrollHeight, maxHeight);
  promptInput.style.height = `${newHeight}px`;
}

// Initialize and setup listeners
promptInput.addEventListener('input', autoExpand);
window.addEventListener('resize', autoExpand);

// Initial expansion
timeout(autoExpand, 100);
