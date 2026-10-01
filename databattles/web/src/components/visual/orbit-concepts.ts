/**
 * The six platform concepts the hero visualisation orbits around, in the order evidence flows through
 * DataBattles: data → learn → build → compete → verify → showcase. Shared by the WebGL scene, its CSS
 * fallback and the caption so all three always tell the same story.
 */
export interface OrbitConcept {
  key: string;
  label: string;
  caption: string;
  /** Hex colour used by the 3D node (dark theme). */
  color: string;
  /** Hex colour used by the 3D node (light theme). */
  colorLight: string;
}

export const ORBIT_CONCEPTS: OrbitConcept[] = [
  { key: "data", label: "Data", caption: "Versioned datasets with licences, previews and checksums.", color: "#3ddcf2", colorLight: "#0891b2" },
  { key: "learn", label: "Learn", caption: "Courses with quizzes and server-graded challenges.", color: "#6cc4ff", colorLight: "#0284c7" },
  { key: "build", label: "Build", caption: "Projects and open-source work, attributed to you.", color: "#6d93ff", colorLight: "#2f6bff" },
  { key: "compete", label: "Compete", caption: "Reproducible scoring on public and private leaderboards.", color: "#9b82ff", colorLight: "#5b3df5" },
  { key: "verify", label: "Verify", caption: "Certificates with checksummed IDs and a public verification page.", color: "#34d399", colorLight: "#059669" },
  { key: "showcase", label: "Showcase", caption: "A profile that separates verified results from claims.", color: "#c9bcff", colorLight: "#7c3aed" },
];
