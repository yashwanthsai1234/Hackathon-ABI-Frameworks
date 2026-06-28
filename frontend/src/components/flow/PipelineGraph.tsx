import { useMemo } from "react";
import { ReactFlow, Background, Handle, Position, type Node, type Edge, type NodeProps, BackgroundVariant } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useReducedMotion } from "framer-motion";
import type { RunManifest } from "../../types";

// Live pipeline: API → Fetch/Retry → Sniff → Extract → DB → Route → {accept/flag/reject}
// Pre-positioned nodes + CSS dash-flow edges (no physics → no recompute risk).

type StageNodeData = { label: string; sub: string; tone: "teal" | "amber" | "accept" | "flag" | "reject" | "slate" };

function StageNode({ data }: NodeProps) {
  const d = data as StageNodeData;
  const tones: Record<StageNodeData["tone"], string> = {
    teal: "bg-teal-600 text-white",
    amber: "bg-amber-500 text-white",
    accept: "bg-teal-600 text-white",
    flag: "bg-amber-500 text-white",
    reject: "bg-reject text-white",
    slate: "bg-ink text-white",
  };
  return (
    <div className={`rounded-lg ${tones[d.tone]} px-3.5 py-2.5 shadow-sm ring-1 ring-black/5`}>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-white/70" />
      <div className="text-[13px] font-semibold leading-tight">{d.label}</div>
      <div className="tabular mt-0.5 text-[11px] font-medium opacity-90">{d.sub}</div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-white/70" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

export function PipelineGraph({ manifest }: { manifest: RunManifest }) {
  const reduce = useReducedMotion();
  const s = (id: string) => manifest.stages.find((x) => x.id === id);

  const nodes: Node[] = useMemo(
    () => [
      { id: "api", type: "stage", position: { x: 0, y: 120 }, data: { label: "PCC API", sub: `${manifest.total_patients} patients`, tone: "slate" } },
      { id: "fetch", type: "stage", position: { x: 170, y: 120 }, data: { label: "Fetch · Retry", sub: `${s("S0")?.out ?? 0} ✓ · ${manifest.rate_limit_hits}× 429`, tone: "amber" } },
      { id: "sniff", type: "stage", position: { x: 350, y: 120 }, data: { label: "Sniff format", sub: `${s("S3")?.out ?? 0} typed`, tone: "teal" } },
      { id: "extract", type: "stage", position: { x: 525, y: 120 }, data: { label: "Extract", sub: `${s("S4")?.out ?? 0} fields`, tone: "teal" } },
      { id: "db", type: "stage", position: { x: 700, y: 120 }, data: { label: "SQLite", sub: `${s("S4")?.out ?? 0} rows`, tone: "slate" } },
      { id: "route", type: "stage", position: { x: 700, y: 30 }, data: { label: "Route", sub: `${s("S5")?.out ?? 0} routed`, tone: "teal" } },
      { id: "accept", type: "stage", position: { x: 900, y: 0 }, data: { label: "Auto-accept", sub: `${manifest.routes.auto_accept}`, tone: "accept" } },
      { id: "flag", type: "stage", position: { x: 900, y: 95 }, data: { label: "Flag review", sub: `${manifest.routes.flag_for_review}`, tone: "flag" } },
      { id: "reject", type: "stage", position: { x: 900, y: 190 }, data: { label: "Reject", sub: `${manifest.routes.reject}`, tone: "reject" } },
    ],
    [manifest],
  );

  const baseEdge = (id: string, source: string, target: string, color: string): Edge => ({
    id,
    source,
    target,
    animated: false,
    style: { stroke: color, strokeWidth: 2.5 },
    className: reduce ? "" : "edge-flow",
  });

  const edges: Edge[] = [
    baseEdge("e1", "api", "fetch", "#9b99a3"),
    baseEdge("e2", "fetch", "sniff", "#c2790b"),
    baseEdge("e3", "sniff", "extract", "#1f835f"),
    baseEdge("e4", "extract", "db", "#1f835f"),
    baseEdge("e5", "db", "route", "#137551"),
    baseEdge("e6", "route", "accept", "#137551"),
    baseEdge("e7", "route", "flag", "#b06a10"),
    baseEdge("e8", "route", "reject", "#b3341f"),
  ];

  return (
    <div className="h-[300px] w-full overflow-hidden rounded-xl" aria-hidden="true">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        preventScrolling={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(26,26,31,0.08)" />
      </ReactFlow>
    </div>
  );
}
