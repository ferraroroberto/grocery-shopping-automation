// The load status of anything the PWA reads from the server, declared once.
//
// A sheet or card is waiting for its read (loading), has it (ready) or lost it
// (error). The facts a caller holds are two booleans, and deriveLoadState is the
// only code that turns them into a status, so no view spells a status word of its
// own. Both flags at once cannot happen: starting a read clears the failure first.

export const LoadState = Object.freeze({ LOADING: "loading", READY: "ready", ERROR: "error" });

export function deriveLoadState({ pending, failed }) {
  if (pending && failed) throw new Error("a read cannot be pending and failed at once");
  if (pending) return LoadState.LOADING;
  return failed ? LoadState.ERROR : LoadState.READY;
}
