/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The UI talks to the backend directly (C1 base URL), so no rewrites are needed.
  // Set NEXT_PUBLIC_API_URL if the backend is not on http://localhost:8000.
};

export default nextConfig;
