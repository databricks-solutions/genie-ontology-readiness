import { useState } from 'react';
import type { BuildInfo } from '../types';

/** Detail lines describing the running build (tooltip, copy, error reports). */
export function buildLines(build?: BuildInfo | null): string[] {
  if (!build) return [];
  const lines = [`App build: ${build.label}`];
  if (build.commit) lines.push(`Commit: ${build.commit}${build.ref ? ` (${build.ref})` : ''}`);
  if (build.deployment_id) lines.push(`Deployment: ${build.deployment_id}`);
  if (build.deployed_at) lines.push(`Deployed: ${build.deployed_at}`);
  return lines;
}

/** Small, low-contrast build label pinned to the bottom-right corner. Hover for
 * the full commit and deployment; click to copy them (for bug reports). */
export default function BuildWatermark({ build }: { build?: BuildInfo | null }) {
  const [copied, setCopied] = useState(false);
  if (!build?.label) return null;
  const details = buildLines(build).join('\n');
  const copy = () =>
    navigator.clipboard?.writeText(details).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => undefined
    );
  return (
    <button
      type="button"
      onClick={copy}
      title={`${details}\n\nClick to copy`}
      aria-label={`App build ${build.label}. Click to copy build details.`}
      data-testid="build-watermark"
      className="fixed bottom-2 right-3 z-30 select-text rounded px-1.5 py-0.5 font-mono text-[10px] text-ink-400/70 hover:text-ink-600 hover:bg-white/80 transition-colors"
    >
      {copied ? 'Copied build details' : build.label}
    </button>
  );
}
