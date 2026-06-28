import { ResponsiveContainer, Sankey, Tooltip, Layer, Rectangle } from "recharts";
import type { SankeyEdge } from "../../types";

// Payer → eligibility → route flow. Band width ∝ patient count.
export function PayerSankey({ edges }: { edges: SankeyEdge[] }) {
  // build indexed node/link structure recharts expects
  const names: string[] = [];
  const idx = (n: string) => {
    let i = names.indexOf(n);
    if (i === -1) {
      i = names.length;
      names.push(n);
    }
    return i;
  };
  const links = edges.map((e) => ({ source: idx(e.source), target: idx(e.target), value: e.value }));
  const nodes = names.map((name) => ({ name }));
  const data = { nodes, links };

  if (links.length === 0) {
    return <p className="py-8 text-center text-sm text-ink-soft">No payer flow to display.</p>;
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <Sankey
        data={data}
        nodePadding={26}
        nodeWidth={12}
        linkCurvature={0.5}
        iterations={64}
        link={{ stroke: "url(#sankeyGrad)", strokeOpacity: 0.34 }}
        node={<SankeyNode />}
        margin={{ left: 4, right: 90, top: 8, bottom: 8 }}
      >
        <defs>
          <linearGradient id="sankeyGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#137551" />
            <stop offset="100%" stopColor="#2f9772" />
          </linearGradient>
        </defs>
        <Tooltip
          contentStyle={{
            borderRadius: 8,
            border: "1px solid #e6e4dd",
            background: "#ffffff",
            fontSize: 12,
          }}
        />
      </Sankey>
    </ResponsiveContainer>
  );
}

interface SankeyNodeProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  index?: number;
  payload?: { name: string };
  containerWidth?: number;
}
function SankeyNode({ x = 0, y = 0, width = 0, height = 0, index = 0, payload, containerWidth = 0 }: SankeyNodeProps) {
  const isRight = x + width + 140 > containerWidth;
  return (
    <Layer key={`node-${index}`}>
      <Rectangle x={x} y={y} width={width} height={height} fill="#137551" fillOpacity={0.95} radius={2} />
      <text
        x={isRight ? x - 8 : x + width + 8}
        y={y + height / 2}
        textAnchor={isRight ? "end" : "start"}
        dominantBaseline="middle"
        fontSize={12}
        fontWeight={600}
        fill="#1a1a1f"
      >
        {payload?.name}
      </text>
    </Layer>
  );
}
