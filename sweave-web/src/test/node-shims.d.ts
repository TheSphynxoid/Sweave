// Minimal ambient declarations for the node builtins a few tests use to
// read repo files (globals.css pin). @types/node is deliberately NOT a
// dependency (no runtime need); if it ever gets added, delete this file
// (the duplicate ambient declarations will fail the build loudly).
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
declare module "node:path" {
  export function dirname(p: string): string;
  export function resolve(...parts: string[]): string;
}
declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}
