import { useMemo, useEffect } from 'react';
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
import '@xyflow/react/dist/style.css';

// Define hierarchical node interface from backend
export interface MindmapNode {
  id: string;
  label: string;
  summary: string;
  children: MindmapNode[];
}

// Define the custom node component
function FlatCustomNode({ data }: { data: any }) {
  const isSelected = data.isSelected;
  const isRoot = data.id === 'root';

  return (
    <div 
      className={`relative px-4 py-3 bg-white border text-left min-h-[64px] w-[220px] flex items-center justify-between select-none cursor-pointer
        ${isSelected ? 'border-blue-500 ring-[1px] ring-blue-500' : 'border-slate-200 hover:border-slate-300'}
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
          className="absolute right-2 top-1/2 -translate-y-1/2 w-5 h-5 border border-slate-200 text-slate-500 flex items-center justify-center bg-slate-50 hover:bg-slate-100 text-[10px] font-bold select-none cursor-pointer focus:outline-none"
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

  // Calculate layout of visible nodes recursively
  // Horizontal hierarchy: parent x=0, children x=300, grandchildren x=600...
  const calculateLayout = useMemo(() => {
    if (!mindmap) return { nodes: [], edges: [] };

    const resultNodes: Node[] = [];
    const resultEdges: Edge[] = [];

    // Helper function to traverse and position nodes
    function layoutTree(
      node: MindmapNode,
      x: number,
      yStart: number,
      depth: number
    ): { height: number; midpointY: number } {
      const isExpanded = expandedIds.has(node.id);
      const hasChildren = node.children && node.children.length > 0;
      
      // Node Height standard unit is 110px vertical space band per leaf node
      if (!isExpanded || !hasChildren) {
        const y = yStart + 35; // centered in its 70px slot
        resultNodes.push({
          id: node.id,
          type: 'custom',
          position: { x, y },
          data: {
            id: node.id,
            label: node.label,
            summary: node.summary,
            hasChildren: hasChildren,
            isExpanded: false,
            isSelected: node.id === selectedNodeId,
            onSelect: () => onSelectNode(node.id, node.label, node.summary),
            onToggleExpand: () => onToggleNodeExpand(node.id),
          },
        });
        return { height: 80, midpointY: yStart + 32 };
      }

      // If expanded, recursively calculate positions for all visible children
      let currentY = yStart;
      const childMidpoints: number[] = [];

      for (const child of node.children) {
        const { height: childHeight, midpointY: childMidpoint } = layoutTree(
          child,
          x + 280, // Horizontal offset
          currentY,
          depth + 1
        );

        resultEdges.push({
          id: `edge-${node.id}-${child.id}`,
          source: node.id,
          target: child.id,
          type: 'smoothstep',
          style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
        });

        childMidpoints.push(childMidpoint);
        currentY += childHeight;
      }

      const totalHeight = currentY - yStart;
      const parentYMidpoint = childMidpoints.reduce((a, b) => a + b, 0) / childMidpoints.length;
      
      // Node is 64px high, midpoint is parentYMidpoint. Top Y is parentYMidpoint - 32
      const parentNodeY = parentYMidpoint - 32;

      resultNodes.push({
        id: node.id,
        type: 'custom',
        position: { x, y: parentNodeY },
        data: {
          id: node.id,
          label: node.label,
          summary: node.summary,
          hasChildren: true,
          isExpanded: true,
          isSelected: node.id === selectedNodeId,
          onSelect: () => onSelectNode(node.id, node.label, node.summary),
          onToggleExpand: () => onToggleNodeExpand(node.id),
        },
      });

      return { height: totalHeight, midpointY: parentYMidpoint };
    }

    layoutTree(mindmap, 40, 40, 0);
    return { nodes: resultNodes, edges: resultEdges };

  }, [mindmap, expandedIds, selectedNodeId, onToggleNodeExpand, onSelectNode]);

  // Synchronize computed layout with React Flow state
  useEffect(() => {
    if (calculateLayout.nodes.length > 0) {
      setNodes(calculateLayout.nodes);
      setEdges(calculateLayout.edges);
    } else {
      setNodes([]);
      setEdges([]);
    }
  }, [calculateLayout, setNodes, setEdges]);

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
