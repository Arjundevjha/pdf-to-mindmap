import { useMemo, useEffect } from 'react';
import { 
  ReactFlow, 
  Background, 
  Controls, 
  useNodesState, 
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  Handle,
  Position
} from '@xyflow/react';
import type { Node, Edge } from '@xyflow/react';
import dagre from '@dagrejs/dagre';
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

// Custom node component matching the clean, structured design of the application
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
      className={`relative bg-white border text-left flex flex-col select-none cursor-pointer transition-all duration-150 rounded-md shadow-xs
        ${cardWidth}
        ${hasGraph || hasImage ? 'p-3' : hasEquations ? 'p-3 min-h-[80px]' : 'px-4 py-3 min-h-[58px] justify-between'}
        ${isSelected ? 'border-blue-500 ring-2 ring-blue-500 shadow-sm' : 'border-slate-200 hover:border-slate-300'}
      `}
      onClick={data.onSelect}
    >
      {/* Target handle - transparent connection point (no visible dot) */}
      <Handle 
        type="target" 
        position={Position.Left} 
        style={{ 
          visibility: isRoot ? 'hidden' : 'visible',
          opacity: 0,
          width: '1px',
          height: '1px',
          left: '0px',
          background: 'transparent',
          border: 'none'
        }} 
      />
      
      {/* 1. Dynamic 2D Mathematical & Physics Curve Thumbnail */}
      {hasGraph && data.graph && (
        <div className="mb-2.5 w-full overflow-hidden rounded border border-slate-100 bg-slate-50">
          <FunctionPlotter graph={data.graph} isThumbnail={true} />
        </div>
      )}

      {/* 2. Educational Image Card (if no graph) */}
      {!hasGraph && hasImage && (
        <div className="mb-2.5 w-full bg-slate-100 rounded border border-slate-100 overflow-hidden flex flex-col items-center justify-center">
          <img 
            src={data.imageUrl} 
            alt={data.imageCaption || data.label}
            className="w-full max-h-[130px] object-cover rounded-t transition-opacity duration-200"
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
      <div className="flex items-center justify-between flex-1 gap-2">
        <div className="flex-1 py-0.5">
          <span className="text-slate-800 text-xs font-semibold leading-normal font-sans block select-none">
            {data.label}
          </span>
        </div>

        {data.hasChildren && (
          <button 
            onClick={(e) => { 
              e.stopPropagation(); 
              data.onToggleExpand(); 
            }}
            className="shrink-0 w-5 h-5 border border-slate-200 text-slate-600 flex items-center justify-center bg-slate-50 hover:bg-slate-100 text-[10px] font-bold select-none cursor-pointer focus:outline-none transition-colors rounded shadow-xs"
            aria-label={data.isExpanded ? 'Collapse node' : 'Expand node'}
          >
            {data.isExpanded ? '−' : '+'}
          </button>
        )}
      </div>

      {/* Source handle - transparent connection point (no visible dot) */}
      <Handle 
        type="source" 
        position={Position.Right} 
        style={{ 
          visibility: data.hasChildren && data.isExpanded ? 'visible' : 'hidden',
          opacity: 0,
          width: '1px',
          height: '1px',
          right: '0px',
          background: 'transparent',
          border: 'none'
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
  onSelectNode: (node: MindmapNode | null) => void;
}

// Synchronous Dagre layout computation for instant, synchronous placement
function runDagreLayoutSync(
  rawNodes: Array<{ id: string; width: number; height: number }>, 
  rawEdges: Array<{ id: string; source: string; target: string }>
): Record<string, { x: number; y: number }> {
  if (rawNodes.length === 0) return {};

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: 'LR',
    nodesep: 40,
    ranksep: 90,
    marginx: 50,
    marginy: 50,
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

function MindmapCanvasInner({ 
  mindmap, 
  expandedIds, 
  selectedNodeId, 
  onToggleNodeExpand, 
  onSelectNode 
}: MindmapCanvasProps) {
  
  const { setCenter } = useReactFlow();
  const nodeTypes = useMemo(() => ({ custom: FlatCustomNode }), []);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Compute graph hierarchy and positions synchronously
  const { flowNodes, flowEdges } = useMemo(() => {
    if (!mindmap) return { flowNodes: [], flowEdges: [] };

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
      nodeObj: MindmapNode;
    }> = [];
    const edgeList: Array<{ id: string; source: string; target: string }> = [];

    function traverse(node: MindmapNode) {
      // Default to expanded if expandedIds is empty or contains the node
      const isExpanded = expandedIds.size === 0 || expandedIds.has(node.id);
      const hasChildren = Boolean(node.children && node.children.length > 0);
      const hasImage = Boolean(node.imageUrl);
      const hasGraph = Boolean(node.graph && node.graph.fn);
      const hasEquations = Boolean(node.equations && node.equations.length > 0);

      // Compute dynamic bounding box for Dagre layout
      let width = 240;
      let height = 58;
      if (hasGraph || hasImage) {
        width = 270;
        height = 200;
      } else if (hasEquations) {
        width = 260;
        height = 84;
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
        nodeObj: node,
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

    // Compute layout positions synchronously
    const positions = runDagreLayoutSync(nodeList, edgeList);

    const fNodes: Node[] = nodeList.map((n) => {
      const pos = positions[n.id] || { x: 0, y: 0 };
      return {
        id: n.id,
        type: 'custom',
        position: pos,
        data: {
          id: n.id,
          label: n.label,
          summary: n.summary,
          equations: n.equations,
          graph: n.graph,
          imageUrl: n.imageUrl,
          imageCaption: n.imageCaption,
          hasChildren: n.hasChildren,
          isExpanded: n.isExpanded,
          isSelected: n.id === selectedNodeId,
          onSelect: () => onSelectNode(n.nodeObj),
          onToggleExpand: () => onToggleNodeExpand(n.id),
        },
      };
    });

    const fEdges: Edge[] = edgeList.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
    }));

    return { flowNodes: fNodes, flowEdges: fEdges };
  }, [mindmap, expandedIds, selectedNodeId, onSelectNode, onToggleNodeExpand]);

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  // Whenever the document/mindmap changes, orient camera smoothly focused onto the first (root) node
  useEffect(() => {
    if (!mindmap || flowNodes.length === 0) return;

    const rootNode = flowNodes.find(n => n.id === mindmap.id) || flowNodes[0];
    if (rootNode && rootNode.position) {
      const timer = setTimeout(() => {
        // Center camera smoothly onto the initial node
        setCenter(rootNode.position.x + 130, rootNode.position.y + 30, { zoom: 0.95, duration: 400 });
      }, 60);
      return () => clearTimeout(timer);
    }
  }, [mindmap?.id, flowNodes.length, setCenter]);

  if (!mindmap) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-slate-50 text-slate-400 select-none p-6">
        <div className="w-14 h-14 mb-4 rounded-full bg-white border border-slate-200 flex items-center justify-center text-slate-400 shadow-xs">
          <svg className="w-6 h-6 stroke-[1.5]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
        <h3 className="text-sm font-semibold text-slate-700">No Mindmap Selected</h3>
        <p className="text-xs text-slate-400 mt-1 max-w-sm text-center leading-relaxed">
          Upload a PDF or select an existing document from the left sidebar to explore the interactive visual mindmap.
        </p>
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
        onPaneClick={() => onSelectNode(null)}
        nodeTypes={nodeTypes}
        minZoom={0.2}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#e2e8f0" gap={24} size={1} />
        <Controls showInteractive={false} className="border-slate-200" />
      </ReactFlow>
    </div>
  );
}

export function MindmapCanvas(props: MindmapCanvasProps) {
  return (
    <ReactFlowProvider>
      <MindmapCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
