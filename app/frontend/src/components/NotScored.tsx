import type { ExcludedPillar } from '../types';

// One wording for every place a "not scored" pillar appears (card, detail, overall),
// so users always see why the pillar has no score.
export const NOT_SCORED_EXPLAINER =
  "The app couldn't measure this pillar, so it's left out of the overall score instead of counting as 0.";

// The line under the overall score: which pillars weren't scored, and why.
export function ExcludedPillarsNote({ excluded }: { excluded?: ExcludedPillar[] }) {
  if (!excluded?.length) return null;
  const many = excluded.length > 1;
  return (
    <div className="text-xs text-ink-500 leading-relaxed mt-1" data-testid="excluded-pillars">
      <p>
        <span className="font-semibold">Not scored:</span> {excluded.map((e) => e.name).join(', ')}. The app
        couldn&apos;t measure {many ? 'these pillars' : 'this pillar'}, so the overall score averages the other
        pillars instead of counting {many ? 'them' : 'it'} as 0.
      </p>
      <ul className="list-disc pl-4 mt-0.5">
        {excluded.map((e) => (
          <li key={e.key}>
            {e.name}: {e.reason || 'the data it needs could not be read.'}
          </li>
        ))}
      </ul>
    </div>
  );
}
