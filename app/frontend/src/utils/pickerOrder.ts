// Picker list ordering (#65): selected items first, each group keeping its original
// order. The caller decides which selection to test: CatalogFilter passes a snapshot
// taken when the list opens or the search text changes, so ticking a box never moves
// it; SchemaFilter passes the live single selection (a pick closes its list).
export function selectedFirst<T>(items: readonly T[], isSelected: (item: T) => boolean): T[] {
  const on: T[] = [];
  const off: T[] = [];
  for (const item of items) (isSelected(item) ? on : off).push(item);
  return [...on, ...off];
}
