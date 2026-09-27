"use client";
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html><body style={{ background: "#07090e", color: "#e7ebf3", fontFamily: "system-ui", display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div style={{ textAlign: "center" }}>
        <h2 style={{ fontSize: 20, fontWeight: 600 }}>Something went wrong</h2>
        <p style={{ color: "#9aa4b8", marginTop: 8 }}>An unexpected error occurred.</p>
        <button onClick={reset} style={{ marginTop: 16, padding: "8px 16px", borderRadius: 10, background: "#4c8dff", color: "white", border: 0, cursor: "pointer" }}>Try again</button>
      </div>
    </body></html>
  );
}
