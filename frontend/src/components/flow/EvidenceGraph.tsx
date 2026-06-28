import { useMemo } from "react";
import { ReactFlow, Background, Handle, Position, type Node, type Edge, type NodeProps, BackgroundVariant, MarkerType } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { FileText, Stethoscope, ClipboardList, Crosshair } from "lucide-react";
import type { EvidenceGraph as EG, EvidenceNodeType } from "../../types";

// Source nodes (dx / note / assessment) → wound node.
// green edge = agree, red = conflict. Wound node centered on the right.

const ICONS: Record<EvidenceNodeType, typeof FileText> = {
  note: FileText,
  dx: Stethoscope,
  assessment: ClipboardList,
  wound: Crosshair,
};

type EvNodeData = { label: string; kind: EvidenceNodeType };

function EvidenceNodeView({ data }: NodeProps) {
  const d = data as EvNodeData;
  const Icon = ICONS[d.kind];
  const isWound = d.kind === "wound";
  return (
    <div
      className={
        isWound
          ? "max-w-[180px] rounded-xl bg-gradient-to-br from-teal-600 to-cyan-500 px-3 py-2.5 text-white shadow-lg ring-1 ring-white/40"
          : "max-w-[170px] rounded-xl border border-teal-600/15 bg-white/90 px-3 py-2 shadow-sm"
      }
    >
      <Handle type="target" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-teal-400" />
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-teal-400" />
      <div className="flex items-center gap-1.5">
        <Icon className={`h-3.5 w-3.5 ${isWound ? "text-white" : "text-teal-600"}`} aria-hidden="true" />
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${isWound ? "text-white/85" : "text-teal-700"}`}>
          {d.kind}
        </span>
      </div>
      <div className={`mt-0.5 text-[12px] font-medium leading-tight ${isWound ? "text-white" : "text-ink"}`}>{d.label}</div>
    </div>
  );
}

const nodeTypes = { ev: EvidenceNodeView };

export function EvidenceGraph({ graph }: { graph: EG }) {
  const { nodes, edges } = useMemo(() => {
    const sources = graph.nodes.filter((n) => n.type !== "wound");
    const gap = 84;
    const top = -(sources.length - 1) * gap * 0.5;
    const ns: Node[] = sources.map((n, i) => ({
      id: n.id,
      type: "ev",
      position: { x: 0, y: top + i * gap },
      data: { label: n.label, kind: n.type },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }));
    ns.push({
      id: "wound",
      type: "ev",
      position: { x: 300, y: -20 },
      data: { label: graph.nodes.find((n) => n.type === "wound")?.label ?? "Wound", kind: "wound" },
    });
    const es: Edge[] = graph.edges.map((e) => {
      const agree = e.relation === "agree";
      const color = agree ? "#0d9488" : "#e11d48";
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.relation,
        labelStyle: { fontSize: 10, fontWeight: 600, fill: color },
        labelBgStyle: { fill: "rgba(255,255,255,0.85)" },
        style: { stroke: color, strokeWidth: 2, strokeDasharray: agree ? undefined : "5 4" },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });
    return { nodes: ns, edges: es };
  }, [graph]);

  return (
    <div className="h-[220px] w-full overflow-hidden rounded-xl border border-teal-600/10 bg-teal-50/30">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        preventScrolling={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="rgba(13,148,136,0.1)" />
      </ReactFlow>
    </div>
  );
}
