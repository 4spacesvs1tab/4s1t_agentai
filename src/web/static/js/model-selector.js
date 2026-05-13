/**
 * ModelSelector - Enhanced model selection component for 4S1T Agent AI
 * Implements hierarchical provider/model selection with favorites and recently used tracking
 */
class ModelSelector {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.options = {
            apiEndpoint: '/api/v1/models',
            maxRecentModels: 5,
            maxFavorites: 10,
            storageKey: 'model_selector_preferences',
            ...options
        };
        
        this.providers = [];
        this.currentSelection = null;
        this.favorites = this.loadFavorites();
        this.recentlyUsed = this.loadRecentlyUsed();
        this.isOpen = false;
        this.searchTerm = '';
        
        this.init();
    }
    
    init() {
        this.render();
        this.attachEventListeners();
        this.loadProviders();
    }
    
    render() {
        this.container.innerHTML = `
            <div class="model-selector-container">
                <div class="model-selector-display" id="model-selector-display">
                    <div class="model-selector-text">
                        <span class="placeholder">Select provider and model...</span>
                    </div>
                    <div class="model-selector-arrow">▼</div>
                </div>
                <div class="model-selector-dropdown" id="model-selector-dropdown" style="display: none;">
                    <div class="model-selector-search">
                        <input type="text" id="model-search-input" placeholder="Search models..." autocomplete="off">
                    </div>
                    <div class="model-selector-content" id="model-selector-content">
                        <div class="loading">Loading models...</div>
                    </div>
                </div>
            </div>
        `;
    }
    
    attachEventListeners() {
        const display = this.container.querySelector('#model-selector-display');
        const searchInput = this.container.querySelector('#model-search-input');
        
        // Toggle dropdown
        display.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleDropdown();
        });
        
        // Search functionality
        searchInput.addEventListener('input', (e) => {
            this.searchTerm = e.target.value.toLowerCase();
            this.renderDropdownContent();
        });
        
        // Keyboard navigation
        searchInput.addEventListener('keydown', (e) => {
            this.handleKeyboardNavigation(e);
        });
        
        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!this.container.contains(e.target)) {
                this.closeDropdown();
            }
        });
        
        // Prevent dropdown from closing when clicking inside
        this.container.querySelector('.model-selector-dropdown').addEventListener('click', (e) => {
            e.stopPropagation();
        });
    }
    
    async loadProviders() {
        try {
            const response = await window.API.fetch(this.options.apiEndpoint);
            const data = await response.json();
            
            // Group models by provider
            const providerMap = new Map();
            
            data.models.forEach(model => {
                // API returns 'id' as the model identifier
                // Map it to model_id for internal consistency
                const modelWithId = {
                    ...model,
                    model_id: model.id  // Use the id field from API
                };
                
                // Use the slug (provider_id) for internal routing; fall back to
                // display name for legacy responses that don't include the slug.
                const providerId = model.provider_id || model.provider || 'unknown';
                if (!providerMap.has(providerId)) {
                    providerMap.set(providerId, {
                        provider_id: providerId,
                        name: model.provider || model.provider_id || 'Unknown Provider',
                        models: [],
                        subscription_tiers: new Set()
                    });
                }
                
                const provider = providerMap.get(providerId);
                provider.models.push(modelWithId);
                provider.subscription_tiers.add(model.subscription_tier || 'FREE');
            });
            
            this.providers = Array.from(providerMap.values()).map(provider => ({
                ...provider,
                subscription_tiers: Array.from(provider.subscription_tiers)
            }));
            
            // Sort providers and models
            this.providers.sort((a, b) => a.name.localeCompare(b.name));
            this.providers.forEach(provider => {
                provider.models.sort((a, b) => a.name.localeCompare(b.name));
            });
            
            this.renderDropdownContent();
            
        } catch (error) {
            console.error('Error loading providers:', error);
            this.container.querySelector('#model-selector-content').innerHTML = 
                '<div class="error">Error loading models</div>';
        }
    }
    
    renderDropdownContent() {
        const content = this.container.querySelector('#model-selector-content');
        
        if (this.providers.length === 0) {
            content.innerHTML = '<div class="loading">Loading models...</div>';
            return;
        }
        
        let html = '';
        
        // Recently Used Section
        const recentHtml = this.renderRecentlyUsedSection();
        if (recentHtml) {
            html += recentHtml;
        }
        
        // Favorites Section
        const favoritesHtml = this.renderFavoritesSection();
        if (favoritesHtml) {
            if (recentHtml) html += '<div class="section-divider"></div>';
            html += favoritesHtml;
        }
        
        // All Providers Section
        const providersHtml = this.renderProvidersSection();
        if (providersHtml) {
            if (recentHtml || favoritesHtml) html += '<div class="section-divider"></div>';
            html += providersHtml;
        }
        
        content.innerHTML = html || '<div class="no-results">No models found</div>';
        
        // Attach event listeners to interactive elements
        this.attachDropdownEventListeners();
    }
    
    renderRecentlyUsedSection() {
        if (this.recentlyUsed.length === 0) return '';
        
        const filteredRecent = this.recentlyUsed.filter(item => {
            if (!this.searchTerm) return true;
            return this.matchesSearch(item, this.searchTerm);
        });
        
        if (filteredRecent.length === 0) return '';
        
        let html = `
            <div class="section recently-used">
                <div class="section-header">
                    <span class="section-icon">🕒</span>
                    <span class="section-title">Recently Used</span>
                </div>
                <div class="section-content">
        `;
        
        filteredRecent.slice(0, this.options.maxRecentModels).forEach(item => {
            const provider = this.providers.find(p => p.provider_id === item.provider_id);
            const model = provider?.models.find(m => m.model_id === item.model_id);
            if (!model) return;
            
            const timeAgo = this.getTimeAgo(item.last_used);
            const isSelected = this.isCurrentSelection(item.provider_id, item.model_id);
            
            html += `
                <div class="model-item ${isSelected ? 'selected' : ''}" 
                     data-provider-id="${item.provider_id}" 
                     data-model-id="${item.model_id}">
                    <div class="model-info">
                        <div class="model-name">${this.escapeHtml(model.name)}</div>
                        <div class="model-meta">
                            <span class="provider-name">${this.escapeHtml(provider.name)}</span>
                            <span class="subscription-badge ${model.subscription_tier?.toLowerCase()}">${model.subscription_tier || 'FREE'}</span>
                            <span class="time-indicator">${timeAgo}</span>
                        </div>
                    </div>
                    ${isSelected ? '<div class="selected-indicator">✓</div>' : ''}
                </div>
            `;
        });
        
        html += '</div></div>';
        return html;
    }
    
    renderFavoritesSection() {
        if (this.favorites.length === 0) return '';
        
        const filteredFavorites = this.favorites.filter(item => {
            if (!this.searchTerm) return true;
            return this.matchesSearch(item, this.searchTerm);
        });
        
        if (filteredFavorites.length === 0) return '';
        
        let html = `
            <div class="section favorites">
                <div class="section-header">
                    <span class="section-icon">★</span>
                    <span class="section-title">Favorites</span>
                </div>
                <div class="section-content">
        `;
        
        filteredFavorites.slice(0, this.options.maxFavorites).forEach(item => {
            const provider = this.providers.find(p => p.provider_id === item.provider_id);
            const model = provider?.models.find(m => m.model_id === item.model_id);
            if (!model) return;
            
            const isSelected = this.isCurrentSelection(item.provider_id, item.model_id);
            const isFavorite = true;
            
            html += `
                <div class="model-item ${isSelected ? 'selected' : ''}" 
                     data-provider-id="${item.provider_id}" 
                     data-model-id="${item.model_id}">
                    <div class="model-info">
                        <div class="model-name">${this.escapeHtml(model.name)}</div>
                        <div class="model-meta">
                            <span class="provider-name">${this.escapeHtml(provider.name)}</span>
                            <span class="subscription-badge ${model.subscription_tier?.toLowerCase()}">${model.subscription_tier || 'FREE'}</span>
                        </div>
                    </div>
                    <div class="favorite-toggle ${isFavorite ? 'favorited' : ''}" 
                         title="${isFavorite ? 'Remove from favorites' : 'Add to favorites'}">
                        ${isFavorite ? '★' : '☆'}
                    </div>
                    ${isSelected ? '<div class="selected-indicator">✓</div>' : ''}
                </div>
            `;
        });
        
        html += '</div></div>';
        return html;
    }
    
    renderProvidersSection() {
        let html = '';
        let hasVisibleProviders = false;
        
        this.providers.forEach(provider => {
            // Filter models based on search term
            const filteredModels = provider.models.filter(model => {
                if (!this.searchTerm) return true;
                
                // Check if model matches search term
                const fieldsToCheck = [
                    provider.name || '',
                    model.name || '',
                    model.model_id || '',
                    model.subscription_tier || '',
                    model.category || ''
                ];
                
                return fieldsToCheck.some(field => 
                    field.toString().toLowerCase().includes(this.searchTerm)
                );
            });
            
            if (filteredModels.length === 0) return;
            
            hasVisibleProviders = true;
            
            html += `
                <div class="section provider">
                    <div class="section-header provider-header">
                        <span class="section-icon">☰</span>
                        <span class="section-title">${this.escapeHtml(provider.name)}</span>
                    </div>
                    <div class="section-content">
            `;
            
            // Group models by subscription tier
            const modelsByTier = {};
            filteredModels.forEach(model => {
                const tier = model.subscription_tier || 'FREE';
                if (!modelsByTier[tier]) modelsByTier[tier] = [];
                modelsByTier[tier].push(model);
            });
            
            // Render each subscription tier group
            Object.entries(modelsByTier).forEach(([tier, models]) => {
                html += `
                    <div class="tier-group">
                        <div class="tier-header">
                            <span class="subscription-badge ${tier.toLowerCase()}">${tier}</span>
                        </div>
                `;
                
                models.forEach(model => {
                    const isSelected = this.isCurrentSelection(provider.provider_id, model.model_id);
                    const isFavorite = this.isFavorite(provider.provider_id, model.model_id);
                    
                    html += `
                        <div class="model-item ${isSelected ? 'selected' : ''}" 
                             data-provider-id="${provider.provider_id}" 
                             data-model-id="${model.model_id}">
                            <div class="model-info">
                                <div class="model-name">${this.escapeHtml(model.name)}</div>
                                <div class="model-meta">
                                    <span class="subscription-badge ${tier.toLowerCase()}">${tier}</span>
                                </div>
                            </div>
                            <div class="favorite-toggle ${isFavorite ? 'favorited' : ''}" 
                                 title="${isFavorite ? 'Remove from favorites' : 'Add to favorites'}">
                                ${isFavorite ? '★' : '☆'}
                            </div>
                            ${isSelected ? '<div class="selected-indicator">✓</div>' : ''}
                        </div>
                    `;
                });
                
                html += '</div>';
            });
            
            html += '</div></div>';
        });
        
        return hasVisibleProviders ? html : '';
    }
    
    attachDropdownEventListeners() {
        // Model selection
        this.container.querySelectorAll('.model-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                
                // Check if clicking on favorite toggle
                if (e.target.classList.contains('favorite-toggle')) {
                    const providerId = item.dataset.providerId;
                    const modelId = item.dataset.modelId;
                    this.toggleFavorite(providerId, modelId);
                    return;
                }
                
                // Select model
                const providerId = item.dataset.providerId;
                const modelId = item.dataset.modelId;
                this.selectModel(providerId, modelId);
            });
        });
        
        // Favorite toggles
        this.container.querySelectorAll('.favorite-toggle').forEach(toggle => {
            toggle.addEventListener('click', (e) => {
                e.stopPropagation();
                const item = toggle.closest('.model-item');
                const providerId = item.dataset.providerId;
                const modelId = item.dataset.modelId;
                this.toggleFavorite(providerId, modelId);
            });
        });
    }
    
    toggleDropdown() {
        if (this.isOpen) {
            this.closeDropdown();
        } else {
            this.openDropdown();
        }
    }
    
    openDropdown() {
        this.isOpen = true;
        const dropdown = this.container.querySelector('#model-selector-dropdown');
        const arrow = this.container.querySelector('.model-selector-arrow');
        
        dropdown.style.display = 'block';
        arrow.textContent = '▲';
        
        // Focus search input
        setTimeout(() => {
            this.container.querySelector('#model-search-input').focus();
        }, 10);
        
        // Re-render to update content
        this.renderDropdownContent();
    }
    
    closeDropdown() {
        this.isOpen = false;
        const dropdown = this.container.querySelector('#model-selector-dropdown');
        const arrow = this.container.querySelector('.model-selector-arrow');
        
        dropdown.style.display = 'none';
        arrow.textContent = '▼';
        
        // Clear search
        this.searchTerm = '';
        this.container.querySelector('#model-search-input').value = '';
    }
    
    selectModel(providerId, modelId) {
        // Debug: Log what we're looking for
        console.log('selectModel called with:', { providerId, modelId, typeProviderId: typeof providerId, typeModelId: typeof modelId });
        console.log('Available providers:', this.providers.map(p => ({ id: p.provider_id, type: typeof p.provider_id })));
        if (this.providers[0]) {
            console.log('Available models in first provider:', this.providers[0].models.map(m => ({ id: m.model_id, type: typeof m.model_id })));
        }
        
        // Convert to string for comparison since dataset returns strings
        const provider = this.providers.find(p => String(p.provider_id) === String(providerId));
        const model = provider?.models.find(m => String(m.model_id) === String(modelId));
        
        if (!provider || !model) {
            console.error('Model or provider not found', { providerId, modelId, providerFound: !!provider, modelFound: !!model });
            return;
        }
        
        this.currentSelection = { providerId, modelId };
        
        // Update display
        const display = this.container.querySelector('#model-selector-display .model-selector-text');
        display.innerHTML = `
            <span class="selected-model">${this.escapeHtml(provider.name)}: ${this.escapeHtml(model.name)}</span>
            <span class="subscription-badge ${model.subscription_tier?.toLowerCase()}">${model.subscription_tier || 'FREE'}</span>
        `;
        
        // Add to recently used
        this.addToRecentlyUsed(providerId, modelId);
        
        // Close dropdown
        this.closeDropdown();
        
        // Trigger change event
        this.container.dispatchEvent(new CustomEvent('modelSelected', {
            detail: {
                providerId,
                modelId,
                providerName: provider.name,
                modelName: model.name,
                subscriptionTier: model.subscription_tier
            }
        }));
    }
    
    toggleFavorite(providerId, modelId) {
        const index = this.favorites.findIndex(f => 
            f.provider_id === providerId && f.model_id === modelId
        );
        
        if (index >= 0) {
            this.favorites.splice(index, 1);
        } else {
            this.favorites.push({
                provider_id: providerId,
                model_id: modelId,
                added_date: new Date().toISOString()
            });
        }
        
        this.saveFavorites();
        this.renderDropdownContent();
    }
    
    addToRecentlyUsed(providerId, modelId) {
        const existingIndex = this.recentlyUsed.findIndex(item => 
            item.provider_id === providerId && item.model_id === modelId
        );
        
        if (existingIndex >= 0) {
            // Update existing entry
            const item = this.recentlyUsed.splice(existingIndex, 1)[0];
            item.last_used = new Date().toISOString();
            item.use_count = (item.use_count || 0) + 1;
            this.recentlyUsed.unshift(item);
        } else {
            // Add new entry
            this.recentlyUsed.unshift({
                provider_id: providerId,
                model_id: modelId,
                last_used: new Date().toISOString(),
                use_count: 1
            });
        }
        
        // Keep only max recent models
        this.recentlyUsed = this.recentlyUsed.slice(0, this.options.maxRecentModels);
        
        this.saveRecentlyUsed();
    }
    
    matchesSearch(item, searchTerm) {
        const provider = this.providers.find(p => p.provider_id === item.provider_id);
        const model = provider?.models.find(m => m.model_id === item.model_id);
        
        if (!model) return false;
        
        const fieldsToCheck = [
            provider?.name || '',
            model.name || '',
            model.model_id || '',
            model.subscription_tier || '',
            model.category || ''
        ];
        
        return fieldsToCheck.some(field => 
            field.toLowerCase().includes(searchTerm)
        );
    }
    
    isCurrentSelection(providerId, modelId) {
        return this.currentSelection && 
               this.currentSelection.providerId === providerId && 
               this.currentSelection.modelId === modelId;
    }
    
    isFavorite(providerId, modelId) {
        return this.favorites.some(f => 
            f.provider_id === providerId && f.model_id === modelId
        );
    }
    
    getTimeAgo(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? 's' : ''} ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
        if (diffDays === 1) return 'Yesterday';
        return `${diffDays} days ago`;
    }
    
    handleKeyboardNavigation(e) {
        if (e.key === 'Escape') {
            this.closeDropdown();
            return;
        }
        
        if (e.key === 'Enter') {
            // Select first visible item
            const firstItem = this.container.querySelector('.model-item');
            if (firstItem) {
                firstItem.click();
            }
            return;
        }
        
        // Arrow key navigation would be implemented here
        // For now, focusing on basic functionality
    }
    
    loadFavorites() {
        try {
            const stored = localStorage.getItem(`${this.options.storageKey}_favorites`);
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('Error loading favorites:', e);
            return [];
        }
    }
    
    saveFavorites() {
        try {
            localStorage.setItem(`${this.options.storageKey}_favorites`, JSON.stringify(this.favorites));
        } catch (e) {
            console.error('Error saving favorites:', e);
        }
    }
    
    loadRecentlyUsed() {
        try {
            const stored = localStorage.getItem(`${this.options.storageKey}_recently_used`);
            const recent = stored ? JSON.parse(stored) : [];
            
            // Clear old entries (older than 7 days)
            const weekAgo = new Date();
            weekAgo.setDate(weekAgo.getDate() - 7);
            
            return recent.filter(item => new Date(item.last_used) > weekAgo);
        } catch (e) {
            console.error('Error loading recently used:', e);
            return [];
        }
    }
    
    saveRecentlyUsed() {
        try {
            localStorage.setItem(`${this.options.storageKey}_recently_used`, JSON.stringify(this.recentlyUsed));
        } catch (e) {
            console.error('Error saving recently used:', e);
        }
    }
    
    escapeHtml(text) {
        if (text == null) return '';
        if (typeof text !== 'string') text = String(text);
        
        return text.replace(/[&<>"']/g, function(m) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;' }[m];
        });
    }
    
    // Public API methods
    getCurrentSelection() {
        return this.currentSelection;
    }
    
    setCurrentSelection(providerId, modelId) {
        this.selectModel(providerId, modelId);
    }
    
    reset() {
        this.currentSelection = null;
        const display = this.container.querySelector('#model-selector-display .model-selector-text');
        display.innerHTML = '<span class="placeholder">Select provider and model...</span>';
    }
}
