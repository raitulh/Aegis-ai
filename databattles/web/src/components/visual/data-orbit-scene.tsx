"use client";

/**
 * WebGL "Data Orbit": a glowing data core with the six platform concepts orbiting on a ring, signal
 * pulses flowing from the core to each concept and around the ring in loop order, and a sparse dust field.
 *
 * Loaded with next/dynamic (ssr: false) only when WebGL is available and motion is allowed — see
 * `data-orbit.tsx` for detection, fallback and visibility handling. Everything animates through refs in
 * `useFrame`; React never re-renders per frame.
 */

import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import * as THREE from "three";

import type { OrbitConcept } from "./orbit-concepts";

export type OrbitQuality = "high" | "low";

const RING_RADIUS = 2.85;

interface Palette {
  coreInner: string;
  coreRim: string;
  glow: string;
  glowOpacity: number;
  wire: string;
  wireOpacity: number;
  ring: string;
  ringOpacity: number;
  spoke: string;
  spokeOpacity: number;
  dust: string;
  dustOpacity: number;
  pulse: string;
  blending: THREE.Blending;
}

const PALETTES: Record<"dark" | "light", Palette> = {
  dark: {
    coreInner: "#2b1d86",
    coreRim: "#d2c8ff",
    glow: "#8b6dff",
    glowOpacity: 0.55,
    wire: "#6ee7f5",
    wireOpacity: 0.2,
    ring: "#9aa8ff",
    ringOpacity: 0.22,
    spoke: "#a998ff",
    spokeOpacity: 0.16,
    dust: "#c3cbff",
    dustOpacity: 0.55,
    pulse: "#e9fbff",
    blending: THREE.AdditiveBlending,
  },
  light: {
    coreInner: "#4a2de0",
    coreRim: "#c9bcff",
    glow: "#7c5cff",
    glowOpacity: 0.28,
    wire: "#0891b2",
    wireOpacity: 0.32,
    ring: "#4a2de0",
    ringOpacity: 0.22,
    spoke: "#5b3df5",
    spokeOpacity: 0.2,
    dust: "#5b3df5",
    dustOpacity: 0.32,
    pulse: "#2f6bff",
    blending: THREE.NormalBlending,
  },
};

/** Deterministic PRNG so the dust field is identical on every load. */
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function radialTexture(size = 128, soft = 0.0) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.18 + soft, "rgba(255,255,255,0.55)");
  g.addColorStop(0.5, "rgba(255,255,255,0.12)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

const fresnelVertex = /* glsl */ `
  varying vec3 vN;
  varying vec3 vV;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal);
    vV = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;
const fresnelFragment = /* glsl */ `
  uniform vec3 uInner;
  uniform vec3 uRim;
  uniform float uIntensity;
  varying vec3 vN;
  varying vec3 vV;
  void main() {
    float f = pow(1.0 - max(dot(vN, vV), 0.0), 2.0);
    vec3 c = mix(uInner, uRim, f);
    gl_FragColor = vec4(c * uIntensity, 1.0);
  }
`;

function fresnelMaterial(inner: string, rim: string, intensity = 1) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uInner: { value: new THREE.Color(inner) },
      uRim: { value: new THREE.Color(rim) },
      uIntensity: { value: intensity },
    },
    vertexShader: fresnelVertex,
    fragmentShader: fresnelFragment,
  });
}

function circlePoints(radius: number, segments = 160) {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius));
  }
  return pts;
}

function nodeAngle(i: number, n: number) {
  return (i / n) * Math.PI * 2 - Math.PI / 2;
}

interface WorldProps {
  concepts: OrbitConcept[];
  palette: Palette;
  theme: "dark" | "light";
  quality: OrbitQuality;
  activeRef: MutableRefObject<number | null>;
  pointerRef: MutableRefObject<{ x: number; y: number }>;
  scrollRef: MutableRefObject<number>;
  labelsRef: MutableRefObject<(HTMLDivElement | null)[]>;
  onFirstFrame: () => void;
}

function World({ concepts, palette, theme, quality, activeRef, pointerRef, scrollRef, labelsRef, onFirstFrame }: WorldProps) {
  const root = useRef<THREE.Group>(null);
  const spin = useRef<THREE.Group>(null);
  const wire = useRef<THREE.LineSegments>(null);
  const innerRing = useRef<THREE.Group>(null);
  const outerRing = useRef<THREE.Group>(null);
  const dust = useRef<THREE.Points>(null);
  const nodeRefs = useRef<(THREE.Group | null)[]>([]);
  const haloRefs = useRef<(THREE.Sprite | null)[]>([]);
  const first = useRef(true);
  const n = concepts.length;
  const baseZ = quality === "low" ? 11.2 : 10;

  const glowTex = useMemo(() => radialTexture(128), []);
  const dotTex = useMemo(() => radialTexture(64, 0.1), []);

  const objects = useMemo(() => {
    const ringMat = (opacity: number) =>
      new THREE.LineBasicMaterial({ color: palette.ring, transparent: true, opacity, depthWrite: false, blending: palette.blending });

    const mainRing = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePoints(RING_RADIUS)), ringMat(palette.ringOpacity * 1.5));
    const ringA = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePoints(1.75, 128)), ringMat(palette.ringOpacity));
    const ringBGeom = new THREE.BufferGeometry().setFromPoints(circlePoints(3.95, 200));
    const ringB = new THREE.LineLoop(
      ringBGeom,
      new THREE.LineDashedMaterial({ color: palette.ring, transparent: true, opacity: palette.ringOpacity, dashSize: 0.08, gapSize: 0.12, depthWrite: false, blending: palette.blending }),
    );
    ringB.computeLineDistances();
    const ringC = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePoints(5.2, 220)), ringMat(palette.ringOpacity * 0.45));

    // Spokes: core → each concept, coloured by the concept.
    const spokes = concepts.map((c, i) => {
      const a = nodeAngle(i, n);
      const geom = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(Math.cos(a) * RING_RADIUS, 0, Math.sin(a) * RING_RADIUS),
      ]);
      return new THREE.Line(
        geom,
        new THREE.LineBasicMaterial({ color: theme === "dark" ? c.color : c.colorLight, transparent: true, opacity: palette.spokeOpacity, depthWrite: false, blending: palette.blending }),
      );
    });

    // Satellites riding the inner/outer rings (submissions, scores, credentials in transit).
    const satGeom = (count: number, radius: number) => {
      const arr = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const a = (i / count) * Math.PI * 2 + i * 0.37;
        arr[i * 3] = Math.cos(a) * radius;
        arr[i * 3 + 1] = 0;
        arr[i * 3 + 2] = Math.sin(a) * radius;
      }
      return new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(arr, 3));
    };
    const satMat = new THREE.PointsMaterial({ color: palette.pulse, size: 0.09, map: dotTex, transparent: true, depthWrite: false, opacity: 0.9, blending: palette.blending });
    const satsA = new THREE.Points(satGeom(5, 1.75), satMat);
    const satsB = new THREE.Points(satGeom(9, 3.95), satMat);

    // Dust: a flattened shell around the system.
    const rand = mulberry32(7);
    const count = quality === "low" ? 260 : 720;
    const dustArr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = 3.3 + rand() * 4.6;
      const theta = rand() * Math.PI * 2;
      const phi = Math.acos(2 * rand() - 1);
      dustArr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      dustArr[i * 3 + 1] = r * Math.cos(phi) * 0.42;
      dustArr[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const dustPoints = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(dustArr, 3)),
      new THREE.PointsMaterial({ color: palette.dust, size: quality === "low" ? 0.06 : 0.05, map: dotTex, transparent: true, opacity: palette.dustOpacity, depthWrite: false, blending: palette.blending }),
    );

    // Pulses: one per spoke (core → concept) plus one travelling around the ring in loop order.
    const pulseArr = new Float32Array((n + 1) * 3);
    const pulses = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(pulseArr, 3)),
      new THREE.PointsMaterial({ color: palette.pulse, size: 0.16, map: glowTex, transparent: true, depthWrite: false, blending: palette.blending }),
    );

    const wireGeom = new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.02, 1));
    const wireMat = new THREE.LineBasicMaterial({ color: palette.wire, transparent: true, opacity: palette.wireOpacity, depthWrite: false, blending: palette.blending });

    return { mainRing, ringA, ringB, ringC, spokes, satsA, satsB, dustPoints, pulses, wireGeom, wireMat };
  }, [concepts, n, palette, theme, quality, dotTex, glowTex]);

  const coreMat = useMemo(() => fresnelMaterial(palette.coreInner, palette.coreRim, theme === "dark" ? 1.15 : 1), [palette, theme]);
  const nodeMats = useMemo(
    () =>
      concepts.map((c) => {
        const col = new THREE.Color(theme === "dark" ? c.color : c.colorLight);
        const inner = col.clone().multiplyScalar(theme === "dark" ? 0.55 : 0.85);
        const rim = col.clone().lerp(new THREE.Color("#ffffff"), theme === "dark" ? 0.55 : 0.35);
        return fresnelMaterial(`#${inner.getHexString()}`, `#${rim.getHexString()}`, theme === "dark" ? 1.35 : 1.05);
      }),
    [concepts, theme],
  );

  // Free GPU resources when the palette/quality changes or the scene unmounts.
  useEffect(() => {
    return () => {
      const { mainRing, ringA, ringB, ringC, spokes, satsA, satsB, dustPoints, pulses, wireGeom, wireMat } = objects;
      [mainRing, ringA, ringB, ringC, ...spokes].forEach((o) => {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      });
      [satsA, satsB, dustPoints, pulses].forEach((o) => {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      });
      wireGeom.dispose();
      wireMat.dispose();
    };
  }, [objects]);
  useEffect(() => () => coreMat.dispose(), [coreMat]);
  useEffect(() => () => nodeMats.forEach((m) => m.dispose()), [nodeMats]);
  useEffect(() => () => {
    glowTex.dispose();
    dotTex.dispose();
  }, [glowTex, dotTex]);

  // Mutable per-frame state lives in refs: three.js objects are imperative by nature.
  const objectsRef = useRef(objects);
  useEffect(() => {
    objectsRef.current = objects;
  }, [objects]);
  const scratch = useRef<{ tmp: THREE.Vector3; camSpace: THREE.Vector3; coreCam: THREE.Vector3 } | null>(null);

  useFrame((state, delta) => {
    const dt = Math.min(delta, 0.05);
    const t = state.clock.elapsedTime;
    const { camera, size } = state;
    const o = objectsRef.current;
    scratch.current ??= { tmp: new THREE.Vector3(), camSpace: new THREE.Vector3(), coreCam: new THREE.Vector3() };
    const { tmp, camSpace, coreCam } = scratch.current;

    // Camera: pointer parallax + a touch of depth as the hero scrolls away.
    const p = pointerRef.current;
    const k = 1 - Math.exp(-dt * 2.6);
    const depth = Math.min(scrollRef.current, 700) / 700;
    camera.position.x += (p.x * 0.85 - camera.position.x) * k;
    camera.position.y += (0.55 + p.y * 0.55 - camera.position.y) * k;
    camera.position.z += (baseZ + depth * 1.8 - camera.position.z) * k;
    camera.lookAt(0, 0, 0);

    if (root.current) {
      root.current.rotation.x = 0.42 + p.y * 0.06;
      root.current.rotation.z = -0.12 + p.x * 0.04;
    }
    if (spin.current) spin.current.rotation.y += dt * 0.045;
    if (wire.current) {
      wire.current.rotation.y -= dt * 0.12;
      wire.current.rotation.x += dt * 0.05;
    }
    if (innerRing.current) innerRing.current.rotation.y += dt * 0.32;
    if (outerRing.current) outerRing.current.rotation.y -= dt * 0.08;
    if (dust.current) dust.current.rotation.y += dt * 0.012;

    // Concept nodes: breathe; the hovered one swells and lights its spoke.
    const active = activeRef.current;
    for (let i = 0; i < n; i++) {
      const g = nodeRefs.current[i];
      if (!g) continue;
      const target = active === i ? 1.75 : 1 + Math.sin(t * 1.4 + i) * 0.05;
      const s = g.scale.x + (target - g.scale.x) * (1 - Math.exp(-dt * 8));
      g.scale.setScalar(s);
      const halo = haloRefs.current[i];
      if (halo) {
        const mat = halo.material as THREE.SpriteMaterial;
        const to = active === i ? 1 : active === null ? 0.62 : 0.32;
        mat.opacity += (to - mat.opacity) * (1 - Math.exp(-dt * 6));
      }
      const spoke = o.spokes[i].material as THREE.LineBasicMaterial;
      const so = active === i ? 0.75 : palette.spokeOpacity;
      spoke.opacity += (so - spoke.opacity) * (1 - Math.exp(-dt * 6));
    }

    // Pulses.
    const pos = o.pulses.geometry.getAttribute("position") as THREE.BufferAttribute;
    for (let i = 0; i < n; i++) {
      const a = nodeAngle(i, n);
      const f = (t * 0.32 + i / n) % 1;
      const r = f * RING_RADIUS;
      pos.setXYZ(i, Math.cos(a) * r, 0, Math.sin(a) * r);
    }
    const flow = nodeAngle(0, n) + ((t * 0.18) % 1) * Math.PI * 2;
    pos.setXYZ(n, Math.cos(flow) * RING_RADIUS, 0, Math.sin(flow) * RING_RADIUS);
    pos.needsUpdate = true;

    // Project concept labels onto the DOM overlay (dim when behind the core).
    const core = spin.current;
    if (core) core.getWorldPosition(coreCam).applyMatrix4(camera.matrixWorldInverse);
    for (let i = 0; i < n; i++) {
      const g = nodeRefs.current[i];
      const el = labelsRef.current[i];
      if (!g || !el) continue;
      g.getWorldPosition(tmp);
      camSpace.copy(tmp).applyMatrix4(camera.matrixWorldInverse);
      tmp.project(camera);
      const x = (tmp.x * 0.5 + 0.5) * size.width;
      const y = (-tmp.y * 0.5 + 0.5) * size.height;
      const behind = camSpace.z < coreCam.z;
      el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, calc(-100% - 14px))`;
      el.style.opacity = active === i ? "1" : behind ? "0.55" : "1";
      el.style.zIndex = behind ? "0" : "1";
    }

    if (first.current) {
      first.current = false;
      onFirstFrame();
    }
  });

  return (
    <group ref={root}>
      <group ref={spin}>
        {/* Core */}
        <mesh material={coreMat}>
          <sphereGeometry args={[0.62, quality === "low" ? 32 : 48, quality === "low" ? 32 : 48]} />
        </mesh>
        <lineSegments ref={wire} geometry={objects.wireGeom} material={objects.wireMat} />
        <sprite scale={[5.6, 5.6, 1]}>
          <spriteMaterial map={glowTex} color={palette.glow} transparent opacity={palette.glowOpacity} depthWrite={false} blending={palette.blending} />
        </sprite>

        <primitive object={objects.mainRing} />
        {objects.spokes.map((s, i) => (
          <primitive key={i} object={s} />
        ))}

        {/* Concept nodes on the main ring */}
        {concepts.map((c, i) => {
          const a = nodeAngle(i, n);
          return (
            <group
              key={c.key}
              position={[Math.cos(a) * RING_RADIUS, 0, Math.sin(a) * RING_RADIUS]}
              ref={(el) => {
                nodeRefs.current[i] = el;
              }}
            >
              <mesh material={nodeMats[i]}>
                <sphereGeometry args={[0.16, 24, 24]} />
              </mesh>
              <sprite
                scale={[1.3, 1.3, 1]}
                ref={(el) => {
                  haloRefs.current[i] = el;
                }}
              >
                <spriteMaterial map={glowTex} color={theme === "dark" ? c.color : c.colorLight} transparent opacity={0.5} depthWrite={false} blending={palette.blending} />
              </sprite>
            </group>
          );
        })}
        <primitive object={objects.pulses} />
      </group>

      <group ref={innerRing} rotation={[0.55, 0, 0.35]}>
        <primitive object={objects.ringA} />
        <primitive object={objects.satsA} />
      </group>
      <group ref={outerRing} rotation={[-0.18, 0, -0.22]}>
        <primitive object={objects.ringB} />
        <primitive object={objects.satsB} />
      </group>
      <primitive object={objects.ringC} />
      <primitive ref={dust} object={objects.dustPoints} />
    </group>
  );
}

export default function DataOrbitScene({
  concepts,
  quality,
  theme,
  running,
  activeRef,
  labelsRef,
  onReady,
  onLost,
}: {
  concepts: OrbitConcept[];
  quality: OrbitQuality;
  theme: "dark" | "light";
  running: boolean;
  activeRef: MutableRefObject<number | null>;
  labelsRef: MutableRefObject<(HTMLDivElement | null)[]>;
  onReady: () => void;
  onLost: () => void;
}) {
  const pointerRef = useRef({ x: 0, y: 0 });
  const scrollRef = useRef(0);
  const palette = PALETTES[theme];

  useEffect(() => {
    const move = (e: PointerEvent) => {
      pointerRef.current.x = (e.clientX / window.innerWidth) * 2 - 1;
      pointerRef.current.y = -((e.clientY / window.innerHeight) * 2 - 1);
    };
    const scroll = () => {
      scrollRef.current = window.scrollY;
    };
    scroll();
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("scroll", scroll, { passive: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("scroll", scroll);
    };
  }, []);

  return (
    <Canvas
      className="!absolute inset-0"
      style={{ pointerEvents: "none" }}
      frameloop={running ? "always" : "never"}
      dpr={quality === "low" ? [1, 1.25] : [1, 1.75]}
      camera={{ fov: 38, position: [0, 0.55, quality === "low" ? 11.2 : 10], near: 0.1, far: 60 }}
      gl={{ antialias: quality === "high", alpha: true, powerPreference: "high-performance", preserveDrawingBuffer: false }}
      onCreated={({ gl }) => {
        gl.setClearColor(0x000000, 0);
        gl.domElement.addEventListener("webglcontextlost", (e) => {
          e.preventDefault();
          onLost();
        });
      }}
    >
      <World
        concepts={concepts}
        palette={palette}
        theme={theme}
        quality={quality}
        activeRef={activeRef}
        pointerRef={pointerRef}
        scrollRef={scrollRef}
        labelsRef={labelsRef}
        onFirstFrame={onReady}
      />
    </Canvas>
  );
}
