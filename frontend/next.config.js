/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Old addresses keep working. The earlier optimization product's pages are not part of the
  // customer product, so a bookmark to one lands on the overview.
  async redirects() {
    const toOverview = (source) => ({ source, destination: "/overview", permanent: false });
    return [
      { source: "/change-cases", destination: "/programs", permanent: false },
      { source: "/change-cases/:path*", destination: "/programs/:path*", permanent: false },
      { source: "/team", destination: "/settings?tab=team", permanent: false },
      toOverview("/dashboard"),
      toOverview("/projects"),
      toOverview("/projects/:path*"),
      toOverview("/datasets"),
      toOverview("/datasets/:path*"),
      toOverview("/experiments"),
      toOverview("/experiments/:path*"),
      toOverview("/reports"),
      toOverview("/reports/:path*"),
      toOverview("/billing"),
      toOverview("/billing/:path*"),
    ];
  },
};

module.exports = nextConfig;
