export default function Unauth() {
  return (
    <div className="wrap" style={{ textAlign: "center", paddingTop: 80 }}>
      <h1>Private dashboard</h1>
      <div className="sub" style={{ marginTop: 12 }}>
        This dashboard is gated. Use the personal link you were sent.
      </div>
    </div>
  );
}
