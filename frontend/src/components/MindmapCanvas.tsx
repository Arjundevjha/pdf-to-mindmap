import { useMemo, useEffect, useState, useRef } from 'react';
import { 
  ReactFlow, 
  Background, 
  Controls, 
  useNodesState, 
  useEdgesState,
  Handle,
  Position
} from '@xyflow/react';
import type { Node, Edge } from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import type { LayoutWorkerInput, LayoutWorkerOutput } from '../workers/layout.worker';
import { FunctionPlotter, type GraphData } from './FunctionPlotter';
import { KaTeXEquation } from './MathRenderer';
import '@xyflow/react/dist/style.css';

// Define hierarchical node interface from backend
export interface MindmapNode {
  id: string;
  label: string;
  summary: string;
  equations?: string[];
  graph?: GraphData;
  imageUrl?: string;
  imageCaption?: string;
  imageAspectRatio?: number;
  children: MindmapNode[];
}

interface FlatCustomNodeData {
  id: string;
  label: string;
  summary: string;
  equations?: string[];
  graph?: GraphData;
  imageUrl?: string;
  imageCaption?: string;
  hasChildren: boolean;
  isExpanded: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onToggleExpand: () => void;
}

// Custom node component supporting LaTeX previews, 2D Function plots, and Wikimedia images
function FlatCustomNode({ data }: { data: FlatCustomNodeData }) {
  const isSelected = data.isSelected;
  const isRoot = data.id === 'root';
  const hasImage = Boolean(data.imageUrl);
  const hasGraph = Boolean(data.graph && data.graph.fn);
  const hasEquations = Boolean(data.equations && data.equations.length > 0);

  // Dynamic card layout dimensions
  let cardWidth = 'w-[240px]';
  if (hasGraph || hasImage) {
    cardWidth = 'w-[270px]';
  } else if (hasEquations) {
    cardWidth = 'w-[260px]';
  }

  return (
    <div 
      className={`relative bg-white border text-left flex flex-col select-none cursor-pointer transition-all duration-150 rounded-md
        ${cardWidth}
        ${hasGraph || hasImage ? 'p-3' : hasEquations ? 'p-3 min-h-[84px]' : 'px-4 py-3 min-h-[64px] justify-between'}
        ${isSelected ? 'border-blue-500 ring-[1px] ring-blue-500 shadow-md' : 'border-slate-200 hover:border-slate-300 shadow-sm'}
      `}
      onClick={data.onSelect}
    >
      {/* Target handle (incoming link from parent) */}
      <Handle 
        type="target" 
        position={Position.Left} 
        style={{ 
          visibility: isRoot ? 'hidden' : 'visible',
          background: '#cbd5e1',
          width: '6px',
          height: '6px',
          left: '-3.5px'
        }} 
      />
      
      {/* 1. Dynamic 2D Mathematical & Physics Curve Thumbnail */}
      {hasGraph && data.graph && (
        <div className="mb-2.5 w-full overflow-hidden">
          <FunctionPlotter graph={data.graph} isThumbnail={true} />
        </div>
      )}

      {/* 2. Wikimedia Educational Image Card (if no graph) */}
      {!hasGraph && hasImage && (
        <div className="mb-2.5 w-full bg-slate-100 rounded border border-slate-100 overflow-hidden flex flex-col items-center justify-center">
          <img 
            src={data.imageUrl} 
            alt={data.imageCaption || data.label}
            className="w-full max-h-[140px] object-cover rounded-t transition-opacity duration-200"
            loading="lazy"
            onError={(e) => {
              (e.target as HTMLElement).parentElement?.classList.add('hidden');
            }}
          />
          {data.imageCaption && (
            <div className="px-2 py-1 w-full bg-slate-50 border-t border-slate-100">
              <span className="text-[10px] text-slate-500 font-sans block truncate italic">
                {data.imageCaption}
              </span>
            </div>
          )}
        </div>
      )}

      {/* 3. Primary Equation Preview Badge */}
      {hasEquations && data.equations && data.equations[0] && (
        <div className="mb-2 px-2 py-1 bg-slate-50 border border-slate-100 rounded flex items-center justify-center overflow-x-auto">
          <KaTeXEquation formula={data.equations[0]} displayMode={false} className="text-xs text-slate-800" />
        </div>
      )}

      {/* Node Title & Expand Button */}
      <div className="flex items-center justify-between flex-1">
        <div className="flex-1 pr-6 py-0.5">
          <span className="text-slate-700 text-xs font-semibold leading-normal font-sans block truncate-2-lines select-none">
            {data.label}
          </span>
        </div>

        {data.hasChildren && (
          <button 
            onClick={(e) => { 
              e.stopPropagation(); 
              data.onToggleExpand(); 
            }}
            className="absolute right-2.5 top-3 w-5 h-5 border border-slate-200 text-slate-500 flex items-center justify-center bg-slate-50 hover:bg-slate-100 text-[10px] font-bold select-none cursor-pointer focus:outline-none transition-colors rounded"
            aria-label={data.isExpanded ? 'Collapse node' : 'Expand node'}
          >
            {data.isExpanded ? '−' : '+'}
          </button>
        )}
      </div>

      {/* Source handle (outgoing links to children) */}
      <Handle 
        type="source" 
        position={Position.Right} 
        style={{ 
          visibility: data.hasChildren && data.isExpanded ? 'visible' : 'hidden',
          background: '#cbd5e1',
          width: '6px',
          height: '6px',
          right: '-3.5px'
        }} 
      />
    </div>
  );
}

interface MindmapCanvasProps {
  mindmap: MindmapNode | null;
  expandedIds: Set<string>;
  selectedNodeId: string | null;
  onToggleNodeExpand: (nodeId: string) => void;
  onSelectNode: (node: MindmapNode) => void;
}

// Synchronous Dagre fallback if Web Worker is unavailable
function runDagreLayoutSync(
  rawNodes: Array<{ id: string; width: number; height: number }>, 
  rawEdges: Array<{ id: string; source: string; target: string }>
): Record<string, { x: number; y: number }> {
  if (rawNodes.length === 0) return {};

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: 'LR',
    nodesep: 35,
    ranksep: 90,
    marginx: 40,
    marginy: 40,
    align: 'DL',
  });
  g.setDefaultEdgeLabel(() => ({}));

  rawNodes.forEach((n) => g.setNode(n.id, { width: n.width, height: n.height }));
  rawEdges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  const positions: Record<string, { x: number; y: number }> = {};
  rawNodes.forEach((n) => {
    const dn = g.node(n.id);
    if (dn) {
      positions[n.id] = { x: dn.x - n.width / 2, y: dn.y - n.height / 2 };
    }
  });

  return positions;
}

export function MindmapCanvas({ 
  mindmap, 
  expandedIds, 
  selectedNodeId, 
  onToggleNodeExpand, 
  onSelectNode 
}: MindmapCanvasProps) {
  
  const nodeTypes = useMemo(() => ({ custom: FlatCustomNode }), []);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const workerRef = useRef<Worker | null>(null);

  // Initialize layout Web Worker
  useEffect(() => {
    try {
      workerRef.current = new Worker(
        new URL('../workers/layout.worker.ts', import.meta.url),
        { type: 'module' }
      );
    } catch (err) {
      console.warn('Web Worker initialization failed, using synchronous fallback:', err);
      workerRef.current = null;
    }

    return () => {
      workerRef.current?.terminate();
      workerRef.current = null;
    };
  }, []);

  // Extract visible hierarchy nodes and edges based ONLY on mindmap and expandedIds
  const { rawNodes, rawEdges, nodeMap } = useMemo(() => {
    if (!mindmap) return { rawNodes: [], rawEdges: [], nodeMap: new Map<string, MindmapNode>() };

    const nodeList: Array<{
      id: string;
      label: string;
      summary: string;
      equations?: string[];
      graph?: GraphData;
      imageUrl?: string;
      imageCaption?: string;
      hasChildren: boolean;
      isExpanded: boolean;
      width: number;
      height: number;
    }> = [];
    const edgeList: Array<{ id: string; source: string; target: string }> = [];
    const nMap = new Map<string, MindmapNode>();

    function traverse(node: MindmapNode) {
      nMap.set(node.id, node);
      const isExpanded = expandedIds.has(node.id);
      const hasChildren = Boolean(node.children && node.children.length > 0);
      const hasImage = Boolean(node.imageUrl);
      const hasGraph = Boolean(node.graph && node.graph.fn);
      const hasEquations = Boolean(node.equations && node.equations.length > 0);

      // Compute dynamic bounding box for Dagre layout engine
      let width = 240;
      let height = 64;
      if (hasGraph || hasImage) {
        width = 270;
        height = 210;
      } else if (hasEquations) {
        width = 260;
        height = 96;
      }

      nodeList.push({
        id: node.id,
        label: node.label,
        summary: node.summary,
        equations: node.equations,
        graph: node.graph,
        imageUrl: node.imageUrl,
        imageCaption: node.imageCaption,
        hasChildren,
        isExpanded,
        width,
        height,
      });

      if (isExpanded && hasChildren) {
        for (const child of node.children) {
          edgeList.push({
            id: `edge-${node.id}-${child.id}`,
            source: node.id,
            target: child.id,
          });
          traverse(child);
        }
      }
    }

    traverse(mindmap);

    return { rawNodes: nodeList, rawEdges: edgeList, nodeMap: nMap };
  }, [mindmap, expandedIds]);

  // Dispatch layout calculation to Web Worker or fallback
  useEffect(() => {
    if (rawNodes.length === 0) {
      const animFrame = requestAnimationFrame(() => setPositions({}));
      return () => cancelAnimationFrame(animFrame);
    }

    if (workerRef.current) {
      const workerPayload: LayoutWorkerInput = {
        nodes: rawNodes.map((n) => ({ id: n.id, width: n.width, height: n.height })),
        edges: rawEdges,
        direction: 'LR',
      };

      workerRef.current.onmessage = (event: MessageEvent<LayoutWorkerOutput>) => {
        setPositions(event.data.positions);
      };

      workerRef.current.postMessage(workerPayload);
    } else {
      const animFrame = requestAnimationFrame(() => {
        const syncPositions = runDagreLayoutSync(rawNodes, rawEdges);
        setPositions(syncPositions);
      });
      return () => cancelAnimationFrame(animFrame);
    }
  }, [rawNodes, rawEdges]);

  // Construct React Flow nodes and edges by combining layout positions with selection state
  useEffect(() => {
    if (rawNodes.length === 0) {
      setNodes([]);
      setEdges([]);
      return;
    }

    const flowNodes: Node[] = rawNodes.map((node) => {
      const pos = positions[node.id] || { x: 0, y: 0 };
      const originalNode = nodeMap.get(node.id) || {
        id: node.id,
        label: node.label,
        summary: node.summary,
        equations: node.equations,
        graph: node.graph,
        imageUrl: node.imageUrl,
        imageCaption: node.imageCaption,
        children: [],
      };

      return {
        id: node.id,
        type: 'custom',
        position: pos,
        data: {
          id: node.id,
          label: node.label,
          summary: node.summary,
          equations: node.equations,
          graph: node.graph,
          imageUrl: node.imageUrl,
          imageCaption: node.imageCaption,
          hasChildren: node.hasChildren,
          isExpanded: node.isExpanded,
          isSelected: node.id === selectedNodeId,
          onSelect: () => onSelectNode(originalNode),
          onToggleExpand: () => onToggleNodeExpand(node.id),
        },
      };
    });

    const flowEdges: Edge[] = rawEdges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
    }));

    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [rawNodes, rawEdges, positions, selectedNodeId, nodeMap, onSelectNode, onToggleNodeExpand, setNodes, setEdges]);

  if (!mindmap) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-slate-50 text-slate-400 select-none">
        <p className="text-sm font-medium">No active mindmap</p>
        <p className="text-xs mt-1">Upload a PDF from the sidebar to generate one.</p>
      </div>
    );
  }

  return (
    <div className="w-full h-full bg-slate-50 relative">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#cbd5e1" gap={20} size={1} />
        <Controls showInteractive={false} className="border-slate-200" />
      </ReactFlow>
    </div>
  );
}
