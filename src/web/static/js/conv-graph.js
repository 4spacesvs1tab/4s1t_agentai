/* ============================================================
   4S1T Conversation Map — conv-graph.js
   River (POC 034 meander + stems + particles) + Cluster (domain halos)
   ============================================================ */

// Domain colours are injected at runtime by the page template (conversations.html)
// via window.CONV_DOMAIN_COLORS built from kb_domains.yaml.
const DOMAIN_COLORS = Object.assign({}, window.CONV_DOMAIN_COLORS || {});
const _DEFAULT_COL = '#607090';

const CONV_RELATION_COLORS = {
  continues:      '#4a8fd4',
  spawned_from:   '#50b870',
  references:     '#f0a040',
  shares_context: '#9060d0',
  contradicts:    '#d45060',
};

const _CARD_W  = 162;
const _CARD_H  = 78;
const _DAY_PX  = 195;   // world-space pixels per day at zoom=1
const _PAD_L   = 140;
const _PAD_R   = 140;
const _STEM_BASE = 85;

class ConvGraph {
  /**
   * @param {string}  containerId  id of the wrapper div (position:relative)
   * @param {object}  callbacks    { onOpen(id), onLinkReady(srcId, tgtId) }
   */
  constructor(containerId, callbacks = {}) {
    this.container = document.getElementById(containerId);
    this.callbacks = callbacks;

    this._convs  = [];
    this._links  = [];
    this.mode    = 'cluster';

    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.W    = 800;
    this.H    = 500;

    this._panStart     = null;
    this._dragCard     = null;
    this._dragStart    = null;
    this._hoveredCard  = null;
    this._hoveredLink  = null;
    this._selectedCard = null;
    this._linkMode       = false;
    this._linkSource     = null;
    this._linkSelections = new Set();

    this._deleteMode       = false;
    this._deleteSelections = new Set();

    this._riverCards  = [];
    this._clusterPos  = {};
    this._particles   = [];
    this._animT       = 0;
    this._animId      = null;
    this._splineCache = null;
    this._actCache    = null;
    this._day0        = null;

    this._activeDomains = new Set(Object.keys(DOMAIN_COLORS));

    this._buildDOM();
    this._injectStyles();
    this._initEvents();
    this._initParticles();
    this._animate();
  }

  /* ── Public API ─────────────────────────────────────────── */

  loadData(convs, links) {
    this._convs  = convs  || [];
    this._links  = links  || [];
    this._splineCache = null;
    this._actCache    = null;
    this._day0        = null;
    this._recompute();
    this._updateDomainFilters();
  }

  setMode(mode) {
    this.mode = mode;
    if (mode === 'river')   this._fitRiver();
    else                    this._fitCluster();
  }

  startLinkMode() {
    this._linkMode       = true;
    this._linkSource     = null;
    this._linkSelections = new Set();
    this.canvas.style.cursor = 'crosshair';
  }

  cancelLinkMode() {
    this._linkMode       = false;
    this._linkSource     = null;
    this._linkSelections = new Set();
    this._selectedCard   = null;
    this.canvas.style.cursor = 'grab';
  }

  /* Allow conversations.html to sync selections (e.g. list-view clicks) */
  setLinkSelections(ids) {
    this._linkSelections = new Set(ids);
  }

  startDeleteMode() {
    if (this._linkMode) this.cancelLinkMode();
    this._deleteMode       = true;
    this._deleteSelections = new Set();
    this.canvas.style.cursor = 'crosshair';
  }

  cancelDeleteMode() {
    this._deleteMode       = false;
    this._deleteSelections = new Set();
    this.canvas.style.cursor = 'grab';
  }

  isDeleteMode() { return this._deleteMode; }

  isLinkMode() { return this._linkMode; }

  destroy() {
    if (this._animId) cancelAnimationFrame(this._animId);
    this.container.innerHTML = '';
  }

  _resize() {
    const r = this.container.getBoundingClientRect();
    this.W = this.canvas.width  = r.width  || this.container.clientWidth  || 800;
    this.H = this.canvas.height = r.height || this.container.clientHeight || 500;
    this._recompute();
  }

  /* ── DOM setup ──────────────────────────────────────────── */

  _buildDOM() {
    this.container.style.position = 'relative';
    this.container.__cgInstance__ = this;

    // Canvas
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText =
      'position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab;';
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    // Zoom controls — top-right overlay
    const zoomBar = document.createElement('div');
    zoomBar.className = 'cgr-ctrl-bar';
    zoomBar.style.cssText =
      'position:absolute;top:10px;right:10px;z-index:10;display:flex;align-items:center;gap:4px;';
    zoomBar.innerHTML =
      '<button class="cgr-ctrl" data-cga="zm-">−</button>' +
      '<span   class="cgr-zoom-lbl">100%</span>' +
      '<button class="cgr-ctrl" data-cga="zm+">+</button>' +
      '<button class="cgr-ctrl" data-cga="fit" title="Fit view" style="font-size:10px;padding:0 5px;width:auto;">⌖</button>';
    this._zoomLbl = null; // set after append
    this.container.appendChild(zoomBar);
    this._zoomLbl = zoomBar.querySelector('.cgr-zoom-lbl');

    zoomBar.querySelector('[data-cga="zm-"]').onclick = () => this.zoomStep(-1);
    zoomBar.querySelector('[data-cga="zm+"]').onclick = () => this.zoomStep(+1);
    zoomBar.querySelector('[data-cga="fit"]').onclick = () => this._fitView();

    // Domain filter pills — top-left overlay
    this._domainBar = document.createElement('div');
    this._domainBar.style.cssText =
      'position:absolute;top:10px;left:10px;z-index:10;display:flex;gap:5px;flex-wrap:wrap;max-width:55%;';
    this.container.appendChild(this._domainBar);

    // Detail card — right side
    this._detail = document.createElement('div');
    this._detail.className = 'cgr-detail';
    this._detail.style.display = 'none';
    this._detail.innerHTML =
      '<span class="cgr-detail-close">×</span>' +
      '<div  class="cgr-d-title"></div>' +
      '<div  class="cgr-d-date"></div>' +
      '<div  class="cgr-d-domains"></div>' +
      '<div  class="cgr-d-prev"></div>' +
      '<div  class="cgr-d-links"></div>' +
      '<button class="cgr-d-open v05-btn v05-btn-outline v05-btn-sm">Open →</button>';
    this._detail.querySelector('.cgr-detail-close').onclick =
      () => (this._detail.style.display = 'none');
    this.container.appendChild(this._detail);

    this._resize();
  }

  _injectStyles() {
    if (document.getElementById('cgr-styles')) return;
    const s = document.createElement('style');
    s.id = 'cgr-styles';
    s.textContent = `
      .cgr-ctrl {
        width:22px;height:22px;border-radius:4px;
        border:1px solid rgba(255,255,255,0.10);
        background:rgba(255,255,255,0.04);
        color:rgba(100,140,180,0.8);cursor:pointer;font-size:13px;line-height:1;
      }
      .cgr-ctrl:hover { background:rgba(255,255,255,0.09); }
      .cgr-zoom-lbl {
        font-size:10px;color:var(--t-3,rgba(120,130,150,0.8));
        min-width:36px;text-align:center;
      }
      .cgr-domain-btn {
        padding:2px 8px;border-radius:11px;font-size:10px;cursor:pointer;
        border:1px solid rgba(255,255,255,0.07);
        background:rgba(255,255,255,0.03);
        color:rgba(80,110,150,0.8);transition:all .15s;
      }
      .cgr-domain-btn.active { background:rgba(255,255,255,0.09); }
      .cgr-detail {
        position:absolute;top:50px;right:10px;width:240px;z-index:20;
        background:rgba(4,8,15,0.95);border:1px solid rgba(255,255,255,0.09);
        border-radius:10px;padding:14px 16px;
        backdrop-filter:blur(14px);box-shadow:0 8px 32px rgba(0,0,0,0.5);
      }
      .cgr-detail-close {
        position:absolute;top:8px;right:12px;cursor:pointer;
        color:rgba(200,200,220,0.35);font-size:15px;
      }
      .cgr-detail-close:hover { color:rgba(200,200,220,0.75); }
      .cgr-d-title  { font-size:13px;font-weight:700;margin-bottom:3px;line-height:1.3;padding-right:18px; }
      .cgr-d-date   { font-size:10px;color:rgba(80,120,180,0.8);letter-spacing:.06em;margin-bottom:6px; }
      .cgr-d-domains{ display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;min-height:4px; }
      .cgr-d-prev   { font-size:11px;color:rgba(110,140,180,1);line-height:1.6;
                      border-top:1px solid rgba(255,255,255,0.05);padding-top:8px; }
      .cgr-d-links  { margin-top:7px;font-size:10px;color:rgba(60,100,150,0.8); }
      .cgr-d-open   { width:100%;margin-top:10px; }
    `;
    document.head.appendChild(s);
  }

  /* ── Domain filter UI ───────────────────────────────────── */

  _updateDomainFilters() {
    const present = new Set();
    for (const c of this._convs) present.add(this._cardDomain(c));

    this._domainBar.innerHTML = '';
    for (const [id, col] of Object.entries(DOMAIN_COLORS)) {
      if (!present.has(id)) continue;
      const btn = document.createElement('button');
      btn.className = 'cgr-domain-btn active';
      btn.textContent = id.slice(0, 7);
      btn.style.cssText = `border-color:${col}50;color:${col};`;
      btn.onclick = () => {
        if (this._activeDomains.has(id)) this._activeDomains.delete(id);
        else                              this._activeDomains.add(id);
        btn.classList.toggle('active', this._activeDomains.has(id));
        this._recompute();
      };
      this._domainBar.appendChild(btn);
    }
  }

  /* ── Helpers ────────────────────────────────────────────── */

  _cardDomain(c) {
    if (c.domains && c.domains.length) return c.domains[0];
    if (c.tags    && c.tags.length)    return c.tags[0];
    return 'general';
  }

  _cardColor(c) {
    return DOMAIN_COLORS[this._cardDomain(c)] || _DEFAULT_COL;
  }

  _isVisible(c) {
    const d = this._cardDomain(c);
    return DOMAIN_COLORS[d] ? this._activeDomains.has(d) : true;
  }

  /* ── Layout computations ────────────────────────────────── */

  _recompute() {
    this._splineCache = null;
    this._actCache    = null;
    this._day0        = null;
    if (!this._convs.length) return;
    this._riverCards = this._computeRiverCards();
    this._clusterPos = this._computeClusterPositions();
  }

  _computeDailyActivity() {
    if (this._actCache) return this._actCache;
    const counts = {};
    for (const c of this._convs) {
      if (!c.timestamp) continue;
      const day = c.timestamp.slice(0, 10);
      counts[day] = (counts[day] || 0) + 1;
    }
    this._actCache = counts;
    return counts;
  }

  _getSplinePoints() {
    if (this._splineCache) return this._splineCache;

    const activity = this._computeDailyActivity();
    const dates    = Object.keys(activity).sort();
    if (!dates.length) { this._splineCache = []; return []; }

    this._day0 = new Date(dates[0]);

    const counts = dates.map(d => activity[d]);
    const minC   = Math.min(...counts);
    const maxC   = Math.max(...counts);
    const range  = maxC - minC || 1;
    const AMP    = 0.20;

    this._splineCache = dates.map(d => {
      const daysFromStart = (new Date(d) - this._day0) / 86_400_000;
      const wx            = _PAD_L + daysFromStart * _DAY_PX;
      const norm          = (activity[d] - minC) / range;
      // High activity → river curves up (negative offset on screen)
      const offsetFrac    = AMP * (1 - norm * 2);
      return { wx, offsetFrac };
    });
    return this._splineCache;
  }

  _catmullRom(p0, p1, p2, p3, t) {
    const t2 = t * t, t3 = t2 * t;
    return 0.5 * (
      2 * p1 +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    );
  }

  _sampleSplineOffsetFrac(worldX) {
    const pts = this._getSplinePoints();
    if (!pts.length) return 0;
    if (worldX <= pts[0].wx)               return pts[0].offsetFrac;
    if (worldX >= pts[pts.length - 1].wx)  return pts[pts.length - 1].offsetFrac;

    let i = 1;
    while (i < pts.length - 1 && pts[i].wx < worldX) i++;
    const t  = (worldX - pts[i - 1].wx) / (pts[i].wx - pts[i - 1].wx);
    const p0 = pts[Math.max(0,            i - 2)].offsetFrac;
    const p1 = pts[i - 1].offsetFrac;
    const p2 = pts[i].offsetFrac;
    const p3 = pts[Math.min(pts.length - 1, i + 1)].offsetFrac;
    return this._catmullRom(p0, p1, p2, p3, t);
  }

  _riverWorldY(worldX) {
    return this._sampleSplineOffsetFrac(worldX) * this.H;
  }

  _riverScreenY(screenX) {
    const worldX = (screenX - this.panX) / this.zoom;
    /* panY shifts the entire river scene up/down — fixes off-screen elements after zoom */
    return this.H / 2 + this.panY + this._riverWorldY(worldX);
  }

  _dateToWorldX(ts) {
    /* Use the full timestamp (not just date) so same-day convs spread horizontally */
    if (!ts || !this._day0) return _PAD_L;
    const msFromStart = new Date(ts) - this._day0;
    return _PAD_L + (msFromStart / 86_400_000) * _DAY_PX;
  }

  _computeRiverCards() {
    this._getSplinePoints(); // ensures _day0 is set
    if (!this._day0) this._day0 = new Date();

    const sorted = [...this._convs]
      .filter(c => c.timestamp)
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp));

    /* Row-packing so cards never overlap.
       Even rows = above the river, odd = below.
       STEM_STEP = CARD_H + 10 so same-side levels never vertically overlap.
       rowEnds[r] = rightmost world-X consumed in row r. */
    const rowEnds  = [];
    const STEM_STEP = _CARD_H + 10;   // 88 px — no vertical bleed between levels

    return sorted.map(c => {
      const wx    = this._dateToWorldX(c.timestamp);
      const wy0   = this._riverWorldY(wx);
      const xL    = wx - _CARD_W / 2 - 10;
      const xR    = wx + _CARD_W / 2 + 10;

      let row = 0;
      while (rowEnds[row] !== undefined && rowEnds[row] > xL) row++;
      rowEnds[row] = xR;

      const above   = row % 2 === 0;
      const stemLen = _STEM_BASE + Math.floor(row / 2) * STEM_STEP;
      return { ...c, wx, wy0, above, stemLen };
    });
  }

  /* Card rect in screen pixels.
     Anchor Y to _riverScreenY(), then add stem in screen pixels.
     _screenOffsetX / _screenOffsetY are user-drag deltas applied on top. */
  _riverCardScreenRect(card) {
    const screenX      = card.wx * this.zoom + this.panX;
    const spineScreenY = this._riverScreenY(screenX);
    const stemPx       = card.stemLen * this.zoom;
    const baseY = card.above
      ? spineScreenY - stemPx - _CARD_H * this.zoom
      : spineScreenY + stemPx;
    return {
      sx: screenX - (_CARD_W / 2) * this.zoom + (card._screenOffsetX || 0),
      sy: baseY + (card._screenOffsetY || 0),
      sw: _CARD_W * this.zoom,
      sh: _CARD_H * this.zoom,
    };
  }

  _riverStemScreen(card) {
    const screenX = card.wx * this.zoom + this.panX;
    const ry      = this._riverScreenY(screenX);
    const stemPx  = card.stemLen * this.zoom;
    // card end of stem tracks user drag; river attachment stays on spine
    const cy = (card.above ? ry - stemPx : ry + stemPx) + (card._screenOffsetY || 0);
    const cx = screenX + (card._screenOffsetX || 0);
    return { rx: screenX, ry, cx, cy };
  }

  _computeClusterPositions() {
    const groups = {};
    for (const c of this._convs) {
      const key = this._cardDomain(c);
      (groups[key] = groups[key] || []).push(c);
    }

    const keys  = Object.keys(groups);
    const cols  = Math.ceil(Math.sqrt(keys.length * 1.6));
    const cellW = 340, cellH = 320;

    const positions = {};
    keys.forEach((domain, gi) => {
      const col     = gi % cols;
      const row     = Math.floor(gi / cols);
      const cx      = 80 + col * cellW + cellW / 2;
      const cy      = 80 + row * cellH + cellH / 2;
      const members = groups[domain];

      members.forEach((c, mi) => {
        const angle  = (mi / members.length) * Math.PI * 2 - Math.PI / 2;
        const radius = members.length === 1 ? 0 : 68 + members.length * 9;
        positions[c.id] = {
          x: cx + Math.cos(angle) * radius - _CARD_W / 2,
          y: cy + Math.sin(angle) * radius - _CARD_H / 2,
          clusterX: cx,
          clusterY: cy,
          clusterDomain: domain,
        };
      });
    });
    return positions;
  }

  _clusterCardScreen(pos) {
    return {
      sx: pos.x * this.zoom + this.panX,
      sy: pos.y * this.zoom + this.panY,
      sw: _CARD_W * this.zoom,
      sh: _CARD_H * this.zoom,
    };
  }

  /* ── Particles (river only) ─────────────────────────────── */

  _initParticles(n = 55) {
    this._particles = Array.from({ length: n }, () => ({
      t:     Math.random(),
      speed: 0.00015 + Math.random() * 0.00025,
      yOff:  (Math.random() - 0.5) * 18,
      alpha: 0.15 + Math.random() * 0.35,
      size:  1 + Math.random() * 2.5,
    }));
  }

  _updateParticles() {
    for (const p of this._particles) {
      p.t += p.speed;
      if (p.t > 1) p.t -= 1;
    }
  }

  /* ── Input events ───────────────────────────────────────── */

  _initEvents() {
    const c = this.canvas;

    c.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      // In river mode: if we land on a card, start a card drag (not canvas pan)
      if (this.mode === 'river' && !this._linkMode) {
        const hit = this._hitTest(e.clientX, e.clientY);
        if (hit) {
          this._dragCard  = hit;
          this._dragStart = {
            mx: e.clientX, my: e.clientY,
            ox: hit._screenOffsetX || 0, oy: hit._screenOffsetY || 0,
          };
          c.style.cursor = 'grabbing';
          return;
        }
      }
      this._panStart = { mx: e.clientX, my: e.clientY, px: this.panX, py: this.panY };
      c.classList.add('panning');
    });

    c.addEventListener('mousemove', e => {
      // Card drag takes priority over canvas pan
      if (this._dragCard && this._dragStart) {
        this._dragCard._screenOffsetX = this._dragStart.ox + (e.clientX - this._dragStart.mx);
        this._dragCard._screenOffsetY = this._dragStart.oy + (e.clientY - this._dragStart.my);
        c.style.cursor = 'grabbing';
        return;
      }
      this._hoveredCard = this._hitTest(e.clientX, e.clientY);
      if (!this._hoveredCard) {
        this._hoveredLink = this._hitTestLink(e.clientX, e.clientY);
      } else {
        this._hoveredLink = null;
      }
      c.style.cursor = this._linkMode
        ? 'crosshair'
        : (this._hoveredCard || this._hoveredLink) ? 'pointer' : 'grab';
      if (this._panStart) {
        this.panX = this._panStart.px + (e.clientX - this._panStart.mx);
        this.panY = this._panStart.py + (e.clientY - this._panStart.my);
      }
    });

    c.addEventListener('mouseup', e => {
      // End card drag
      if (this._dragCard) {
        const moved = this._dragStart &&
          Math.hypot(e.clientX - this._dragStart.mx, e.clientY - this._dragStart.my) >= 6;
        const card = this._dragCard;
        this._dragCard  = null;
        this._dragStart = null;
        c.style.cursor = 'grab';
        if (!moved) this._onCardClick(card);
        return;
      }
      const wasDragging = this._panStart &&
        Math.hypot(e.clientX - this._panStart.mx, e.clientY - this._panStart.my) >= 6;
      c.classList.remove('panning');
      this._panStart = null;
      if (!wasDragging) {
        const hit = this._hitTest(e.clientX, e.clientY);
        if (hit) {
          this._onCardClick(hit);
        } else {
          const linkHit = this._hitTestLink(e.clientX, e.clientY);
          if (linkHit) this._onLinkClick(linkHit);
          else { this._selectedCard = null; this._closeDetail(); }
        }
      }
    });

    c.addEventListener('mouseleave', () => {
      this._panStart = null;
      if (this._dragCard) { this._dragCard = null; this._dragStart = null; }
    });

    c.addEventListener('wheel', e => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      const nz     = Math.max(0.18, Math.min(6, this.zoom * factor));
      const rect   = c.getBoundingClientRect();
      const mx     = e.clientX - rect.left;
      const my     = e.clientY - rect.top;
      this.panX = mx - (mx - this.panX) * (nz / this.zoom);
      /* River: _riverWorldY is proportional to H (not zoom), so panY must NOT
         be adjusted by the zoom formula — only manual drag changes it */
      if (this.mode !== 'river') {
        this.panY = my - (my - this.panY) * (nz / this.zoom);
      }
      this.zoom = nz;
      this._updateZoomLabel();
    }, { passive: false });

    let lastPinchDist = 0;
    c.addEventListener('touchstart', e => {
      if (e.touches.length === 2) {
        lastPinchDist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        e.preventDefault();
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        this._panStart = { mx: t.clientX, my: t.clientY, px: this.panX, py: this.panY };
      }
    }, { passive: false });

    c.addEventListener('touchmove', e => {
      if (e.touches.length === 2 && lastPinchDist) {
        e.preventDefault();
        const d  = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const rect = c.getBoundingClientRect();
        const mx = cx - rect.left, my = cy - rect.top;
        const nz = Math.max(0.18, Math.min(6, this.zoom * (d / lastPinchDist)));
        this.panX = mx - (mx - this.panX) * (nz / this.zoom);
        this.panY = my - (my - this.panY) * (nz / this.zoom);
        this.zoom = nz;
        lastPinchDist = d;
        this._updateZoomLabel();
      } else if (e.touches.length === 1 && this._panStart) {
        const t = e.touches[0];
        this.panX = this._panStart.px + (t.clientX - this._panStart.mx);
        this.panY = this._panStart.py + (t.clientY - this._panStart.my);
      }
    }, { passive: false });

    c.addEventListener('touchend', () => { this._panStart = null; lastPinchDist = 0; });

    window.addEventListener('resize', () => this._resize());
  }

  _onCardClick(card) {
    if (this._deleteMode) {
      if (this._deleteSelections.has(card.id)) this._deleteSelections.delete(card.id);
      else                                      this._deleteSelections.add(card.id);
      this.callbacks.onDeleteToggle && this.callbacks.onDeleteToggle(card.id, [...this._deleteSelections]);
    } else if (this._linkMode) {
      /* Multi-select: toggle card in/out of selection */
      if (this._linkSelections.has(card.id)) this._linkSelections.delete(card.id);
      else                                    this._linkSelections.add(card.id);
      this.callbacks.onLinkAdd && this.callbacks.onLinkAdd(card.id, [...this._linkSelections]);
    } else {
      this._selectedCard = card;
      this._showDetail(card);
    }
  }

  _onLinkClick(link) {
    /* Show relation type in detail panel */
    const col = CONV_RELATION_COLORS[link.relation_type] || _DEFAULT_COL;
    const sc  = this._convs.find(c => c.id === link.source_id);
    const tc  = this._convs.find(c => c.id === link.target_id);
    this._detail.style.display = 'block';
    const titleEl = this._detail.querySelector('.cgr-d-title');
    titleEl.textContent = link.relation_type.replace(/_/g, ' ');
    titleEl.style.color = col;
    this._detail.querySelector('.cgr-d-date').textContent =
      `${(sc?.title || link.source_id).slice(0, 34)} → ${(tc?.title || link.target_id).slice(0, 34)}`;
    this._detail.querySelector('.cgr-d-domains').innerHTML = '';
    this._detail.querySelector('.cgr-d-prev').textContent = '';
    this._detail.querySelector('.cgr-d-links').textContent = '';
    const openBtn = this._detail.querySelector('.cgr-d-open');
    openBtn.style.display = 'none';
  }

  /* ── Hit testing ────────────────────────────────────────── */

  _hitTest(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const cx = clientX - rect.left;
    const cy = clientY - rect.top;

    if (this.mode === 'river') {
      for (let i = this._riverCards.length - 1; i >= 0; i--) {
        const card = this._riverCards[i];
        if (!this._isVisible(card)) continue;
        const { sx, sy, sw, sh } = this._riverCardScreenRect(card);
        if (cx >= sx && cx <= sx + sw && cy >= sy && cy <= sy + sh) return card;
      }
    } else {
      for (const c of this._convs) {
        if (!this._isVisible(c)) continue;
        const pos = this._clusterPos[c.id];
        if (!pos) continue;
        const { sx, sy, sw, sh } = this._clusterCardScreen(pos);
        if (cx >= sx && cx <= sx + sw && cy >= sy && cy <= sy + sh) return c;
      }
    }
    return null;
  }

  /* ── Link hit testing ───────────────────────────────────── */

  _hitTestLink(clientX, clientY) {
    const rect  = this.canvas.getBoundingClientRect();
    const cx    = clientX - rect.left;
    const cy    = clientY - rect.top;
    const THRESH = 10;
    let best = null, bestD = THRESH;

    for (const link of this._links) {
      let sx, sy, tx, ty;
      if (this.mode === 'river') {
        const sc = this._riverCards.find(c => c.id === link.source_id);
        const tc = this._riverCards.find(c => c.id === link.target_id);
        if (!sc || !tc) continue;
        const sr = this._riverCardScreenRect(sc);
        const tr = this._riverCardScreenRect(tc);
        sx = sr.sx + sr.sw / 2;
        sy = sr.sy + sr.sh / 2;
        tx = tr.sx + tr.sw / 2;
        ty = tr.sy + tr.sh / 2;
      } else {
        const sp = this._clusterPos[link.source_id];
        const tp = this._clusterPos[link.target_id];
        if (!sp || !tp) continue;
        sx = (sp.x + _CARD_W / 2) * this.zoom + this.panX;
        sy = (sp.y + _CARD_H / 2) * this.zoom + this.panY;
        tx = (tp.x + _CARD_W / 2) * this.zoom + this.panX;
        ty = (tp.y + _CARD_H / 2) * this.zoom + this.panY;
      }
      const { mx, my } = this._linkControlPoint(sx, sy, tx, ty);
      /* Sample 20 points along the quadratic bezier */
      for (let i = 0; i <= 20; i++) {
        const t  = i / 20;
        const bx = (1 - t) * (1 - t) * sx + 2 * (1 - t) * t * mx + t * t * tx;
        const by = (1 - t) * (1 - t) * sy + 2 * (1 - t) * t * my + t * t * ty;
        const d  = Math.hypot(bx - cx, by - cy);
        if (d < bestD) { bestD = d; best = link; }
      }
    }
    return best;
  }

  /* Shared control-point logic for both draw and hit-test */
  _linkControlPoint(sx, sy, tx, ty) {
    const dist = Math.hypot(tx - sx, ty - sy);
    if (this.mode === 'river') {
      /* Bow AWAY from the river spine so arc runs outside the card cluster.
         midY < riverY  →  cards are above river  →  bow further up (bowDir = -1)
         midY >= riverY →  cards are below river  →  bow further down (bowDir = +1) */
      const midX   = (sx + tx) / 2;
      const midY   = (sy + ty) / 2;
      const riverY = this._riverScreenY(midX);
      const bowDir = midY <= riverY ? -1 : 1;
      const bowAmt = Math.max(dist * 0.30, 50 * this.zoom);
      return { mx: midX, my: midY + bowDir * bowAmt };
    } else {
      return { mx: (sx + tx) / 2 + dist * 0.15, my: (sy + ty) / 2 + dist * 0.10 };
    }
  }

  /* ── Zoom controls ──────────────────────────────────────── */

  zoomStep(dir) {
    const f  = dir > 0 ? 1.22 : 0.82;
    const nz = Math.max(0.18, Math.min(6, this.zoom * f));
    this.panX = this.W / 2 - (this.W / 2 - this.panX) * (nz / this.zoom);
    if (this.mode !== 'river') {
      this.panY = this.H / 2 - (this.H / 2 - this.panY) * (nz / this.zoom);
    }
    this.zoom = nz;
    this._updateZoomLabel();
  }

  _fitView() {
    if (this.mode === 'river') this._fitRiver();
    else                       this._fitCluster();
  }

  _fitRiver() {
    this._getSplinePoints();
    if (!this._riverCards.length) return;
    const last      = this._riverCards[this._riverCards.length - 1];
    const totalDays = (new Date(last.timestamp) - this._day0) / 86_400_000;
    const worldW    = _PAD_L + Math.max(totalDays, 0.5) * _DAY_PX + _PAD_R;
    this.zoom = Math.max(0.18, Math.min(1.2, (this.W - 80) / worldW));
    this.panX = 40;
    this.panY = 0;
    this._updateZoomLabel();
  }

  _fitCluster() {
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this._updateZoomLabel();
  }

  _updateZoomLabel() {
    if (this._zoomLbl)
      this._zoomLbl.textContent = Math.round(this.zoom * 100) + '%';
  }

  /* ── Detail card ────────────────────────────────────────── */

  _showDetail(card) {
    const col = this._cardColor(card);
    this._detail.style.display = 'block';

    this._detail.querySelector('.cgr-d-title').textContent = card.title || card.label || '(empty)';
    this._detail.querySelector('.cgr-d-title').style.color = col;

    const d = card.timestamp
      ? new Date(card.timestamp).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
      : '';
    this._detail.querySelector('.cgr-d-date').textContent =
      d + (card.msgs ? ` · ${card.msgs} msgs` : '');

    const domains = card.domains || card.tags || [];
    this._detail.querySelector('.cgr-d-domains').innerHTML = domains.map(tag => {
      const tc = DOMAIN_COLORS[tag] || _DEFAULT_COL;
      return `<span style="padding:2px 7px;border-radius:9px;font-size:9px;` +
             `background:${tc}18;color:${tc};border:1px solid ${tc}44;">${tag}</span>`;
    }).join('');

    this._detail.querySelector('.cgr-d-prev').textContent = card.preview || '';

    const linked = this._links
      .filter(l => l.source_id === card.id || l.target_id === card.id)
      .map(l => {
        const oid   = l.source_id === card.id ? l.target_id : l.source_id;
        const other = this._convs.find(c => c.id === oid);
        return (other?.title || oid).slice(0, 28);
      });
    this._detail.querySelector('.cgr-d-links').textContent =
      linked.length ? '⟶ ' + linked.join(' · ') : '';

    const openBtn = this._detail.querySelector('.cgr-d-open');
    openBtn.style.display = '';
    openBtn.onclick = () => {
      this._closeDetail();
      this.callbacks.onOpen && this.callbacks.onOpen(card.id);
    };
  }

  _closeDetail() { this._detail.style.display = 'none'; }

  /* ── Animation loop ─────────────────────────────────────── */

  _animate() {
    const loop = () => {
      this._animT += 0.012;
      if (this.mode === 'river') this._updateParticles();
      this._render();
      this._animId = requestAnimationFrame(loop);
    };
    loop();
  }

  /* ── Main render ────────────────────────────────────────── */

  _render() {
    const { ctx } = this;
    const light = document.documentElement.getAttribute('data-theme') === 'light';

    ctx.clearRect(0, 0, this.W, this.H);
    ctx.fillStyle = light ? '#f4f2ec' : '#04080f';
    ctx.fillRect(0, 0, this.W, this.H);

    if (!this._convs.length) {
      ctx.font      = '12px -apple-system, sans-serif';
      ctx.fillStyle = light ? 'rgba(60,55,80,0.35)' : 'rgba(160,160,180,0.35)';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No conversations', this.W / 2, this.H / 2);
      return;
    }

    if (this.mode === 'river') {
      this._drawRiverPath(light);
      this._drawParticles();
      this._drawActivityBars(light);
      this._drawDateAxis(light);
      this._drawStems(light);
      this._drawLinks(light);
      for (const card of this._riverCards) {
        if (!this._isVisible(card)) continue;
        const r = this._riverCardScreenRect(card);
        if (r.sx > this.W + 20 || r.sx + r.sw < -20 ||
            r.sy > this.H + 20 || r.sy + r.sh < -20) continue;
        this._drawCard(card, r, light);
      }
    } else {
      this._drawClusterHalos(light);
      this._drawLinks(light);
      for (const c of this._convs) {
        if (!this._isVisible(c)) continue;
        const pos = this._clusterPos[c.id];
        if (!pos) continue;
        this._drawCard(c, this._clusterCardScreen(pos), light);
      }
    }

    if (this._deleteMode) this._drawDeleteOverlay(light);
    if (this._linkMode)   this._drawLinkOverlay(light);
  }

  /* ── River drawing ──────────────────────────────────────── */

  _drawRiverPath(light) {
    const { ctx } = this;
    if (!this._riverCards.length) return;

    const last      = this._riverCards[this._riverCards.length - 1];
    const totalDays = Math.max((new Date(last.timestamp) - this._day0) / 86_400_000, 0.5);
    const steps     = 220;
    const pts       = [];

    for (let i = 0; i <= steps; i++) {
      const sx = _PAD_L * this.zoom + this.panX + (i / steps) * totalDays * _DAY_PX * this.zoom;
      pts.push({ x: sx, y: this._riverScreenY(sx) });
    }

    const stroke = (lw, color) => {
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length - 1; i++) {
        const mx = (pts[i].x + pts[i + 1].x) / 2;
        const my = (pts[i].y + pts[i + 1].y) / 2;
        ctx.quadraticCurveTo(pts[i].x, pts[i].y, mx, my);
      }
      ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
      ctx.strokeStyle = color;
      ctx.lineWidth   = lw;
      ctx.lineCap = ctx.lineJoin = 'round';
      ctx.stroke();
    };

    if (light) {
      stroke(36 * this.zoom, 'rgba(100,140,200,0.06)');
      stroke(22 * this.zoom, 'rgba(80,120,200,0.10)');
      stroke(12 * this.zoom, 'rgba(60,110,200,0.16)');
      stroke(6  * this.zoom, 'rgba(50,100,200,0.22)');
      stroke(2.5 * this.zoom,'rgba(60,130,220,0.45)');
      ctx.save();
      ctx.globalAlpha = 0.20 + 0.08 * Math.sin(this._animT * 1.8);
      stroke(1.2 * this.zoom,'rgba(100,160,240,0.70)');
      ctx.restore();
    } else {
      stroke(52 * this.zoom, 'rgba(20,70,140,0.08)');
      stroke(36 * this.zoom, 'rgba(25,90,170,0.13)');
      stroke(24 * this.zoom, 'rgba(30,110,200,0.18)');
      stroke(14 * this.zoom, 'rgba(40,130,220,0.26)');
      stroke(7  * this.zoom, 'rgba(60,160,240,0.38)');
      stroke(2.5 * this.zoom,'rgba(130,210,255,0.55)');
      ctx.save();
      ctx.globalAlpha = 0.30 + 0.12 * Math.sin(this._animT * 1.8);
      stroke(1.2 * this.zoom,'rgba(200,240,255,0.80)');
      ctx.restore();
    }
  }

  _drawParticles() {
    const { ctx } = this;
    if (!this._riverCards.length) return;

    const last      = this._riverCards[this._riverCards.length - 1];
    const totalDays = Math.max((new Date(last.timestamp) - this._day0) / 86_400_000, 0.5) + 0.5;

    for (const p of this._particles) {
      const wx = _PAD_L + p.t * totalDays * _DAY_PX;
      const sx = wx * this.zoom + this.panX;
      const sy = this._riverScreenY(sx) + p.yOff * this.zoom;
      if (sx < -20 || sx > this.W + 20) continue;
      ctx.beginPath();
      ctx.arc(sx, sy, p.size * Math.min(1, this.zoom * 0.8), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(120,200,255,${p.alpha})`;
      ctx.fill();
    }
  }

  _drawActivityBars(light) {
    const { ctx } = this;
    const activity = this._computeDailyActivity();
    const counts   = Object.values(activity);
    if (!counts.length) return;
    const maxC = Math.max(...counts);

    for (const [d, cnt] of Object.entries(activity)) {
      if (!this._day0) continue;
      const daysFromStart = (new Date(d) - this._day0) / 86_400_000;
      const wx = _PAD_L + daysFromStart * _DAY_PX;
      const sx = wx * this.zoom + this.panX;
      const sy = this._riverScreenY(sx);
      if (sx < -60 || sx > this.W + 60) continue;

      const frac = cnt / maxC;
      const bh   = frac * 28 * this.zoom;
      const bw   = 7 * this.zoom;

      ctx.fillStyle = light
        ? `rgba(60,110,200,${0.10 + frac * 0.20})`
        : `rgba(80,160,255,${0.12 + frac * 0.22})`;
      ctx.fillRect(sx - bw / 2, sy - bh - 6 * this.zoom, bw, bh);

      ctx.fillStyle    = light
        ? `rgba(40,90,180,${0.4 + frac * 0.4})`
        : `rgba(60,130,210,${0.4 + frac * 0.4})`;
      ctx.font         = `${Math.max(7, 8 * this.zoom)}px monospace`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(cnt, sx, sy - bh - 8 * this.zoom);
    }
  }

  _drawDateAxis(light) {
    const { ctx } = this;
    const seen = new Set();

    for (const card of this._riverCards) {
      if (!card.timestamp) continue;
      const day = card.timestamp.slice(0, 10);
      if (seen.has(day)) continue;
      seen.add(day);

      const sx = card.wx * this.zoom + this.panX;
      const sy = this._riverScreenY(sx);
      if (sx < -60 || sx > this.W + 60) continue;

      const tickLen = 6 * this.zoom;
      ctx.strokeStyle = light ? 'rgba(80,110,180,0.30)' : 'rgba(100,180,255,0.35)';
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(sx, sy - tickLen);
      ctx.lineTo(sx, sy + tickLen);
      ctx.stroke();

      const date = new Date(card.timestamp);
      ctx.font         = `${Math.max(9, 10 * this.zoom)}px monospace`;
      ctx.fillStyle    = light ? 'rgba(50,80,150,0.65)' : 'rgba(60,120,180,0.70)';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(
        `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`,
        sx, sy + 28 * this.zoom
      );
    }
  }

  _drawStems(light) {
    const { ctx } = this;
    for (const card of this._riverCards) {
      if (!this._isVisible(card)) continue;
      const stem = this._riverStemScreen(card);
      const col  = this._cardColor(card);

      ctx.beginPath();
      ctx.moveTo(stem.rx, stem.ry);
      const cpX = stem.rx + (card.above ? -15 : 15) * this.zoom;
      ctx.bezierCurveTo(
        cpX, stem.ry + (stem.cy - stem.ry) * 0.3,
        cpX, stem.ry + (stem.cy - stem.ry) * 0.7,
        stem.cx, stem.cy
      );
      ctx.strokeStyle = col + '55';
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([3, 5]);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.beginPath();
      ctx.arc(stem.rx, stem.ry, 3.5 * this.zoom, 0, Math.PI * 2);
      ctx.fillStyle = col + 'cc';
      ctx.fill();
    }
  }

  _drawLinks(light) {
    const { ctx } = this;
    /* Direct relations (solid); indirect (dashed) — POC 034 style */
    const SOLID_TYPES = new Set(['continues', 'spawned_from']);

    for (const link of this._links) {
      let sx, sy, tx, ty;

      if (this.mode === 'river') {
        const sc = this._riverCards.find(c => c.id === link.source_id);
        const tc = this._riverCards.find(c => c.id === link.target_id);
        if (!sc || !tc) continue;
        if (!this._isVisible(sc) || !this._isVisible(tc)) continue;
        /* Connect card centers so arcs run between the rectangles, not timeline dots */
        const sr = this._riverCardScreenRect(sc);
        const tr = this._riverCardScreenRect(tc);
        sx = sr.sx + sr.sw / 2;
        sy = sr.sy + sr.sh / 2;
        tx = tr.sx + tr.sw / 2;
        ty = tr.sy + tr.sh / 2;
      } else {
        const sp = this._clusterPos[link.source_id];
        const tp = this._clusterPos[link.target_id];
        if (!sp || !tp) continue;
        const sc = this._convs.find(c => c.id === link.source_id);
        const tc = this._convs.find(c => c.id === link.target_id);
        if (sc && !this._isVisible(sc)) continue;
        if (tc && !this._isVisible(tc)) continue;
        sx = (sp.x + _CARD_W / 2) * this.zoom + this.panX;
        sy = (sp.y + _CARD_H / 2) * this.zoom + this.panY;
        tx = (tp.x + _CARD_W / 2) * this.zoom + this.panX;
        ty = (tp.y + _CARD_H / 2) * this.zoom + this.panY;
      }

      const col      = CONV_RELATION_COLORS[link.relation_type] || _DEFAULT_COL;
      const isDirect = SOLID_TYPES.has(link.relation_type);
      const isCardHov = this._hoveredCard &&
        (this._hoveredCard.id === link.source_id || this._hoveredCard.id === link.target_id);
      const isLinkHov = this._hoveredLink === link;
      const isHov    = isCardHov || isLinkHov;
      const alpha    = isHov ? 0.92 : (isDirect ? 0.60 : 0.45);
      const lw       = isHov ? 2.5 : (isDirect ? 2 : 1.5);

      const { mx, my } = this._linkControlPoint(sx, sy, tx, ty);

      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.quadraticCurveTo(mx, my, tx, ty);
      ctx.strokeStyle = col;
      ctx.lineWidth   = lw;
      ctx.globalAlpha = alpha;
      if (!isDirect) ctx.setLineDash([4, 7]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      /* Arrow tip on hover */
      if (isHov) {
        const angle = Math.atan2(ty - my, tx - mx);
        const ar    = 7;
        ctx.fillStyle   = col;
        ctx.globalAlpha = 0.85;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(tx - ar * Math.cos(angle - 0.4), ty - ar * Math.sin(angle - 0.4));
        ctx.lineTo(tx - ar * Math.cos(angle + 0.4), ty - ar * Math.sin(angle + 0.4));
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      /* Relation-type label when link is directly hovered */
      if (isLinkHov) {
        const label   = link.relation_type.replace(/_/g, ' ');
        const fontSize = Math.max(10, 11 * this.zoom);
        ctx.font         = `600 ${fontSize}px system-ui`;
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        /* Background pill */
        const tw = ctx.measureText(label).width;
        const px = 6, py = 3;
        const lx = mx, ly = my - 18 * this.zoom;
        ctx.fillStyle   = light ? 'rgba(240,240,255,0.92)' : 'rgba(4,8,20,0.88)';
        ctx.globalAlpha = 1;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(lx - tw / 2 - px, ly - fontSize / 2 - py, tw + px * 2, fontSize + py * 2, 4);
        else               ctx.rect(lx - tw / 2 - px, ly - fontSize / 2 - py, tw + px * 2, fontSize + py * 2);
        ctx.fill();
        ctx.fillStyle = col;
        ctx.fillText(label, lx, ly);
      }
    }
  }

  /* ── Card drawing (shared River + Cluster) ──────────────── */

  _drawCard(c, rect, light) {
    const { ctx }          = this;
    const { sx, sy, sw, sh } = rect;
    const col   = this._cardColor(c);
    const isHov = this._hoveredCard?.id   === c.id;
    const isSel = this._selectedCard?.id  === c.id;
    const isLnk = this._linkSelections.has(c.id);
    const isDel = this._deleteSelections.has(c.id);
    const r     = 7 * this.zoom;

    // Shadow
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(sx + 3, sy + 3, sw, sh, r);
    else               ctx.rect(sx + 3, sy + 3, sw, sh);
    ctx.fill();

    // Body
    ctx.fillStyle = isDel
      ? (light ? '#2a0808' : '#1c0606')
      : light
        ? (isSel || isHov ? '#e8e4dc' : '#f4f2ec')
        : (isSel || isHov ? '#111e34' : '#0a1424');
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(sx, sy, sw, sh, r);
    else               ctx.rect(sx, sy, sw, sh);
    ctx.fill();

    // Left accent bar
    ctx.fillStyle   = isDel ? '#d45060' : col;
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(sx, sy, 3 * this.zoom, sh, [r, 0, 0, r]);
    else               ctx.rect(sx, sy, 3 * this.zoom, sh);
    ctx.fill();
    ctx.globalAlpha = 1;

    // Border
    ctx.strokeStyle = isDel ? '#d45060'
      : isLnk ? '#f0a040'
      : isSel ? col
      : isHov ? col + 'bb'
      : light  ? col + '38'
      :          col + '44';
    ctx.lineWidth = isDel || isSel || isLnk ? 1.8 : 1;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(sx, sy, sw, sh, r);
    else               ctx.rect(sx, sy, sw, sh);
    ctx.stroke();

    // In-KB dot (green, top-right)
    if (c.in_kb) {
      ctx.beginPath();
      ctx.arc(sx + sw - 9 * this.zoom, sy + 9 * this.zoom, 3.5 * this.zoom, 0, Math.PI * 2);
      ctx.fillStyle = '#50b870';
      ctx.fill();
    }

    // Date label
    ctx.fillStyle    = col + 'cc';
    ctx.font         = `${Math.max(7, 8 * this.zoom)}px monospace`;
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'top';
    if (c.timestamp) {
      const d = new Date(c.timestamp);
      ctx.fillText(
        `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`,
        sx + 8 * this.zoom, sy + 7 * this.zoom
      );
    }

    // Title — 2-line wrap
    const title   = (c.title || c.label || '(empty)');
    const words   = title.split(' ');
    const midW    = Math.ceil(words.length / 2);
    const lines   = [words.slice(0, midW).join(' '), words.slice(midW).join(' ')];
    const fsize   = Math.max(7, 9 * this.zoom);
    const avail   = Math.floor((_CARD_W - 16) / (0.55 * fsize));
    lines.forEach((line, li) => {
      if (!line) return;
      ctx.fillStyle    = light ? 'rgba(20,30,60,0.90)' : '#d0e0f4';
      ctx.font         = `600 ${fsize}px system-ui`;
      ctx.textBaseline = 'top';
      ctx.fillText(
        line.length > avail ? line.slice(0, avail - 1) + '…' : line,
        sx + 8 * this.zoom, sy + (21 + li * 13) * this.zoom
      );
    });

    // Domain tags
    if (this.zoom > 0.45) {
      const domains = c.domains || c.tags || [];
      domains.slice(0, 2).forEach((tag, ti) => {
        ctx.fillStyle    = (DOMAIN_COLORS[tag] || _DEFAULT_COL) + '88';
        ctx.font         = `${Math.max(6, 7.5 * this.zoom)}px system-ui`;
        ctx.textBaseline = 'bottom';
        ctx.fillText('#' + tag.slice(0, 7), sx + (8 + ti * 72) * this.zoom, sy + sh - 7 * this.zoom);
      });
    }
  }

  /* ── Cluster drawing ────────────────────────────────────── */

  _drawClusterHalos(light) {
    const { ctx } = this;
    const groups  = {};
    for (const c of this._convs) {
      const pos = this._clusterPos[c.id];
      if (!pos) continue;
      const dom = this._cardDomain(c);
      if (!groups[dom]) groups[dom] = pos;
    }

    for (const [domain, pos] of Object.entries(groups)) {
      if (DOMAIN_COLORS[domain] && !this._activeDomains.has(domain)) continue;
      const col     = DOMAIN_COLORS[domain] || _DEFAULT_COL;
      const sx      = pos.clusterX * this.zoom + this.panX;
      const sy      = pos.clusterY * this.zoom + this.panY;
      const members = this._convs.filter(c => this._cardDomain(c) === domain);
      const r       = (100 + members.length * 22) * this.zoom;

      const grd = ctx.createRadialGradient(sx, sy, r * 0.1, sx, sy, r);
      grd.addColorStop(0, col + '1a');
      grd.addColorStop(1, 'transparent');
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle    = col + (light ? '66' : '55');
      ctx.font         = `700 ${Math.max(10, 13 * this.zoom)}px system-ui`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(domain.toUpperCase(), sx, sy - r * 0.75);
    }
  }

  /* ── Delete mode overlay ────────────────────────────────── */

  _drawDeleteOverlay(light) {
    const { ctx } = this;
    ctx.save();
    ctx.fillStyle = 'rgba(212,80,96,0.06)';
    ctx.fillRect(0, this.H - 38, this.W, 38);
    ctx.font         = '11px -apple-system, sans-serif';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = '#d45060';
    const n = this._deleteSelections.size;
    ctx.fillText(
      n === 0 ? 'Kliknij rozmowy aby zaznaczyć do usunięcia'
              : `${n} zaznaczone — kliknij "Usuń zaznaczone" w pasku narzędzi`,
      this.W / 2, this.H - 19
    );
    ctx.restore();
  }

  /* ── Link mode overlay ──────────────────────────────────── */

  _drawLinkOverlay(light) {
    const { ctx } = this;
    ctx.save();
    ctx.fillStyle = 'rgba(240,160,64,0.06)';
    ctx.fillRect(0, this.H - 38, this.W, 38);
    ctx.font         = '11px -apple-system, sans-serif';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = '#f0a040';
    const n = this._linkSelections.size;
    ctx.fillText(
      n === 0 ? 'Click conversations to select, then click "Zatwierdź"'
              : `${n} selected — click more or "Zatwierdź" to link`,
      this.W / 2, this.H - 19
    );
    ctx.restore();
  }
}
