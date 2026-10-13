import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { FakeWebSocket } from "./fakeSocket";
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  // The live channel never touches the network in tests unless a test drives it.
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});
window.scrollTo = vi.fn();
const computedStyle = window.getComputedStyle;
window.getComputedStyle = (element) => computedStyle(element);
