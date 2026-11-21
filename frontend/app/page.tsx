"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// The product home is the overview. (This route used to
// render a duplicate of another page.)
export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/overview");
  }, [router]);
  return null;
}
