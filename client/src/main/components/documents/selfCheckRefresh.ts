// 0638 T#2 review: getRequest coalesces an identical in-flight GET, so a Self-check list refresh
// asked for while one is pending (e.g. a run-created or run-linked event arriving during the first
// load) would only receive that older response. Serialize the refreshes: at most one load runs at
// a time, a request made meanwhile reruns it once after it settles, and every caller waits until
// the latest read has been applied.
export function serializedRefresh(load: () => Promise<void>): () => Promise<void> {
  let running: Promise<void> | null = null;
  let again = false;
  return () => {
    if (running) { again = true; return running; }
    running = (async () => {
      try { do { again = false; await load(); } while (again); }
      finally { running = null; }
    })();
    return running;
  };
}
