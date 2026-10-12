import Link from "next/link";

export default function NotFound() {
  return (
    <main className="auth">
      <section className="auth-card">
        <p className="kicker">Orion</p>
        <h1>Page not found</h1>
        <p className="lead">That route is not part of the control plane.</p>
        <Link href="/" className="btn" style={{ display: "inline-block", textAlign: "center" }}>
          Sign in
        </Link>
      </section>
    </main>
  );
}
