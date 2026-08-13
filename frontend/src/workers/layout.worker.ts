import dagre from '@dagrejs/dagre';

export interface LayoutNodeInput {
  id: string;
  width?: number;
  height?: number;
}

export interface LayoutEdgeInput {
  id: string;
  source: string;
  target: string;
}

export interface LayoutWorkerInput {
  nodes: LayoutNodeInput[];
  edges: LayoutEdgeInput[];
  direction?: 'LR' | 'TB' | 'BT' | 'RL';
}

export interface LayoutWorkerOutput {
  positions: Record<string, { x: number; y: number }>;
}

self.onmessage = (event: MessageEvent<LayoutWorkerInput>) => {
  const { nodes, edges, direction = 'LR' } = event.data;

  if (!nodes || nodes.length === 0) {
    self.postMessage({ positions: {} });
    return;
  }

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: direction,
    nodesep: 30,
    ranksep: 80,
    marginx: 40,
    marginy: 40,
    align: 'DL',
  });
  g.setDefaultEdgeLabel(() => ({}));

  const nodeWidth = 220;
  const nodeHeight = 64;

  nodes.forEach((node) => {
    const width = node.width || nodeWidth;
    const height = node.height || nodeHeight;
    g.setNode(node.id, { width, height });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const positions: Record<string, { x: number; y: number }> = {};

  nodes.forEach((node) => {
    const dagreNode = g.node(node.id);
    if (dagreNode) {
      const width = node.width || nodeWidth;
      const height = node.height || nodeHeight;
      // Dagre positions are center-based, React Flow needs top-left
      positions[node.id] = {
        x: dagreNode.x - width / 2,
        y: dagreNode.y - height / 2,
      };
    }
  });

  self.postMessage({ positions });
};
