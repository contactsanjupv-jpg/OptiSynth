/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The earlier optimization product's pages are not part of the qualification
  // diagnostic and must not confuse a customer who types or bookmarks an old URL.
  async redirects() {
    return [
      { source: "/dashboard", destination: "/change-cases", permanent: false },
      { source: "/projects", destination: "/change-cases", permanent: false },
      { source: "/projects/:path*", destination: "/change-cases", permanent: false },
      { source: "/datasets", destination: "/change-cases", permanent: false },
      { source: "/datasets/:path*", destination: "/change-cases", permanent: false },
      { source: "/experiments", destination: "/change-cases", permanent: false },
      { source: "/experiments/:path*", destination: "/change-cases", permanent: false },
      { source: "/reports", destination: "/change-cases", permanent: false },
      { source: "/reports/:path*", destination: "/change-cases", permanent: false },
      { source: "/billing", destination: "/change-cases", permanent: false },
      { source: "/billing/:path*", destination: "/change-cases", permanent: false },
    ];
  },
};

module.exports = nextConfig;
