/** Canonical certificate ID format, e.g. DB-7K2M-9QXA-F3. */
export const CERT_ID_RE = /^DB-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{2}$/;

/** Accepts "DB-7K2M-9QXA-F3", "db7k2m9qxaf3" or a pasted verification URL, and returns the canonical form. */
export function normalizeCertificateId(raw: string): string {
  let v = raw.trim();
  const fromUrl = v.match(/\/verify\/([^/?#\s]+)/i);
  if (fromUrl) {
    try {
      v = decodeURIComponent(fromUrl[1]);
    } catch {
      v = fromUrl[1];
    }
  }
  const compact = v.toUpperCase().replace(/[^0-9A-Z]/g, "");
  if (compact.length === 12 && compact.startsWith("DB")) return `DB-${compact.slice(2, 6)}-${compact.slice(6, 10)}-${compact.slice(10)}`;
  return v.toUpperCase();
}
