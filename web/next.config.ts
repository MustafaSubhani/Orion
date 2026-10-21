import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/graphs/:path*",
        destination: "http://127.0.0.1:8000/graphs/:path*",
      },
    ];
  },
};

export default nextConfig;
