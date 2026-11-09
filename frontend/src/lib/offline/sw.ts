// Service worker registration (owner P08). The worker script itself is shipped by the POS module (P12) at
// /pos-sw.js; the shell only registers it when the POS feature asks for it, in secure contexts.
export async function registerServiceWorker(url = "/pos-sw.js", scope = "/m/pos"): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator) || !window.isSecureContext) return null;
  try {
    return await navigator.serviceWorker.register(url, { scope });
  } catch {
    return null;
  }
}
