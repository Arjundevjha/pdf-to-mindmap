# Graph Report - .  (2026-08-13)

## Corpus Check
- Corpus is ~14,171 words - fits in a single context window. You may not need a graph.

## Summary
- 193 nodes · 204 edges · 19 communities (15 shown, 4 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.71)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 16

## God Nodes (most connected - your core abstractions)
1. `compilerOptions` - 17 edges
2. `compilerOptions` - 16 edges
3. `generate_mindmap()` - 7 edges
4. `System Architecture Overview` - 6 edges
5. `get_supabase_headers()` - 5 edges
6. `scripts` - 5 edges
7. `useToast()` - 5 edges
8. `UploadZone()` - 4 edges
9. `frontend` - 4 edges
10. `backend` - 4 edges

## Surprising Connections (you probably didn't know these)
- `Frontend HTML Entrypoint` --implements--> `System Architecture Overview`  [INFERRED]
  frontend/index.html → ARCHITECTURE.md
- `Handoff Session Executive Summary` --references--> `System Architecture Overview`  [EXTRACTED]
  handoff.md → ARCHITECTURE.md
- `Backend Python Dependencies` --implements--> `Dual-Layer PDF Extraction`  [INFERRED]
  backend/requirements.txt → ARCHITECTURE.md
- `Favicon Brand Icon` --semantically_similar_to--> `Vite Brand Logo`  [INFERRED] [semantically similar]
  frontend/public/favicon.svg → frontend/src/assets/vite.svg
- `React TypeScript Vite Template Guide` --conceptually_related_to--> `Frontend HTML Entrypoint`  [INFERRED]
  frontend/README.md → frontend/index.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Study App Core System Architecture** — architecture_system_overview, handoff_session_summary, backend_requirements_dependencies [EXTRACTED 1.00]

## Communities (19 total, 4 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (31): autoprefixer, eslint, @eslint/js, eslint-plugin-react-hooks, eslint-plugin-react-refresh, devDependencies, autoprefixer, eslint (+23 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (20): App(), DocumentItem, parseBoldText(), parseSummaryText(), FlatCustomNode(), MindmapCanvas(), MindmapCanvasProps, MindmapNode (+12 more)

### Community 2 - "Community 2"
Cohesion: 0.12
Nodes (21): clean_json_string(), consolidate_summaries(), delete_document(), DocumentSavePayload, generate_mindmap(), get_documents(), get_supabase_headers(), get_system_prompt() (+13 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (22): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection, moduleResolution (+14 more)

### Community 4 - "Community 4"
Cohesion: 0.10
Nodes (20): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, moduleResolution, noEmit (+12 more)

### Community 5 - "Community 5"
Cohesion: 0.18
Nodes (11): dependencies, lucide-react, react, react-dom, @supabase/supabase-js, @xyflow/react, lucide-react, react (+3 more)

### Community 6 - "Community 6"
Cohesion: 0.20
Nodes (9): name, private, scripts, build, dev, lint, preview, type (+1 more)

### Community 7 - "Community 7"
Cohesion: 0.20
Nodes (9): entrypoint, root, routePrefix, experimentalServices, backend, frontend, framework, root (+1 more)

### Community 8 - "Community 8"
Cohesion: 0.22
Nodes (9): Distributed Multi-Model LLM Routing, Dual-Layer PDF Extraction, Dynamic Tree Layout Algorithm, Hybrid Storage & Sync Architecture, System Architecture Overview, Backend Python Dependencies, Frontend HTML Entrypoint, React TypeScript Vite Template Guide (+1 more)

### Community 9 - "Community 9"
Cohesion: 0.22
Nodes (8): author, description, keywords, license, name, scripts, start, version

### Community 10 - "Community 10"
Cohesion: 0.50
Nodes (4): Favicon Brand Icon, Hero Isometric Layer Graphic, React Framework Logo, Vite Brand Logo

## Knowledge Gaps
- **93 isolated node(s):** `build.sh script`, `name`, `private`, `version`, `type` (+88 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `devDependencies` connect `Community 0` to `Community 6`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Why does `dependencies` connect `Community 5` to `Community 6`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **What connects `build.sh script`, `name`, `private` to the rest of the system?**
  _93 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.06451612903225806 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.11965811965811966 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.08695652173913043 - nodes in this community are weakly interconnected._