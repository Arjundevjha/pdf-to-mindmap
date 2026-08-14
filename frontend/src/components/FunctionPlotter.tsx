import React, { useMemo, useState, useRef } from 'react';

export interface GraphData {
  fn: string;
  domain?: [number, number];
  xLabel?: string;
  yLabel?: string;
  title?: string;
}

interface FunctionPlotterProps {
  graph: GraphData;
  isThumbnail?: boolean;
  className?: string;
}

/**
 * Parses and evaluates mathematical string expressions in terms of variable 'x' safely.
 */
function createEvaluator(rawExpr: string): (x: number) => number | null {
  try {
    // Normalize equation syntax (e.g., f(x) = x^2, y = sin(x))
    let expr = rawExpr
      .replace(/^[fgyht]\s*\([a-z]\)\s*=\s*/i, '')
      .replace(/^[yf]\s*=\s*/i, '')
      .trim();

    // Replace power syntax: x^2 -> (x)**2, a^b -> (a)**b
    // Convert mathematical constants & functions
    expr = expr
      .replace(/\bpi\b/gi, 'Math.PI')
      .replace(/\be\b/g, 'Math.E')
      .replace(/\bg\b/g, '9.81')
      .replace(/\bsin\b/gi, 'Math.sin')
      .replace(/\bcos\b/gi, 'Math.cos')
      .replace(/\btan\b/gi, 'Math.tan')
      .replace(/\bsqrt\b/gi, 'Math.sqrt')
      .replace(/\babs\b/gi, 'Math.abs')
      .replace(/\bln\b/gi, 'Math.log')
      .replace(/\blog\b/gi, 'Math.log10')
      .replace(/\bexp\b/gi, 'Math.exp')
      .replace(/(\d+)\s*\(/g, '$1 * (')
      .replace(/(\d+)\s*([a-zA-Z])/g, '$1 * $2')
      .replace(/([a-zA-Z])\s*(\()/g, '$1$2')
      .replace(/\^/g, '**');

    // Return a safe evaluation function
    const fn = new Function('x', 'Math', `

      try {
        const val = ${expr};
        if (typeof val !== 'number' || isNaN(val) || !isFinite(val)) return null;
        return val;
      } catch (e) {
        return null;
      }
    `);

    return (x: number) => {
      try {
        return fn(x, Math);
      } catch {
        return null;
      }
    };
  } catch {
    return () => null;
  }
}

export function FunctionPlotter({ graph, isThumbnail = false, className = '' }: FunctionPlotterProps) {
  const containerRef = useRef<SVGSVGElement>(null);
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number; pixelX: number; pixelY: number } | null>(null);

  const { fn: rawFn, domain = [-5, 5], xLabel = 'x', yLabel = 'y', title } = graph;
  const [minX, maxX] = (domain && domain.length === 2 && domain[0] < domain[1]) ? domain : [-5, 5];

  // Evaluate curve data points
  const { pathSegments, minY, maxY, samplePoints } = useMemo(() => {
    const evaluator = createEvaluator(rawFn);
    const numSamples = isThumbnail ? 80 : 200;
    const step = (maxX - minX) / numSamples;

    const points: Array<{ x: number; y: number }> = [];
    let computedMinY = Infinity;
    let computedMaxY = -Infinity;

    for (let i = 0; i <= numSamples; i++) {
      const x = minX + i * step;
      const y = evaluator(x);
      if (y !== null && typeof y === 'number' && !isNaN(y) && isFinite(y)) {
        // Clamp outliers to prevent runaway graph scaling
        if (Math.abs(y) < 1000) {
          points.push({ x, y });
          if (y < computedMinY) computedMinY = y;
          if (y > computedMaxY) computedMaxY = y;
        }
      }
    }

    if (points.length === 0 || computedMinY === Infinity) {
      computedMinY = -5;
      computedMaxY = 5;
    }

    // Add padding to Y bounds
    const ySpan = Math.max(computedMaxY - computedMinY, 1.0);
    const finalMinY = computedMinY - ySpan * 0.15;
    const finalMaxY = computedMaxY + ySpan * 0.15;

    // Build SVG path segments (splitting on asymptotes / discontinuities)
    const segments: string[] = [];
    let currentSegment = '';
    const width = isThumbnail ? 240 : 380;
    const height = isThumbnail ? 120 : 220;
    const padding = isThumbnail ? 12 : 28;

    const scaleX = (xVal: number) => padding + ((xVal - minX) / (maxX - minX)) * (width - 2 * padding);
    const scaleY = (yVal: number) => height - padding - ((yVal - finalMinY) / (finalMaxY - finalMinY)) * (height - 2 * padding);

    for (let i = 0; i < points.length; i++) {
      const pt = points[i];
      const prev = points[i - 1];
      const px = scaleX(pt.x);
      const py = scaleY(pt.y);

      // Detect asymptote jump
      const isJump = prev && Math.abs(pt.y - prev.y) > ySpan * 0.8;

      if (!currentSegment || isJump) {
        if (currentSegment) segments.push(currentSegment);
        currentSegment = `M ${px.toFixed(1)} ${py.toFixed(1)}`;
      } else {
        currentSegment += ` L ${px.toFixed(1)} ${py.toFixed(1)}`;
      }
    }

    if (currentSegment) segments.push(currentSegment);

    return {
      pathSegments: segments,
      minY: finalMinY,
      maxY: finalMaxY,
      samplePoints: points,
    };
  }, [rawFn, minX, maxX, isThumbnail]);

  const width = isThumbnail ? 240 : 380;
  const height = isThumbnail ? 120 : 220;
  const padding = isThumbnail ? 12 : 28;

  const scaleX = (xVal: number) => padding + ((xVal - minX) / (maxX - minX)) * (width - 2 * padding);
  const scaleY = (yVal: number) => height - padding - ((yVal - minY) / (maxY - minY)) * (height - 2 * padding);

  // Axes coordinates
  const originX = Math.max(padding, Math.min(width - padding, scaleX(0)));
  const originY = Math.max(padding, Math.min(height - padding, scaleY(0)));

  // Interactive mouse move handler for tooltip readout
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (isThumbnail || !containerRef.current || samplePoints.length === 0) return;
    const rect = containerRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, (mouseX - padding) / (width - 2 * padding)));
    const targetX = minX + ratio * (maxX - minX);

    // Find closest point
    let closest = samplePoints[0];
    let minDiff = Infinity;
    for (const pt of samplePoints) {
      const diff = Math.abs(pt.x - targetX);
      if (diff < minDiff) {
        minDiff = diff;
        closest = pt;
      }
    }

    if (closest) {
      setHoverPos({
        x: closest.x,
        y: closest.y,
        pixelX: scaleX(closest.x),
        pixelY: scaleY(closest.y),
      });
    }
  };

  const handleMouseLeave = () => {
    setHoverPos(null);
  };

  return (
    <div className={`bg-slate-900 border border-slate-800 rounded-md overflow-hidden flex flex-col items-center select-none ${className}`}>
      {/* Top Header bar */}
      <div className="w-full px-2.5 py-1 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-1.5 truncate">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
          <span className="text-[10px] font-mono text-emerald-400 truncate">
            {title || rawFn}
          </span>
        </div>
        <span className="text-[9px] font-mono text-slate-500">
          [{minX}, {maxX}]
        </span>
      </div>

      {/* SVG Canvas Plot */}
      <div className="relative w-full flex justify-center items-center bg-slate-900/90 py-1">
        <svg
          ref={containerRef}
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-auto max-h-[220px] overflow-visible"
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          {/* Subtle Grid Lines */}
          <line x1={padding} y1={originY} x2={width - padding} y2={originY} stroke="#334155" strokeWidth="1" strokeDasharray={originY === padding || originY === height - padding ? "2 2" : "none"} />
          <line x1={originX} y1={padding} x2={originX} y2={height - padding} stroke="#334155" strokeWidth="1" strokeDasharray={originX === padding || originX === width - padding ? "2 2" : "none"} />

          {/* Grid ticks & labels in full mode */}
          {!isThumbnail && (
            <>
              {/* X Bounds */}
              <text x={padding} y={height - 8} fill="#64748b" fontSize="9" fontFamily="monospace" textAnchor="start">
                {minX.toFixed(1)}
              </text>
              <text x={width - padding} y={height - 8} fill="#64748b" fontSize="9" fontFamily="monospace" textAnchor="end">
                {maxX.toFixed(1)}
              </text>
              <text x={width - 10} y={originY - 4} fill="#94a3b8" fontSize="9" fontFamily="monospace" textAnchor="end">
                {xLabel}
              </text>

              {/* Y Bounds */}
              <text x={originX + 4} y={padding + 8} fill="#94a3b8" fontSize="9" fontFamily="monospace">
                {yLabel}
              </text>
            </>
          )}

          {/* Plotted Function Curves */}
          {pathSegments.map((d, idx) => (
            <path
              key={idx}
              d={d}
              fill="none"
              stroke="#38bdf8"
              strokeWidth={isThumbnail ? "1.75" : "2"}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}

          {/* Active Hover Crosshair */}
          {hoverPos && !isThumbnail && (
            <>
              <circle cx={hoverPos.pixelX} cy={hoverPos.pixelY} r="4" fill="#38bdf8" stroke="#ffffff" strokeWidth="1.5" />
              <line x1={hoverPos.pixelX} y1={padding} x2={hoverPos.pixelX} y2={height - padding} stroke="#64748b" strokeWidth="0.75" strokeDasharray="3 3" />
              <line x1={padding} y1={hoverPos.pixelY} x2={width - padding} y2={hoverPos.pixelY} stroke="#64748b" strokeWidth="0.75" strokeDasharray="3 3" />
            </>
          )}
        </svg>

        {/* Hover Coordinate Tooltip */}
        {hoverPos && !isThumbnail && (
          <div 
            className="absolute top-2 right-2 bg-slate-950/90 border border-slate-700 px-2 py-1 rounded text-[10px] font-mono text-cyan-300 pointer-events-none shadow-lg"
          >
            x: {hoverPos.x.toFixed(2)}, y: {hoverPos.y.toFixed(2)}
          </div>
        )}
      </div>
    </div>
  );
}
