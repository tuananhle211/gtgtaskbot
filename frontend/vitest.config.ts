import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // ``jsx: "preserve"`` in tsconfig is for Next's compiler; vitest transforms
  // the test files itself and needs the automatic runtime, or every JSX call
  // site looks for a `React` binding the files deliberately do not import.
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Brand fonts (src/app/fonts.ts) are a Next build feature; see the stub.
      "next/font/google": path.resolve(__dirname, "./tests/next-font-stub.ts"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
