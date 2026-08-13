import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // better-sqlite3 is a native module — it must stay external rather than be
  // bundled, or the .node binary fails to resolve at runtime.
  serverExternalPackages: ["better-sqlite3"],
  // Ship only the traced dependency graph to the container: ~50 MB against the
  // 376 MB node_modules tree it replaces. The Dockerfile copies .next/static
  // and public/ separately, since output tracing deliberately excludes them.
  output: "standalone",
};

export default nextConfig;
