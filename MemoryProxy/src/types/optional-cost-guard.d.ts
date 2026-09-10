/** Public builds do not ship the private cost-guard extension. */
declare module "@context-proxy/cost-guard" {
  export function createCosStorageAdapter(...args: unknown[]): unknown;
  export function createStorageAdapter(...args: unknown[]): unknown;
  export function openKernelStsCosBackend(...args: unknown[]): unknown;
  const extension: Record<string, unknown>;
  export default extension;
}
