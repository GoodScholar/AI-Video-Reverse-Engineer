import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom has no Web Locks; serialize callbacks to model the browser's exclusive lock.
const locks = new Map<string, Promise<unknown>>();
Object.defineProperty(navigator, "locks", { configurable: true, value: {
  request: (name: string, callback: () => unknown) => {
    const next = (locks.get(name) ?? Promise.resolve()).catch(() => undefined).then(callback);
    locks.set(name, next.catch(() => undefined));
    return next;
  },
} });
