import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { Background, BackgroundVariant, ReactFlow, getNodesBounds, useNodesState, useReactFlow, type Edge, type Node } from "@xyflow/react";
import type { WireData } from "../layout";
import { edgeTypes, nodeTypes } from "./nodes";

export interface BoardApi {
  fit: () => void;
  zoom: (dir: 1 | -1) => void;
  reveal: (nodeId: string) => void;
}

interface Props {
  nodes: Node[];
  edges: Edge<WireData>[];
  fitKey: string;
  leftInset?: number;
  rightInset: number;
  onDragStart: () => void;
  onDragStop: (nodes: Node[]) => void;
  onPaneClick: () => void;
  api: Ref<BoardApi>;
}

const TOP_INSET = 104;
const SIDE = 40;

export default function Board({ nodes: layoutNodes, edges, fitKey, leftInset = 0, rightInset, onDragStart, onDragStop, onPaneClick, api }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(layoutNodes);
  const rf = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  const lastFit = useRef<string | null>(null);

  useEffect(() => setNodes(layoutNodes), [layoutNodes, setNodes]);

  const fit = useCallback(
    (animate = true) => {
      const el = wrap.current;
      if (!el || !layoutNodes.length) return;
      const boxes = layoutNodes.filter((n) => !n.parentId);
      const b = getNodesBounds(boxes);
      const W = el.clientWidth - leftInset - rightInset - 2 * SIDE;
      const H = el.clientHeight - TOP_INSET - SIDE;
      let zoom = Math.min(1, W / Math.max(1, b.width), H / Math.max(1, b.height));
      let x: number;
      if (zoom < 0.72) {
        zoom = Math.max(0.72, Math.min(1, W / Math.max(1, b.width)));
        x = b.width * zoom <= W ? leftInset + SIDE + (W - b.width * zoom) / 2 - b.x * zoom : leftInset + SIDE - b.x * zoom;
      } else {
        x = leftInset + SIDE + (W - b.width * zoom) / 2 - b.x * zoom;
      }
      const y = TOP_INSET - b.y * zoom;
      rf.setViewport({ x, y, zoom }, animate ? { duration: 260 } : undefined);
    },
    [layoutNodes, rf, leftInset, rightInset],
  );

  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!ready || lastFit.current === fitKey) return;
    const first = lastFit.current === null;
    lastFit.current = fitKey;
    const id = requestAnimationFrame(() => fit(!first));
    return () => cancelAnimationFrame(id);
  }, [fitKey, fit, ready]);

  useImperativeHandle(
    api,
    () => ({
      fit: () => fit(true),
      zoom: (dir) => (dir > 0 ? rf.zoomIn({ duration: 160 }) : rf.zoomOut({ duration: 160 })),
      reveal: (nodeId) => {
        const n = rf.getInternalNode(nodeId);
        const el = wrap.current;
        if (!n || !el) return;
        const { x: nx, y: ny } = n.internals.positionAbsolute;
        const h = n.measured.height ?? n.height ?? 80;
        const w = n.measured.width ?? n.width ?? 300;
        const vp = rf.getViewport();
        const top = ny * vp.zoom + vp.y;
        const bottom = (ny + h) * vp.zoom + vp.y;
        const left = nx * vp.zoom + vp.x;
        const right = (nx + w) * vp.zoom + vp.x;
        let dy = 0;
        let dx = 0;
        if (top < TOP_INSET) dy = TOP_INSET - top;
        else if (bottom > el.clientHeight - SIDE) dy = el.clientHeight - SIDE - bottom;
        if (left < leftInset + SIDE) dx = leftInset + SIDE - left;
        else if (right > el.clientWidth - rightInset - SIDE) dx = el.clientWidth - rightInset - SIDE - right;
        if (dx || dy) rf.setViewport({ ...vp, x: vp.x + dx, y: vp.y + dy }, { duration: 200 });
      },
    }),
    [fit, rf, leftInset, rightInset],
  );

  return (
    <div className="board" ref={wrap}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onDragStart}
        onNodeDragStop={(_e, _n, dragged) => onDragStop(dragged)}
        onPaneClick={onPaneClick}
        onInit={() => setReady(true)}
        nodesConnectable={false}
        elementsSelectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        panOnScroll
        panOnDrag
        zoomOnDoubleClick={false}
        minZoom={0.25}
        maxZoom={1.6}
        nodeDragThreshold={4}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} className="board-dots" />
      </ReactFlow>
    </div>
  );
}
