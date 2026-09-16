import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The console is a static export served same-origin by the local
  // Pilot Site Agent service; it has no server-side runtime of its own.
  output: "export",
};

export default nextConfig;
