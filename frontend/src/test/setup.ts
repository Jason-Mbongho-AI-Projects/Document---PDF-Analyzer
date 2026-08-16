import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

// jsdom implements neither of these, and components legitimately use both.
globalThis.URL.createObjectURL = vi.fn(() => "blob:mock");
globalThis.URL.revokeObjectURL = vi.fn();

class MockIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// Thumbnails defer loading until visible; in tests treat everything as visible.
(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver =
  MockIntersectionObserver;
