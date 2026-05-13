/* ============================================================
   4S1T Agent AI — KB Knowledge Graph 3D
   True 3D force simulation · perspective projection
   Based on POC NetworkGraph_012
   ============================================================ */

// Domain colours are injected at runtime by the page template (kb_graph.html)
// via KB_DOMAIN_COLORS[id] = color assignments generated from kb_domains.yaml.
const KB_DOMAIN_COLORS = {};

const KB_EDGE_COLORS = {
  direct:    '#3a70b0',
  l1l2:      '#3a9060',
  indirect:  '#405060',
  support:   '#3a9060',
  contradict:'#b04040',
};

const KB_EDGE_ALPHAS = {
  direct:    0.60,
  l1l2:      0.55,
  indirect:  0.28,
  support:   0.70,
  contradict:0.70,
};

function _kbDomainColor(domain) {
  return KB_DOMAIN_COLORS[domain] || '#6B7280';
}

class KBGraph3D {
  constructor(containerId, onSelect) {
    this.container   = document.getElementById(containerId);
    this.onSelect    = onSelect;
    this.nodes       = [];
    this.edges       = [];
    this.stars       = [];
    this.animId      = null;

    // Camera
    this.autoRotate  = false;
    this.rotY        = 0.35;
    this.rotX        = 0.28;
    this.zoom        = 1.0;

    // Interaction
    this.dragging    = false;
    this.lastMouse   = null;
    this.hoveredNode = null;

    // Filters
    this.activeDomains = new Set();
    this.activeLevel   = 'l1';    // 'all' | 'domains' | 'l1' | 'l2'
    this.edgeFlags     = { direct: true, l1l2: true, indirect: true, support: true, contradict: true };

    // Simulation settling (frame-count based)
    this._settled       = false;
    this._simFramesLeft = 200;

    this._paused = false;
    this._buildCanvas();
    this._initEvents();
    this._initVisibility();
    this._animate();
  }

  /* ── Canvas setup ──────────────────────────────────────────── */
  _buildCanvas() {
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText =
      'position:absolute;inset:0;width:100%;height:100%;cursor:grab;display:block;';
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }

  _resize() {
    const W   = this.container.offsetWidth  || 800;
    const H   = this.container.offsetHeight || 500;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width  = W * dpr;
    this.canvas.height = H * dpr;
    this.canvas.style.width  = W + 'px';
    this.canvas.style.height = H + 'px';
    this.ctx.scale(dpr, dpr);
    this.W = W;
    this.H = H;
  }

  /* ── Data loading ──────────────────────────────────────────── */
  /**
   * @param {Array}  accounts   - from /api/v1/kb/accounts
   * @param {Array}  graphEdges - from /api/v1/kb/graph  (edges array)
   * @param {Object} statsMap   - { account_id: item_count }
   * @returns {string[]} sorted domain list (for building filter buttons)
   */
  loadData(accounts, graphEdges, statsMap) {
    this.nodes = [];
    this.edges = [];
    this._settled = false;
    this._simFramesLeft = 200;
    this._ensureAnimating();

    // Collect unique domains
    const domainSet = new Set();
    accounts.forEach(a => {
      (a.domains || '').split('|').map(s => s.trim()).filter(Boolean)
        .forEach(d => domainSet.add(d));
    });
    const domainList = [...domainSet].sort();
    this.activeDomains = new Set(domainList);

    const nodeMap = {};

    /* Pass 1 — domain nodes on a sphere (golden-angle distribution) */
    domainList.forEach((d, i) => {
      const phi   = Math.acos(1 - 2 * (i + 0.5) / domainList.length);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      const R = 200;
      const n = {
        id:     'd_' + d,
        label:  d.charAt(0).toUpperCase() + d.slice(1),
        type:   'domain',
        domain: d,
        layer:  0,
        chunks: 0,
        data:   null,
        x3: R * Math.sin(phi) * Math.cos(theta),
        y3: R * Math.cos(phi),
        z3: R * Math.sin(phi) * Math.sin(theta),
        vx: 0, vy: 0, vz: 0,
      };
      n.r = this._chunkRadius(0);
      nodeMap[n.id] = n;
    });

    /* Pass 2 — L1 accounts orbit their domain */
    const l1 = accounts.filter(a => (a.layer || 1) === 1);
    const l2 = accounts.filter(a => (a.layer || 1) >= 2);

    l1.forEach((a, i) => {
      const allDomains  = (a.domains || '').split('|').map(s => s.trim()).filter(Boolean);
      const primaryDomain = allDomains[0] || '';
      const dom   = nodeMap['d_' + primaryDomain];
      const angle = (i / Math.max(l1.length, 1)) * Math.PI * 2;
      const R     = 115;
      const chunks = statsMap[a.id] || 0;
      const n = {
        id:        a.id,
        label:     a.display_name || a.id,
        type:      'l1',
        domain:    primaryDomain,
        allDomains,
        layer:     1,
        chunks,
        data:      a,
        x3: (dom?.x3 ?? 0) + R * Math.cos(angle),
        y3: (dom?.y3 ?? 0) + (Math.random() - 0.5) * 60,
        z3: (dom?.z3 ?? 0) + R * Math.sin(angle),
        vx: 0, vy: 0, vz: 0,
      };
      n.r = this._chunkRadius(chunks);
      nodeMap[n.id] = n;
      if (dom) dom.chunks += chunks;
    });

    /* Pass 3 — L2 accounts orbit their L1 parent (or domain fallback) */
    l2.forEach((a, i) => {
      const allDomains    = (a.domains || '').split('|').map(s => s.trim()).filter(Boolean);
      const primaryDomain = allDomains[0] || '';
      const parentEdge = graphEdges.find(
        e => e.to === a.id && nodeMap[e.from] && nodeMap[e.from].layer === 1
      );
      const ref   = parentEdge ? nodeMap[parentEdge.from] : nodeMap['d_' + primaryDomain];
      const angle = (i / Math.max(l2.length, 1)) * Math.PI * 2;
      const R     = 65;
      const chunks = statsMap[a.id] || 0;
      const n = {
        id:        a.id,
        label:     a.display_name || a.id,
        type:      'l2',
        domain:    primaryDomain,
        allDomains,
        layer:     2,
        chunks,
        data:      a,
        x3: (ref?.x3 ?? 0) + R * Math.cos(angle),
        y3: (ref?.y3 ?? 0) + (Math.random() - 0.5) * 35,
        z3: (ref?.z3 ?? 0) + R * Math.sin(angle),
        vx: 0, vy: 0, vz: 0,
      };
      n.r = this._chunkRadius(chunks);
      nodeMap[n.id] = n;
    });

    // Fix domain radii now that chunk sums are complete
    domainList.forEach(d => {
      const n = nodeMap['d_' + d];
      if (n) n.r = this._chunkRadius(n.chunks);
    });

    this.nodes = Object.values(nodeMap);

    /* Build edges */
    // Synthetic direct edges:
    //   L1: one edge per domain they belong to (multi-domain accounts get multiple links)
    //   L2: edge to their L1 parent; fall back to domain edges if no parent found
    this.nodes.forEach(acc => {
      if (acc.type === 'domain') return;
      if (acc.type === 'l1') {
        (acc.allDomains || [acc.domain]).forEach(d => {
          const dom = nodeMap['d_' + d];
          if (dom) this.edges.push({ source: dom, target: acc, type: 'direct', label: '' });
        });
      } else {
        // L2: prefer L1 parent from API relations
        const parentEdge = graphEdges.find(
          e => e.to === acc.id && nodeMap[e.from] && nodeMap[e.from].type === 'l1'
        );
        if (parentEdge) {
          this.edges.push({ source: nodeMap[parentEdge.from], target: acc, type: 'l1l2', label: '' });
        } else {
          (acc.allDomains || [acc.domain]).forEach(d => {
            const dom = nodeMap['d_' + d];
            if (dom) this.edges.push({ source: dom, target: acc, type: 'direct', label: '' });
          });
        }
      }
    });

    // API-provided relations
    graphEdges.forEach(e => {
      const s = nodeMap[e.from];
      const t = nodeMap[e.to];
      if (!s || !t) return;
      const type = this._mapRelationType(e.relation_type);
      this.edges.push({ source: s, target: t, type, label: e.relation_type || '' });
    });

    /* Ambient stars */
    this.stars = Array.from({ length: 200 }, () => ({
      x3: (Math.random() - 0.5) * 1400,
      y3: (Math.random() - 0.5) * 1200,
      z3: (Math.random() - 0.5) * 1400,
      r:  Math.random() * 1.3 + 0.2,
      a:  Math.random() * 0.15 + 0.03,
    }));

    return domainList;
  }

  _mapRelationType(rt) {
    if (!rt) return 'indirect';
    const r = rt.toLowerCase();
    if (r.includes('contradict') || r.includes('dispute')) return 'contradict';
    if (r.includes('support') || r.includes('cit') || r.includes('amplif') || r.includes('mention'))
      return 'support';
    return 'indirect';
  }

  _chunkRadius(chunks) {
    return 9 + Math.sqrt(Math.max(0, chunks)) * 0.85;
  }

  /* ── Filter control API (called from template) ─────────────── */
  _wake() { this._settled = false; this._simFramesLeft = 60; this._ensureAnimating(); }

  setDomainFilter(domain, active) {
    if (active) this.activeDomains.add(domain);
    else        this.activeDomains.delete(domain);
    this._wake();
  }

  setLevel(level) { this.activeLevel = level; this._wake(); }

  toggleEdge(type) {
    this.edgeFlags[type] = !this.edgeFlags[type];
    this._wake();
    return this.edgeFlags[type];
  }

  toggleAutoRotate() {
    this.autoRotate = !this.autoRotate;
    if (this.autoRotate) this._ensureAnimating();
    return this.autoRotate;
  }

  /* ── Visibility ────────────────────────────────────────────── */
  _levelVisible(type) {
    if (this.activeLevel === 'domains') return type === 'domain';
    if (this.activeLevel === 'l1')     return type !== 'l2';
    return true;
  }

  _nodeVisible(n) {
    return this.activeDomains.has(n.domain) && this._levelVisible(n.type);
  }

  /* ── 3D force simulation ───────────────────────────────────── */
  _simulate3D() {
    const REPEL     = 55000;
    const LINK_BASE = 170;
    const CENTER    = 0.00045;
    const DAMP      = 0.82;

    for (let i = 0; i < this.nodes.length; i++) {
      const a = this.nodes[i];
      if (!this._nodeVisible(a)) continue;

      a.vx -= a.x3 * CENTER;
      a.vy -= a.y3 * CENTER;
      a.vz -= a.z3 * CENTER;

      for (let j = i + 1; j < this.nodes.length; j++) {
        const b = this.nodes[j];
        if (!this._nodeVisible(b)) continue;
        const dx = a.x3 - b.x3, dy = a.y3 - b.y3, dz = a.z3 - b.z3;
        const d2 = dx*dx + dy*dy + dz*dz + 1;
        const d  = Math.sqrt(d2);
        const f  = REPEL / d2;
        a.vx += dx/d*f; a.vy += dy/d*f; a.vz += dz/d*f;
        b.vx -= dx/d*f; b.vy -= dy/d*f; b.vz -= dz/d*f;
      }
    }

    this.edges.forEach(e => {
      if (!this._nodeVisible(e.source) || !this._nodeVisible(e.target)) return;
      if (!this.edgeFlags[e.type]) return;
      const dx = e.target.x3 - e.source.x3;
      const dy = e.target.y3 - e.source.y3;
      const dz = e.target.z3 - e.source.z3;
      const d  = Math.sqrt(dx*dx + dy*dy + dz*dz) + 1;
      const td = e.type === 'direct' ? LINK_BASE * 0.65 : LINK_BASE * 1.45;
      const f  = (d - td) * 0.0048;
      const fx = dx/d*f, fy = dy/d*f, fz = dz/d*f;
      e.source.vx += fx; e.source.vy += fy; e.source.vz += fz;
      e.target.vx -= fx; e.target.vy -= fy; e.target.vz -= fz;
    });

    this.nodes.forEach(n => {
      if (!this._nodeVisible(n)) return;
      n.vx *= DAMP; n.vy *= DAMP; n.vz *= DAMP;
      n.x3 += n.vx; n.y3 += n.vy; n.z3 += n.vz;
    });

    if (this._simFramesLeft > 0) {
      this._simFramesLeft--;
      if (this._simFramesLeft === 0) this._settled = true;
    }
  }

  /* ── Perspective projection ────────────────────────────────── */
  _project(x3, y3, z3) {
    const cY = Math.cos(this.rotY), sY = Math.sin(this.rotY);
    const x1 =  x3*cY + z3*sY;
    const z1 = -x3*sY + z3*cY;
    const cX = Math.cos(this.rotX), sX = Math.sin(this.rotX);
    const y2 = y3*cX - z1*sX;
    const z2 = y3*sX + z1*cX;
    const fov   = 620 * this.zoom;
    const depth = fov / (fov + z2 + 500);
    return { x: this.W/2 + x1*depth, y: this.H/2 + y2*depth, s: depth, z: z2 };
  }

  _projectAll() {
    this.nodes.forEach(n => {
      const p = this._project(n.x3, n.y3, n.z3);
      n.px = p.x; n.py = p.y; n.ps = p.s; n.pz = p.z;
    });
  }

  /* ── Hit test ──────────────────────────────────────────────── */
  _hitTest(mx, my) {
    const sorted = this.nodes
      .filter(n => this._nodeVisible(n))
      .sort((a, b) => b.pz - a.pz);
    for (const n of sorted) {
      if (Math.hypot(mx - n.px, my - n.py) < (n.r || 10) * n.ps * 1.35 + 6) return n;
    }
    return null;
  }

  /* ── Draw: perspective grid ────────────────────────────────── */
  _drawGrid(light) {
    const { ctx } = this;
    const GRID_Y = 400, GRID_EXT = 700, STEPS = 10;
    ctx.save();
    ctx.strokeStyle = light ? 'rgba(0,0,0,0.045)' : 'rgba(255,255,255,0.022)';
    ctx.lineWidth = 1;
    for (let i = -STEPS; i <= STEPS; i++) {
      const x = (i / STEPS) * GRID_EXT;
      const pA = this._project(x,         GRID_Y, -GRID_EXT);
      const pB = this._project(x,         GRID_Y,  GRID_EXT);
      const pC = this._project(-GRID_EXT, GRID_Y,  x);
      const pD = this._project( GRID_EXT, GRID_Y,  x);
      if (pA.s > 0 && pB.s > 0) {
        ctx.beginPath(); ctx.moveTo(pA.x, pA.y); ctx.lineTo(pB.x, pB.y); ctx.stroke();
      }
      if (pC.s > 0 && pD.s > 0) {
        ctx.beginPath(); ctx.moveTo(pC.x, pC.y); ctx.lineTo(pD.x, pD.y); ctx.stroke();
      }
    }
    ctx.restore();
  }

  /* ── Draw: ambient stars ───────────────────────────────────── */
  _drawStars(light) {
    const { ctx } = this;
    const starRgb = light ? '60,70,110' : '190,210,245';
    for (const s of this.stars) {
      const p = this._project(s.x3, s.y3, s.z3);
      if (p.s < 0.05) continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, s.r * p.s, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${starRgb},${s.a * p.s * 2})`;
      ctx.fill();
    }
  }

  /* ── Draw: edges ───────────────────────────────────────────── */
  _drawEdges() {
    const { ctx } = this;
    const visible = this.edges
      .filter(e => this._nodeVisible(e.source) && this._nodeVisible(e.target) && this.edgeFlags[e.type])
      .sort((a, b) =>
        (a.source.pz + a.target.pz) / 2 - (b.source.pz + b.target.pz) / 2
      );

    visible.forEach(e => {
      const sx = e.source.px, sy = e.source.py;
      const tx = e.target.px, ty = e.target.py;
      const avgS  = (e.source.ps + e.target.ps) / 2;
      const isHov = this.hoveredNode &&
        (this.hoveredNode.id === e.source.id || this.hoveredNode.id === e.target.id);
      const baseAlpha = KB_EDGE_ALPHAS[e.type] * avgS * 1.6;
      const alpha = Math.min(0.92, isHov ? baseAlpha * 1.7 : baseAlpha);
      const col   = KB_EDGE_COLORS[e.type] || '#445566';

      const mx = (sx + tx) / 2 + (ty - sy) * 0.09;
      const my = (sy + ty) / 2 - (tx - sx) * 0.09;

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = col;
      ctx.lineWidth   = (e.type === 'direct' || e.type === 'l1l2' ? 1.8 : 1.2) * avgS;
      if (e.type === 'indirect') ctx.setLineDash([4, 7]);

      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.quadraticCurveTo(mx, my, tx, ty);
      ctx.stroke();
      ctx.setLineDash([]);

      // Arrow tip (all except indirect/l1l2)
      if (e.type !== 'indirect' && e.type !== 'l1l2') {
        const angle    = Math.atan2(ty - my, tx - mx);
        const ar       = 6 * avgS;
        const nr       = (e.target.r || 10) * e.target.ps;
        const baseAngle = Math.atan2(ty - sy, tx - sx);
        const tipX     = tx - nr * Math.cos(baseAngle);
        const tipY     = ty - nr * Math.sin(baseAngle);
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - ar * Math.cos(angle - 0.42), tipY - ar * Math.sin(angle - 0.42));
        ctx.lineTo(tipX - ar * Math.cos(angle + 0.42), tipY - ar * Math.sin(angle + 0.42));
        ctx.closePath();
        ctx.fill();
      }

      // Edge label on hover
      if (isHov && e.label) {
        ctx.globalAlpha  = 0.88;
        ctx.fillStyle    = col;
        ctx.font         = '10px system-ui';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(e.label, (sx + tx) / 2, (sy + ty) / 2 - 11);
      }

      ctx.restore();
    });
  }

  /* ── Draw: nodes ───────────────────────────────────────────── */
  _drawNodes(light) {
    const { ctx } = this;
    const sorted = this.nodes
      .filter(n => this._nodeVisible(n))
      .sort((a, b) => a.pz - b.pz);   // back-to-front

    sorted.forEach(node => {
      const px = node.px, py = node.py, ps = node.ps;
      // Skip nodes behind or too close to camera — avoids gradient radius errors
      if (ps <= 0.05) return;
      const r   = node.r * ps;
      if (r < 1) return;   // too small to draw cleanly
      const col = _kbDomainColor(node.domain);
      const isHov = node === this.hoveredNode;
      const depth = Math.max(0.15, Math.min(1, ps * 1.5));

      ctx.save();

      // Outer glow
      if (isHov || node.type === 'domain') {
        const gr  = Math.max(r * 2.0, 2);
        const grd = ctx.createRadialGradient(px, py, Math.max(r * 0.3, 0.1), px, py, gr);
        grd.addColorStop(0, col + (isHov ? '38' : '18'));
        grd.addColorStop(1, 'transparent');
        ctx.fillStyle = grd;
        ctx.beginPath(); ctx.arc(px, py, gr, 0, Math.PI * 2); ctx.fill();
      }

      // Body gradient
      const brd = ctx.createRadialGradient(px - r*.3, py - r*.35, Math.max(r*.05, 0.1), px, py, Math.max(r, 0.1));
      const faceHex = node.type === 'domain' ? 'cc' : node.type === 'l1' ? '99' : '77';
      brd.addColorStop(0, col + faceHex);
      brd.addColorStop(1, col + '22');
      ctx.globalAlpha = depth;
      ctx.fillStyle   = brd;
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fill();

      // Rim
      ctx.strokeStyle = isHov ? (light ? '#222' : '#fff') : col + 'bb';
      ctx.lineWidth   = (node.type === 'domain' ? 2 : 1.2) * ps;
      ctx.stroke();

      // Specular highlight
      const shine = ctx.createRadialGradient(px - r*.28, py - r*.28, 0, px, py, Math.max(r * .7, 0.1));
      shine.addColorStop(0, light ? 'rgba(255,255,255,0.30)' : 'rgba(255,255,255,0.18)');
      shine.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = shine;
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fill();

      // Chunk arc (progress ring on non-domain nodes)
      if (node.type !== 'domain' && node.chunks > 0) {
        const pct = Math.min(1, node.chunks / 250);
        ctx.strokeStyle = col + '40';
        ctx.lineWidth   = 2.2 * ps;
        ctx.beginPath();
        ctx.arc(px, py, r + 3.5 * ps, -Math.PI / 2, -Math.PI / 2 + pct * Math.PI * 2);
        ctx.stroke();
      }

      // Label
      if (r > 4 || isHov) {
        const fs = Math.max(8, Math.min(12, r * 0.78));
        ctx.globalAlpha  = Math.min(0.92, depth * 1.05);
        ctx.fillStyle    = node.type === 'domain' ? col : (light ? '#1C1C26' : '#d0e0f0');
        ctx.font         = `${node.type === 'domain' ? 700 : 500} ${fs}px system-ui`;
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        const words = node.label.split(' ');
        const line1 = words[0].length > 10 ? words[0].slice(0, 9) + '…' : words[0];
        ctx.fillText(line1, px, words.length > 1 ? py - 5 : py);
        if (words.length > 1 && r > 13) {
          ctx.font      = `${Math.max(7, Math.min(9, r * .55))}px system-ui`;
          ctx.fillStyle = col + 'aa';
          ctx.fillText(words.slice(1).join(' ').slice(0, 9), px, py + 7);
        }
      }

      // Hover badge: item count
      if (isHov) {
        ctx.globalAlpha = 1;
        ctx.fillStyle   = light ? 'rgba(240,237,230,0.92)' : 'rgba(0,0,0,0.78)';
        ctx.fillRect(px - 30, py + r + 3, 60, 13);
        ctx.fillStyle    = col;
        ctx.font         = '9px monospace';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(
          node.chunks > 0 ? `${node.chunks} items` : node.type,
          px, py + r + 9.5
        );
      }

      ctx.restore();
    });
  }

  /* ── Main draw frame ───────────────────────────────────────── */
  _drawFrame() {
    const { ctx, W, H } = this;
    if (!W || !H) return;

    const light = document.documentElement.getAttribute('data-theme') === 'light';
    ctx.clearRect(0, 0, W, H);

    if (!this.nodes.length) {
      ctx.font      = '14px system-ui';
      ctx.fillStyle = light ? 'rgba(60,55,80,0.4)' : 'rgba(180,180,200,0.4)';
      ctx.textAlign = 'center';
      ctx.fillText('Loading graph…', W / 2, H / 2);
      return;
    }

    if (this.autoRotate) this.rotY += 0.004;

    if (!this._settled) this._simulate3D();
    this._projectAll();
    this._drawGrid(light);
    this._drawStars(light);
    this._drawEdges();
    this._drawNodes(light);
  }

  /* ── Events ────────────────────────────────────────────────── */
  _initEvents() {
    const c = this.canvas;

    c.addEventListener('mousedown', e => {
      this.dragging   = true;
      this.lastMouse  = { x: e.clientX, y: e.clientY };
      this.autoRotate = false;
      this._ensureAnimating();
      c.style.cursor  = 'grabbing';
      if (window._onKBGraphRotateStop) window._onKBGraphRotateStop();
    });

    c.addEventListener('mousemove', e => {
      if (this.dragging && this.lastMouse) {
        this.rotY += (e.clientX - this.lastMouse.x) * 0.007;
        this.rotX += (e.clientY - this.lastMouse.y) * 0.007;
        this.rotX  = Math.max(-1.4, Math.min(1.4, this.rotX));
        this.lastMouse = { x: e.clientX, y: e.clientY };
      } else {
        const rect = c.getBoundingClientRect();
        this.hoveredNode = this._hitTest(e.clientX - rect.left, e.clientY - rect.top);
        c.style.cursor = this.hoveredNode ? 'pointer' : (this.dragging ? 'grabbing' : 'grab');
      }
    });

    c.addEventListener('mouseup', e => {
      if (!this.dragging) return;
      this.dragging  = false;
      c.style.cursor = 'grab';
      const rect = c.getBoundingClientRect();
      const hit  = this._hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (hit) this.onSelect?.(hit);
    });

    c.addEventListener('mouseleave', () => { this.dragging = false; });

    c.addEventListener('wheel', e => {
      e.preventDefault();
      this.zoom = Math.max(0.2, Math.min(5, this.zoom * (e.deltaY > 0 ? 0.92 : 1.09)));
    }, { passive: false });

    // Touch
    let lastTouchDist = 0;
    c.addEventListener('touchstart', e => {
      if (e.touches.length === 1) {
        this.dragging   = true;
        this.lastMouse  = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        this.autoRotate = false;
        if (window._onKBGraphRotateStop) window._onKBGraphRotateStop();
      } else if (e.touches.length === 2) {
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        lastTouchDist = Math.hypot(dx, dy);
      }
      e.preventDefault();
    }, { passive: false });

    c.addEventListener('touchmove', e => {
      if (e.touches.length === 1 && this.dragging && this.lastMouse) {
        this.rotY += (e.touches[0].clientX - this.lastMouse.x) * 0.007;
        this.rotX += (e.touches[0].clientY - this.lastMouse.y) * 0.007;
        this.rotX  = Math.max(-1.4, Math.min(1.4, this.rotX));
        this.lastMouse = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      } else if (e.touches.length === 2) {
        const dx   = e.touches[0].clientX - e.touches[1].clientX;
        const dy   = e.touches[0].clientY - e.touches[1].clientY;
        const dist = Math.hypot(dx, dy);
        if (lastTouchDist)
          this.zoom = Math.max(0.2, Math.min(5, this.zoom * dist / lastTouchDist));
        lastTouchDist = dist;
      }
      e.preventDefault();
    }, { passive: false });

    c.addEventListener('touchend', () => { this.dragging = false; lastTouchDist = 0; });
  }

  _animate() {
    if (this._paused) { this.animId = null; return; }

    // Stop loop when settled, not auto-rotating, and not dragging — resume on interaction
    if (this._settled && !this.autoRotate && !this.dragging) {
      this.animId = null;
      return;
    }

    const now = performance.now();
    const interval = this._settled ? 67 : 50;  // 15fps settled, 20fps settling
    if (now - (this._lastFrame || 0) >= interval) {
      this._lastFrame = now;
      this._drawFrame();
    }
    this.animId = requestAnimationFrame(() => this._animate());
  }

  // Restart loop if stopped
  _ensureAnimating() {
    if (!this.animId && !this._paused) this._animate();
  }

  _onVisibility() {
    if (document.hidden) {
      this._paused = true;
      if (this.animId) { cancelAnimationFrame(this.animId); this.animId = null; }
    } else {
      this._paused = false;
      this._animate();
    }
  }

  _initVisibility() {
    this._visHandler = () => this._onVisibility();
    document.addEventListener('visibilitychange', this._visHandler);
  }

  dispose() {
    if (this.animId) cancelAnimationFrame(this.animId);
    if (this._visHandler) document.removeEventListener('visibilitychange', this._visHandler);
    this.canvas.remove();
  }
}
