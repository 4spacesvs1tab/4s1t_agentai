// Initialize model filter
function initModelFilter() {
  const filterBtn = document.querySelector('.filter-dropdown[data-target="model-filter"]');
  const dropdown = document.getElementById('model-filter-dropdown');

  filterBtn.addEventListener('click', (e) => {
    // Position ABOVE the filter bar (since it's at bottom)
    const rect = e.target.getBoundingClientRect();
    dropdown.style.bottom = `${window.innerHeight - rect.top + 10}px`;
    dropdown.style.left = `${rect.left}px`;
    dropdown.style.display = 'block';

    // Build dropdown content
    dropdown.innerHTML = `
      <input type="text" class="filter-search" placeholder="Search models..." autofocus>
      <div class="model-list"></div>
    `;

    // Populate model list
    fetchModels();

    // Setup search
    const searchInput = dropdown.querySelector('.filter-search');
    searchInput.addEventListener('input', debounce(fetchModels, 300));
  });

  // Click outside closes dropdown
  document.addEventListener('click', (e) => {
    if (!dropdown.contains(e.target) && e.target !== filterBtn) {
      dropdown.style.display = 'none';
    }
  });
}

// Fetch and display models
function fetchModels() {
  const search = document.querySelector('.filter-search')?.value || '';
  const modelList = document.querySelector('.model-list');
  
  // In a real implementation, this would fetch from the API
  // For now, we'll use mock data
  const mockModels = [
    { model_id: 'glm-4-6-chat', name: 'GLM-4-6 Chat', category: 'Text' },
    { model_id: 'nano-gpt-3.5-turbo', name: 'Nano GPT-3.5 Turbo', category: 'Text' },
    { model_id: 'nano-gpt-4', name: 'Nano GPT-4', category: 'Text' },
    { model_id: 'nano-gpt-vision', name: 'Nano GPT Vision', category: 'Image' }
  ];
  
  // Filter models based on search
  const filteredModels = mockModels.filter(model => 
    model.name.toLowerCase().includes(search.toLowerCase()) ||
    model.model_id.toLowerCase().includes(search.toLowerCase())
  );
  
  modelList.innerHTML = filteredModels
    .map(model => `
      <div class="model-item" data-model="${model.model_id}">
        ${model.name} <span class="model-category">(${model.category})</span>
      </div>
    `)
    .join('');

  // Select model on click
  document.querySelectorAll('.model-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelector('.filter-dropdown[data-target="model-filter"]').textContent = 
        item.textContent.split(' (')[0] + ' ▼';
      dropdown.style.display = 'none';
    });
  });
}

// Debounce utility
function debounce(func, delay) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), delay);
  };
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', initModelFilter);
