"use client";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Group } from "three";
import { useClientValue, usePrefersReducedMotion } from "@/lib/hooks";

/**
 * Trust Core: a decorative visual of the assurance loop — a core (evidence) inside three orbits
 * (policy, runtime, tests). It carries no data and makes no claims; it is aria-hidden.
 *
 * Performance & accessibility: loaded lazily on the client only; renders on demand and stops when
 * scrolled out of view; with `prefers-reduced-motion` it renders one still frame; without WebGL it
 * falls back to a static SVG.
 */
const RINGS = [
  { radius: 1.55, tilt: [0.35, 0, 0.2] as const, speed: 0.12, color: "#6ba3ff", nodes: 5 },
  { radius: 2.05, tilt: [-0.55, 0.3, 0] as const, speed: -0.08, color: "#34d399", nodes: 7 },
  { radius: 2.55, tilt: [1.2, -0.2, 0.4] as const, speed: 0.05, color: "#a78bfa", nodes: 9 },
];

let webgl: boolean | undefined;
function hasWebGL() {
  if (webgl === undefined) {
    try {
      const c = document.createElement("canvas");
      webgl = !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch {
      webgl = false;
    }
  }
  return webgl;
}

function Ring({ radius, tilt, speed, color, nodes, animate }: (typeof RINGS)[number] & { animate: boolean }) {
  const ref = useRef<Group>(null);
  useFrame((_, delta) => {
    if (animate && ref.current) ref.current.rotation.z += delta * speed;
  });
  const points = useMemo(() => Array.from({ length: nodes }, (_, i) => (i / nodes) * Math.PI * 2), [nodes]);
  return (
    <group rotation={[tilt[0], tilt[1], tilt[2]]}>
      <group ref={ref}>
        <mesh>
          <torusGeometry args={[radius, 0.006, 8, 160]} />
          <meshBasicMaterial color={color} transparent opacity={0.55} />
        </mesh>
        {points.map((a) => (
          <mesh key={a} position={[Math.cos(a) * radius, Math.sin(a) * radius, 0]}>
            <sphereGeometry args={[0.045, 16, 16]} />
            <meshBasicMaterial color={color} />
          </mesh>
        ))}
      </group>
    </group>
  );
}

function Core({ animate }: { animate: boolean }) {
  const ref = useRef<Group>(null);
  useFrame((_, delta) => {
    if (animate && ref.current) {
      ref.current.rotation.y += delta * 0.15;
      ref.current.rotation.x += delta * 0.05;
    }
  });
  return (
    <group ref={ref}>
      <mesh>
        <icosahedronGeometry args={[0.85, 1]} />
        <meshStandardMaterial color="#4c8dff" wireframe transparent opacity={0.6} />
      </mesh>
      <mesh>
        <icosahedronGeometry args={[0.55, 2]} />
        <meshStandardMaterial color="#1d3b8a" emissive="#2f5bd3" emissiveIntensity={0.6} roughness={0.35} metalness={0.2} />
      </mesh>
    </group>
  );
}

function StaticCore() {
  return (
    <svg viewBox="0 0 400 400" className="h-full w-full" aria-hidden>
      <defs>
        <radialGradient id="tc-core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#4c8dff" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#1d3b8a" stopOpacity="0.2" />
        </radialGradient>
      </defs>
      {[150, 120, 92].map((r, i) => (
        <ellipse key={r} cx="200" cy="200" rx={r} ry={r * 0.42} fill="none" stroke={["#a78bfa", "#34d399", "#6ba3ff"][i]} strokeOpacity="0.55" transform={`rotate(${[-25, 20, 60][i]} 200 200)`} />
      ))}
      <circle cx="200" cy="200" r="48" fill="url(#tc-core)" />
    </svg>
  );
}

export default function TrustCore() {
  const reduced = usePrefersReducedMotion();
  const supported = useClientValue(hasWebGL, false);
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const el = container.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const animate = !reduced && visible;
  return (
    <div ref={container} className="h-full w-full" aria-hidden>
      {supported ? (
        <Canvas frameloop={animate ? "always" : "demand"} dpr={[1, 1.75]} camera={{ position: [0, 0, 7], fov: 45 }} gl={{ antialias: true, powerPreference: "low-power" }}>
          <ambientLight intensity={0.6} />
          <pointLight position={[4, 5, 6]} intensity={40} color="#6ba3ff" />
          <Core animate={animate} />
          {RINGS.map((r) => (
            <Ring key={r.radius} {...r} animate={animate} />
          ))}
        </Canvas>
      ) : (
        <StaticCore />
      )}
    </div>
  );
}
