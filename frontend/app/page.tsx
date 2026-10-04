"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// The product's home is the qualification diagnostic list. (This route used to
// render a duplicate of another page.)
export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/change-cases");
  }, [router]);
  return null;
}
