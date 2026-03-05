# Directory Organization Rules - 4S1T Agent AI

## Overview
This document defines the directory structure rules to maintain a clean, organized project structure.

## Main Directory (/project-root/)
**Purpose:** Active project files only

### KEEP in Root:
- Source code (`src/` directory)
- Frontend (`web/` directory)
- Configuration: `.env`, `requirements.txt`, `config/`
- Core documentation: `README.md`, `INSTALL.md`, `DIRECTORY_ORGANIZATION_RULES.md`
- Data & vector DB: `data/` (contains `agent.db` and `chroma/` subdirectory)
- Docker: `Dockerfile`, `docker-compose.yml`
- Git: `.git/`, `.gitignore`, `.github/`
- Services: `services/` (executor, gateway)
- Active documentation: `docs/` (architecture, module references — see structure below)
- Secrets: `.secrets/` (gitignored)
- Runtime dirs: `certs/`, `logs/`
- Version: `VERSION`

### NEVER put in Root:
- Implementation notes / phase reports (→ `01_archive/docs/implementation/`)
- Old deployment scripts or packages (→ `01_archive/`)
- Temporary or test files
- Nested `01_archive/` inside `docs/` — **docs/ must NOT have its own archive subfolder**

### MOVE to 01_archive/:
- Documentation → Appropriate `01_archive/docs/` subfolder
- Old deployment packages → `01_archive/deploy_package/`
- Deployment archives (*.tar.gz) → `01_archive/deploy_package/`
- Old scripts → `01_archive/old_scripts/`
- Temporary files → `01_archive/temp_files/`
- Test files → `01_archive/tests/`
- Log files → `01_archive/logs/`

## docs/ Structure (Active Documentation Only)
```
docs/
├── architecture/        # System architecture docs (agent_orchestration.md, etc.)
└── modules/             # Per-module reference docs (nostr_nip17.md, privacy_layer.md, etc.)
```
**Rule:** `docs/` contains only active reference documentation. Implementation notes, deployment
summaries, and fix reports go to `01_archive/docs/implementation/` immediately after completion.

## data/ Structure
```
data/
├── agent.db             # Main SQLite database (DATABASE_URL=sqlite:///./data/agent.db)
├── chroma/              # ChromaDB vector database (CHROMA_PERSIST_DIR=./data/chroma)
└── .gitkeep
```

## 01_archive/ Structure

### 01_archive/docs/ - Documentation Archive by Category
| Subdirectory | What Goes Here | Examples |
|--------------|----------------|----------|
| `docs/implementation/` | **Implementation & security docs** | Security audits, hardening plans, phase reports, NIP17 deployment notes, Marmot_Old |
| `docs/design/` | Architecture & design documents | System design, API specs, data models, diagrams |
| `docs/analysis/` | Research & analysis | Gap analysis, performance studies, decision records, verification reports |
| `docs/requirements/` | Requirements documents | Use cases, user stories, feature specs |

### 01_archive/ Other Archive Directories
```
01_archive/
├── docs/                    # Documentation archive (with subfolders)
│   ├── analysis/            # Research & gap analysis docs
│   ├── design/              # Architecture & design docs
│   ├── implementation/      # Security audits, hardening plans, phase reports, old code
│   └── requirements/        # Requirements & use cases
├── deploy_package/          # Deployment packages (*.tar.gz archives)
├── old_scripts/             # Deprecated scripts
├── patches/                 # Patch files
├── tests/                   # Test files & test data
├── logs/                    # Archived logs
├── packages/                # General package files
├── temp_files/              # Temporary working files
└── venv/                    # Virtual environment backups
```

## Cleanup Commands

```bash
# Move implementation docs (current project work)
mv PHASE1_*.md 01_archive/docs/implementation/ 2>/dev/null
mv *_SUMMARY.md 01_archive/docs/implementation/ 2>/dev/null
mv *_REPORT.md 01_archive/docs/implementation/ 2>/dev/null
mv SecurityHardening.md 01_archive/docs/implementation/ 2>/dev/null
mv SECURITY_AUDIT.md 01_archive/docs/implementation/ 2>/dev/null

# Move design docs (architectural)
mv ARCHITECTURE_*.md 01_archive/docs/design/ 2>/dev/null
mv DESIGN_*.md 01_archive/docs/design/ 2>/dev/null

# Move analysis docs
mv GAP_ANALYSIS*.md 01_archive/docs/analysis/ 2>/dev/null
mv COMPARISON_*.md 01_archive/docs/analysis/ 2>/dev/null

# Move requirements docs
mv REQUIREMENTS*.md 01_archive/docs/requirements/ 2>/dev/null
mv USE_CASE*.md 01_archive/docs/requirements/ 2>/dev/null

# Move deployment packages
mv *.tar.gz 01_archive/deploy_package/ 2>/dev/null
mv deployment_*/ 01_archive/deploy_package/ 2>/dev/null

# Move old scripts
mv deploy_*.sh 01_archive/old_scripts/ 2>/dev/null
mv *_deploy.py 01_archive/old_scripts/ 2>/dev/null
mv fix_*.sh 01_archive/old_scripts/ 2>/dev/null
mv fix_*.py 01_archive/old_scripts/ 2>/dev/null

# Move tests
mv test_*.py 01_archive/tests/ 2>/dev/null
mv test_*.sh 01_archive/tests/ 2>/dev/null

# Remove empty/temp files
rm -f find cat ls create_deployment_package.sh 2>/dev/null

# Move temp files
mv test.db* 01_archive/temp_files/ 2>/dev/null
```

## Rules Summary

1. **Root = Active Code Only** — If not actively being used, move it
2. **Archive by Category** — Group docs by `implementation/design/analysis/requirements`
3. **Date-Based Cleanup** — Files older than 30 days in root should be archived
4. **No Duplicate Scripts** — Keep only current version in root
5. **docs/ is for active references only** — Never nest `01_archive/` inside `docs/`
6. **Documentation Flow**:
   - Active module reference docs → `docs/modules/` or `docs/architecture/`
   - Completed implementation work → `01_archive/docs/implementation/`
   - Design docs → `01_archive/docs/design/`
   - Analysis docs → `01_archive/docs/analysis/`
   - Requirements → `01_archive/docs/requirements/`
7. **Deployment Packages**:
   - Current → Keep in root briefly
   - After deployment → Move to `01_archive/deploy_package/`
8. **Data paths**:
   - SQLite DB → `data/agent.db` (`DATABASE_URL=sqlite:///./data/agent.db`)
   - ChromaDB → `data/chroma/` (`CHROMA_PERSIST_DIR=./data/chroma`, `CHROMA_PATH=./data/chroma`)

## File Classification Guidelines

When deciding where to archive a document:

| Document Type | Destination | Example |
|--------------|-------------|---------|
| Security hardening plan | `docs/implementation/` | `SecurityHardening.md` |
| Security audit report | `docs/implementation/` | `SECURITY_AUDIT.md` |
| Phase implementation report | `docs/implementation/` | `PHASE1_*_REPORT.md` |
| Protocol deployment notes | `docs/implementation/` | `NIP17_DEPLOYMENT_AND_TESTING.md` |
| Old implementation code | `docs/implementation/<Project>/` | `Marmot_Old/` |
| System architecture | `docs/design/` | `SystemDesign.md` |
| API specification | `docs/design/` | `API_Specification.md` |
| Gap analysis | `docs/analysis/` | `GAP_ANALYSIS.md` |
| Performance study | `docs/analysis/` | `PerformanceAnalysis.md` |
| Verification report | `docs/analysis/` | `verification_status_*.md` |
| Use cases | `docs/requirements/` | `UserStories.md` |

## Maintenance Schedule

- **Weekly**: Review root directory for files to archive
- **Monthly**: Clean up `01_archive/temp_files/` and old logs
- **Quarterly**: Review and purge deprecated scripts

---
**Last Updated:** March 2026
**Status:** Active
