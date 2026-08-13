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
  children: MindmapNode[];
}

interface FlatCustomNodeData {
  id: string;
  label: string;
  summary: string;
  hasChildren: boolean;
  isExpanded: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onToggleExpand: () => void;
}

// Define the custom node component
function FlatCustomNode({ data }: { data: FlatCustomNodeData }) {
  const isSelected = data.isSelected;
  const isRoot = data.id === 'root';

  return (
    <div 
      className={`relative px-4 py-3 bg-white border text-left min-h-[64px] w-[220px] flex items-center justify-between select-none cursor-pointer transition-colors duration-150
        ${isSelected ? 'border-blue-500 ring-[1px] ring-blue-500 shadow-sm' : 'border-slate-200 hover:border-slate-300'}
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
          className="absolute right-2 top-1/2 -translate-y-1/2 w-5 h-5 border border-slate-200 text-slate-500 flex items-center justify-center bg-slate-50 hover:bg-slate-100 text-[10px] font-bold select-none cursor-pointer focus:outline-none transition-colors"
          aria-label={data.isExpanded ? 'Collapse node' : 'Expand node'}
        >
          {data.isExpanded ? '−' : '+'}
        </button>
      )}

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
  rawNodes: Array<{ id: string }>, 
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

  rawNodes.forEach((n) => g.setNode(n.id, { width: 220, height: 64 }));
  rawEdges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  const positions: Record<string, { x: number; y: number }> = {};
  rawNodes.forEach((n) => {
    const dn = g.node(n.id);
    if (dn) {
      positions[n.id] = { x: dn.x - 110, y: dn.y - 32 };
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
      hasChildren: boolean;
      isExpanded: boolean;
    }> = [];
    const edgeList: Array<{ id: string; source: string; target: string }> = [];

    function traverse(node: MindmapNode) {
      const isExpanded = expandedIds.has(node.id);
      const hasChildren = Boolean(node.children && node.children.length > 0);

      nodeList.push({
        id: node.id,
        label: node.label,
        summary: node.summary,
        hasChildren,
        isExpanded,
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
      setPositions({});
      return;
    }

    if (workerRef.current) {
      const workerPayload: LayoutWorkerInput = {
        nodes: rawNodes.map((n) => ({ id: n.id, width: 220, height: 64 })),
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
