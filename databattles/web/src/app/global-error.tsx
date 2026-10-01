"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui", background: "#0a0c11", color: "#e9ecf3", display: "grid", placeItems: "center", minHeight: "100vh" }}>
        <div style={{ textAlign: "center" }}>
          <h1>DataBattles is having trouble</h1>
          <p>Please refresh the page. If this keeps happening, try again later.</p>
          <button onClick={reset} style={{ marginTop: 16, padding: "8px 16px" }}>Retry</button>
        </div>
      </body>
    </html>
  );
}
