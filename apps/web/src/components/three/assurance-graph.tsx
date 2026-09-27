"use client";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Float, MeshDistortMaterial, OrbitControls, Sparkles } from "@react-three/drei";
import { useMemo, useRef, useState, useEffect } from "react";
import * as THREE from "three";

/**
 * World-Class 3D AI Assurance Hologram Centerpiece (React 19 native).
 * 
 * Architecture:
 * - Pure WebGL 3D scene (no React 19 sub-root unmount conflicts).
 * - Multi-layer Quantum Core with vertex distortion & gyroscopic rings.
 * - 6 Orbiting Assurance Nodes with pulsating radar halos & traveling photon beams.
 * - Dynamic mouse tracking parallax with smooth inertia damping.
 * - Overlay HUD with live telemetry badges synchronized bi-directionally with the 3D scene.
 */

type AssuranceDomain = {
  id: string;
  name: string;
  metric: string;
  detail: string;
  pos: [number, number, number];
  color: string;
  accent: string;
};

const DOMAINS: AssuranceDomain[] = [
  {
    id: "fairness",
    name: "Fairness",
    metric: "0.0% Disparity",
    detail: "Counterfactual Parity Verified",
    pos: [-3.3, 1.8, 0.4],
    color: "#4c8dff",
    accent: "#6ba3ff",
  },
  {
    id: "truth",
    name: "Grounding",
    metric: "100% Evidenced",
    detail: "Claim-Level Proof Chain",
    pos: [3.3, 1.7, -0.2],
    color: "#3ecf8e",
    accent: "#6ee7b7",
  },
  {
    id: "safety",
    name: "Safety",
    metric: "99.8% Containment",
    detail: "Harmful Probe Rejection",
    pos: [2.8, -1.9, 0.8],
    color: "#ff5c6c",
    accent: "#ff8a96",
  },
  {
    id: "security",
    name: "Security",
    metric: "Zero Leakage",
    detail: "Jailbreak & Injection Shield",
    pos: [-3.0, -1.7, -0.6],
    color: "#a78bfa",
    accent: "#c4b5fd",
  },
  {
    id: "privacy",
    name: "Privacy",
    metric: "PII Nullified",
    detail: "Automated Redaction Active",
    pos: [0.0, 2.9, -0.8],
    color: "#38bdf8",
    accent: "#7dd3fc",
  },
  {
    id: "governance",
    name: "Compliance",
    metric: "NIST · ISO 42001",
    detail: "Automated Control Mapping",
    pos: [0.0, -2.8, 0.6],
    color: "#f5c451",
    accent: "#fde047",
  },
];

/* Central Holographic Quantum Core */
function QuantumCore({ hovered }: { hovered: string | null }) {
  const meshRef = useRef<THREE.Mesh>(null);
  const cageRef = useRef<THREE.Mesh>(null);
  const ring1Ref = useRef<THREE.Mesh>(null);
  const ring2Ref = useRef<THREE.Mesh>(null);

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    if (meshRef.current) {
      meshRef.current.rotation.y += delta * 0.45;
      meshRef.current.rotation.x = Math.sin(t * 0.5) * 0.2;
    }
    if (cageRef.current) {
      cageRef.current.rotation.y -= delta * 0.35;
      cageRef.current.rotation.z += delta * 0.2;
    }
    if (ring1Ref.current) {
      ring1Ref.current.rotation.x = t * 0.6;
      ring1Ref.current.rotation.y = t * 0.4;
    }
    if (ring2Ref.current) {
      ring2Ref.current.rotation.y = -t * 0.5;
      ring2Ref.current.rotation.z = t * 0.3;
    }
  });

  return (
    <group position={[0, 0, 0]}>
      {/* Inner morphing iridescent core */}
      <mesh ref={meshRef}>
        <sphereGeometry args={[1.05, 64, 64]} />
        <MeshDistortMaterial
          color={hovered ? "#3ecf8e" : "#4c8dff"}
          emissive={hovered ? "#2b6cb0" : "#1e3a8a"}
          emissiveIntensity={hovered ? 1.0 : 0.65}
          roughness={0.15}
          metalness={0.85}
          distort={0.42}
          speed={2.2}
          transparent
          opacity={0.9}
        />
      </mesh>

      {/* Outer rotating geometric icosahedron cage */}
      <mesh ref={cageRef}>
        <icosahedronGeometry args={[1.65, 1]} />
        <meshStandardMaterial
          color="#6ba3ff"
          emissive="#4c8dff"
          emissiveIntensity={0.7}
          wireframe
          transparent
          opacity={0.38}
        />
      </mesh>

      {/* Gyroscopic Quantum Orbit Rings */}
      <mesh ref={ring1Ref}>
        <torusGeometry args={[2.05, 0.022, 16, 100]} />
        <meshStandardMaterial
          color="#38bdf8"
          emissive="#38bdf8"
          emissiveIntensity={1.2}
          transparent
          opacity={0.65}
        />
      </mesh>
      <mesh ref={ring2Ref} rotation={[Math.PI / 3, 0, 0]}>
        <torusGeometry args={[2.3, 0.02, 16, 100]} />
        <meshStandardMaterial
          color="#3ecf8e"
          emissive="#3ecf8e"
          emissiveIntensity={1.0}
          transparent
          opacity={0.5}
        />
      </mesh>
    </group>
  );
}

/* Domain Node with radar pulse */
function DomainNode({
  domain,
  hovered,
  onHover,
}: {
  domain: AssuranceDomain;
  hovered: boolean;
  onHover: (id: string | null) => void;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const haloRef = useRef<THREE.Mesh>(null);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    if (meshRef.current) {
      meshRef.current.position.y = domain.pos[1] + Math.sin(t * 1.2 + domain.pos[0] * 1.5) * 0.12;
      meshRef.current.position.x = domain.pos[0] + Math.cos(t * 0.8 + domain.pos[1]) * 0.06;
    }
    if (haloRef.current) {
      const s = 1 + (Math.sin(t * 2.5 + domain.pos[0]) * 0.5 + 0.5) * 0.65;
      haloRef.current.scale.set(s, s, s);
      const mat = haloRef.current.material as THREE.MeshBasicMaterial;
      if (mat) mat.opacity = (2 - s) * 0.35;
    }
  });

  return (
    <group position={domain.pos}>
      {/* Outer pulsing energy halo */}
      <mesh ref={haloRef}>
        <sphereGeometry args={[0.38, 24, 24]} />
        <meshBasicMaterial color={domain.accent} transparent opacity={0.3} wireframe />
      </mesh>

      {/* Solid emissive node sphere */}
      <mesh
        ref={meshRef}
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover(domain.id);
        }}
        onPointerOut={() => onHover(null)}
      >
        <sphereGeometry args={[hovered ? 0.35 : 0.26, 32, 32]} />
        <meshStandardMaterial
          color={domain.color}
          emissive={domain.color}
          emissiveIntensity={hovered ? 1.8 : 0.95}
          roughness={0.2}
          metalness={0.9}
        />
      </mesh>
    </group>
  );
}

/* Pulsing Data Stream Beam connecting Core to Domain Node */
function CoreBeam({ domain, active }: { domain: AssuranceDomain; active: boolean }) {
  const pulseRef = useRef<THREE.Mesh>(null);

  const points = useMemo(() => {
    const start = new THREE.Vector3(0, 0, 0);
    const end = new THREE.Vector3(...domain.pos);
    const mid = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5);
    mid.y += (domain.pos[0] > 0 ? 0.35 : -0.35);
    mid.z += 0.4;
    const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
    return curve.getPoints(28);
  }, [domain.pos]);

  const curveObject = useMemo(() => {
    const start = new THREE.Vector3(0, 0, 0);
    const end = new THREE.Vector3(...domain.pos);
    const mid = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5);
    mid.y += (domain.pos[0] > 0 ? 0.35 : -0.35);
    mid.z += 0.4;
    return new THREE.QuadraticBezierCurve3(start, mid, end);
  }, [domain.pos]);

  useFrame((state) => {
    if (pulseRef.current) {
      const speed = active ? 0.8 : 0.45;
      const t = (state.clock.elapsedTime * speed + Math.abs(domain.pos[0])) % 1;
      const pos = curveObject.getPoint(t);
      pulseRef.current.position.copy(pos);
    }
  });

  const lineGeometry = useMemo(() => {
    return new THREE.BufferGeometry().setFromPoints(points);
  }, [points]);

  const lineMaterial = useMemo(() => {
    return new THREE.LineBasicMaterial({
      color: active ? domain.color : "#1e293b",
      transparent: true,
      opacity: active ? 0.95 : 0.35,
    });
  }, [active, domain.color]);

  return (
    <group>
      <primitive object={new THREE.Line(lineGeometry, lineMaterial)} />
      {/* Traveling photon pulse */}
      <mesh ref={pulseRef}>
        <sphereGeometry args={[active ? 0.08 : 0.05, 12, 12]} />
        <meshBasicMaterial color={domain.color} />
      </mesh>
    </group>
  );
}

/* Inter-Node Mesh Web Lines */
function InterNodeLinks({ hovered }: { hovered: string | null }) {
  const lines = useMemo(() => {
    const pairs: [AssuranceDomain, AssuranceDomain][] = [
      [DOMAINS[0], DOMAINS[1]],
      [DOMAINS[1], DOMAINS[2]],
      [DOMAINS[2], DOMAINS[5]],
      [DOMAINS[5], DOMAINS[3]],
      [DOMAINS[3], DOMAINS[0]],
      [DOMAINS[0], DOMAINS[4]],
      [DOMAINS[4], DOMAINS[1]],
    ];
    return pairs;
  }, []);

  return (
    <group>
      {lines.map(([a, b], idx) => {
        const isLit = hovered === a.id || hovered === b.id;
        const geom = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(...a.pos),
          new THREE.Vector3(...b.pos),
        ]);
        return (
          <primitive
            key={idx}
            object={
              new THREE.Line(
                geom,
                new THREE.LineBasicMaterial({
                  color: isLit ? "#6ba3ff" : "#1e293b",
                  transparent: true,
                  opacity: isLit ? 0.8 : 0.22,
                })
              )
            }
          />
        );
      })}
    </group>
  );
}

/* Main Interactive 3D Scene */
function AssuranceScene({
  hovered,
  setHovered,
}: {
  hovered: string | null;
  setHovered: (id: string | null) => void;
}) {
  const sceneRef = useRef<THREE.Group>(null);
  const { pointer } = useThree();

  // Smooth cursor tracking parallax inertia
  useFrame((_, delta) => {
    if (sceneRef.current) {
      const targetRotationY = pointer.x * 0.38;
      const targetRotationX = -pointer.y * 0.26;
      sceneRef.current.rotation.y += (targetRotationY - sceneRef.current.rotation.y) * (delta * 3.5);
      sceneRef.current.rotation.x += (targetRotationX - sceneRef.current.rotation.x) * (delta * 3.5);
    }
  });

  return (
    <group ref={sceneRef}>
      {/* Lighting */}
      <ambientLight intensity={0.7} />
      <directionalLight position={[10, 10, 5]} intensity={1.4} color="#6ba3ff" />
      <pointLight position={[-8, -6, 4]} intensity={25} color="#3ecf8e" />
      <pointLight position={[6, -6, -4]} intensity={30} color="#ff5c6c" />
      <pointLight position={[0, 8, 2]} intensity={20} color="#a78bfa" />

      {/* Cybernetic Particle Stardust Swarm */}
      <Sparkles
        count={85}
        scale={11}
        size={2.4}
        speed={0.35}
        noise={0.3}
        color="#6ba3ff"
        opacity={0.65}
      />
      <Sparkles
        count={45}
        scale={8}
        size={3.2}
        speed={0.25}
        noise={0.5}
        color="#3ecf8e"
        opacity={0.5}
      />

      <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.3}>
        {/* Core */}
        <QuantumCore hovered={hovered} />

        {/* Inter-node perimeter links */}
        <InterNodeLinks hovered={hovered} />

        {/* Central Beams */}
        {DOMAINS.map((domain) => (
          <CoreBeam
            key={domain.id}
            domain={domain}
            active={hovered === domain.id || hovered === null}
          />
        ))}

        {/* Domain Nodes */}
        {DOMAINS.map((domain) => (
          <DomainNode
            key={domain.id}
            domain={domain}
            hovered={hovered === domain.id}
            onHover={setHovered}
          />
        ))}
      </Float>
    </group>
  );
}

/* Fallback for environments without WebGL */
function Fallback() {
  return (
    <div className="relative flex h-full w-full items-center justify-center rounded-2xl border border-slate-800 bg-slate-950/60 p-6 backdrop-blur-xl">
      <div className="text-center">
        <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-blue-500/10 text-blue-400">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
        </div>
        <p className="text-sm font-semibold text-slate-200">Aegis Neural Assurance Grid</p>
        <p className="mt-1 text-xs text-slate-400">Continuous 3D verification online</p>
      </div>
    </div>
  );
}

export default function AssuranceGraph() {
  const [webgl, setWebgl] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    try {
      const c = document.createElement("canvas");
      setWebgl(!!(c.getContext("webgl2") || c.getContext("webgl")));
    } catch {
      setWebgl(false);
    }
  }, []);

  if (!webgl) return <Fallback />;

  return (
    <div className="relative h-full w-full overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/40 backdrop-blur-sm">
      {/* Background ambient radial glow */}
      <div className="pointer-events-none absolute inset-0 bg-radial from-blue-500/10 via-transparent to-transparent opacity-80" />

      {/* Top Telemetry Header HUD */}
      <div className="pointer-events-none absolute left-4 right-4 top-4 z-10 flex items-center justify-between">
        <div className="flex items-center gap-2 rounded-full border border-sky-500/30 bg-slate-950/80 px-3 py-1 text-xs backdrop-blur-md">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-mono text-[11px] font-semibold tracking-wider text-sky-200 uppercase">
            Aegis Core · Active
          </span>
        </div>
        <div className="hidden font-mono text-[10px] text-slate-400 sm:block">
          6 Evaluators Online
        </div>
      </div>

      {/* Pure Three.js Canvas */}
      <Canvas
        camera={{ position: [0, 0, 7.8], fov: 46 }}
        dpr={[1, 1.5]}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: "high-performance",
        }}
        className="cursor-grab active:cursor-grabbing"
      >
        <AssuranceScene hovered={hovered} setHovered={setHovered} />
        <OrbitControls
          enableZoom={false}
          enablePan={false}
          maxPolarAngle={Math.PI / 1.7}
          minPolarAngle={Math.PI / 2.5}
          rotateSpeed={0.5}
        />
      </Canvas>

      {/* Interactive Domain Telemetry Badges Grid (HTML Overlay - Zero React 19 Unmount Conflicts) */}
      <div className="pointer-events-none absolute bottom-9 left-2 right-2 z-10 grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:gap-2">
        {DOMAINS.map((domain) => {
          const isSelected = hovered === domain.id;
          return (
            <div
              key={domain.id}
              onMouseEnter={() => setHovered(domain.id)}
              onMouseLeave={() => setHovered(null)}
              className={`pointer-events-auto cursor-pointer rounded-lg border px-2 py-1.5 backdrop-blur-md transition-all duration-200 ${
                isSelected
                  ? "border-sky-400/80 bg-slate-900/90 shadow-[0_0_15px_rgba(76,141,255,0.4)] scale-[1.02]"
                  : "border-slate-800/80 bg-slate-950/60 hover:border-slate-700 hover:bg-slate-900/60"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: domain.color, boxShadow: `0 0 6px ${domain.color}` }}
                  />
                  {domain.name}
                </span>
                <span className="font-mono text-[10px] font-semibold text-emerald-400">
                  {domain.metric}
                </span>
              </div>
              {isSelected && (
                <div className="mt-0.5 text-[9px] text-slate-400 truncate">
                  {domain.detail}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottom Hint */}
      <div className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5 text-[10px] font-mono text-slate-500">
        <span className="h-1 w-1 rounded-full bg-sky-400 animate-ping" />
        Interactive 3D Grid · Drag to Rotate
      </div>
    </div>
  );
}
