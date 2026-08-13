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
import '@xyflow/react/dist/style.css';

// Define hierarchical node interface from backend
export interface MindmapNode {
  id: string;
  label: string;
  summary: string;
  imageUrl?: string;
  imageCaption?: string;
  imageAspectRatio?: number;
  children: MindmapNode[];
}

interface FlatCustomNodeData {
  id: string;
  label: string;
  summary: string;
  imageUrl?: string;
  imageCaption?: string;
  hasChildren: boolean;
  isExpanded: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onToggleExpand: () => void;
}

// Define the custom node component with adaptive image frame
function FlatCustomNode({ data }: { data: FlatCustomNodeData }) {
  const isSelected = data.isSelected;
  const isRoot = data.id === 'root';
  const hasImage = Boolean(data.imageUrl);

  return (
    <div 
      className={`relative bg-white border text-left flex flex-col select-none cursor-pointer transition-all duration-150 rounded-md
        ${hasImage ? 'w-[270px] p-3' : 'w-[240px] px-4 py-3 min-h-[64px] justify-between'}
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
      
      {/* Wikimedia Educational Image Card */}
      {hasImage && (
        <div className="mb-2.5 w-full bg-slate-100 rounded border border-slate-100 overflow-hidden flex flex-col items-center justify-center">
          <img 
            src={data.imageUrl} 
            alt={data.imageCaption || data.label}
            className="w-full max-h-[140px] object-cover rounded-t transition-opacity duration-200"
            loading="lazy"
            onError={(e) => {
              // Hide image container on broken URL load
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
  onSelectNode: (nodeId: string, label: string, summary: string) => void;
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
    nodesep: 30,
    ranksep: 80,
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
  const { rawNodes, rawEdges } = useMemo(() => {
    if (!mindmap) return { rawNodes: [], rawEdges: [] };

    const nodeList: Array<{
      id: string;
      label: string;
      summary: string;
      imageUrl?: string;
      imageCaption?: string;
      hasChildren: boolean;
      isExpanded: boolean;
      width: number;
      height: number;
    }> = [];
    const edgeList: Array<{ id: string; source: string; target: string }> = [];

    function traverse(node: MindmapNode) {
      const isExpanded = expandedIds.has(node.id);
      const hasChildren = Boolean(node.children && node.children.length > 0);
      const hasImage = Boolean(node.imageUrl);

      const width = hasImage ? 270 : 240;
      const height = hasImage ? 210 : 64;

      nodeList.push({
        id: node.id,
        label: node.label,
        summary: node.summary,
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

    return { rawNodes: nodeList, rawEdges: edgeList };
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
      return {
        id: node.id,
        type: 'custom',
        position: pos,
        data: {
          id: node.id,
          label: node.label,
          summary: node.summary,
          imageUrl: node.imageUrl,
          imageCaption: node.imageCaption,
          hasChildren: node.hasChildren,
          isExpanded: node.isExpanded,
          isSelected: node.id === selectedNodeId,
          onSelect: () => onSelectNode(node.id, node.label, node.summary),
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
  }, [rawNodes, rawEdges, positions, selectedNodeId, onSelectNode, onToggleNodeExpand, setNodes, setEdges]);

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
